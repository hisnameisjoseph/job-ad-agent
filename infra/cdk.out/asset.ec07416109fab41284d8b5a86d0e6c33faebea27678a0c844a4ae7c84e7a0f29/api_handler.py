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

logging.basicConfig(level=logging.INFO)

# lifespan="off": FastAPI's startup/shutdown events are unused here, and Lambda
# freezes the container between invocations rather than running an event loop
# to completion, so lifespan handling has nothing useful to do.
handler = Mangum(app, lifespan="off")
