"""Load profile.yaml and turn it into a compact text block for the LLM.

Kept deliberately simple: we dump the structured profile to readable YAML-ish
text. The scorer and (later) the cover-letter generator both consume this.
"""

from __future__ import annotations

import os

import yaml


def load_profile(path: str = "profile.yaml") -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. Copy profile.example.yaml to {path} and fill it in."
        )
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def profile_to_text(profile: dict) -> str:
    """Flatten the profile into a readable block for prompting."""
    return yaml.safe_dump(profile, sort_keys=False, allow_unicode=True).strip()
