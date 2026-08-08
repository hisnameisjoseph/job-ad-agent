"""Invalidate cached scores so the next `python main.py` re-scores them.

Use this after editing profile.yaml or the scoring prompt, since scores are
cached by job id and will not otherwise be recalculated.

Examples:
    python rescore.py --dry-run          # show what would be cleared
    python rescore.py --all              # clear every cached score
    python rescore.py --min-score 40     # clear only scores of 40 and above
    python rescore.py --stale-days 45    # also drop postings older than this
"""

from __future__ import annotations

import argparse

import config
from cache import load_cache, save_cache
from filters import days_since_posted


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

    cache = load_cache(config.CACHE_PATH)

    if args.hidden:
        from visibility import hidden_reason

        rows = [(sj, hidden_reason(sj)) for sj in cache.values()]
        hidden = [(sj, why) for sj, why in rows if why]
        print(f"{len(hidden)} of {len(rows)} cached jobs are hidden:\n")
        for sj, why in sorted(hidden, key=lambda x: x[0].score.fit_score, reverse=True):
            print(f"  [{sj.score.fit_score:>3}] {sj.job.title[:52]:<52} {why}")
        return

    if not (args.all or args.min_score is not None or args.stale_days is not None):
        p.error("choose at least one of --all, --min-score, --stale-days, or --hidden")
    if not cache:
        print(f"Cache at {config.CACHE_PATH} is empty. Nothing to do.")
        return

    total = len(cache)
    dropped_stale = 0
    cleared = 0
    kept: dict = {}

    for jid, sj in cache.items():
        # 1. Permanently remove stale postings, so they never return.
        if args.stale_days is not None:
            age = days_since_posted(sj.job.created)
            if age is not None and age > args.stale_days:
                dropped_stale += 1
                continue

        # 2. Clear scores (by removing the entry, the job is rescored next run).
        if args.all or (
            args.min_score is not None and sj.score.fit_score >= args.min_score
        ):
            cleared += 1
            continue

        kept[jid] = sj

    print(f"Cached jobs:        {total}")
    print(f"Dropped as stale:   {dropped_stale}")
    print(f"Cleared for rescore:{cleared:>4}")
    print(f"Left untouched:     {len(kept)}")

    if args.dry_run:
        print("\nDry run: no changes written.")
        return

    save_cache(config.CACHE_PATH, kept)
    print(f"\nSaved. Run `python main.py` to rescore {cleared} job(s).")


if __name__ == "__main__":
    main()