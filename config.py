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
# Broad on purpose: Joseph is open to any tech role, and the LLM ranks by fit
# afterwards. These queries just decide which jobs enter the funnel.
SEARCHES = [
    {"query": "graduate software engineer", "location": "Melbourne"},
    {"query": "junior backend engineer", "location": "Melbourne"},
    {"query": "full stack developer", "location": "Melbourne"},
    {"query": "web developer", "location": "Melbourne"},
    {"query": "cloud engineer", "location": "Melbourne"},
    {"query": "data analyst", "location": "Melbourne"},
    {"query": "junior data engineer", "location": "Melbourne"},
    {"query": "AI engineer", "location": "Melbourne"},
]

MAX_RESULTS_PER_SEARCH = 20

# Drop anything the model flags as a hard blocker (e.g. clearance required)?
DROP_HARD_BLOCKERS = True


# --- Pre-filters (run before the LLM to save calls) ---
# Whole-word title matches that are auto-skipped. Tune freely.
EXCLUDE_TITLE_KEYWORDS = [
    "senior", "snr", "principal", "staff", "head of", "director",
    "vice president", "vp",
]
# Drop postings that clearly require this many years of experience or more.
# Conservative on purpose: borderline jobs pass through and the LLM decides.
MAX_YEARS_EXPERIENCE = 6

# --- Persistence ---
CACHE_PATH = ".cache/scored_jobs.json"

# --- Pacing and retries (defaults suit a free tier like Gemini Flash-Lite:
# ~15 requests/minute). Raise the interval if you still see rate-limit errors. ---
REQUEST_INTERVAL_SECONDS = 4.0

# --- Display ---
TOP_N = 25