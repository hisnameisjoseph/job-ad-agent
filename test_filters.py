"""Unit tests for the deterministic pre-filters.

These are pure functions with no network and no LLM, so they run in
milliseconds. The regression tests below cover a real bug: the original
years regex captured only two digits, so '100 years of experience' parsed
as 10 and dropped graduate roles before they were ever scored.

Run:  pytest -q
"""

from __future__ import annotations

import pytest

from filters import min_years_required, prefilter_reason, title_excluded
from models import JobPosting

KEYWORDS = ["senior", "snr", "principal", "staff", "head of", "director"]


def job(title: str = "Graduate Engineer", description: str = "") -> JobPosting:
    return JobPosting(id="1", source="test", title=title, description=description)


# --- title_excluded ---------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    ["Senior Engineer", "SNR Developer", "Principal Architect", "Head of Data"],
)
def test_title_excluded_matches_seniority(title):
    assert title_excluded(title, KEYWORDS) is not None


@pytest.mark.parametrize(
    "title",
    [
        "Graduate Software Engineer",
        "Staffing Coordinator",  # 'staff' must not match inside 'staffing'
        "Directorate Support Officer",  # nor 'director' inside 'directorate'
    ],
)
def test_title_excluded_respects_word_boundaries(title):
    assert title_excluded(title, KEYWORDS) is None


# --- min_years_required: genuine requirements -------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("5 years of experience required", 5),
        ("5+ years of experience required", 5),
        ("3-5 years experience", 3),  # range -> lower bound
        ("2 to 4 years of experience", 2),
        ("we want 8 years experience minimum", 8),
    ],
)
def test_min_years_parses_real_requirements(text, expected):
    assert min_years_required(text) == expected


def test_min_years_returns_lowest_so_we_over_keep():
    """Two figures -> take the min, so borderline jobs reach the LLM."""
    assert min_years_required("2 years experience, ideally 8 years experience") == 2


@pytest.mark.parametrize(
    "text",
    [
        "",
        "No experience necessary, full training provided",
        "Founded 40 years ago",  # 'years' but no 'experience' nearby
    ],
)
def test_min_years_returns_none_when_no_requirement(text):
    assert min_years_required(text) is None


# --- min_years_required: regression tests for the 3-digit truncation bug ----
#
# The old pattern r"(\d{1,2})\s*\+?\s*(?:-|to)?\s*\d{0,2}\s*years?" captured
# only the first two digits, so each of these parsed as a plausible seniority
# bar and dropped the job pre-LLM.


@pytest.mark.parametrize(
    "text,old_wrong_value",
    [
        ("With over 100 years of experience, Acme builds...", 10),
        ("130 years ago we started, and bring deep experience", 13),
        ("25 years of combined experience across our team", 25),
    ],
)
def test_company_history_is_not_read_as_a_requirement(text, old_wrong_value):
    assert min_years_required(text) is None, (
        f"company blurb parsed as {old_wrong_value}+ years and would drop the job"
    )


def test_graduate_role_with_company_blurb_survives_prefilter():
    """End-to-end: the exact failure case, through the public entry point."""
    posting = job(
        title="Graduate Software Engineer",
        description="With over 100 years of experience, Acme seeks graduates.",
    )
    assert prefilter_reason(posting, KEYWORDS, max_years=6) is None


# --- prefilter_reason -------------------------------------------------------


def test_prefilter_drops_genuine_senior_requirement():
    posting = job(description="You will have 10 years of experience in Python.")
    assert prefilter_reason(posting, KEYWORDS, max_years=6) == (
        "requires ~10+ years experience"
    )


def test_prefilter_keeps_job_below_threshold():
    posting = job(description="2 years of experience preferred.")
    assert prefilter_reason(posting, KEYWORDS, max_years=6) is None


def test_title_exclusion_takes_precedence():
    posting = job(title="Senior Engineer", description="1 year of experience.")
    assert prefilter_reason(posting, KEYWORDS, max_years=6) == "title contains 'senior'"
