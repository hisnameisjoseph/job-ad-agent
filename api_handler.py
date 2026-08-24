"""AWS Lambda entry point for the read-only viewer API.

Mangum adapts the existing FastAPI app in server.py to Lambda's event/response
shape, so the API served on the internet and the one served by `uvicorn
server:app` locally are literally the same application. No second
implementation to drift.

Note what this deliberately does NOT do: it never calls
aws_config.bootstrap_environment(). The scoring Lambda needs that because it
talks to Adzuna and an LLM and scores against profile.yaml. This function only
reads already-scored rows out of DynamoDB, so it needs no API keys and no
profile download. Skipping the bootstrap keeps the cold start short and lets
this function's IAM role hold nothing but DynamoDB read access.

Auth is enforced upstream: API Gateway's Cognito JWT authorizer rejects
unauthenticated requests before this function is ever invoked.
"""

from __future__ import annotations

import logging

from mangum import Mangum

from server import app

# logging.basicConfig() is a NO-OP under Lambda. The runtime installs a handler
# on the root logger before this module is imported, and basicConfig() returns
# early whenever root.handlers is non-empty — so the level is never applied, the
# root logger stays at WARNING, and every log.info() below is silently dropped.
#
# The symptom is nasty precisely because nothing errors: rich's console.print
# goes to stdout and survives, so a healthy run appears to fetch jobs, score
# them, and then stop dead with no completion summary. Set the level explicitly.
logging.getLogger().setLevel(logging.INFO)

# lifespan="off": FastAPI's startup/shutdown events are unused here, and Lambda
# freezes the container between invocations rather than running an event loop
# to completion, so lifespan handling has nothing useful to do.
handler = Mangum(app, lifespan="off")
