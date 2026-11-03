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
    description_truncated: bool = False
    """True when `description` is a summary, not the full posting.

    Adzuna's search API caps descriptions at 500 characters, usually too short
    to include the requirements section. Downstream code (filters, scoring
    prompt) must know this so it never treats an unseen requirement as an
    absent one.
    """


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
    insufficient_information: bool = Field(
        default=False,
        description=(
            "True when the posting text was too short or vague to judge "
            "properly, e.g. a truncated summary with no requirements section."
        ),
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

    # --- Provenance ---
    # What this score was computed AGAINST, so the viewer can say "scored under
    # an older profile" instead of presenting a stale judgement as current.
    # Stored in the payload rather than as a DynamoDB attribute so both
    # backends carry it and the JSON cache stays self-describing.
    #
    # All optional: rows scored before this existed load with None, and None
    # means "unknown", never "stale". Flagging thousands of pre-existing rows
    # as stale on the day this shipped would be noise, not information.
    profile_hash: Optional[str] = None
    prompt_version: Optional[str] = None
    model: Optional[str] = None

    # When this score was first stored, as Unix epoch seconds (UTC). Powers the
    # viewer's "new since your last visit" marker and its "sort by scored".
    #
    # Stamped by the store on first write, and preserved on every later write,
    # so copying a row between backends (migrate_to_dynamo.py) keeps its real
    # age. None for rows written before this field existed, and the viewer
    # treats None as "not new": an unknown age must never light up hundreds of
    # old rows as fresh.
    scored_at: Optional[int] = None

    def is_stale(self, current_profile_hash: str) -> bool:
        """True only when we KNOW this was scored against a different profile."""
        return (
            self.profile_hash is not None
            and self.profile_hash != current_profile_hash
        )
