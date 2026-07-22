# Job Matcher

An AI job-matching pipeline that fetches Australian software roles, scores each
one against my profile with an LLM, flags automatic blockers (like roles
requiring citizenship or a security clearance), and ranks the best fits. Built
to run as a daily job and, later, to draft tailored cover letters and resume
bullets.

This is Phase 1: a local script that fetches, scores, and ranks. Cloud
deployment and email delivery come in later phases.

## Why it is built this way

- **API-first ingestion.** Sources are proper HTTP APIs (Adzuna first), not
  scrapers, so the pipeline is stable, legal, and safe to run unattended.
- **Pluggable providers.** Every source implements one `JobSource.fetch`
  method, so adding JSearch, Jooble, or The Muse later is a new file, not a
  rewrite.
- **Provider-agnostic LLM.** LiteLLM plus Instructor means any model (free or
  paid) can be swapped in by changing one string, and output is validated
  against a Pydantic schema with automatic retries.
- **Honest scoring.** The model returns a fit score and a met / partial /
  missing requirements breakdown, not a fake probability of getting hired.

## Architecture (Phase 1)

```
config.py ──> sources/adzuna.py ──> [JobPosting]
                                        │
                     profile.yaml ──> scoring.py (LiteLLM + Instructor)
                                        │
                                   [ScoreResult]
                                        │
                            main.py: rank + print table
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env            # add your Adzuna + LLM keys
cp profile.example.yaml profile.yaml   # fill in your real profile

python main.py
```

Get free keys: Adzuna at https://developer.adzuna.com/ and an LLM key from
OpenRouter (https://openrouter.ai/) or the Gemini free tier
(https://aistudio.google.com/).

## Roadmap

- Phase 1: local fetch, score, rank (this).
- Phase 2: daily HTML email digest.
- Phase 3: deploy to AWS (EventBridge + Lambda + DynamoDB + SES) via CDK.
- Phase 4: on-demand tailored cover letter and resume bullets.
- Phase 5: architecture diagram, tests, walkthrough video.
