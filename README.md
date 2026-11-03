# job-ad-agent

An AI job-matching pipeline that fetches Australian tech roles, scores each one
against my profile with an LLM, flags automatic blockers (roles requiring
citizenship or a security clearance), and ranks the best fits in a local web
viewer. Built as both a genuinely useful daily tool for my own job hunt and a
portfolio project demonstrating backend, cloud, and LLM-integration skills.

## How it works

```
config.py ──► sources/ (adzuna, greenhouse, lever, ashby)
                       └──► JobPosting objects, deduped by source#id
                                          │
                              filters.py (pre-filters: senior titles,
                                          high experience bars)
                                          │
              profile.yaml ──► scoring.py (LiteLLM + Instructor,
                                          concurrent, retry with backoff)
                                          │
                              validated ScoreResult per job
                                          │
                              store.py (JsonStore -> .cache/scored_jobs.json
                                        or DynamoStore -> DynamoDB;
                                        written through as jobs are scored,
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
  endpoints) retry with exponential backoff, identified by exception type
  rather than by substring-matching the message; failed jobs stay uncached so
  they are retried next run instead of silently lost. Billing errors are
  deliberately not retried.
- **Concurrent, and bounded.** Scoring is almost entirely network wait, so
  calls run concurrently under a semaphore (`SCORING_CONCURRENCY`) instead of
  being paced with sleeps. Every run is capped by `MAX_JOBS_PER_RUN` and a
  wall-clock deadline, and newest postings are scored first, so an
  invocation always ends cleanly and the rest are picked up next run.
- **Write-through persistence.** Scores are written to the store as they
  arrive, not saved once at the end, so an interrupt — Ctrl-C, OOM, or a
  Lambda timeout, which is a SIGKILL with no cleanup — costs at most a few
  jobs rather than the whole paid run.
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

To run the tests as well, add the dev dependencies. They are kept in a separate
file because the Dockerfile installs `requirements.txt` into the Lambda image,
and the test framework has no business in a deployed artefact:

```bash
pip install -r requirements-dev.txt
pytest -q

# Frontend list logic (filter, sort, "new since last visit"). Node's built-in
# test runner, nothing to install:
node --test tests/js/view.test.mjs

# Infrastructure assertions (security headers, throttling, IAM scope):
cd infra && npm test
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

Rescore listed job ads after updating `profile.yaml` or the scoring prompt:

```bash
python rescore.py --all
```

Drop cached postings older than 45 days (`--dry-run` reports without writing):

```bash
python rescore.py --all --stale-days 45 --dry-run
```

`rescore.py` goes through the same store the pipeline uses, so it acts on the
local JSON cache or on DynamoDB according to `STORE_BACKEND`.

### What you can change, and where

Two kinds of configuration, split by who edits them.

**`settings.yaml` — edited from the website or your editor.** Searches and
pre-filter thresholds. Changing these needs no rebuild and no redeploy.

| | |
|---|---|
| `searches` | the query + location pairs pulled from Adzuna |
| `max_results_per_search` | volume dial; every result is a candidate LLM call |
| `filters.exclude_title_keywords` | whole-word title matches to skip |
| `filters.max_years_experience` | drop postings asking this many years or more |
| `filters.max_posting_age_days` | drop stale ads; undated postings are kept |
| `filters.drop_hard_blockers` | hide roles needing citizenship or a clearance |
| `filters.top_n` | rows in the terminal table |

Copy `settings.example.yaml` to `settings.yaml` to start. Delete it and the
pipeline falls back to the same values, so it is optional.

**`config.py` — code, edited in your editor.** Secrets, the model, and the
operational limits that are not worth a UI:

- `SCORING_MODEL` – any LiteLLM model string
- `SCORING_CONCURRENCY` – how many LLM calls run at once (lower it if you hit
  rate limits)
- `MAX_JOBS_PER_RUN`, `RUN_BUDGET_SECONDS` – hard caps on one run
- `STORE_BACKEND` – `json` for local files, `dynamodb` once deployed

### Editing configuration once it is deployed

The settings page at `/settings.html` edits `settings.yaml` and `profile.yaml`
directly. Locally those are the very same files in this repo, so the web form
and your editor are one source of truth. Deployed, they are objects in the
versioned config bucket that the scoring run downloads each morning.

To move documents between the two, use `config_sync.py`:

```bash
export CONFIG_BUCKET=job-ad-agent-config-<your-account-id>
python config_sync.py --diff    # what differs, changing nothing
python config_sync.py --pull    # S3 -> local, take what the site saved
python config_sync.py --push    # local -> S3, publish your edits
```

`--pull` and `--push` refuse to run when the other side has changes you have
not seen; pass `--force` when you mean to discard them.

### Stale scores

Every score records a fingerprint of the profile it was computed against. Edit
your profile and existing scores are **flagged**, not deleted or hidden — a
judgement made against last week's profile is still the best information
available until that posting is scored again. The viewer shows an "old
profile" badge on those rows.

Scores written before this existed carry no fingerprint. They are treated as
unknown and never flagged, because "we cannot tell" must not render as "this
is stale".

## Privacy

`profile.yaml`, `.env`, and `.cache/` are gitignored. The committed
`profile.example.yaml` contains only resume-level information.


## Authentication and hosting

The viewer runs on the public internet, so it is gated by Google sign-in. No
password is ever created or stored: Google holds the credential, Cognito holds
a federated identity, and this application holds neither.

```
browser ──► CloudFront ──► S3                    the static viewer
        ──► Cognito managed login ──► Google     sign-in, no passwords stored
        ──► API Gateway (JWT authorizer)
                     ──► Lambda (FastAPI/Mangum) ──► DynamoDB   (read only)
                                                 └─► S3 config  (read + write)
```

### Adding it to your phone's home screen

The CloudFront domain never changes between deploys, so this is a one-time
setup. Open `SiteUrl` on your phone, then **Share → Add to Home Screen** (iOS)
or **⋮ → Add to Home screen** (Android). A manifest and icons ship with the
site, so it opens full-screen with its own icon and you never have to find the
link again.

The interface follows a Flighty-style design system: light by default with a
matching dark mode, a floating Jobs | Settings tab bar, and colour used only
for meaning (green Apply, amber Maybe, red Skip, blue for actions and New).

- **Phone:** each row shows the fit score, role, company, location and the
  call, plus a blue **New** label for jobs scored since your last visit on that
  device and a grey **Old profile** label for scores made before your last
  profile edit. Tap a row for a bottom sheet with the reasoning and the
  Meets / Partial / Missing breakdown.
- **Desktop:** the same rows gain the one-line reason, the posted date and a
  direct Open link; details open in a centred pop-up.
- **Search & sort** (the round button): text search, sort by fit, posted date
  or when scored, and switches for "only new" and "hide old-profile scores".
  Your choices are remembered per device.
- **Staying signed in:** reopening the home-screen app signs you back in
  automatically (one silent redirect through Cognito). Signing out stops this.
- The profile editor works on a phone but is built for a desktop, because it is
  a YAML document.

Design notes:

- **The allowlist is the security model.** "Sign in with Google" means every
  Google account on earth can reach the login page. A Cognito PreSignUp Lambda
  rejects any email not in an SSM parameter, so account creation is blocked
  outright rather than access being revoked afterwards. It fails closed: if the
  parameter is unreadable, empty, or SSM is down, nobody gets in.
- **Adding a person needs no deploy.** The allowlist lives in SSM, not in code.
- **The API is a second entry point on the same container image.** `cmd` is
  applied to the Lambda image config rather than baked in, so the scoring
  function and the API function share one build and one ECR image.
- **The API Lambda cannot alter a score.** It holds DynamoDB read access only,
  holds no API keys, and skips the SSM/S3 bootstrap the scoring function needs.
  The settings page means it *does* hold `s3:PutObject` on the config bucket —
  granted as `grantRead` + `grantPut` rather than `grantReadWrite`, because the
  latter silently adds `s3:DeleteObject` and nothing here deletes a document.
  Bucket versioning is the undo. A CDK test asserts both halves of this.
- **Every write is behind the same authorizer as the reads.** The `PUT` routes
  for settings and profile use the identical Cognito JWT authorizer; there is
  no unauthenticated write path, and a test asserts no route lacks one.
- **Cognito is pinned to the Essentials tier**, which is what managed login
  requires and carries a perpetual 10,000 monthly-active-user free tier. Social
  sign-ins draw from that same allowance. The Plus tier has no free tier.
- **Separate origins, not one.** The API is not behind CloudFront because the
  user pool client needs CloudFront's domain, the authorizer needs that
  client's id, and CloudFront would need the API — a genuine CloudFormation
  dependency cycle. Splitting them costs one CORS rule.

### One-time setup

**1. Create the Google OAuth client.** In the
[Google Cloud console](https://console.cloud.google.com/apis/credentials):

- Configure the OAuth consent screen (External). While it is in *Testing*
  mode, only accounts added as test users can sign in — so you would be
  maintaining two lists. Publishing the app avoids that.
- Credentials → Create credentials → OAuth client ID → **Web application**.
- Leave the redirect URI blank for now; it does not exist until step 3.
- Copy the client ID and client secret.

**2. Store the credentials and the allowlist in SSM.**

```bash
export AWS_PROFILE=job-ad-agent
aws ssm put-parameter --name /job-ad-agent/auth/google-client-id \
  --type String --value "YOUR_CLIENT_ID"
aws ssm put-parameter --name /job-ad-agent/auth/google-client-secret \
  --type SecureString --value "YOUR_CLIENT_SECRET"
aws ssm put-parameter --name /job-ad-agent/auth/allowed-emails \
  --type SecureString --value "you@gmail.com"
```

The allowlist is deliberately a separate path from the API keys: `aws_config.py`
reads `/job-ad-agent` non-recursively, so nothing under `auth/` leaks into the
scoring function's environment.

**3. Deploy.** The Google client secret is passed as a `noEcho` stack
parameter rather than read from SSM by CloudFormation, because
`{{resolve:ssm-secure:...}}` is not supported on Cognito identity providers —
it would store the literal unresolved string and sign-in would fail silently.
SSM stays the source of truth; the deploy command reads it:

```bash
cd infra
npx cdk deploy --all --parameters "WebStack:GoogleClientSecret=$(aws ssm get-parameter --name /job-ad-agent/auth/google-client-secret --with-decryption --query Parameter.Value --output text)"
```

**4. Close the loop with Google.** The deploy prints `GoogleRedirectUri` and
`GoogleJavaScriptOrigin`. Paste them into the same Google credential, under
*Authorised redirect URIs* and *Authorised JavaScript origins*. Sign-in fails
with `redirect_uri_mismatch` until this is done.

**5. Open the `SiteUrl` from the outputs** and sign in.

### Adding someone later

```bash
aws ssm put-parameter --name /job-ad-agent/auth/allowed-emails --overwrite \
  --type SecureString --value "you@gmail.com,friend@gmail.com"
```

No redeploy. If the Google consent screen is still in Testing mode, add them as
a Google test user too.

Note that everyone on the allowlist sees the *same* job list, scored against
the one `profile.yaml` in S3. Per-person profiles and per-person scores are a
separate piece of work: scores are a function of (person, job), so that means a
user dimension in the DynamoDB key and a scoring run per person.

### Staying signed in to AWS

SSO credentials cannot go in `.env` — SSO issues short-lived tokens, so there
is nothing static to store. Long-lived IAM access keys would work and are a
deliberate downgrade; don't.

The fix is the session length, not the credential type. This project's profile
already uses the `[sso-session]` block, which is what enables refresh tokens,
so raising the session duration is all that is needed:

**IAM Identity Center → Settings → Authentication → Session duration**, up to
90 days. After that, one `aws sso login --profile job-ad-agent` carries you for
months and the CLI refreshes silently.

If the account is managed by an organisation, that setting may be locked and
you are stuck with whatever duration the org allows.

### Local development is unchanged

`config.json` is written into the S3 bucket at deploy time and does not exist
locally. The frontend treats its absence as "no auth", so
`uvicorn server:app --reload` behaves exactly as it did before. The 404 for
`/config.json` in the browser console is that check, not a fault.

## Roadmap

- [x] Phase 1: fetch, pre-filter, score, cache, rank (local)
- [x] Web viewer with requirement breakdown per job
- [ ] Phase 2: daily HTML email digest of new high-fit jobs (SES)
- [x] Phase 3: deploy to AWS via CDK — container Lambda on ARM64, DynamoDB
      with TTL, S3 config bucket, secrets in SSM, EventBridge Scheduler on a
      daily Melbourne-time cron. *SES is not part of this; it belongs with the
      Phase 2 digest.*
- [x] Authenticated hosting: the viewer on the internet behind Google
      sign-in (Cognito + API Gateway + Lambda + S3/CloudFront) — see
      "Authentication and hosting" above
- [ ] Phase 4: on-demand tailored cover letter and resume bullet generator
      *(in progress)*
- [ ] Phase 5: tests, architecture diagram, model comparison write-up,
      walkthrough video

Phases 3 and 4 currently live on the `feat/cognito-auth-and-hosted-api`
branch, not on `main`.
