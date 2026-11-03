# job-ad-agent

An AI job-matching pipeline that fetches Australian tech roles, scores each one
against my profile with an LLM, flags automatic blockers (roles requiring
citizenship or a security clearance), and ranks the best fits. It runs itself
every morning on AWS and serves the results to a phone-friendly web app behind
Google sign-in.

Built as both a genuinely useful daily tool for my own job hunt and a portfolio
project demonstrating backend, cloud, frontend and LLM-integration skills.

## How it works

```
EventBridge Scheduler (7:00 am Melbourne, daily)
        │
        ▼
Scoring Lambda (container, ARM64) ── or ── python main.py on a laptop
        │
        ├── sources/ (adzuna, greenhouse, lever, ashby)
        │        └──► JobPosting objects, deduped by source#id
        │
        ├── filters.py         pre-filters: senior titles, high experience
        │                      bars, stale postings (before any LLM call)
        │
        ├── profile.yaml ──► scoring.py
        │                      LiteLLM + Instructor, concurrent,
        │                      retry with backoff, prompt caching
        │
        ▼
store.py   JsonStore  -> .cache/scored_jobs.json   (local)
           DynamoStore -> DynamoDB                  (deployed)
           written through as jobs are scored; each row records when it was
           scored and which profile, prompt and model produced it
        │
        ▼
server.py (FastAPI)  ── same app locally (uvicorn) and on Lambda (Mangum)
        │
        ▼
static/  the web viewer: Jobs, Settings, Profile
```

## Design decisions

- **API-first ingestion.** Sources are official HTTP APIs (Adzuna plus the
  public Greenhouse, Lever and Ashby job boards), not scrapers, so the pipeline
  is stable, legal and safe to run unattended. The ATS boards return full job
  descriptions; Adzuna's are truncated, and the scoring prompt is told so.
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
- **One interface, two backends.** `Store` (scores) and `ConfigStore`
  (profile and settings) each have a local and an AWS implementation behind
  one factory. The pipeline, the API and the tests never know which they are
  using; `STORE_BACKEND` and `CONFIG_BUCKET` decide.
- **Cost-aware.** Cross-run caching plus deterministic pre-filters mean the
  LLM only ever scores genuinely new, plausible jobs. In steady state a daily
  run costs a few cents at most, often nothing on a free tier.
- **A frontend with no build step.** Plain HTML, CSS and ES modules served
  from S3, so there is no bundler to maintain and nothing to compile. Text
  from job ads only ever enters the page through `textContent`, and a strict
  Content-Security-Policy backs that up. The list logic (filtering, sorting,
  "new since your last visit") lives in one pure module, `static/view.js`,
  with its own unit tests.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt

cp .env.example .env                    # add your keys (see below)
cp profile.example.yaml profile.yaml    # fill in your own profile
```

> **Using Anaconda?** Run `conda deactivate` before creating the venv. A venv
> built while conda's `(base)` is active can end up without `pip`
> (`No module named pip`). If that happens, delete `.venv` and recreate it.
> Running `conda config --set auto_activate_base false` stops it recurring.

Free keys:

- Adzuna: https://developer.adzuna.com/
- LLM, one of:
  - Gemini free tier: https://aistudio.google.com/apikey, then set
    `SCORING_MODEL=gemini/gemini-2.5-flash-lite`
  - OpenRouter: https://openrouter.ai/keys, then set a model such as
    `SCORING_MODEL=openrouter/meta-llama/llama-3.3-70b-instruct:free`
    (0-credit accounts are limited to ~50 free requests a day)
  - What I use: DeepSeek through OpenRouter. Not free, but cheap, good at
    reasoning, and its prompt caching suits this workload, where every request
    starts with the same profile.

## Tests

Three suites, each covering a different layer. Dev dependencies are kept out of
`requirements.txt` because the Dockerfile installs that file into the Lambda
image, and test tooling has no business in a deployed artefact.

```bash
python -m pip install -r requirements-dev.txt
pytest -q                               # pipeline, stores, settings, API contract
node --test tests/js/view.test.mjs      # frontend list logic; nothing to install
cd infra && npm install && npm test     # infrastructure assertions
```

| Suite | What it pins down |
|---|---|
| `test_filters.py`, `test_settings.py` | Pre-filters, settings validation, config document safety |
| `test_store.py` | Write-through persistence, key migration, `scored_at` stamping and DynamoDB backfill |
| `test_server.py` | Every field the web viewer reads from `/api/jobs`, and the settings and profile round trips |
| `test_presignup.py` | The sign-up allowlist fails closed |
| `tests/js/view.test.mjs` | Filter, sort, search, "new since your last visit", remembered view state |
| `infra/test/infra.test.ts` | Authorizer on every route, least-privilege IAM, retained data, security headers, API throttling |

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

Locally the viewer reads whichever store `STORE_BACKEND` names. Once the
pipeline runs on AWS your scores live in DynamoDB, so to see them on your
laptop, point the local server at the table:

```bash
export AWS_PROFILE=job-ad-agent
aws sso login
STORE_BACKEND=dynamodb uvicorn server:app --reload
```

Sign-in is off locally, so the page opens straight to your jobs.

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
pipeline falls back to the same values, so it is optional. It is gitignored.

**`config.py` — code, edited in your editor.** Secrets, the model, and the
operational limits that are not worth a UI:

- `SCORING_MODEL` – any LiteLLM model string
- `SCORING_CONCURRENCY` – how many LLM calls run at once (lower it if you hit
  rate limits)
- `MAX_JOBS_PER_RUN`, `RUN_BUDGET_SECONDS` – hard caps on one run
- `STORE_BACKEND` – `json` for local files, `dynamodb` once deployed

### Editing configuration once it is deployed

The Settings page edits `settings.yaml`, and its **Edit profile** row opens the
profile editor for `profile.yaml`. Locally those are the very same files in
this repo, so the web form and your editor are one source of truth. Deployed,
they are objects in the versioned config bucket that the scoring run downloads
each morning, and versioning is the undo.

To move documents between the two, use `config_sync.py`:

```bash
export CONFIG_BUCKET=job-ad-agent-config-<your-account-id>
python config_sync.py --diff    # what differs, changing nothing
python config_sync.py --pull    # S3 -> local, take what the site saved
python config_sync.py --push    # local -> S3, publish your edits
```

`--pull` and `--push` refuse to run when the other side has changes you have
not seen; pass `--force` when you mean to discard them.

### Stale scores and new scores

Every score records a fingerprint of the profile it was computed against. Edit
your profile and existing scores are **flagged**, not deleted or hidden — a
judgement made against last week's profile is still the best information
available until that posting is scored again. The viewer shows a grey **Old
profile** label on those rows.

Every score also records `scored_at`, the time it was first stored. It is kept
when a row is copied between stores, so a migration never makes old jobs look
new. The viewer compares it with your previous visit on that device to show a
blue **New** label.

Rows written before either field existed carry neither. They are treated as
unknown, never as stale or new, because "we cannot tell" must not render as a
claim.

## The web viewer

A Flighty-style design system: light by default with a matching dark mode that
follows the device, a floating Jobs | Settings tab bar, and colour used only
for meaning — green **Apply**, amber **Maybe**, red **Skip**, blue for actions
and **New**.

- **Jobs, on a phone.** Each row shows the fit score, role, company, location
  and the call, with the **New** and **Old profile** labels beside the call.
  Tap a row for a bottom sheet with the model's reasoning, any hard blocker,
  and the Meets / Partial / Missing breakdown.
- **Jobs, on a desktop.** The same rows gain the one-line reason, the posted
  date and a direct Open link; details open in a centred pop-up.
- **Filter, search and sort.** Filter by call or to new jobs from the bar at
  the top. The round button opens Search & sort: text search, sort by fit,
  posted date or when scored, and switches for "only new" and "hide
  old-profile scores". Choices are remembered per device.
- **Settings.** Your account, the Adzuna searches (tap to edit, or add one),
  and the filter thresholds, saved together with one button.
- **Profile.** A plain-text YAML editor. It works on a phone but is built for a
  desktop, because the profile is a long document.

"New since your last visit" is tracked per device, in the browser. Your laptop
and your phone each remember their own last visit.

## Privacy

`profile.yaml`, `settings.yaml`, `.env` and `.cache/` are gitignored. The
committed `profile.example.yaml` contains only resume-level information.
Deployed, the profile lives in a private, encrypted S3 bucket that only the
scoring function and the viewer API can read.

Sign-in tokens are kept in `sessionStorage`, which is cleared when the tab
closes, never in `localStorage`. The only thing the site keeps long-term in the
browser is a flag meaning "this device has signed in before", plus your filter
choices and last-visit time.

## Authentication and hosting

The viewer runs on the public internet, so it is gated by Google sign-in. No
password is ever created or stored: Google holds the credential, Cognito holds
a federated identity, and this application holds neither.

```
browser ──► CloudFront ──► S3                    the static viewer
        ──► Cognito managed login ──► Google     sign-in, no passwords stored
        ──► API Gateway (JWT authorizer, throttled)
                     ──► Lambda (FastAPI/Mangum) ──► DynamoDB   (read only)
                                                 └─► S3 config  (read + write)
```

### Adding it to your phone's home screen

The CloudFront domain never changes between deploys, so this is a one-time
setup. Open `SiteUrl` on your phone, then **Share → Add to Home Screen** (iOS)
or **⋮ → Add to Home screen** (Android). A manifest and icons ship with the
site, so it opens full-screen with its own icon and you never have to find the
link again.

Reopening the app signs you back in without a tap: a device that has signed in
before makes one silent redirect through Cognito, whose session cookie sends it
straight back. It tries once per session, so a failure shows the sign-in
screen instead of looping. Signing out stops it.

Design notes:

- **The allowlist is the security model.** "Sign in with Google" means every
  Google account on earth can reach the login page. A Cognito PreSignUp Lambda
  rejects any email not in an SSM parameter, so account creation is blocked
  outright rather than access being revoked afterwards. It fails closed: if the
  parameter is unreadable, empty, or SSM is down, nobody gets in.
- **Adding a person needs no deploy.** The allowlist lives in SSM, not in code.
- **Security headers on every page.** A CloudFront response headers policy
  sends a Content-Security-Policy with no inline script or style and network
  access limited to Cognito and API Gateway, plus HSTS, `nosniff`,
  frame-denial, `no-referrer` and a restrictive Permissions-Policy. The CSP
  names API Gateway by regional wildcard rather than exact URL, because the
  API's CORS rule already depends on the CloudFront domain and the reverse
  would close a CloudFormation dependency cycle.
- **The API is throttled.** 5 requests a second with bursts of 20 — far above
  one person's use, but a ceiling on the bill if the URL ever leaks.
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

**3. Deploy.** See [Deploying](#deploying) below.

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

Every AWS tool (the CLI, boto3 and CDK) picks the profile from `AWS_PROFILE`.
Set it once for every terminal:

```bash
echo 'export AWS_PROFILE=job-ad-agent' >> ~/.zshrc
```

Without it the CLI falls back to the `default` profile and `aws sso login`
fails with "Missing the following required SSO configuration values".

The fix for frequent re-logins is the session length, not the credential type.
This project's profile already uses the `[sso-session]` block, which is what
enables refresh tokens, so raising the session duration is all that is needed:
**IAM Identity Center → Settings → Authentication → Session duration**, up to
90 days. After that, one `aws sso login` carries you for months and the CLI
refreshes silently. If the account is managed by an organisation, that setting
may be locked.

### Local development is unchanged

`config.json` is written into the S3 bucket at deploy time and does not exist
locally. The frontend treats its absence as "no auth", so
`uvicorn server:app --reload` behaves exactly as it did before. The 404 for
`/config.json` in the browser console is that check, not a fault.

## Deploying

Deploy from the feature branch, check the live site, then merge. `cdk deploy`
builds from the files on disk, not from GitHub, so keeping `main` equal to
"deployed and working" means `main` is always a safe version to roll back to.

```bash
# 1. Tests pass, Docker is running (it builds the Lambda image), AWS is signed in
pytest -q && node --test tests/js/view.test.mjs
open -a Docker
export AWS_PROFILE=job-ad-agent && aws sso login

# 2. Preview. Stop if anything says "replacement" next to the DynamoDB table.
cd infra
npm install && npm test
npx cdk diff --all

# 3. Deploy both stacks. Approve the security prompt with y.
npx cdk deploy --all --parameters "WebStack:GoogleClientSecret=$(aws ssm get-parameter --name /job-ad-agent/auth/google-client-secret --with-decryption --query Parameter.Value --output text)"

# 4. Confirm the security headers are live
curl -sI https://<SiteUrl>/ | grep -i -E "content-security|strict-transport"
```

The Google client secret is passed as a `noEcho` stack parameter rather than
read from SSM by CloudFormation, because `{{resolve:ssm-secure:...}}` is not
supported on Cognito identity providers — it would store the literal
unresolved string and sign-in would fail silently. SSM stays the source of
truth; the deploy command reads it.

A deploy takes 10–20 minutes, mostly the Docker build and CloudFront applying
changes. CloudFront then clears its cache, so allow a few minutes before
checking. On a phone, swipe the home-screen app away and reopen it to load the
new version.

To roll back, check out `main` and run the same deploy command. The DynamoDB
table is retained, so a rollback never touches your scores.

**Never commit `infra/node_modules/` or `infra/cdk.out/`.** Both are generated,
both are gitignored, and `git add -f` overrides that. Stage with
`git add -A` (or file by file) and check `git status` before committing.

## Roadmap

- [x] Phase 1: fetch, pre-filter, score, cache, rank (local)
- [x] Web viewer with requirement breakdown per job
- [x] Phase 3: deploy to AWS via CDK — container Lambda on ARM64, DynamoDB
      with TTL, S3 config bucket, secrets in SSM, EventBridge Scheduler on a
      daily Melbourne-time cron
- [x] Authenticated hosting: the viewer on the internet behind Google
      sign-in (Cognito + API Gateway + Lambda + S3/CloudFront)
- [x] Settings and profile editable from the website
- [x] Redesigned viewer: Flighty-style design system, dark mode, filters,
      search and sort, "new since your last visit", stay signed in on reopen
- [x] Hardening: Content-Security-Policy and security headers, API throttling
- [ ] Reliability: retry on job-source fetches, CloudWatch log retention
- [ ] Restore the one-run-at-a-time cap on the scoring function
      (`reservedConcurrentExecutions: 1`, currently commented out in
      `infra/lib/infra-stack.ts`) once the account's Lambda concurrency quota
      is raised above 10
- [ ] Phase 2: daily HTML email digest of new high-fit jobs (SES)
- [ ] Phase 4: on-demand tailored cover letter and resume bullet generator
      *(in progress)*
- [ ] Phase 5: architecture diagram, model comparison write-up, walkthrough
      video
