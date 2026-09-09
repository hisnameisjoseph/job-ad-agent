"""AWS Lambda entry point for the daily scoring run.

Deliberately thin. It owns only the things that differ from a local run:
secrets, config download, and the invocation deadline. The pipeline itself is
main.py's, unchanged, so the CLI and the scheduled run can never diverge.
"""

from __future__ import annotations

import asyncio
import logging
import time

# MUST run before `import config` — config reads os.environ at import time.
from aws_config import bootstrap_environment

# logging.basicConfig() is a NO-OP under Lambda. The runtime installs a handler
# on the root logger before this module is imported, and basicConfig() returns
# early whenever root.handlers is non-empty — so the level is never applied, the
# root logger stays at WARNING, and every log.info() below is silently dropped.
#
# The symptom is nasty precisely because nothing errors: rich's console.print
# goes to stdout and survives, so a healthy run appears to fetch jobs, score
# them, and then stop dead with no completion summary. Set the level explicitly.
logging.getLogger().setLevel(logging.INFO)

bootstrap_environment()

import config  # noqa: E402
from main import collect_jobs, score_new_jobs  # noqa: E402
from profile_loader import (  # noqa: E402
    load_profile,
    profile_fingerprint,
    profile_to_text,
)
from scoring import PROMPT_VERSION, cache_summary  # noqa: E402
from store import make_store  # noqa: E402

log = logging.getLogger(__name__)

# Stop starting new work with this much of the invocation left, so the run
# always finishes cleanly instead of being SIGKILLed at the timeout.
SAFETY_MARGIN_SECONDS = 60


async def _run(deadline: float) -> dict:
    profile_text = profile_to_text(load_profile(config.PROFILE_PATH))

    store = make_store(metadata={
        "model": config.SCORING_MODEL,
        "prompt_version": PROMPT_VERSION,
        "profile_hash": profile_fingerprint(profile_text),
    })

    jobs = collect_jobs()
    try:
        return await score_new_jobs(profile_text, jobs, store, deadline)
    finally:
        store.flush()  # no-op for DynamoDB, correct for any future backend


def handler(event, context) -> dict:
    """EventBridge Scheduler target. `event` is unused."""
    remaining = context.get_remaining_time_in_millis() / 1000.0
    deadline = time.monotonic() + remaining - SAFETY_MARGIN_SECONDS
    log.info("Invocation budget: %.0fs (margin %ds)", remaining, SAFETY_MARGIN_SECONDS)

    stats = asyncio.run(_run(deadline))

    log.info("Run complete: %s", stats)
    log.info(cache_summary())
    return {"statusCode": 200, "stats": stats}
