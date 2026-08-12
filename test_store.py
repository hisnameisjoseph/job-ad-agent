"""Unit tests for the scored-job store.

The store is the piece that makes an interrupted run survivable, so its
round-trip, key-migration, and corrupt-row behaviour are worth pinning down.

Run:  pytest -q
"""

from __future__ import annotations

import json

from models import JobPosting, ScoreResult, ScoredJob
from store import JsonStore, job_key


def make(job_id: str = "1", source: str = "adzuna") -> ScoredJob:
    return ScoredJob(
        job=JobPosting(id=job_id, source=source, title="Graduate Engineer"),
        score=ScoreResult(fit_score=70, recommendation="apply", one_line="ok"),
    )


def test_job_key_is_namespaced_by_source():
    """Ids are only unique within a provider, so the key must include it."""
    a = JobPosting(id="123", source="adzuna", title="X")
    b = JobPosting(id="123", source="lever", title="Y")
    assert job_key(a) != job_key(b)


def test_put_then_get_round_trips(tmp_path):
    path = str(tmp_path / "c.json")
    store = JsonStore(path, flush_every=1)
    store.put("adzuna#1", make())
    assert store.get("adzuna#1").score.fit_score == 70

    reopened = JsonStore(path)
    assert reopened.get("adzuna#1").score.fit_score == 70


def test_writes_survive_without_explicit_flush(tmp_path):
    """The whole point: an interrupt must not discard paid-for scores."""
    path = str(tmp_path / "c.json")
    store = JsonStore(path, flush_every=1)
    for i in range(3):
        store.put(f"adzuna#{i}", make(str(i)))
    # No flush() call, simulating a process that never reached its cleanup.
    assert len(JsonStore(path).all()) == 3


def test_pending_writes_are_flushed_on_demand(tmp_path):
    path = str(tmp_path / "c.json")
    store = JsonStore(path, flush_every=10)
    store.put("adzuna#1", make())
    assert JsonStore(path).get("adzuna#1") is None  # still buffered
    store.flush()
    assert JsonStore(path).get("adzuna#1") is not None


def test_legacy_bare_id_keys_are_migrated(tmp_path):
    """Old caches keyed on bare id must not trigger a full paid rescore."""
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"555": make("555").model_dump(mode="json")}))

    store = JsonStore(str(path))
    assert store.get("adzuna#555") is not None
    assert store.get("555") is None


def test_rows_that_no_longer_match_the_schema_are_skipped(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps(
            {
                "adzuna#1": make().model_dump(mode="json"),
                "adzuna#2": {"job": {"nope": True}},  # invalid
            }
        )
    )
    store = JsonStore(str(path))
    assert len(store.all()) == 1


def test_unreadable_file_starts_empty_instead_of_crashing(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{ not json")
    assert JsonStore(str(path)).all() == {}