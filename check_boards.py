"""Check which configured job-board tokens actually work.

Board tokens change and companies switch ATS providers, so verify before
relying on them:

    python check_boards.py

Prints how many roles each board returns, or the error if it is dead. Also
tries to guess a token from a company name:

    python check_boards.py --guess canva
"""

from __future__ import annotations

import argparse

import httpx
import yaml

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER = "https://api.lever.co/v0/postings/{company}"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{board}"


def check_greenhouse(token: str) -> str:
    try:
        r = httpx.get(
            GREENHOUSE.format(token=token),
            params={"content": "true"},
            timeout=20,
            follow_redirects=True,
        )
        if r.status_code == 404:
            return "not found (wrong token, or not on Greenhouse)"
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
        if not jobs:
            return "OK but 0 open roles"
        sample = len(jobs[0].get("content") or "")
        return f"OK, {len(jobs)} roles (first description ~{sample} chars)"
    except Exception as e:
        return f"FAILED: {e}"


def check_lever(company: str) -> str:
    try:
        r = httpx.get(
            LEVER.format(company=company),
            params={"mode": "json"},
            timeout=20,
            follow_redirects=True,
        )
        if r.status_code == 404:
            return "not found (wrong slug, or not on Lever)"
        r.raise_for_status()
        jobs = r.json()
        if not jobs:
            return "OK but 0 open roles"
        sample = len(jobs[0].get("descriptionPlain") or "")
        return f"OK, {len(jobs)} roles (first description ~{sample} chars)"
    except Exception as e:
        return f"FAILED: {e}"


def check_ashby(board: str) -> str:
    try:
        r = httpx.get(ASHBY.format(board=board), timeout=20, follow_redirects=True)
        if r.status_code == 404:
            return "not found (wrong slug, or not on Ashby)"
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
        if not jobs:
            return "OK but 0 open roles"
        sample = len(jobs[0].get("descriptionPlain") or "")
        return f"OK, {len(jobs)} roles (first description ~{sample} chars)"
    except Exception as e:
        return f"FAILED: {e}"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--file", default="companies.yaml")
    p.add_argument("--guess", help="try this token against both providers")
    args = p.parse_args()

    if args.guess:
        slug = args.guess.strip().lower().replace(" ", "")
        print(f"greenhouse/{slug}: {check_greenhouse(slug)}")
        print(f"lever/{slug}:      {check_lever(slug)}")
        print(f"ashby/{slug}:      {check_ashby(slug)}")
        return

    try:
        cfg = yaml.safe_load(open(args.file, encoding="utf-8")) or {}
    except FileNotFoundError:
        print(f"{args.file} not found. Copy companies.example.yaml to it first.")
        return

    gh = [t for t in (cfg.get("greenhouse") or []) if isinstance(t, str)]
    lv = [c for c in (cfg.get("lever") or []) if isinstance(c, str)]

    if not gh and not lv and not (cfg.get("ashby") or []):
        print("No boards configured yet.")
        return

    print("Greenhouse:")
    for t in gh:
        print(f"  {t:<24} {check_greenhouse(t)}")
    print("Lever:")
    for c in lv:
        print(f"  {c:<24} {check_lever(c)}")
    ash = [b for b in (cfg.get("ashby") or []) if isinstance(b, str)]
    print("Ashby:")
    for b in ash:
        print(f"  {b:<24} {check_ashby(b)}")


if __name__ == "__main__":
    main()
