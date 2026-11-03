"""Bootstrap runtime configuration from AWS for the Lambda entry point.

Two jobs, both of which MUST happen before `config` is imported:

  1. Pull API keys out of SSM Parameter Store into os.environ.
  2. Download profile.yaml / companies.yaml from S3 into /tmp.

The ordering matters because config.py reads os.environ at IMPORT time. If
config is imported first, it captures empty strings and every Adzuna call
fails with no obvious cause.

Nothing here runs locally: main.py reads .env and local files as before.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

SSM_PREFIX = os.getenv("SSM_PREFIX", "/job-ad-agent")


def _load_secrets_from_ssm(prefix: str = SSM_PREFIX) -> int:
    """Copy SSM parameters into os.environ.

    /job-ad-agent/adzuna-app-key  ->  ADZUNA_APP_KEY

    An existing env var always wins, so a local .env can override AWS during
    debugging without editing code.
    """
    import boto3

    ssm = boto3.client("ssm")
    loaded = 0
    paginator = ssm.get_paginator("get_parameters_by_path")

    for page in paginator.paginate(Path=prefix, WithDecryption=True):
        for param in page["Parameters"]:
            key = param["Name"].rsplit("/", 1)[-1].upper().replace("-", "_")
            if os.environ.get(key):
                continue
            os.environ[key] = param["Value"]
            loaded += 1

    log.info("Loaded %d parameters from SSM path %s", loaded, prefix)
    return loaded


def _download_config_from_s3(bucket: str) -> None:
    """Fetch profile.yaml and companies.yaml to the paths config.py will read.

    Destinations default under /tmp, Lambda's only writable directory, but are
    taken from PROFILE_PATH / COMPANIES_PATH so this and config.py can never
    disagree about where the files went.

    Fails loudly on a missing profile. Scoring every job against an absent
    profile would silently burn the whole run's API budget producing garbage.
    """
    import boto3

    s3 = boto3.client("s3")

    # Destinations come from the SAME env vars config.py resolves its paths
    # from, so there is one source of truth. Hardcoding /tmp here would mean
    # two places had to agree, and changing only the CDK env var would land the
    # file where nothing ever reads it -- a FileNotFoundError visible only on
    # Lambda, long after a green deploy.
    for key, env_var, default, required in (
        ("profile.yaml", "PROFILE_PATH", "/tmp/profile.yaml", True),
        ("companies.yaml", "COMPANIES_PATH", "/tmp/companies.yaml", False),
        # Optional by design: settings.py falls back to the values this project
        # shipped with, which is a working configuration rather than an empty
        # one. A missing document means "never edited", not "broken".
        ("settings.yaml", "SETTINGS_PATH", "/tmp/settings.yaml", False),
    ):
        dest = os.environ.get(env_var) or default
        try:
            s3.download_file(bucket, key, dest)
            log.info("Downloaded s3://%s/%s -> %s", bucket, key, dest)
        except Exception as e:
            if required:
                raise RuntimeError(
                    f"Could not download required s3://{bucket}/{key}: {e}"
                ) from None
            elif key == "companies.yaml":
                # ERROR, not WARNING. Without companies.yaml every ATS board is
                # silently skipped and the run scores only Adzuna's 500-char
                # truncated descriptions -- so the citizenship and experience
                # filters read incomplete requirements and quality collapses
                # with no other signal that anything is wrong.
                log.error(
                    "Optional config s3://%s/%s missing (%s). ALL ATS boards "
                    "(Greenhouse/Lever/Ashby) will be skipped and only Adzuna's "
                    "truncated descriptions will be scored.", bucket, key, e
                )
            else:
                log.info(
                    "s3://%s/%s not present (%s); using built-in defaults.",
                    bucket, key, e
                )


def bootstrap_environment() -> None:
    """Prepare os.environ. Call this BEFORE importing config."""
    _load_secrets_from_ssm()

    bucket = os.environ.get("CONFIG_BUCKET")
    if bucket:
        _download_config_from_s3(bucket)
    else:
        log.warning("CONFIG_BUCKET unset; using whatever paths config resolves to.")
