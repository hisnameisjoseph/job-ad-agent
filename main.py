"""CLI entry point.

Fetch jobs from every enabled source, skip ones already scored and ones the
cheap pre-filters reject, score the rest CONCURRENTLY, then rank and print.

Scores are written through to the store as they arrive rather than saved once
at the end, so an interrupt (Ctrl-C, OOM, or a Lambda timeout) costs at most a
few jobs instead of the whole run.

Run:  python main.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import hashlib

from rich.console import Console
from rich.table import Table

import config
from filters import prefilter_reason
from models import ScoredJob
from profile_loader import load_profile, profile_to_text
from scoring import cache_summary, score_job, PROMPT_VERSION
from store import JsonStore, Store, job_key, make_store
from visibility import hidden_reason
from sources.adzuna import AdzunaSource
from sources.ashby import AshbySource
from sources.greenhouse import GreenhouseSource
from sources.lever import LeverSource

console = Console()


def collect_jobs() -> list:
    """Gather jobs from every enabled source, deduped by (source, id).

    ATS boards (Greenhouse, Lever, Ashby) run first because they return the
    COMPLETE description; Adzuna adds breadth but its descriptions are
    truncated to 500 characters and are flagged as such.

    A source that fails is logged and skipped: one bad board or one 429 from
    Adzuna must not discard everything else already fetched.
    """
    jobs = []
    seen: set[str] = set()

    def add(new_jobs: list) -> int:
        added = 0
        for job in new_jobs:
            key = job_key(job)
            if job.id and key not in seen:
                seen.add(key)
                jobs.append(job)
                added += 1
        return added

    def safe(label: str, fn) -> None:
        try:
            n = add(fn())
            console.print(f"  +{n} roles")
        except Exception as e:
            console.print(f"  [yellow]{label} failed, skipping: {e}[/yellow]")

    if config.ENABLE_ATS:
        boards = config.load_companies()
        keywords = boards["title_keywords"]

        for provider, cls in (
            ("greenhouse", GreenhouseSource),
            ("lever", LeverSource),
            ("ashby", AshbySource),
        ):
            names = boards.get(provider) or []
            if not names:
                continue
            console.print(
                f"Fetching [cyan]{len(names)}[/cyan] {provider} board(s)"
            )
            safe(
                provider,
                lambda cls=cls, names=names: cls(
                    names, keywords, config.ALLOWED_LOCATIONS
                ).fetch(max_results=config.MAX_RESULTS_PER_BOARD),
            )

        if not any(boards.get(k) for k in ("greenhouse", "lever", "ashby")):
            console.print(
                "[yellow]No ATS boards configured. Copy companies.example.yaml "
                "to companies.yaml to get full job descriptions.[/yellow]"
            )

    if config.ENABLE_ADZUNA:
        if not config.ADZUNA_APP_ID or not config.ADZUNA_APP_KEY:
            console.print("[red]Missing Adzuna credentials. Set them in .env[/red]")
            if not jobs:
                sys.exit(1)
        else:
            adzuna = AdzunaSource(
                config.ADZUNA_APP_ID,
                config.ADZUNA_APP_KEY,
                config.ADZUNA_COUNTRY,
                max_days_old=config.ADZUNA_MAX_DAYS_OLD,
                sort_by=config.ADZUNA_SORT_BY,
            )
            for s in config.SEARCHES:
                console.print(f"Fetching: [cyan]{s['query']}[/cyan] in {s['location']}")
                safe(
                    f"adzuna {s['query']!r}",
                    lambda s=s: adzuna.fetch(
                        s["query"], s["location"], config.MAX_RESULTS_PER_SEARCH
                    ),
                )

    full = sum(1 for j in jobs if not j.description_truncated)
    console.print(
        f"\nCollected [bold]{len(jobs)}[/bold] unique jobs "
        f"([green]{full}[/green] full descriptions, "
        f"[yellow]{len(jobs) - full}[/yellow] truncated).\n"
    )
    return jobs


async def score_new_jobs(
    profile_text: str, jobs: list, store: Store, deadline: float
) -> dict:
    """Score uncached, un-prefiltered jobs concurrently, writing each through.

    deadline is a time.monotonic() value. Work is never STARTED past it, so the
    run always ends cleanly rather than being killed mid-call.
    """
    stats = {
        "cached": 0, "filtered": 0, "scored": 0, "blocked": 0,
        "failed": 0, "deferred": 0, "out_of_time": 0,
    }

    to_score = []
    for job in jobs:
        if store.get(job_key(job)) is not None:
            stats["cached"] += 1
            continue
        if prefilter_reason(
            job,
            config.EXCLUDE_TITLE_KEYWORDS,
            config.MAX_YEARS_EXPERIENCE,
            config.MAX_POSTING_AGE_DAYS,
        ):
            stats["filtered"] += 1
            continue
        to_score.append(job)

    # Newest first, so a capped run keeps the freshest postings and the rest
    # are picked up tomorrow.
    to_score.sort(key=lambda j: j.created or "", reverse=True)
    if len(to_score) > config.MAX_JOBS_PER_RUN:
        stats["deferred"] = len(to_score) - config.MAX_JOBS_PER_RUN
        to_score = to_score[: config.MAX_JOBS_PER_RUN]

    total = len(to_score)
    if not total:
        return stats

    console.print(
        f"Scoring [bold]{total}[/bold] new jobs "
        f"(concurrency {config.SCORING_CONCURRENCY})..."
    )

    sem = asyncio.Semaphore(config.SCORING_CONCURRENCY)
    done = 0

    async def worker(job) -> None:
        nonlocal done
        if time.monotonic() >= deadline:
            stats["out_of_time"] += 1
            return
        async with sem:
            # Re-check: a coroutine can sit on the semaphore for a long time.
            if time.monotonic() >= deadline:
                stats["out_of_time"] += 1
                return
            try:
                result = await score_job(profile_text, job, config.SCORING_MODEL)
            except Exception as e:
                stats["failed"] += 1
                console.print(
                    f"  [yellow]Failed (retry next run): {job.title[:45]} — {e}[/yellow]"
                )
                return

            # Written through immediately. No await between here and the store
            # write, so this is atomic with respect to other coroutines.
            store.put(job_key(job), ScoredJob(job=job, score=result))
            if result.hard_blockers:
                stats["blocked"] += 1
            else:
                stats["scored"] += 1
            done += 1
            console.print(f"  [{done}/{total}] {job.title[:60]}")

    await asyncio.gather(*(worker(j) for j in to_score))
    return stats


def build_ranking(jobs: list, store: Store) -> list[ScoredJob]:
    """Rank jobs from THIS run, hiding blocked / stale / over-experienced ones.

    Visibility rules are re-applied here (not just at ingestion) so that
    already-cached jobs respect current config thresholds.
    """
    ranked = []
    for job in jobs:
        sj = store.get(job_key(job))
        if sj and hidden_reason(sj) is None:
            ranked.append(sj)
    ranked.sort(key=lambda s: s.score.fit_score, reverse=True)
    return ranked


def print_table(scored: list[ScoredJob]) -> None:
    table = Table(title="Ranked job matches")
    table.add_column("Score", justify="right", style="bold")
    table.add_column("Title")
    table.add_column("Company")
    table.add_column("Why", max_width=50)
    for s in scored:
        colour = "green" if s.score.fit_score >= 70 else (
            "yellow" if s.score.fit_score >= 45 else "red"
        )
        table.add_row(
            f"[{colour}]{s.score.fit_score}[/{colour}]",
            s.job.title,
            s.job.company or "-",
            s.score.one_line,
        )
    console.print(table)


async def run() -> None:
    profile_text = profile_to_text(load_profile())

    # Recorded with every score so a prompt/model/profile change is detectable
    # later. Phase 2 stores it; invalidation comes after.
    store = make_store(metadata={
        "model": config.SCORING_MODEL,
        "prompt_version": PROMPT_VERSION,
        "profile_hash": hashlib.sha256(profile_text.encode()).hexdigest()[:16],
    })
    jobs = collect_jobs()

    deadline = time.monotonic() + config.RUN_BUDGET_SECONDS
    try:
        stats = await score_new_jobs(profile_text, jobs, store, deadline)
    finally:
        store.flush()  # nothing scored is ever lost, however we exit

    print_table(build_ranking(jobs, store)[: config.TOP_N])

    console.print(
        f"\nNew scored: {stats['scored']}  |  Blocked: {stats['blocked']}  |  "
        f"Filtered pre-LLM: {stats['filtered']}  |  Already cached: {stats['cached']}  |  "
        f"Failed (retry next run): {stats['failed']}"
    )
    if stats["deferred"]:
        console.print(
            f"[dim]Deferred {stats['deferred']} jobs "
            f"(MAX_JOBS_PER_RUN={config.MAX_JOBS_PER_RUN}). Run again to score them.[/dim]"
        )
    if stats["out_of_time"]:
        console.print(
            f"[yellow]Ran out of time budget with {stats['out_of_time']} jobs "
            f"unscored. They are not cached, so they retry next run.[/yellow]"
        )
    console.print(f"[dim]{cache_summary()}[/dim]")


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted. Scores already written are safe.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()