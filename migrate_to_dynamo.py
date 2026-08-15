"""One-off: migrate the local JSON cache into DynamoDB.

Existing scores cost real money. Copying them across means switching backends
is free rather than a full rescore.

Safe to re-run: put_item overwrites by key, so nothing duplicates.

Run:  python migrate_to_dynamo.py
"""

from __future__ import annotations

import sys

import config
from store import DynamoStore, JsonStore


def main() -> None:
    source = JsonStore(config.CACHE_PATH)
    items = source.all()
    if not items:
        print(f"Nothing to migrate: {config.CACHE_PATH} is empty or missing.")
        return

    target = DynamoStore(config.STORE_TABLE_NAME, region=config.AWS_REGION)
    print(f"Copying {len(items)} scored jobs -> {config.STORE_TABLE_NAME} ...")

    copied = 0
    for key, scored in items.items():
        try:
            target.put(key, scored)
            copied += 1
        except Exception as e:
            print(f"  failed {key}: {e}", file=sys.stderr)

    print(f"Done. {copied}/{len(items)} copied.")
    print("Spot-check one:", next(iter(items)))


if __name__ == "__main__":
    main()