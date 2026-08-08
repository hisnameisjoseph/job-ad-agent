"""Discover which ATS each company uses, and write them into companies.yaml.

You should not have to know that a company uses Greenhouse. This finds out.

Two strategies, run in order per company:

1. SLUG PROBE (default, cheap). Turn "HUB24 Limited" into candidate slugs
   ("hub24", "hub24limited", ...) and ask each ATS's public API whether such a
   board exists. Only ever hits official API endpoints. Roughly a third of
   companies are found this way; the miss cases are companies whose board slug
   is unrelated to their trading name.

2. REDIRECT TRACE (--trace, more accurate). Adzuna's redirect_url resolves to
   the employer's real application page, which is frequently the ATS itself.
   Reading the final URL identifies both the provider and the exact token, so
   there is no guessing. Slower (one request per job) and it touches non-API
   URLs, so it is opt-in.

Usage:
    python discover_boards.py                 # probe companies from Adzuna
    python discover_boards.py --trace         # also follow redirect URLs
    python discover_boards.py --write         # save results to companies.yaml
"""

from __future__ import annotations

import argparse
import re
import time
from urllib.parse import urlparse

import httpx
import yaml

import config
from sources.adzuna import AdzunaSource

# --- Probe endpoints (all free and unauthenticated) ------------------------
PROBES = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}

# --- Recognising an ATS from a landing URL ---------------------------------
URL_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([^/?#]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([^/?#]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)", re.I)),
]

_SUFFIXES = re.compile(
    r"\b(pty|ltd|limited|inc|incorporated|llc|group|holdings|australia|"
    r"au|nz|corp|corporation|co|company|technologies|technology|solutions|"
    r"services|recruitment|consulting|the)\b",
    re.I,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def candidate_slugs(company: str) -> list[str]:
    """Plausible board slugs for a company name, most likely first."""
    if not company:
        return []
    base = company.lower().strip()
    stripped = _SUFFIXES.sub(" ", base)

    variants = []
    for text in (stripped, base):
        collapsed = _NON_ALNUM.sub("", text)          # "hub24limited"
        hyphenated = _NON_ALNUM.sub("-", text).strip("-")  # "hub24-limited"
        first = text.split()[0] if text.split() else ""
        for v in (collapsed, hyphenated, _NON_ALNUM.sub("", first)):
            if v and len(v) >= 2 and v not in variants:
                variants.append(v)
    return variants[:4]  # cap the probe budget per company


def probe(client: httpx.Client, provider: str, slug: str) -> int | None:
    """Number of open roles if this board exists, else None."""
    try:
        r = client.get(PROBES[provider].format(slug=slug), timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        jobs = data if isinstance(data, list) else data.get("jobs", [])
        return len(jobs) if isinstance(jobs, list) else None
    except Exception:
        return None


def trace_redirect(client: httpx.Client, url: str) -> tuple[str, str] | None:
    """Follow an Adzuna redirect and identify the ATS from where it lands."""
    if not url:
        return None
    try:
        r = client.get(url, timeout=20, follow_redirects=True)
        final = str(r.url)
    except Exception:
        return None
    for provider, pattern in URL_PATTERNS:
        m = pattern.search(final)
        if m:
            return provider, m.group(1).lower()
    return None


def collect_companies() -> list[tuple[str, str]]:
    """(company, sample_url) pairs from the configured Adzuna searches."""
    src = AdzunaSource(
        config.ADZUNA_APP_ID,
        config.ADZUNA_APP_KEY,
        config.ADZUNA_COUNTRY,
        max_days_old=config.ADZUNA_MAX_DAYS_OLD,
    )
    seen: dict[str, str] = {}
    for s in config.SEARCHES:
        try:
            for job in src.fetch(s["query"], s["location"], config.MAX_RESULTS_PER_SEARCH):
                if job.company and job.company not in seen:
                    seen[job.company] = job.url
        except Exception as e:
            print(f"  Adzuna search failed for '{s['query']}': {e}")
    return sorted(seen.items())


def merge_into_yaml(path: str, found: dict[str, set[str]]) -> None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        cfg = {}

    for provider, slugs in found.items():
        existing = [s for s in (cfg.get(provider) or []) if isinstance(s, str)]
        cfg[provider] = sorted(set(existing) | slugs)

    cfg.setdefault("title_keywords", ["engineer", "developer", "data", "cloud",
                                      "graduate", "junior", "support", "analyst"])
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--trace", action="store_true",
                   help="also follow Adzuna redirect URLs (slower, more accurate)")
    p.add_argument("--write", action="store_true",
                   help="merge discoveries into companies.yaml")
    p.add_argument("--delay", type=float, default=0.3,
                   help="seconds between requests (be polite)")
    args = p.parse_args()

    if not config.ADZUNA_APP_ID or not config.ADZUNA_APP_KEY:
        print("Missing Adzuna credentials in .env")
        return

    print("Collecting companies from Adzuna searches...")
    companies = collect_companies()
    print(f"Found {len(companies)} distinct companies.\n")

    found: dict[str, set[str]] = {"greenhouse": set(), "lever": set(), "ashby": set()}
    headers = {"User-Agent": "job-ad-agent/1.0 (personal job search tool)"}

    with httpx.Client(headers=headers, follow_redirects=True) as client:
        for company, url in companies:
            hit = None

            for slug in candidate_slugs(company):
                for provider in PROBES:
                    n = probe(client, provider, slug)
                    time.sleep(args.delay)
                    if n is not None:
                        hit = (provider, slug, n, "slug")
                        break
                if hit:
                    break

            if not hit and args.trace:
                traced = trace_redirect(client, url)
                time.sleep(args.delay)
                if traced:
                    provider, slug = traced
                    n = probe(client, provider, slug)
                    hit = (provider, slug, n if n is not None else 0, "trace")

            if hit:
                provider, slug, n, how = hit
                found[provider].add(slug)
                print(f"  FOUND  {company[:34]:<34} -> {provider}/{slug} "
                      f"({n} roles, via {how})")
            else:
                print(f"  ---    {company[:34]:<34}")

    total = sum(len(v) for v in found.values())
    print(f"\nDiscovered {total} board(s):")
    for provider, slugs in found.items():
        if slugs:
            print(f"  {provider}: {', '.join(sorted(slugs))}")

    if not total:
        print("\nNothing found. Try --trace, which is much more accurate.")
        return

    if args.write:
        merge_into_yaml(config.COMPANIES_PATH, found)
        print(f"\nMerged into {config.COMPANIES_PATH}. Run `python main.py` next.")
    else:
        print("\nRe-run with --write to save these to companies.yaml.")


if __name__ == "__main__":
    main()
