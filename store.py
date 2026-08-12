"""Persistence for scored jobs.

The pipeline used to hold every score in memory and call save_cache() once,
after the entire scoring loop. On a laptop that is merely wasteful. On Lambda
it is fatal: a timeout is a SIGKILL with no exception and no cleanup, so every
score computed in that run is lost, and the next run repeats the same work and
times out in the same place, forever.

Store fixes that by writing through as jobs are scored. JsonStore keeps the
existing .cache/scored_jobs.json format; a DynamoStore exposing the same four
methods drops in later without touching the pipeline.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional, Protocol

from models import JobPosting, ScoredJob

log = logging.getLogger(__name__)


def job_key(job: JobPosting) -> str:
    """Cache key for a posting.

    Namespaced by source because ids are only unique WITHIN a provider. With
    Adzuna, Greenhouse, Lever and Ashby all running, a bare id will eventually
    collide and attach one job's score to a different job's posting.
    """
    return f"{job.source}#{job.id}"


class Store(Protocol):
    """Minimal persistence interface. DynamoStore will implement this too."""

    def get(self, key: str) -> Optional[ScoredJob]: ...
    def put(self, key: str, scored: ScoredJob) -> None: ...
    def all(self) -> dict[str, ScoredJob]: ...
    def flush(self) -> None: ...


class JsonStore:
    """Scored jobs in a single JSON file, written through as they arrive.

    flush_every trades durability against IO. Rewriting the whole file on every
    put is O(n) per job, which gets slow as the cache grows, so we batch a few
    writes. A crash loses at most flush_every jobs instead of the entire run.
    DynamoDB removes this tradeoff entirely (one PutItem per job).

    put() performs no awaits, so under asyncio it is atomic with respect to
    other coroutines and needs no lock.
    """

    def __init__(self, path: str, flush_every: int = 5):
        self.path = path
        self.flush_every = max(1, flush_every)
        self._data: dict[str, ScoredJob] = self._load()
        self._pending = 0

    def _load(self) -> dict[str, ScoredJob]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Could not read %s (%s). Starting empty.", self.path, e)
            return {}

        out: dict[str, ScoredJob] = {}
        dropped = 0
        migrated = 0
        for key, data in raw.items():
            try:
                scored = ScoredJob.model_validate(data)
            except Exception:
                dropped += 1
                continue
            # Migrate pre-namespace keys (bare source id) so switching to
            # composite keys does not silently trigger a full paid rescore.
            if "#" not in key:
                key = job_key(scored.job)
                migrated += 1
            out[key] = scored

        if dropped:
            log.warning(
                "Dropped %d cached entries that no longer match the schema. "
                "They will be rescored.", dropped
            )
        if migrated:
            log.info("Migrated %d cache keys to 'source#id' form.", migrated)
        return out

    def get(self, key: str) -> Optional[ScoredJob]:
        return self._data.get(key)

    def put(self, key: str, scored: ScoredJob) -> None:
        self._data[key] = scored
        self._pending += 1
        if self._pending >= self.flush_every:
            self.flush()

    def all(self) -> dict[str, ScoredJob]:
        return dict(self._data)

    def flush(self) -> None:
        if self._pending == 0:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        payload = {k: v.model_dump(mode="json") for k, v in self._data.items()}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, self.path)  # write then swap: a crash cannot corrupt it
        self._pending = 0

    def __len__(self) -> int:
        return len(self._data)