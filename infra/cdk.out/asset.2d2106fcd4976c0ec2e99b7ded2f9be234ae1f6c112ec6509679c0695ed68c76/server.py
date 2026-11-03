"""Backend for the job-matcher viewer.

Serves the scored jobs the pipeline wrote to the store:
  - GET /api/jobs   ranked jobs as JSON
  - everything else  static frontend from ./static (index.html, style.css, app.js)

The frontend fetches /api/jobs and renders the table, so this file holds no
HTML or CSS. Run:  uvicorn server:app --reload   then open http://localhost:8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import config
from models import ScoredJob
from store import JsonStore
from visibility import visible_ranked

app = FastAPI(title="Job Matcher")

STATIC_DIR = Path(__file__).parent / "static"


def _ranked() -> list[ScoredJob]:
    """Currently-visible jobs. Shares visibility rules with main.py, so the
    web viewer and the terminal table can never disagree.

    The store is re-read per request so the page reflects a run that finished
    after the server started.
    """
    return visible_ranked(JsonStore(config.CACHE_PATH).all())


@app.get("/api/jobs")
def api_jobs():
    return JSONResponse([sj.model_dump(mode="json") for sj in _ranked()])


# Mounted last so /api/jobs wins. html=True serves index.html at "/".
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")