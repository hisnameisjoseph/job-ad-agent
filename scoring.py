"""Score one job against the candidate profile using an LLM.

LiteLLM gives us a single call that works across OpenAI, Gemini, DeepSeek,
OpenRouter, etc. Instructor forces the model to return a valid ScoreResult and
retries if the JSON is malformed. On top of that, we retry with backoff on
TRANSIENT errors so a job is never dropped just because a provider was
momentarily jammed.

Async: scoring is almost entirely spent waiting on the network, so calls are
issued concurrently (see main.py) rather than paced with sleeps. This module
must never print — it runs in Lambda, where stdout is CloudWatch.
"""

from __future__ import annotations

import asyncio
import logging

import instructor
import litellm
from litellm import acompletion

from models import JobPosting, ScoreResult

log = logging.getLogger(__name__)

_client = instructor.from_litellm(acompletion)


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
        # Counted first: a provider that omits usage entirely still made a
        # request, and reporting "No LLM requests made." after a successful
        # run is worse than reporting no token data.
        CACHE_STATS["requests"] += 1

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


# Retry only on genuinely transient failures, identified by EXCEPTION TYPE.
# The previous version substring-matched the stringified error for "500", which
# also matched permanent errors whose message merely contained a token count
# ("you requested 17500 tokens"), burning 35s of backoff on a doomed call.
# Billing/quota errors are absent on purpose: retrying a depleted key is futile.
_TRANSIENT_EXCEPTIONS = tuple(
    exc
    for exc in (
        getattr(litellm, name, None)
        for name in (
            "RateLimitError",
            "ServiceUnavailableError",
            "InternalServerError",
            "Timeout",
            "APIConnectionError",
        )
    )
    if isinstance(exc, type) and issubclass(exc, BaseException)
) or (TimeoutError,)


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
     section carefully. Count only paid professional work, NOT study, academic
     projects, or coursework.
  2. Compare it against the candidate's actual professional experience, which
     is roughly 3 months of software internship plus non-technical part-time
     work. Academic and honours projects do NOT count toward this total.
  3. If the posting's stated minimum exceeds the candidate's actual years, that
     IS a hard blocker. Add it to hard_blockers verbatim, e.g.
     "requires 5+ years experience, candidate has ~3 months".
  Do not soften a stated numeric requirement into a preference. Phrases like
  "likely expects mid-level experience" are wrong when the posting states a
  number: report the number.
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
{truncation_note}Description:
{description}
"""

_TRUNCATION_NOTE = (
    "NOTE: the description below is a TRUNCATED SUMMARY (~500 characters), not "
    "the full posting. The requirements section is probably missing. Do NOT "
    "infer hard blockers you cannot actually see: if citizenship or a years-of-"
    "experience bar is not stated in the text below, do not assert one. Set "
    "insufficient_information to true and score on what is visible.\n"
)


async def score_job(
    profile_text: str,
    job: JobPosting,
    model: str,
    max_transient_retries: int = 3,
    retry_base_delay: float = 5.0,
) -> ScoreResult:
    """Score one posting. Raises if it cannot be scored after retries."""
    user = USER_TEMPLATE.format(
        profile=profile_text,
        title=job.title,
        company=job.company or "Unknown",
        location=job.location or "Unknown",
        truncation_note=_TRUNCATION_NOTE if job.description_truncated else "",
        description=job.description[:12000],  # full ATS postings need headroom
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]

    attempt = 0
    while True:
        try:
            result, raw = await _client.chat.completions.create_with_completion(
                model=model,
                response_model=ScoreResult,
                max_retries=2,  # Instructor: retries on malformed JSON
                messages=messages,
            )
            _record_usage(raw)
            return result
        except _TRANSIENT_EXCEPTIONS as err:
            if attempt >= max_transient_retries:
                raise
            delay = retry_base_delay * (2**attempt)  # 5s, 10s, 20s
            log.warning(
                "Transient %s scoring %r, retrying in %.0fs",
                type(err).__name__, job.title[:50], delay,
            )
            await asyncio.sleep(delay)  # async: does not block other jobs
            attempt += 1