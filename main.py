"""Phase 1 entry point.

Fetch jobs from Adzuna, score each against your profile, rank, and print.
No cloud, no email yet. This proves the valuable core works.

Run:  python main.py
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.table import Table

import config
from models import ScoredJob
from profile_loader import load_profile, profile_to_text
from scoring import score_job
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
            if job.id and job.id not in seen:
                seen.add(job.id)
                jobs.append(job)
    console.print(f"Collected [bold]{len(jobs)}[/bold] unique jobs.\n")
    return jobs


def rank_jobs(profile_text: str, jobs: list) -> list[ScoredJob]:
    scored: list[ScoredJob] = []
    with console.status("Scoring jobs..."):
        for job in jobs:
            try:
                result = score_job(profile_text, job, config.SCORING_MODEL)
            except Exception as e:  # keep going if one job fails
                console.print(f"[yellow]Skipped {job.title}: {e}[/yellow]")
                continue
            if config.DROP_HARD_BLOCKERS and result.hard_blockers:
                continue
            scored.append(ScoredJob(job=job, score=result))
    scored.sort(key=lambda s: s.score.fit_score, reverse=True)
    return scored


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
    jobs = collect_jobs()
    scored = rank_jobs(profile_text, jobs)
    print_table(scored)


if __name__ == "__main__":
    main()
