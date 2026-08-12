# job-ad-agent

An AI job-matching pipeline that fetches Australian tech roles, scores each one
against my profile with an LLM, flags automatic blockers (roles requiring
citizenship or a security clearance), and ranks the best fits in a local web
viewer. Built as both a genuinely useful daily tool for my own job hunt and a
portfolio project demonstrating backend, cloud, and LLM-integration skills.

## How it works

```
config.py ──► sources/adzuna.py ──► unique JobPosting objects
                                          │
                              filters.py (pre-filters: senior titles,
                                          high experience bars)
                                          │
              profile.yaml ──► scoring.py (LiteLLM + Instructor,
                                          retry with backoff, paced calls)
                                          │
                              validated ScoreResult per job
                                          │
                              cache.py (.cache/scored_jobs.json,
                                        only new jobs ever scored)
                                          │
                    main.py: rank + terminal table
                    server.py + static/: browsable web viewer
```

## Design decisions

- **API-first ingestion.** Sources are official HTTP APIs (Adzuna first), not
  scrapers, so the pipeline is stable, legal, and safe to run unattended.
- **Pluggable sources.** Every provider implements one `JobSource.fetch`
  method (`sources/base.py`), so adding another job board is a new file, not a
  rewrite.
- **Provider-agnostic LLM.** LiteLLM means any model (free or paid, OpenRouter,
  Gemini, DeepSeek, OpenAI) is a one-line config change, which made it easy to
  compare free-tier models during development.
- **Validated structured output.** Instructor forces the LLM to return a
  Pydantic `ScoreResult` (fit score, met / partial / missing requirements,
  hard blockers) and retries on malformed JSON, so weaker free models cannot
  break the pipeline.
- **Honest scoring by design.** The model is prompted to act as a blunt
  recruiter. It returns a 0-100 fit score and a requirements gap breakdown,
  never a fabricated "chance of getting an interview", because an LLM cannot
  calibrate that.
- **Resilient to flaky free tiers.** Transient errors (rate limits, overloaded
  endpoints) retry with exponential backoff; calls are paced to stay under
  free-tier rate limits; failed jobs stay uncached so they are retried next
  run instead of silently lost. Billing errors are deliberately not retried.
- **Cost-aware.** Cross-run caching plus deterministic pre-filters mean the
  LLM only ever scores genuinely new, plausible jobs. In steady state a daily
  run costs a few cents at most, often nothing on a free tier.
- **Separated frontend and backend.** FastAPI serves a JSON API
  (`/api/jobs`); a static HTML/CSS/JS frontend renders the ranked table.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env                    # add your keys (see below)
cp profile.example.yaml profile.yaml    # fill in your own profile
```

Free keys:

- Adzuna: https://developer.adzuna.com/
- LLM, one of:
  - Gemini free tier (recommended): https://aistudio.google.com/apikey then
    set `SCORING_MODEL=gemini/gemini-2.5-flash-lite`
  - OpenRouter: https://openrouter.ai/keys then set a model like
    `SCORING_MODEL=openrouter/meta-llama/llama-3.3-70b-instruct:free`
    (note: 0-credit accounts are limited to ~50 free requests/day)
  - Currently I am using deepseek-v4-flash via OpenRouter (not free) as 
  it is efficient and good for reasoning (and cheap).

## Usage

Score jobs (fetch, pre-filter, score new ones, rank):

```bash
python main.py
```

Browse results in the web viewer:

```bash
uvicorn server:app --reload
# open http://localhost:8000
```

Rescoring listed job ads if profile gets updated.

```bash
python rescore.py --all
```

Update cahced job postings and drop the ones that's older than 45 days.

```bash
python rescore.py --all --stale-days 45 --dry-run
```

Useful knobs in `config.py`:

- `SEARCHES` – the queries and locations to pull from Adzuna
- `MAX_RESULTS_PER_SEARCH` – volume dial (keep low while testing)
- `EXCLUDE_TITLE_KEYWORDS`, `MAX_YEARS_EXPERIENCE` – pre-filters
- `REQUEST_INTERVAL_SECONDS` – pacing between LLM calls

## Privacy

`profile.yaml`, `.env`, and `.cache/` are gitignored. The committed
`profile.example.yaml` contains only resume-level information.

## Roadmap

- [x] Phase 1: fetch, pre-filter, score, cache, rank (local)
- [x] Web viewer with requirement breakdown per job
- [ ] Phase 2: daily HTML email digest of new high-fit jobs
- [ ] Phase 3: deploy to AWS (EventBridge + Lambda + DynamoDB + SES) via CDK
- [ ] Phase 4: on-demand tailored cover letter and resume bullet generator
- [ ] Phase 5: tests, architecture diagram, model comparison write-up,
      walkthrough video