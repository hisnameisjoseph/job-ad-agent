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


# --- Cache telemetry -------------------------------------------------------
# DeepSeek (and some other providers) cache repeated prompt PREFIXES server
# side and bill them at a large discount. It is applied silently, so the only
# way to know it is working is to read the usage object. We accumulate totals
# here and print them at the end of a run.
CACHE_STATS = {"prompt_tokens": 0, "cached_tokens": 0, "requests": 0}


def _record_usage(raw) -> None:
    """Pull cached-token counts out of a raw completion, defensively.

    Providers disagree on field names and OpenRouter does not always pass them
    through, so every lookup is optional and failure is silent.
    """
    try:
        usage = getattr(raw, "usage", None)
        if usage is None:
            return
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0

        # OpenAI-style (LiteLLM normalises to this): prompt_tokens_details
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0

        # DeepSeek native field, if it survives the OpenRouter hop
        if not cached:
            cached = getattr(usage, "prompt_cache_hit_tokens", 0) or 0

        CACHE_STATS["prompt_tokens"] += prompt_tokens
        CACHE_STATS["cached_tokens"] += cached
        CACHE_STATS["requests"] += 1
    except Exception:
        pass  # telemetry must never break scoring


def cache_summary() -> str:
    total = CACHE_STATS["prompt_tokens"]
    cached = CACHE_STATS["cached_tokens"]
    if CACHE_STATS["requests"] == 0:
        return "No LLM requests made."
    if total == 0:
        return "Token usage not reported by this provider."
    if cached == 0:
        return (
            f"Prompt cache: 0 hits across {total:,} prompt tokens "
            "(provider may not cache, or may not report it)."
        )
    pct = cached / total * 100
    return (
        f"Prompt cache: {cached:,} of {total:,} prompt tokens served from cache "
        f"({pct:.0f}%)."
    )


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
- YEARS OF EXPERIENCE, follow this exactly:
  1. Find any stated minimum years of professional experience in the posting
     (e.g. "5+ years", "minimum 3 years", "2-4 years"). Check the requirements
     section carefully.
  2. Compare it against the candidate's actual professional experience, which
     is roughly 3 months of software internship plus non-technical part-time
     work.
  Do not soften a stated numeric requirement into a preference. Phrases like
  "likely expects mid-level experience" are wrong when the posting states a
  number: report the number, unless the posting does not state one.
- HARD BLOCKERS also include anything else the candidate cannot satisfy, in
  particular Australian citizenship or a security clearance.
- If there is ANY hard blocker, recommendation must be "skip" and fit_score
  must be at most 30, regardless of how well the skills otherwise match.
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
            result, raw = _client.chat.completions.create_with_completion(
                model=model,
                response_model=ScoreResult,
                max_retries=2,  # Instructor: retries on malformed JSON
                messages=messages,
            )
            _record_usage(raw)
            return result
        except Exception as err:
            if _is_transient(err) and attempt < max_transient_retries:
                delay = retry_base_delay * (2 ** attempt)  # 5s, 10s, 20s
                time.sleep(delay)
                attempt += 1
                continue
            raise