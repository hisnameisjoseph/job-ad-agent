"""The runtime-editable settings: what to search for, and what to filter out.

These used to be module constants in config.py, which meant changing the job
titles you search for required a Docker rebuild and a full CDK redeploy. They
now live in settings.yaml so the website can edit them.

Everything here is validated with Pydantic rather than trusted, because the
write path is an HTTP endpoint. A settings document that parses but is
nonsense -- a negative age limit, an empty query string -- would silently
change what enters the funnel, and the symptom would be "no jobs today" with
nothing in the logs.

The defaults below are the values config.py used to hardcode, so a checkout
with no settings.yaml behaves exactly as it did before this file existed.
"""

from __future__ import annotations

import logging
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)


class Search(BaseModel):
    """One Adzuna query: a phrase and where to run it."""

    query: str = Field(min_length=1, max_length=120)
    location: str = Field(min_length=1, max_length=120)

    @field_validator("query", "location")
    @classmethod
    def _strip(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class Filters(BaseModel):
    """Pre-filters applied before the LLM, and again at display time."""

    exclude_title_keywords: list[str] = Field(
        default_factory=lambda: [
            "senior", "snr", "principal", "staff", "head of", "director",
            "vice president", "vp", "sr", "manager",
        ]
    )
    # A graduate with ~3 months of professional experience cannot reach a role
    # asking 3+ years, and letting those through wastes API calls.
    max_years_experience: Optional[int] = Field(default=3, ge=0, le=40)
    # None disables the age filter. Jobs with no date are always kept.
    max_posting_age_days: Optional[int] = Field(default=45, ge=1, le=365)
    drop_hard_blockers: bool = True
    top_n: int = Field(default=25, ge=1, le=500)

    @field_validator("exclude_title_keywords")
    @classmethod
    def _clean_keywords(cls, v: list[str]) -> list[str]:
        return [k.strip().lower() for k in v if isinstance(k, str) and k.strip()]


class Settings(BaseModel):
    """The whole editable document."""

    searches: list[Search] = Field(
        default_factory=lambda: [
            Search(query=q, location="Melbourne")
            for q in (
                "graduate software engineer",
                "junior backend engineer",
                "full stack developer",
                "web developer",
                "cloud engineer",
                "data analyst",
                "junior data engineer",
                "AI engineer",
            )
        ]
    )
    max_results_per_search: int = Field(default=20, ge=1, le=100)
    filters: Filters = Field(default_factory=Filters)

    @field_validator("searches")
    @classmethod
    def _at_least_one(cls, v: list[Search]) -> list[Search]:
        # An empty search list is almost certainly an accident, and its effect
        # -- no Adzuna results at all, forever -- looks like a broken API key
        # rather than a config mistake. Refuse it at the door.
        if not v:
            raise ValueError("at least one search is required")
        return v


def parse(text: str) -> Settings:
    """Validate a settings document. Raises on anything malformed."""
    data = yaml.safe_load(text)
    if data is None:
        return Settings()
    if not isinstance(data, dict):
        raise ValueError("settings.yaml must be a mapping at the top level")
    return Settings.model_validate(data)


def dump(s: Settings) -> str:
    """Serialise settings back to YAML, ready to store."""
    return yaml.safe_dump(
        s.model_dump(mode="json"), sort_keys=False, allow_unicode=True
    )


def load(text: Optional[str]) -> Settings:
    """Settings from a document, falling back to defaults.

    Never raises. A missing or unreadable settings.yaml must not stop a
    scoring run: the defaults are the values this project shipped with, so
    falling back to them is a working configuration rather than an empty one.
    Use parse() instead when a caller needs to see the error, such as the
    website rejecting a bad edit.
    """
    if not text:
        return Settings()
    try:
        return parse(text)
    except Exception as e:
        log.error("settings.yaml is invalid (%s). Using defaults.", e)
        return Settings()
