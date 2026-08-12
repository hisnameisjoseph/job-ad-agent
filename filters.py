"""Cheap, deterministic pre-filters that run BEFORE the LLM.

They drop obviously-unsuitable jobs (senior titles, clearly high experience
bars) so we never spend an API call on them. These are best-effort heuristics,
and the LLM's hard_blocker check is still the real backstop, so it is fine for
them to be conservative and let a few borderline jobs through to be scored.

The bias is deliberately asymmetric: a false keep costs one API call, a false
drop costs a job the candidate never sees. Everything below errs towards keeping.
"""

from __future__ import annotations

from datetime import datetime, timezone
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


# Matches '5 years', '5+ years', '3-5 years', '2 to 4 years'.
#
# The lookarounds are load-bearing. Without them a 3-digit number is silently
# truncated: the old pattern read '100 years' as 10 and '130 years' as 13,
# because the capture group was capped at two digits and the trailing digit was
# absorbed by the range-upper-bound group. That turned company blurbs into
# fake seniority bars and dropped graduate roles pre-LLM.
#
# We now capture the FULL number and reject implausible ones below, rather than
# mis-parsing them into a plausible-looking range.
_YEARS_RE = re.compile(
    r"(?<!\d)(\d{1,3})(?!\d)"                              # complete integer
    r"\s*\+?"                                              # '5+'
    r"(?:\s*(?:-|–|—|to)\s*(?<!\d)\d{1,3}(?!\d)\s*\+?)?"   # optional '-7' / 'to 7'
    r"\s*years?\b",
    re.I,
)

# No real junior/mid posting asks for more than this. A larger figure is company
# history ('100 years of experience', '130 years ago'), not a requirement.
_MAX_PLAUSIBLE_YEARS = 20

# Phrases that mark a years figure as company history even when it sits next to
# the word 'experience'. Checked against the same window as the 'experien' test.
_BLURB_MARKERS = (
    "years ago",
    "combined experience",
    "collective experience",
    "years of history",
    "years in business",
    "years of operation",
    "years of service",
    "years young",
    "years strong",
    "over the years",
    "founded",
    "established in",
    "celebrating",
    "anniversary",
)

# How far either side of a years figure we look for supporting context.
_CONTEXT_CHARS = 40


def min_years_required(description: str) -> Optional[int]:
    """Lowest 'N years ... experience' figure found, else None.

    Returns the minimum so we only ever over-keep, never over-drop: if a posting
    mentions both '2 years' and '8 years', we keep it and let the LLM decide.

    A figure only counts as a requirement when it sits near the word
    'experience', is not part of a company-history phrase, and is small enough
    to be a genuine seniority bar.
    """
    text = description.lower()
    found: list[int] = []
    for m in _YEARS_RE.finditer(text):
        window = text[max(0, m.start() - _CONTEXT_CHARS) : m.end() + _CONTEXT_CHARS]

        if "experien" not in window:  # only count years tied to 'experience'
            continue
        if any(marker in window for marker in _BLURB_MARKERS):
            continue  # company history, not a requirement

        years = int(m.group(1))
        if years < 1 or years > _MAX_PLAUSIBLE_YEARS:
            continue  # implausible as a seniority bar

        found.append(years)
    return min(found) if found else None

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
