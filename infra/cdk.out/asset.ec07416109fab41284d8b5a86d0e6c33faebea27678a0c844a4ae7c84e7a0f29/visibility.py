"""Single source of truth for which scored jobs are worth showing.

Pre-filters used to run only at ingestion, which meant any job scored BEFORE a
threshold changed stayed visible forever (it sat in the cache and was never
re-examined). Applying the same rules at display time fixes that: changing
MAX_POSTING_AGE_DAYS or MAX_YEARS_EXPERIENCE now affects existing cached jobs
immediately, with no rescore needed.

Both main.py (terminal table) and server.py (web viewer) call this, so the two
can never disagree.
"""

from __future__ import annotations

from typing import Optional

import config
from filters import prefilter_reason
from models import ScoredJob


def hidden_reason(sj: ScoredJob) -> Optional[str]:
    """Why this scored job should be hidden, or None if it should show."""
    if config.DROP_HARD_BLOCKERS and sj.score.hard_blockers:
        return "; ".join(sj.score.hard_blockers)

    # Re-apply the ingestion pre-filters to already-cached jobs.
    reason = prefilter_reason(
        sj.job,
        config.EXCLUDE_TITLE_KEYWORDS,
        config.MAX_YEARS_EXPERIENCE,
        config.MAX_POSTING_AGE_DAYS,
    )
    if reason:
        return reason
    return None


def visible_ranked(cache: dict[str, ScoredJob]) -> list[ScoredJob]:
    """All currently-visible cached jobs, best fit first."""
    jobs = [sj for sj in cache.values() if hidden_reason(sj) is None]
    jobs.sort(key=lambda s: s.score.fit_score, reverse=True)
    return jobs