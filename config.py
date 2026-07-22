"""Central configuration.

Reads secrets from the environment (or a .env file via python-dotenv).
Change SCORING_MODEL to any LiteLLM-supported model string to compare providers.
Examples:
  "openrouter/deepseek/deepseek-chat"          (cheap paid)
  "openrouter/meta-llama/llama-3.3-70b-instruct:free"  (free tier)
  "gemini/gemini-2.5-flash-lite"               (cheap, needs GEMINI_API_KEY)
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


# --- Adzuna credentials (free at https://developer.adzuna.com/) ---
ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
ADZUNA_COUNTRY = os.getenv("ADZUNA_COUNTRY", "au")

# --- LLM model (any LiteLLM string). Provider API key must be set in env. ---
SCORING_MODEL = os.getenv("SCORING_MODEL", "openrouter/deepseek/deepseek-chat")

# --- What and where to search. Edit freely. ---
SEARCHES = [
    {"query": "junior backend engineer", "location": "Melbourne"},
    {"query": "graduate software engineer", "location": "Melbourne"},
    {"query": "full stack developer", "location": "Melbourne"},
]

MAX_RESULTS_PER_SEARCH = 20

# Drop anything the model flags as a hard blocker (e.g. clearance required)?
DROP_HARD_BLOCKERS = True
