"""Backend for the job-matcher viewer.

Serves the scored jobs the pipeline wrote to .cache/scored_jobs.json:
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
from cache import load_cache
from models import ScoredJob
from visibility import visible_ranked

app = FastAPI(title="Job Matcher")

STATIC_DIR = Path(__file__).parent / "static"


def _ranked() -> list[ScoredJob]:
    """Currently-visible jobs. Shares visibility rules with main.py, so the
    web viewer and the terminal table can never disagree."""
    return visible_ranked(load_cache(config.CACHE_PATH))


@app.get("/api/jobs")
def api_jobs():
    return JSONResponse([sj.model_dump(mode="json") for sj in _ranked()])


# Mounted last so /api/jobs wins. html=True serves index.html at "/".
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")