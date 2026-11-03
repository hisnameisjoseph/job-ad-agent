"""Unit tests for the scored-job store.

The store is the piece that makes an interrupted run survivable, so its
round-trip, key-migration, and corrupt-row behaviour are worth pinning down.

Run:  pytest -q
"""

from __future__ import annotations

import json
from decimal import Decimal

from models import JobPosting, ScoreResult, ScoredJob
from store import DynamoStore, JsonStore, job_key, stamp_scored_at


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


# --- scored_at: the "new since your last visit" timestamp ------------------


def test_stamp_sets_scored_at_when_missing():
    assert stamp_scored_at(make(), now=1_700_000_000).scored_at == 1_700_000_000


def test_stamp_never_overwrites_an_existing_scored_at():
    """A copied row (JSON -> DynamoDB) must keep its real age, or every
    migrated job would show up as 'new' the morning after the migration."""
    original = make().model_copy(update={"scored_at": 1_600_000_000})
    assert stamp_scored_at(original, now=1_700_000_000).scored_at == 1_600_000_000


def test_stamp_does_not_mutate_its_argument():
    original = make()
    stamp_scored_at(original, now=1_700_000_000)
    assert original.scored_at is None


def test_json_store_stamps_on_put_and_persists_it(tmp_path):
    path = str(tmp_path / "c.json")
    store = JsonStore(path, flush_every=1)
    store.put("adzuna#1", make())

    stamped = store.get("adzuna#1").scored_at
    assert isinstance(stamped, int) and stamped > 0
    assert JsonStore(path).get("adzuna#1").scored_at == stamped


def test_legacy_rows_without_scored_at_still_load(tmp_path):
    """Rows written before the field existed load with None ('unknown'),
    which the viewer treats as not new."""
    path = tmp_path / "c.json"
    legacy = make().model_dump(mode="json")
    legacy.pop("scored_at", None)
    path.write_text(json.dumps({"adzuna#1": legacy}))
    assert JsonStore(str(path)).get("adzuna#1").scored_at is None


class _FakeTable:
    """Just enough of a boto3 Table for DynamoStore, with no AWS involved."""

    def __init__(self, items=None):
        self.items = {i["job_key"]: i for i in (items or [])}

    def put_item(self, Item):
        self.items[Item["job_key"]] = Item

    def get_item(self, Key):
        item = self.items.get(Key["job_key"])
        return {"Item": item} if item else {}

    def scan(self, **_):
        return {"Items": list(self.items.values())}


def _dynamo(table: _FakeTable) -> DynamoStore:
    # Bypass __init__, which would build a real boto3 resource.
    store = DynamoStore.__new__(DynamoStore)
    store._table = table
    store._ttl_seconds = 90 * 24 * 3600
    store._metadata = {}
    return store


def test_dynamo_put_writes_the_same_scored_at_to_payload_and_attribute():
    table = _FakeTable()
    _dynamo(table).put("adzuna#1", make())

    item = table.items["adzuna#1"]
    payload = json.loads(item["payload"])
    assert payload["scored_at"] == item["scored_at"]
    assert item["expires_at"] > item["scored_at"]


def test_dynamo_backfills_scored_at_for_rows_written_before_the_field():
    """Older rows hold scored_at only as a top-level attribute (a Decimal, as
    boto3 returns it). The viewer must still see their real age."""
    legacy = make().model_dump(mode="json")
    legacy.pop("scored_at", None)
    table = _FakeTable([
        {"job_key": "adzuna#1", "payload": json.dumps(legacy),
         "scored_at": Decimal("1650000000")},
    ])
    store = _dynamo(table)

    assert store.get("adzuna#1").scored_at == 1_650_000_000
    assert store.all()["adzuna#1"].scored_at == 1_650_000_000


def test_dynamo_payload_value_wins_over_the_attribute():
    stored = make().model_copy(update={"scored_at": 1_700_000_000})
    table = _FakeTable([
        {"job_key": "adzuna#1", "payload": stored.model_dump_json(),
         "scored_at": Decimal("1")},
    ])
    assert _dynamo(table).get("adzuna#1").scored_at == 1_700_000_000
