"""Cheap, deterministic pre-filters that run BEFORE the LLM.

They drop obviously-unsuitable jobs (senior titles, clearly high experience
bars) so we never spend an API call on them. These are best-effort heuristics,
and the LLM's hard_blocker check is still the real backstop, so it is fine for
them to be conservative and let a few borderline jobs through to be scored.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
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


# Phrases that reliably indicate a citizenship or clearance requirement.
# Deliberately narrow: these must be near-unambiguous, because a false match
# hides a job you could have applied for. Softer phrasing is left to the LLM.
_CLEARANCE_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"\b(must|required to)\s+(be|hold)\s+(an?\s+)?australian\s+citizen",
        r"\baustralian\s+citizenship\s+is\s+(a\s+)?(mandatory|required|essential)",
        r"\bmust\s+(hold|have|possess)\s+.{0,30}security\s+clearance",
        r"\b(nv1|nv2|baseline|negative\s+vetting)\s+.{0,20}clearance\b",
        r"\bsecurity\s+clearance\s+is\s+(mandatory|required|essential)",
        r"\beligibility\s+for\s+.{0,30}security\s+clearance",
        r"\bcitizens?\s+only\b",
    )
]


def clearance_required(description: str) -> Optional[str]:
    """Matched phrase if the posting clearly requires citizenship/clearance."""
    for pattern in _CLEARANCE_PATTERNS:
        m = pattern.search(description or "")
        if m:
            return m.group(0).strip()
    return None


def days_since_posted(created: Optional[str]) -> Optional[int]:
    """Age of the posting in days, or None if the date is missing/unparseable.

    Adzuna returns ISO 8601 like '2025-11-03T09:14:22Z'. Unknown dates return
    None so the job is KEPT rather than silently dropped.
    """
    if not created:
        return None
    try:
        text = created.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(delta.days, 0)
    except Exception:
        return None


def prefilter_reason(
    job: JobPosting,
    exclude_keywords: list[str],
    max_years: int,
    max_age_days: Optional[int] = None,
) -> Optional[str]:
    """Return why a job should be skipped before the LLM, or None to keep it."""
    reason = title_excluded(job.title, exclude_keywords)
    if reason:
        return reason

    if max_age_days is not None:
        age = days_since_posted(job.created)
        if age is not None and age > max_age_days:
            return f"posted {age} days ago"

    # Only trust the years check on FULL descriptions. A 500-char teaser
    # rarely contains the requirements section, so a "no match" there means
    # "not visible", not "no requirement".
    if not job.description_truncated:
        yrs = min_years_required(job.description)
        if yrs is not None and yrs >= max_years:
            return f"requires ~{yrs}+ years experience"

        phrase = clearance_required(job.description)
        if phrase:
            return f"citizenship/clearance required ('{phrase}')"
    return None
