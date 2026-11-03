"""Backend for the job-matcher viewer.

Serves the scored jobs the pipeline wrote to the store:
  - GET /api/jobs   ranked jobs as JSON
  - GET /api/me     who the caller is, per their verified token
  - everything else  static frontend from ./static (local development only)

The frontend fetches /api/jobs and renders the table, so this file holds no
HTML or CSS.

Local:  uvicorn server:app --reload   then open http://localhost:8000
Cloud:  api_handler.py wraps this same app in Mangum behind API Gateway, and
        the static files are served by CloudFront from S3 instead.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import config
from models import ScoredJob
from store import Store, make_store
from visibility import visible_ranked

log = logging.getLogger(__name__)

app = FastAPI(title="Job Matcher")

STATIC_DIR = Path(__file__).parent / "static"

_dynamo_store: Store | None = None


def _store() -> Store:
    """The store this deployment is configured for.

    Previously this was hardcoded to JsonStore, which meant the viewer read a
    local file while the deployed pipeline wrote to DynamoDB — so the hosted
    page would have shown an empty list forever. Going through make_store()
    means the viewer always reads whatever the pipeline just wrote.

    The DynamoDB store is built once and reused, because its all() re-reads the
    table on every call regardless and constructing a boto3 resource per
    request is pure latency. The JSON store caches the file contents in memory
    at construction, so it is rebuilt per request to pick up a run that
    finished after the server started.
    """
    global _dynamo_store
    if config.STORE_BACKEND == "dynamodb":
        if _dynamo_store is None:
            _dynamo_store = make_store()
        return _dynamo_store
    return make_store()


def _ranked() -> list[ScoredJob]:
    """Currently-visible jobs. Shares visibility rules with main.py, so the web
    viewer and the terminal table can never disagree.
    """
    return visible_ranked(_store().all())


def _claims(request: Request) -> dict:
    """Verified JWT claims for this request, or {} when running locally.

    These are trustworthy without re-checking: API Gateway's Cognito authorizer
    validates the token's signature, issuer, audience and expiry and rejects
    the request outright before this function is ever invoked. Anything that
    reaches here already presented a valid token.

    Empty locally, where uvicorn serves the app with no gateway in front.
    """
    event = request.scope.get("aws.event") or {}
    authorizer = event.get("requestContext", {}).get("authorizer", {})
    return authorizer.get("jwt", {}).get("claims", {}) or {}


@app.get("/api/jobs")
def api_jobs(request: Request):
    jobs = _ranked()
    caller = _claims(request).get("email", "local")
    log.info("Served %d jobs to %s", len(jobs), caller)
    return JSONResponse([sj.model_dump(mode="json") for sj in jobs])


@app.get("/api/me")
def api_me(request: Request):
    """Who the caller is. Lets the frontend show the signed-in account and
    gives us a cheap way to confirm the whole auth chain works end to end.
    """
    claims = _claims(request)
    return {
        "email": claims.get("email"),
        "authenticated": bool(claims),
    }


# Local development only. The Docker image deliberately does not copy static/
# in — on AWS these files are served by CloudFront from S3, so a Lambda has no
# business serving them and mounting a missing directory would raise at import.
if STATIC_DIR.is_dir():
    # Mounted last so the /api routes win. html=True serves index.html at "/".
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
