"""Phase 1 entry point.

Fetch jobs from Adzuna, skip ones we have already scored (local cache) and ones
the cheap pre-filters reject, score the rest with pacing + retry, then rank and
print. No cloud, no email yet.

Run:  python main.py
"""

from __future__ import annotations

import sys
import time

from rich.console import Console
from rich.table import Table

import config
from cache import load_cache, save_cache
from filters import prefilter_reason
from models import ScoredJob
from profile_loader import load_profile, profile_to_text
from scoring import cache_summary, score_job
from sources.adzuna import AdzunaSource

console = Console()


def collect_jobs() -> list:
    if not config.ADZUNA_APP_ID or not config.ADZUNA_APP_KEY:
        console.print("[red]Missing Adzuna credentials. Set them in .env[/red]")
        sys.exit(1)

    source = AdzunaSource(
        config.ADZUNA_APP_ID, config.ADZUNA_APP_KEY, config.ADZUNA_COUNTRY
    )
    seen: set[str] = set()
    jobs = []
    for s in config.SEARCHES:
        console.print(f"Fetching: [cyan]{s['query']}[/cyan] in {s['location']}")
        for job in source.fetch(
            s["query"], s["location"], config.MAX_RESULTS_PER_SEARCH
        ):
            if job.id and job.id not in seen:  # dedupe by id within this run
                seen.add(job.id)
                jobs.append(job)
    console.print(f"Collected [bold]{len(jobs)}[/bold] unique jobs.\n")
    return jobs


def score_new_jobs(profile_text: str, jobs: list, cache: dict) -> dict:
    """Score only jobs not already cached and not pre-filtered. Mutates cache."""
    stats = {"cached": 0, "filtered": 0, "scored": 0, "blocked": 0, "failed": 0}
    to_score = []
    for job in jobs:
        if job.id in cache:
            stats["cached"] += 1
            continue
        reason = prefilter_reason(
            job, config.EXCLUDE_TITLE_KEYWORDS, config.MAX_YEARS_EXPERIENCE
        )
        if reason:
            stats["filtered"] += 1
            continue
        to_score.append(job)

    total = len(to_score)
    for i, job in enumerate(to_score, 1):
        console.print(f"  Scoring {i}/{total}: {job.title[:60]}")
        try:
            result = score_job(profile_text, job, config.SCORING_MODEL)
        except Exception as e:
            # Not cached, so it is retried on the next run rather than lost.
            stats["failed"] += 1
            console.print(f"  [yellow]Failed (will retry next run): {e}[/yellow]")
            time.sleep(config.REQUEST_INTERVAL_SECONDS)
            continue

        cache[job.id] = ScoredJob(job=job, score=result)  # cache even if blocked
        if result.hard_blockers:
            stats["blocked"] += 1
        else:
            stats["scored"] += 1
        time.sleep(config.REQUEST_INTERVAL_SECONDS)  # pace to stay under RPM

    return stats


def build_ranking(jobs: list, cache: dict) -> list[ScoredJob]:
    """Rank jobs from THIS run using cached scores, dropping hard-blocked ones.

    Using this run's jobs means expired postings fall off the list naturally.
    """
    ranked = []
    for job in jobs:
        sj = cache.get(job.id)
        if sj and not (config.DROP_HARD_BLOCKERS and sj.score.hard_blockers):
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


def main() -> None:
    profile = load_profile()
    profile_text = profile_to_text(profile)
    cache = load_cache(config.CACHE_PATH)
    jobs = collect_jobs()

    stats = score_new_jobs(profile_text, jobs, cache)
    save_cache(config.CACHE_PATH, cache)

    ranking = build_ranking(jobs, cache)
    print_table(ranking[: config.TOP_N])

    console.print(
        f"\nNew scored: {stats['scored']}  |  Blocked: {stats['blocked']}  |  "
        f"Filtered pre-LLM: {stats['filtered']}  |  Already cached: {stats['cached']}  |  "
        f"Failed (retry next run): {stats['failed']}"
    )
    console.print(f"[dim]{cache_summary()}[/dim]")


if __name__ == "__main__":
    main()