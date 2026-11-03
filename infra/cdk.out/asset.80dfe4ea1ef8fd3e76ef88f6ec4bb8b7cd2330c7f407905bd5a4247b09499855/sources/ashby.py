"""Ashby job board source.

Free, unauthenticated JSON API per company:
    https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true

Like Greenhouse and Lever, returns the COMPLETE description rather than a
truncated summary. `name` is the slug in the careers URL, e.g.
https://jobs.ashbyhq.com/linear -> "linear".
"""

from __future__ import annotations

import httpx

from models import JobPosting
from sources.base import JobSource
from sources.utils import html_to_text, location_matches, title_matches

_BOARD = "https://api.ashbyhq.com/posting-api/job-board/{name}"


class AshbySource(JobSource):
    name = "ashby"

    def __init__(self, boards: list[str], title_keywords: list[str] | None = None,
        allowed_locations: list[str] | None = None,
    ):
        self.boards = boards
        self.title_keywords = title_keywords or []
        self.allowed_locations = allowed_locations or []

    def fetch(
        self, query: str = "", location: str = "", max_results: int = 50
    ) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        for board in self.boards:
            try:
                resp = httpx.get(
                    _BOARD.format(name=board),
                    params={"includeCompensation": "true"},
                    timeout=30,
                    follow_redirects=True,
                )
                resp.raise_for_status()
                payload = resp.json()
            except Exception as e:
                print(f"  [ashby] skipped '{board}': {e}")
                continue

            for r in payload.get("jobs", [])[:max_results]:
                title = (r.get("title") or "").strip()
                if not title_matches(title, self.title_keywords):
                    continue

                if not location_matches(r.get("location"), self.allowed_locations):
                    continue

                description = r.get("descriptionPlain") or html_to_text(
                    r.get("descriptionHtml")
                )
                if not description:
                    continue

                jobs.append(
                    JobPosting(
                        id=f"ashby:{board}:{r.get('id')}",
                        source=self.name,
                        title=title,
                        company=r.get("companyName") or board,
                        location=r.get("location"),
                        description=description,
                        url=r.get("jobUrl") or r.get("applyUrl", ""),
                        remote=r.get("isRemote"),
                        created=r.get("publishedAt"),
                        description_truncated=False,  # full posting
                    )
                )
        return jobs
