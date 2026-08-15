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
# Ask Adzuna to exclude stale ads at the API level, and return newest first.
ADZUNA_MAX_DAYS_OLD = 45
ADZUNA_SORT_BY = "date"

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
    "vice president", "vp", "sr", "manager"
]
# Drop postings that clearly require this many years of experience or more.
# Set to 3 for a graduate: with ~3 months professional experience, anything
# asking 3+ years is out of reach, and letting them through wastes API calls
# and clutters the ranking. Raise it if you start seeing good roles filtered.
MAX_YEARS_EXPERIENCE = 3
# Skip postings older than this many days. Stale ads are often filled or
# abandoned. Set to None to disable. Jobs with no date are always kept.
MAX_POSTING_AGE_DAYS = 45

# --- Persistence ---
# Env-overridable: Lambda can only write to /tmp, and the CWD-relative default
# silently yields an empty list if the server is started from another folder.
CACHE_PATH = os.getenv("CACHE_PATH", ".cache/scored_jobs.json")
# Rewriting the whole JSON file per job is O(n); batch a few writes. A crash
# loses at most this many scores. DynamoDB will make this per-job.
CACHE_FLUSH_EVERY = int(os.getenv("CACHE_FLUSH_EVERY", "5"))

# --- Storage backend -------------------------------------------------------
# "json" for local files, "dynamodb" once the CDK stack is deployed. The
# pipeline is identical either way; only this line changes.
STORE_BACKEND = os.getenv("STORE_BACKEND", "json")
STORE_TABLE_NAME = os.getenv("STORE_TABLE_NAME", "job-ad-agent-scored-jobs")
AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-2")

# --- Concurrency and run budget ---
# Replaces the old REQUEST_INTERVAL_SECONDS sleep. Overlapping LLM waits inside
# one process is both faster and (on Lambda, which bills wall-clock including
# idle I/O) cheaper than pacing serially. Lower this if you hit rate limits.
SCORING_CONCURRENCY = int(os.getenv("SCORING_CONCURRENCY", "5"))
# Hard cap on LLM calls per run, so one invocation is always bounded. Newest
# postings are scored first; the rest are picked up on the next run.
MAX_JOBS_PER_RUN = int(os.getenv("MAX_JOBS_PER_RUN", "150"))
# Stop STARTING new scoring work after this many seconds. Locally this is a
# generous ceiling; on Lambda it is derived from the remaining invocation time
# so the run always exits cleanly instead of being SIGKILLed mid-flight.
RUN_BUDGET_SECONDS = float(os.getenv("RUN_BUDGET_SECONDS", "3600"))

# --- Display ---
TOP_N = 25


# --- ATS job boards (full, untruncated descriptions) ---
# Adzuna truncates descriptions at 500 chars; Greenhouse and Lever return the
# complete posting, so the experience/citizenship filters can actually read the
# requirements. Configure boards in companies.yaml.
COMPANIES_PATH = os.getenv("COMPANIES_PATH", "companies.yaml")
ENABLE_ADZUNA = True
ENABLE_ATS = True
MAX_RESULTS_PER_BOARD = 50

# ATS boards return every open role at a company WORLDWIDE, so roles must be
# filtered to places you can actually work. Substring match, case-insensitive.
# Unknown locations are kept (the LLM is the backstop); a role named for
# another country is dropped.
ALLOWED_LOCATIONS = [
    "australia", "melbourne", "sydney", "brisbane", "perth", "adelaide",
    "canberra", "hobart", "victoria", "nsw", "qld", "apac",
]


def load_companies() -> dict:
    """Board config from companies.yaml. Returns empty lists if absent."""
    import os

    import yaml

    if not os.path.exists(COMPANIES_PATH):
        return {"greenhouse": [], "lever": [], "ashby": [], "title_keywords": []}
    with open(COMPANIES_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    clean = lambda key: [x for x in (cfg.get(key) or []) if isinstance(x, str)]
    return {
        "greenhouse": clean("greenhouse"),
        "lever": clean("lever"),
        "ashby": clean("ashby"),
        "title_keywords": clean("title_keywords"),
    }