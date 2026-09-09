"""Invalidate cached scores so the next `python main.py` re-scores them.

Use this after editing profile.yaml or the scoring prompt, since scores are
cached per job and will not otherwise be recalculated.

Backend-agnostic: it goes through the same `Store` the pipeline uses, so it
operates on the local JSON cache or on DynamoDB according to STORE_BACKEND.
That matters because the thing you are usually trying to clear is the scores
the deployed run paid for.

Examples:
    python rescore.py --dry-run          # show what would be cleared
    python rescore.py --all              # clear every cached score
    python rescore.py --min-score 40     # clear only scores of 40 and above
    python rescore.py --stale-days 45    # also drop postings older than this
"""

from __future__ import annotations

import argparse

import config
from filters import days_since_posted
from store import make_store


def main() -> None:
    p = argparse.ArgumentParser(description="Clear cached job scores.")
    p.add_argument("--all", action="store_true", help="clear every cached score")
    p.add_argument(
        "--min-score",
        type=int,
        help="clear only cached scores greater than or equal to this",
    )
    p.add_argument(
        "--stale-days",
        type=int,
        help="permanently drop cached postings older than this many days",
    )
    p.add_argument(
        "--dry-run", action="store_true", help="report changes without saving"
    )
    p.add_argument(
        "--hidden",
        action="store_true",
        help="just list cached jobs currently hidden, and why, then exit",
    )
    args = p.parse_args()

    store = make_store()
    cached = store.all()
    where = (
        config.STORE_TABLE_NAME
        if config.STORE_BACKEND == "dynamodb"
        else config.CACHE_PATH
    )

    if args.hidden:
        from visibility import hidden_reason

        rows = [(sj, hidden_reason(sj)) for sj in cached.values()]
        hidden = [(sj, why) for sj, why in rows if why]
        print(f"{len(hidden)} of {len(rows)} cached jobs are hidden:\n")
        for sj, why in sorted(hidden, key=lambda x: x[0].score.fit_score, reverse=True):
            print(f"  [{sj.score.fit_score:>3}] {sj.job.title[:52]:<52} {why}")
        return

    if not (args.all or args.min_score is not None or args.stale_days is not None):
        p.error("choose at least one of --all, --min-score, --stale-days, or --hidden")
    if not cached:
        print(f"Cache at {where} is empty. Nothing to do.")
        return

    total = len(cached)
    stale: list[str] = []
    cleared: list[str] = []

    for key, sj in cached.items():
        # 1. Permanently remove stale postings, so they never return.
        if args.stale_days is not None:
            age = days_since_posted(sj.job.created)
            if age is not None and age > args.stale_days:
                stale.append(key)
                continue

        # 2. Clear scores. Removing the entry is what makes it rescore next run.
        if args.all or (
            args.min_score is not None and sj.score.fit_score >= args.min_score
        ):
            cleared.append(key)

    kept = total - len(stale) - len(cleared)
    print(f"Backend:            {config.STORE_BACKEND} ({where})")
    print(f"Cached jobs:        {total}")
    print(f"Dropped as stale:   {len(stale)}")
    print(f"Cleared for rescore:{len(cleared):>4}")
    print(f"Left untouched:     {kept}")

    if args.dry_run:
        print("\nDry run: no changes written.")
        return

    # Delete rather than rewrite-the-survivors: on DynamoDB there is no
    # whole-table replace, and deleting only what changed is correct on both
    # backends. flush() is a no-op for Dynamo and one file write for JSON.
    for key in stale + cleared:
        store.delete(key)
    store.flush()

    print(f"\nSaved. Run `python main.py` to rescore {len(cleared)} job(s).")


if __name__ == "__main__":
    main()
