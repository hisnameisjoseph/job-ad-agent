"""Tests for the editable settings document and its store.

These two modules are the write path for a public HTTP endpoint, so the cases
that matter are the ones where the input is hostile or merely wrong. A
settings document that parses but is nonsense changes what enters the funnel,
and the symptom is "no jobs today" with nothing in the logs.

Run:  pytest -q
"""

from __future__ import annotations

import pytest

import settings as settings_mod
from config_store import LocalConfigStore, PROFILE_DOC, SETTINGS_DOC


# --- Defaults --------------------------------------------------------------


def test_absent_document_falls_back_to_shipped_defaults():
    """A missing settings.yaml must be a working config, not an empty one."""
    s = settings_mod.load(None)
    assert len(s.searches) == 8
    assert s.filters.top_n == 25
    assert s.filters.max_years_experience == 3


def test_invalid_document_falls_back_rather_than_crashing_a_run():
    """load() is used by the scoring run, which must never die on bad YAML."""
    s = settings_mod.load("searches: []")
    assert len(s.searches) == 8  # defaults, not the empty list


def test_parse_raises_where_load_forgives():
    """The website needs the error; the pipeline needs to keep going."""
    with pytest.raises(Exception):
        settings_mod.parse("searches: []")


# --- Validation ------------------------------------------------------------


def test_empty_search_list_is_rejected():
    """No searches means no Adzuna results forever, which reads as a broken
    API key rather than a config mistake."""
    with pytest.raises(Exception):
        settings_mod.parse("searches: []")


@pytest.mark.parametrize(
    "doc",
    [
        'searches:\n  - query: "   "\n    location: Melbourne',
        "searches:\n  - query: dev\n    location: ''",
        "filters:\n  top_n: 0",
        "filters:\n  top_n: 99999",
        "filters:\n  max_years_experience: -1",
        "filters:\n  max_posting_age_days: 0",
        "max_results_per_search: 0",
        "- not\n- a\n- mapping",
    ],
)
def test_nonsense_values_are_rejected(doc):
    with pytest.raises(Exception):
        settings_mod.parse(doc)


def test_null_thresholds_are_allowed_and_mean_disabled():
    s = settings_mod.parse(
        "filters:\n  max_years_experience: null\n  max_posting_age_days: null"
    )
    assert s.filters.max_years_experience is None
    assert s.filters.max_posting_age_days is None


def test_queries_are_trimmed_and_keywords_lowercased():
    s = settings_mod.parse(
        "searches:\n  - query: '  Grad Engineer  '\n    location: '  Melbourne '\n"
        "filters:\n  exclude_title_keywords: ['  SENIOR ', 'Head Of', '', '  ']"
    )
    assert s.searches[0].query == "Grad Engineer"
    assert s.searches[0].location == "Melbourne"
    assert s.filters.exclude_title_keywords == ["senior", "head of"]


def test_round_trip_through_yaml_is_stable():
    """dump() output must parse back to the same thing, or the website's save
    would quietly mutate settings it did not touch."""
    original = settings_mod.parse(
        "searches:\n  - query: cloud engineer\n    location: Sydney\n"
        "max_results_per_search: 7\nfilters:\n  top_n: 10"
    )
    assert settings_mod.parse(settings_mod.dump(original)) == original


# --- The document store ----------------------------------------------------


def test_local_store_round_trips(tmp_path):
    store = LocalConfigStore(str(tmp_path))
    assert store.read(SETTINGS_DOC) is None
    store.write(SETTINGS_DOC, "searches:\n  - query: a\n    location: b\n")
    assert "query: a" in store.read(SETTINGS_DOC)


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
        ".env",
        "companies.yaml",
        "scored_jobs.json",
    ],
)
def test_only_the_two_editable_documents_are_reachable(tmp_path, name):
    """The name reaches a filesystem path and an S3 key, so an unchecked value
    from an HTTP request is a traversal or an arbitrary object write."""
    store = LocalConfigStore(str(tmp_path))
    with pytest.raises(ValueError):
        store.read(name)
    with pytest.raises(ValueError):
        store.write(name, "x")


def test_write_is_atomic_leaving_no_partial_file(tmp_path):
    store = LocalConfigStore(str(tmp_path))
    store.write(PROFILE_DOC, "name: Joseph\n")
    assert not (tmp_path / (PROFILE_DOC + ".tmp")).exists()
    assert (tmp_path / PROFILE_DOC).read_text() == "name: Joseph\n"


def test_make_config_store_picks_s3_only_when_a_bucket_is_set(monkeypatch):
    import config_store

    monkeypatch.delenv("CONFIG_BUCKET", raising=False)
    assert isinstance(config_store.make_config_store(), LocalConfigStore)
