"""API contract tests for the viewer.

The redesigned frontend reads specific fields off /api/jobs (scored_at for the
"New" marker and "sort by scored", stale for the "Old profile" marker). These
tests pin that contract so a backend change cannot silently blank part of the
UI. They run the real FastAPI app against a temporary JSON store and a
temporary config directory: no AWS, no network.

Run:  pytest -q
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config
import server
from models import JobPosting, ScoreResult, ScoredJob
from profile_loader import profile_fingerprint, profile_to_text
from store import JsonStore

PROFILE = "name: Test Person\nskills:\n  languages: [Python]\n"


def scored(job_id: str, **extra) -> ScoredJob:
    return ScoredJob(
        job=JobPosting(
            id=job_id,
            source="greenhouse",
            title="Graduate Software Engineer",
            company="Example Co",
            location="Melbourne, VIC",
            url=f"https://example.com/jobs/{job_id}",
        ),
        score=ScoreResult(fit_score=80, recommendation="apply", one_line="Good fit."),
        **extra,
    )


_SETTINGS_NAMES = (
    "SEARCHES", "MAX_RESULTS_PER_SEARCH", "DROP_HARD_BLOCKERS",
    "EXCLUDE_TITLE_KEYWORDS", "MAX_YEARS_EXPERIENCE", "MAX_POSTING_AGE_DAYS", "TOP_N",
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The real app, pointed at throwaway local files."""
    # Every request calls config.apply_settings(), which rebinds module globals
    # that monkeypatch never touched. Registering them here makes monkeypatch
    # restore them afterwards, so one test's settings cannot leak into the
    # filters another test file relies on.
    for name in _SETTINGS_NAMES:
        monkeypatch.setattr(config, name, getattr(config, name))
    monkeypatch.delenv("CONFIG_BUCKET", raising=False)
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "STORE_BACKEND", "json")
    monkeypatch.setattr(config, "CACHE_PATH", str(tmp_path / "scored.json"))
    (tmp_path / "profile.yaml").write_text(PROFILE)
    return TestClient(server.app)


def seed(*rows: tuple[str, ScoredJob]) -> None:
    store = JsonStore(config.CACHE_PATH, flush_every=1)
    for key, sj in rows:
        store.put(key, sj)
    store.flush()


def current_hash() -> str:
    import yaml

    return profile_fingerprint(profile_to_text(yaml.safe_load(PROFILE)))


def test_jobs_include_scored_at_for_the_new_marker(client):
    seed(("greenhouse#1", scored("1", scored_at=1_700_000_000)))
    body = client.get("/api/jobs").json()
    assert body[0]["scored_at"] == 1_700_000_000


def test_jobs_carry_every_field_the_frontend_renders(client):
    """Each key here is read by static/app.js. Losing one blanks a UI element."""
    seed(("greenhouse#1", scored("1")))
    row = client.get("/api/jobs").json()[0]

    for key in ("id", "source", "title", "company", "location", "url", "created"):
        assert key in row["job"], key
    for key in (
        "fit_score", "recommendation", "one_line", "met_requirements",
        "partial_requirements", "missing_requirements", "hard_blockers",
        "insufficient_information",
    ):
        assert key in row["score"], key
    assert {"scored_at", "stale"} <= row.keys()


def test_stale_is_true_only_for_a_known_different_profile(client):
    seed(
        ("greenhouse#1", scored("1", profile_hash="0000000000000000")),
        ("greenhouse#2", scored("2", profile_hash=current_hash())),
        ("greenhouse#3", scored("3")),  # unknown provenance: never stale
    )
    stale = {r["job"]["id"]: r["stale"] for r in client.get("/api/jobs").json()}
    assert stale == {"1": True, "2": False, "3": False}


def test_jobs_are_ranked_best_fit_first(client):
    low = scored("1")
    low.score.fit_score = 30
    seed(("greenhouse#1", low), ("greenhouse#2", scored("2")))
    ids = [r["job"]["id"] for r in client.get("/api/jobs").json()]
    assert ids == ["2", "1"]


def test_settings_round_trip_through_the_api(client):
    current = client.get("/api/settings").json()
    current["filters"]["max_years_experience"] = 2
    current["searches"] = [{"query": " cloud engineer ", "location": "Sydney"}]

    saved = client.put("/api/settings", json=current)
    assert saved.status_code == 200
    # The server's normalised copy is what the settings page re-renders from.
    assert saved.json()["searches"] == [{"query": "cloud engineer", "location": "Sydney"}]
    assert client.get("/api/settings").json()["filters"]["max_years_experience"] == 2


def test_invalid_settings_are_rejected_with_a_readable_message(client):
    current = client.get("/api/settings").json()
    current["searches"] = []
    res = client.put("/api/settings", json=current)
    assert res.status_code == 422
    assert "search" in res.json()["detail"].lower()


def test_profile_round_trip_through_the_api(client):
    new_profile = "name: Someone Else\n"
    res = client.put(
        "/api/profile", content=new_profile, headers={"Content-Type": "text/plain"}
    )
    assert res.status_code == 200
    assert client.get("/api/profile").text == new_profile


def test_a_profile_that_is_not_a_mapping_is_rejected(client):
    res = client.put(
        "/api/profile", content="- just\n- a list\n",
        headers={"Content-Type": "text/plain"},
    )
    assert res.status_code == 422
