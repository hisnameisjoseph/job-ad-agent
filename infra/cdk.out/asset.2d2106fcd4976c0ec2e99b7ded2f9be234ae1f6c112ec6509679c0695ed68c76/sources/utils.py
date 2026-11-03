"""Shared helpers for job sources.

ATS boards return descriptions as HTML (Greenhouse) or a mix of plain text and
HTML fragments (Lever). The LLM only needs readable text, so we strip tags
rather than pull in a parsing dependency.
"""

from __future__ import annotations

import html
import re

_SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_BLOCK_END = re.compile(r"</(p|div|li|ul|ol|h[1-6]|tr|table|section)>", re.I)
_BR = re.compile(r"<br\s*/?>", re.I)
_LI = re.compile(r"<li[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_BLANKLINES = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+\n")


def html_to_text(raw: str | None) -> str:
    """Convert an HTML description into readable plain text.

    Greenhouse double-encodes its `content` field, so we unescape twice; the
    second pass is a no-op on already-decoded text.
    """
    if not raw:
        return ""
    text = html.unescape(html.unescape(raw))
    text = _SCRIPT_STYLE.sub(" ", text)
    text = _BR.sub("\n", text)
    text = _LI.sub("\n- ", text)  # keep bullet structure, it carries requirements
    text = _BLOCK_END.sub("\n", text)
    text = _TAG.sub("", text)
    text = _TRAILING_WS.sub("\n", text)
    text = _BLANKLINES.sub("\n\n", text)
    return text.strip()


def title_matches(title: str, keywords: list[str]) -> bool:
    """True if the title contains any keyword, or if no keywords are given.

    ATS boards return every open role at a company, including sales and legal,
    so titles are filtered before anything reaches the LLM.
    """
    if not keywords:
        return True
    t = (title or "").lower()
    return any(kw.lower() in t for kw in keywords)


# Location matching -------------------------------------------------------
# ATS boards return every open role at a company worldwide, so roles must be
# filtered to places the candidate can actually work.

_REMOTE_HINTS = ("remote", "anywhere", "distributed")


def location_matches(
    location: str | None,
    allowed: list[str],
    allow_remote: bool = True,
    keep_unknown: bool = True,
) -> bool:
    """True if a role's location is somewhere the candidate can work.

    `allowed` are substrings such as ["australia", "melbourne", "sydney"].
    Unknown locations are KEPT by default so a missing field never silently
    drops a good role; the LLM remains the backstop.
    """
    if not allowed:
        return True
    if not location:
        return keep_unknown

    text = location.lower()
    if allow_remote and any(h in text for h in _REMOTE_HINTS):
        # "Remote - US only" is remote but not open to us, so still require an
        # allowed place name when the string names a country/city.
        if any(a.lower() in text for a in allowed):
            return True
        # Bare "Remote" with no geography attached: keep and let the LLM judge.
        return not any(
            token in text
            for token in ("us", "usa", "united states", "uk", "canada", "emea",
                          "europe", "india", "philippines", "singapore")
        )
    return any(a.lower() in text for a in allowed)
