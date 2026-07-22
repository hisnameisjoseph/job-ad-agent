"""Adzuna job source.

Docs: https://developer.adzuna.com/  (free app_id + app_key, generous limits)
We hit the AU endpoint. Each result is mapped into our JobPosting shape.
"""

from __future__ import annotations

import httpx

from models import JobPosting
from sources.base import JobSource

_BASE = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"


class AdzunaSource(JobSource):
    name = "adzuna"

    def __init__(self, app_id: str, app_key: str, country: str = "au"):
        self.app_id = app_id
        self.app_key = app_key
        self.country = country

    def fetch(
        self, query: str, location: str, max_results: int = 20
    ) -> list[JobPosting]:
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": query,
            "where": location,
            "results_per_page": max_results,
            "content-type": "application/json",
        }
        url = _BASE.format(country=self.country)
        resp = httpx.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        jobs: list[JobPosting] = []
        for r in data.get("results", []):
            jobs.append(
                JobPosting(
                    id=str(r.get("id", "")),
                    source=self.name,
                    title=r.get("title", "").strip(),
                    company=(r.get("company") or {}).get("display_name"),
                    location=(r.get("location") or {}).get("display_name"),
                    description=r.get("description", ""),
                    url=r.get("redirect_url", ""),
                    salary_min=r.get("salary_min"),
                    salary_max=r.get("salary_max"),
                    created=r.get("created"),
                )
            )
        return jobs
