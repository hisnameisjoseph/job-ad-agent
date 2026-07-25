"""Score one job against the candidate profile using an LLM.

LiteLLM gives us a single call that works across OpenAI, Gemini, DeepSeek,
OpenRouter, etc. Instructor forces the model to return a valid ScoreResult and
retries if the JSON is malformed. On top of that, we retry with backoff on
TRANSIENT errors (rate limits, 'resource exhausted', server overload) so a job
is never dropped just because a free model was momentarily jammed.
"""

from __future__ import annotations

import time

import instructor
from litellm import completion

from models import JobPosting, ScoreResult

_client = instructor.from_litellm(completion)


# Substrings that mark an error as temporary and worth retrying. Note that
# billing errors like 'Key limit exceeded' are deliberately NOT here, because
# retrying a depleted key is pointless.
_TRANSIENT_MARKERS = (
    "rate limit",
    "ratelimit",
    "resource exhausted",
    "resourceexhausted",
    "limit reached",
    "overloaded",
    "unavailable",
    "timeout",
    "timed out",
    "try again",
    "429",
    "500",
    "502",
    "503",
    "504",
    "internal server",
)


def _is_transient(err: Exception) -> bool:
    text = f"{type(err).__name__} {err}".lower()
    return any(m in text for m in _TRANSIENT_MARKERS)


SYSTEM_PROMPT = """\
You are a blunt, experienced technical recruiter helping ONE specific candidate
decide which jobs are worth applying to. You are honest, not encouraging: an
inflated score wastes the candidate's time.

Rules:
- The candidate is open to ANY tech role (software, cloud, data, web, AI/LLM).
  Score on genuine fit between their skills/experience and THIS role's stated
  requirements. Do not penalise a role just for being a different subfield if
  the required skills clearly transfer; do weigh the required years/seniority.
- Judge fit ONLY against the candidate profile provided. Do not invent skills.
- fit_score is 0 to 100. Reserve 80+ for strong matches where the candidate
  clearly meets most core requirements.
- Do NOT estimate probability of getting an interview. Give a fit score and an
  honest breakdown of met / partial / missing requirements instead.
- HARD BLOCKERS: if the job requires something the candidate cannot satisfy
  (for this candidate: Australian citizenship, a security clearance, or senior
  years of experience they lack), list it in hard_blockers. If there is any
  hard blocker, the recommendation must be "skip" regardless of skill fit.
- "partial" is for transferable or adjacent experience, not wishful thinking.
- Keep one_line to a single honest sentence.
"""

USER_TEMPLATE = """\
CANDIDATE PROFILE:
{profile}

JOB POSTING:
Title: {title}
Company: {company}
Location: {location}
Description:
{description}
"""


def score_job(
    profile_text: str,
    job: JobPosting,
    model: str,
    max_transient_retries: int = 3,
    retry_base_delay: float = 5.0,
) -> ScoreResult:
    user = USER_TEMPLATE.format(
        profile=profile_text,
        title=job.title,
        company=job.company or "Unknown",
        location=job.location or "Unknown",
        description=job.description[:6000],  # keep tokens (and cost) small
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]

    attempt = 0
    while True:
        try:
            return _client.chat.completions.create(
                model=model,
                response_model=ScoreResult,
                max_retries=2,  # Instructor: retries on malformed JSON
                messages=messages,
            )
        except Exception as err:
            if _is_transient(err) and attempt < max_transient_retries:
                delay = retry_base_delay * (2 ** attempt)  # 5s, 10s, 20s
                time.sleep(delay)
                attempt += 1
                continue
            raise