"""Greenhouse job board source.

Greenhouse exposes a free, unauthenticated JSON API per company board:
    https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

Unlike Adzuna's search API, `content` is the COMPLETE posting, so the
requirements section is present and the experience/citizenship filters actually
have something to read.

`token` is the company's board name, visible in its careers URL, e.g.
https://boards.greenhouse.io/canva -> token "canva".
"""

from __future__ import annotations

import httpx

from models import JobPosting
from sources.base import JobSource
from sources.utils import html_to_text, location_matches, title_matches

_BOARD = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


class GreenhouseSource(JobSource):
    name = "greenhouse"

    def __init__(self, board_tokens: list[str], title_keywords: list[str] | None = None,
        allowed_locations: list[str] | None = None,
    ):
        self.board_tokens = board_tokens
        self.title_keywords = title_keywords or []
        self.allowed_locations = allowed_locations or []

    def fetch(
        self, query: str = "", location: str = "", max_results: int = 50
    ) -> list[JobPosting]:
        """Fetch open roles across the configured boards.

        `query` and `location` are unused: an ATS board is company-scoped, so
        filtering happens on title keywords and (downstream) the LLM.
        """
        jobs: list[JobPosting] = []
        for token in self.board_tokens:
            try:
                resp = httpx.get(
                    _BOARD.format(token=token),
                    params={"content": "true"},
                    timeout=30,
                    follow_redirects=True,
                )
                resp.raise_for_status()
                payload = resp.json()
            except Exception as e:
                print(f"  [greenhouse] skipped '{token}': {e}")
                continue

            company = None
            for r in payload.get("jobs", [])[:max_results]:
                title = (r.get("title") or "").strip()
                if not title_matches(title, self.title_keywords):
                    continue

                description = html_to_text(r.get("content"))
                if not description:
                    continue

                offices = r.get("offices") or []
                location_name = (r.get("location") or {}).get("name") or (
                    offices[0].get("name") if offices else None
                )
                if not location_matches(location_name, self.allowed_locations):
                    continue
                company = (r.get("company_name") or token).strip()

                jobs.append(
                    JobPosting(
                        id=f"greenhouse:{token}:{r.get('id')}",
                        source=self.name,
                        title=title,
                        company=company,
                        location=location_name,
                        description=description,
                        url=r.get("absolute_url", ""),
                        created=r.get("updated_at") or r.get("first_published"),
                        description_truncated=False,  # full posting
                    )
                )
        return jobs
