"""Adzuna job source.

Docs: https://developer.adzuna.com/  (free app_id + app_key)

IMPORTANT: Adzuna's search API truncates `description` to ~500 characters, so
postings from this source are marked description_truncated=True. That teaser
rarely includes the requirements section, which is why the ATS sources
(greenhouse, lever) are preferred for accurate scoring. Adzuna is kept for
breadth of coverage.
"""

from __future__ import annotations

from typing import Optional

import httpx

from models import JobPosting
from sources.base import JobSource

_BASE = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"

# Adzuna truncates at 500; anything at or above this is assumed cut off.
_TRUNCATION_LENGTH = 500


class AdzunaSource(JobSource):
    name = "adzuna"

    def __init__(
        self,
        app_id: str,
        app_key: str,
        country: str = "au",
        max_days_old: Optional[int] = None,
        sort_by: str = "date",
    ):
        self.app_id = app_id
        self.app_key = app_key
        self.country = country
        self.max_days_old = max_days_old
        self.sort_by = sort_by

    def fetch(
        self, query: str, location: str, max_results: int = 20, page: int = 1
    ) -> list[JobPosting]:
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": query,
            "where": location,
            "results_per_page": max_results,
            "content-type": "application/json",
        }
        # Ask the API to exclude stale ads rather than filtering them locally.
        if self.max_days_old is not None:
            params["max_days_old"] = self.max_days_old
        if self.sort_by:
            params["sort_by"] = self.sort_by

        url = _BASE.format(country=self.country, page=page)

        # Credentials travel as query params, and httpx puts the full URL in
        # its exception messages. Re-raise WITHOUT the original ('from None'
        # suppresses the chained cause) so app_key never reaches a traceback,
        # a terminal scrollback, or CloudWatch.
        try:
            resp = httpx.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            detail = (e.response.text or "").strip().replace("\n", " ")[:300]
            raise RuntimeError(
                f"Adzuna returned {e.response.status_code} for query={query!r} "
                f"location={location!r}: {detail or '(empty body)'}"
            ) from None
        except httpx.HTTPError as e:
            raise RuntimeError(
                f"Adzuna request failed ({type(e).__name__}) for "
                f"query={query!r} location={location!r}"
            ) from None
        except ValueError as e:  # resp.json() on a non-JSON body
            raise RuntimeError(
                f"Adzuna sent a non-JSON response for query={query!r}: {e}"
            ) from None

        jobs: list[JobPosting] = []
        for r in data.get("results", []):
            description = r.get("description") or ""
            job_id = r.get("id")
            if not job_id:
                continue  # a null id would collide with every other null id
            jobs.append(
                JobPosting(
                    id=str(job_id),
                    source=self.name,
                    title=(r.get("title") or "").strip(),
                    company=(r.get("company") or {}).get("display_name"),
                    location=(r.get("location") or {}).get("display_name"),
                    description=description,
                    url=r.get("redirect_url") or "",
                    salary_min=r.get("salary_min"),
                    salary_max=r.get("salary_max"),
                    created=r.get("created"),
                    description_truncated=len(description) >= _TRUNCATION_LENGTH,
                )
            )
        return jobs