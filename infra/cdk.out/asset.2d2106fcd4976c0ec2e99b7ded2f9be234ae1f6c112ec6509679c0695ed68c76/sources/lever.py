"""Lever job board source.

Lever exposes a free, unauthenticated JSON API per company:
    https://api.lever.co/v0/postings/{company}?mode=json

Returns the full posting split across `descriptionPlain` (the intro), `lists`
(requirements and responsibilities as structured bullets), and
`additionalPlain`. All three are joined so the requirements text is included.

`company` is the slug in the careers URL, e.g.
https://jobs.lever.co/atlassian -> "atlassian".
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from models import JobPosting
from sources.base import JobSource
from sources.utils import html_to_text, location_matches, title_matches

_POSTINGS = "https://api.lever.co/v0/postings/{company}"


def _epoch_ms_to_iso(value) -> str | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None


def _full_description(r: dict) -> str:
    """Join intro, bulleted lists, and closing text into one plain-text blob."""
    parts: list[str] = []
    intro = r.get("descriptionPlain") or html_to_text(r.get("description"))
    if intro:
        parts.append(intro)

    for block in r.get("lists") or []:
        heading = (block.get("text") or "").strip()
        body = html_to_text(block.get("content"))
        if heading:
            parts.append(f"\n{heading}")
        if body:
            parts.append(body)

    closing = r.get("additionalPlain") or html_to_text(r.get("additional"))
    if closing:
        parts.append(closing)

    return "\n".join(p for p in parts if p).strip()


class LeverSource(JobSource):
    name = "lever"

    def __init__(self, companies: list[str], title_keywords: list[str] | None = None,
        allowed_locations: list[str] | None = None,
    ):
        self.companies = companies
        self.title_keywords = title_keywords or []
        self.allowed_locations = allowed_locations or []

    def fetch(
        self, query: str = "", location: str = "", max_results: int = 50
    ) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        for company in self.companies:
            try:
                resp = httpx.get(
                    _POSTINGS.format(company=company),
                    params={"mode": "json"},
                    timeout=30,
                    follow_redirects=True,
                )
                resp.raise_for_status()
                payload = resp.json()
            except Exception as e:
                print(f"  [lever] skipped '{company}': {e}")
                continue

            for r in payload[:max_results]:
                title = (r.get("text") or "").strip()
                if not title_matches(title, self.title_keywords):
                    continue

                description = _full_description(r)
                if not description:
                    continue

                categories = r.get("categories") or {}
                if not location_matches(
                    categories.get("location"), self.allowed_locations
                ):
                    continue
                jobs.append(
                    JobPosting(
                        id=f"lever:{company}:{r.get('id')}",
                        source=self.name,
                        title=title,
                        company=company,
                        location=categories.get("location"),
                        description=description,
                        url=r.get("hostedUrl", ""),
                        created=_epoch_ms_to_iso(r.get("createdAt")),
                        description_truncated=False,  # full posting
                    )
                )
        return jobs
