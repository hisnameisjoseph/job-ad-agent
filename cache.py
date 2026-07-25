"""Tiny JSON-file cache of jobs we have already scored.

Keyed by job id and storing the full ScoredJob, so a re-run neither rescores
already-seen jobs nor loses their scores. In steady state a daily run only
spends API calls on genuinely new postings. Swap this file for DynamoDB in
Phase 3 without touching the rest of the pipeline.
"""

from __future__ import annotations

import json
import os

from models import ScoredJob


def load_cache(path: str) -> dict[str, ScoredJob]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    out: dict[str, ScoredJob] = {}
    for jid, data in raw.items():
        try:
            out[jid] = ScoredJob.model_validate(data)
        except Exception:
            continue  # ignore anything that no longer matches the schema
    return out


def save_cache(path: str, cache: dict[str, ScoredJob]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    serialisable = {jid: sj.model_dump(mode="json") for jid, sj in cache.items()}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(serialisable, f, indent=2)
    os.replace(tmp, path)  # write then swap, so a crash can't corrupt the file