"""Cheap, deterministic pre-filters that run BEFORE the LLM.

They drop obviously-unsuitable jobs (senior titles, clearly high experience
bars) so we never spend an API call on them. These are best-effort heuristics,
and the LLM's hard_blocker check is still the real backstop, so it is fine for
them to be conservative and let a few borderline jobs through to be scored.
"""

from __future__ import annotations

import re
from typing import Optional

from models import JobPosting


def title_excluded(title: str, keywords: list[str]) -> Optional[str]:
    t = title.lower()
    for kw in keywords:
        # Whole-word match so 'staff' does not catch 'staffing', etc.
        if re.search(rf"\b{re.escape(kw.lower())}\b", t):
            return f"title contains '{kw}'"
    return None


# Matches things like '5 years', '5+ years', '5-7 years', '5 to 7 years'.
_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|to)?\s*\d{0,2}\s*years?", re.I)


def min_years_required(description: str) -> Optional[int]:
    """Lowest 'N years ... experience' figure found, else None.

    Returns the minimum so we only ever over-keep, never over-drop: if a posting
    mentions both '2 years' and '8 years', we keep it and let the LLM decide.
    """
    text = description.lower()
    found: list[int] = []
    for m in _YEARS_RE.finditer(text):
        window = text[max(0, m.start() - 40) : m.end() + 40]
        if "experien" in window:  # only count years tied to 'experience'
            found.append(int(m.group(1)))
    return min(found) if found else None


def prefilter_reason(
    job: JobPosting, exclude_keywords: list[str], max_years: int
) -> Optional[str]:
    """Return why a job should be skipped before the LLM, or None to keep it."""
    reason = title_excluded(job.title, exclude_keywords)
    if reason:
        return reason
    yrs = min_years_required(job.description)
    if yrs is not None and yrs >= max_years:
        return f"requires ~{yrs}+ years experience"
    return None