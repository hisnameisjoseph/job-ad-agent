"""Backend for the job-matcher viewer.

Serves the scored jobs the pipeline wrote to the store:
  - GET  /api/jobs      ranked jobs as JSON, each flagged if scored stale
  - GET  /api/me        who the caller is, per their verified token
  - GET  /api/settings  the editable searches and filter thresholds
  - PUT  /api/settings  replace them (validated)
  - GET  /api/profile   profile.yaml as text
  - PUT  /api/profile   replace it (validated)
  - everything else     static frontend from ./static (local development only)

The frontend fetches these and renders, so this file holds no HTML or CSS.

The write endpoints are why the API Lambda now holds s3:PutObject on the
config bucket. It still holds NO DynamoDB write access: settings are editable
from the web, scores are not.

Local:  uvicorn server:app --reload   then open http://localhost:8000
        Edits land in the repo's own profile.yaml / settings.yaml, so the
        settings page and your editor are changing one file.
Cloud:  api_handler.py wraps this same app in Mangum behind API Gateway, and
        the static files are served by CloudFront from S3 instead. Edits land
        in the versioned config bucket the scoring run reads each morning.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import config
import settings as settings_mod
from config_store import PROFILE_DOC, SETTINGS_DOC, make_config_store
from models import ScoredJob
from profile_loader import profile_fingerprint, profile_to_text
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


def _config() -> object:
    """The document store for profile.yaml / settings.yaml."""
    return make_config_store()


def _refresh_settings() -> None:
    """Apply the stored settings to the config module.

    Read per request rather than cached. The thresholds decide which jobs are
    visible, so serving a stale copy would show a list that disagrees with the
    settings page the user is looking at. One small GET is cheaper than that
    confusion, and this app serves one person.

    Note this reads through the CONFIG STORE, not config.SETTINGS_PATH: on
    Lambda the API function never runs the S3 bootstrap, so it has no local
    /tmp/settings.yaml to read and a path-based reload would silently fall
    back to defaults.
    """
    config.apply_settings(settings_mod.load(_config().read(SETTINGS_DOC)))


def _current_profile_hash() -> str | None:
    """Fingerprint of the profile as it stands NOW, for staleness comparison.

    None when there is no profile document, in which case nothing is flagged:
    "we cannot tell" must never render as "this score is stale".
    """
    raw = _config().read(PROFILE_DOC)
    if not raw:
        return None
    try:
        return profile_fingerprint(profile_to_text(yaml.safe_load(raw) or {}))
    except Exception as e:
        log.warning("Could not fingerprint the profile (%s).", e)
        return None


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
    # Thresholds may have been edited since the last request, and visibility
    # is recomputed from them, so refresh before filtering.
    _refresh_settings()
    jobs = _ranked()
    caller = _claims(request).get("email", "local")

    current = _current_profile_hash()
    payload = []
    for sj in jobs:
        row = sj.model_dump(mode="json")
        # Flagged, never hidden: a score computed against an older profile is
        # still the best information available until a rescore happens.
        row["stale"] = bool(current and sj.is_stale(current))
        payload.append(row)

    stale = sum(1 for r in payload if r["stale"])
    log.info("Served %d jobs (%d stale) to %s", len(payload), stale, caller)
    return JSONResponse(payload)


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


def _readable(exc: ValidationError) -> str:
    """Turn a Pydantic ValidationError into something a person can act on.

    str(ValidationError) is a multi-line dump ending in a link to the Pydantic
    docs. Shown in the settings page it reads as a crash rather than as "that
    number is too small", so the field path and the message are pulled out and
    everything else is dropped.
    """
    parts = []
    for err in exc.errors():
        # loc[0] is the model name; the rest is the path the user recognises.
        field = ".".join(str(p) for p in err["loc"]) or "settings"
        parts.append(f"{field}: {err['msg']}")
    return "; ".join(parts[:4]) or "Invalid settings."


# --- Editable configuration -------------------------------------------------
#
# Two documents, two shapes, on purpose.
#
# settings.yaml is a fixed schema of scalars and short lists, so it travels as
# JSON and the browser renders a real form. profile.yaml is prose in nested
# free text, and any form generated from it would be worse than a text box, so
# it travels as raw YAML and the browser shows an editor. That is also why the
# profile page is desktop-first: editing a resume on a phone keyboard is not a
# thing anyone wants to do.


@app.get("/api/settings")
def api_get_settings():
    """Current searches and thresholds, as the form wants them."""
    stored = settings_mod.load(_config().read(SETTINGS_DOC))
    return JSONResponse(stored.model_dump(mode="json"))


@app.put("/api/settings")
def api_put_settings(request: Request, body: dict = Body(...)):
    """Replace settings.yaml. Rejects anything that would not run.

    Validated BEFORE the write, never after: a settings document that parses
    but is nonsense (no searches, a zero page size) changes what enters the
    funnel, and the symptom is an empty run tomorrow with nothing in the logs
    pointing back at this edit.
    """
    try:
        validated = settings_mod.Settings.model_validate(body)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=_readable(e)) from None
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid settings: {e}") from None

    _config().write(SETTINGS_DOC, settings_mod.dump(validated))
    config.apply_settings(validated)  # visible on the very next request

    log.info(
        "Settings updated by %s: %d searches, top_n=%d",
        _claims(request).get("email", "local"),
        len(validated.searches),
        validated.filters.top_n,
    )
    return JSONResponse(validated.model_dump(mode="json"))


@app.get("/api/profile")
def api_get_profile():
    """profile.yaml as text, for the editor."""
    raw = _config().read(PROFILE_DOC)
    if raw is None:
        raise HTTPException(status_code=404, detail="No profile.yaml stored yet.")
    return PlainTextResponse(raw, media_type="text/yaml")


@app.put("/api/profile")
def api_put_profile(request: Request, body: str = Body(..., media_type="text/plain")):
    """Replace profile.yaml.

    Only structural validation: it must be a non-empty YAML mapping. The
    CONTENT is deliberately not constrained, because the profile is free prose
    fed to an LLM and inventing a schema for it would only get in the way. But
    a document that is not a mapping would make load_profile return a string
    or a list, and profile_to_text would then prompt the model with garbage.
    """
    try:
        parsed = yaml.safe_load(body)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=422, detail=f"Not valid YAML: {e}") from None

    if not isinstance(parsed, dict) or not parsed:
        raise HTTPException(
            status_code=422,
            detail="profile.yaml must be a non-empty mapping (key: value pairs).",
        )

    _config().write(PROFILE_DOC, body)
    log.info(
        "Profile updated by %s (%d bytes, %d top-level keys)",
        _claims(request).get("email", "local"), len(body), len(parsed),
    )
    # Returned so the page can say how many existing scores just went stale.
    return {"ok": True, "profile_hash": profile_fingerprint(profile_to_text(parsed))}


# Local development only. The Docker image deliberately does not copy static/
# in — on AWS these files are served by CloudFront from S3, so a Lambda has no
# business serving them and mounting a missing directory would raise at import.
if STATIC_DIR.is_dir():
    # Mounted last so the /api routes win. html=True serves index.html at "/".
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
