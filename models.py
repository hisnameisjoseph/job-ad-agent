"""Typed data models for the pipeline.

These Pydantic models are the contract between every stage: sources produce
JobPosting objects, and the LLM is forced to return a ScoreResult. Because the
LLM output is validated against ScoreResult, a weaker/free model that returns
slightly malformed JSON gets auto-retried instead of crashing the run.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class JobPosting(BaseModel):
    """A normalised job posting, produced by any source."""

    id: str = Field(description="Stable unique id from the source")
    source: str = Field(description="Which provider produced this, e.g. 'adzuna'")
    title: str
    company: Optional[str] = None
    location: Optional[str] = None
    description: str = ""
    url: str = ""
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    remote: Optional[bool] = None
    created: Optional[str] = None  # ISO date string from the source


class Recommendation(str, Enum):
    apply = "apply"
    maybe = "maybe"
    skip = "skip"


class ScoreResult(BaseModel):
    """The LLM's structured judgement about one job for one candidate.

    Note: there is deliberately NO 'probability of interview' field. A model
    cannot calibrate that, so we ask for a fit score plus an honest gap
    breakdown instead.
    """

    fit_score: int = Field(ge=0, le=100, description="Overall fit, 0 to 100")
    recommendation: Recommendation
    one_line: str = Field(description="One sentence on why this score")
    met_requirements: list[str] = Field(
        default_factory=list, description="Requirements the candidate clearly meets"
    )
    partial_requirements: list[str] = Field(
        default_factory=list, description="Requirements partially met or transferable"
    )
    missing_requirements: list[str] = Field(
        default_factory=list, description="Requirements the candidate lacks"
    )
    hard_blockers: list[str] = Field(
        default_factory=list,
        description=(
            "Automatic disqualifiers for THIS candidate, e.g. requires Australian "
            "citizenship or a security clearance. Empty if none."
        ),
    )


class ScoredJob(BaseModel):
    """A job plus its score, used for ranking and display."""

    job: JobPosting
    score: ScoreResult
