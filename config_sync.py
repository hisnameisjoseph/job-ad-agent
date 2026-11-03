"""Move profile.yaml and settings.yaml between your laptop and the cloud.

Why this exists: the website edits the copies in S3, and you edit the copies in
your repo. Those are the same document in two places, so without a way to move
them you end up guessing which is current and quietly overwriting the other.

    python config_sync.py --diff    # what differs, changing nothing
    python config_sync.py --pull    # S3  -> local  (take what the site saved)
    python config_sync.py --push    # local -> S3   (publish your edits)

Needs CONFIG_BUCKET set, and AWS credentials. Get the bucket name from the
stack output:

    aws cloudformation describe-stacks --stack-name InfraStack \\
      --query "Stacks[0].Outputs[?OutputKey=='ConfigBucketName'].OutputValue" \\
      --output text

--pull and --push overwrite the destination, so both refuse to run when the
other side has changes you have not seen. Pass --force when you genuinely mean
to discard them.
"""

from __future__ import annotations

import argparse
import difflib
import os
import sys

from config_store import EDITABLE_DOCS, LocalConfigStore, S3ConfigStore


def _diff(name: str, local: str | None, remote: str | None) -> list[str]:
    return list(
        difflib.unified_diff(
            (remote or "").splitlines(keepends=True),
            (local or "").splitlines(keepends=True),
            fromfile=f"s3://{name}",
            tofile=f"local/{name}",
        )
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--pull", action="store_true", help="S3 -> local")
    g.add_argument("--push", action="store_true", help="local -> S3")
    g.add_argument("--diff", action="store_true", help="show differences only")
    p.add_argument(
        "--force", action="store_true", help="overwrite even if the other side differs"
    )
    args = p.parse_args()

    bucket = os.environ.get("CONFIG_BUCKET")
    if not bucket:
        print(
            "CONFIG_BUCKET is not set. Add it to .env, or export it:\n"
            "  export CONFIG_BUCKET=job-ad-agent-config-<your-account-id>",
            file=sys.stderr,
        )
        return 2

    local = LocalConfigStore(".")
    remote = S3ConfigStore(bucket)

    differing = []
    for name in EDITABLE_DOCS:
        lines = _diff(name, local.read(name), remote.read(name))
        if lines:
            differing.append(name)
            if args.diff:
                print("".join(lines), end="")

    if args.diff:
        if not differing:
            print("Local and S3 are identical.")
        return 0

    if differing and not args.force:
        # The whole point of the check: an unseen remote edit is exactly the
        # thing --push would destroy, and it is invisible until you look.
        print(
            f"These differ between local and S3: {', '.join(differing)}\n"
            "Run --diff to see what changed, then re-run with --force to "
            "overwrite the destination.",
            file=sys.stderr,
        )
        return 1

    src, dst, arrow = (
        (remote, local, "S3 -> local") if args.pull else (local, remote, "local -> S3")
    )
    moved = 0
    for name in EDITABLE_DOCS:
        body = src.read(name)
        if body is None:
            print(f"  skip {name}: not present at the source")
            continue
        dst.write(name, body)
        print(f"  {arrow}  {name} ({len(body)} bytes)")
        moved += 1

    print(f"Done. {moved} document(s) copied.")
    if args.push:
        print("The next scoring run will pick these up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
