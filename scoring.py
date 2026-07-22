"""Score one job against the candidate profile using an LLM.

LiteLLM gives us a single call that works across OpenAI, Gemini, DeepSeek,
OpenRouter, Groq, etc. Instructor forces the model to return a valid
ScoreResult and retries automatically if a (possibly free/weaker) model
returns malformed JSON.
"""

from __future__ import annotations

import instructor
from litellm import completion

from models import JobPosting, ScoreResult

# Wrap LiteLLM's completion with Instructor so we can pass response_model.
_client = instructor.from_litellm(completion)


SYSTEM_PROMPT = """\
You are a blunt, experienced technical recruiter helping ONE specific candidate
decide which jobs are worth applying to. You are honest, not encouraging: an
inflated score wastes the candidate's time.

Rules:
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


def score_job(profile_text: str, job: JobPosting, model: str) -> ScoreResult:
    user = USER_TEMPLATE.format(
        profile=profile_text,
        title=job.title,
        company=job.company or "Unknown",
        location=job.location or "Unknown",
        description=job.description[:6000],  # keep tokens (and cost) small
    )
    result: ScoreResult = _client.chat.completions.create(
        model=model,
        response_model=ScoreResult,
        max_retries=2,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
    )
    return result
