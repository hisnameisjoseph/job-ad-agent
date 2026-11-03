"""Cognito PreSignUp trigger: only allowlisted emails may create an account.

The viewer sits on the public internet behind "Sign in with Google". Without
this trigger that phrase means "anyone on earth may register", because every
Google account in existence can reach the login page. This is the gate.

The allowlist lives in SSM at /job-ad-agent/auth/allowed-emails, not in this
code, so adding a friend is a one-line parameter edit with no redeploy and no
new container image. That is the whole reason it is not a hardcoded list.

Deliberately FAILS CLOSED. A missing parameter, an unreachable SSM, or a
malformed value all deny the sign-up. An allowlist that fails open is not an
allowlist, and the failure mode of getting this backwards is "the internet has
an account on your app" rather than "you have to fix a typo".

boto3 ships in the Lambda Python runtime, so this file needs no bundling.
"""

from __future__ import annotations

import logging
import os

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

ALLOWLIST_PARAM = os.environ.get(
    "ALLOWLIST_PARAMETER", "/job-ad-agent/auth/allowed-emails"
)

_ssm = boto3.client("ssm")


def _allowed_emails() -> set[str]:
    """Read the allowlist from SSM.

    Read on every invocation rather than cached in the container: sign-ups are
    rare, so the SSM call costs nothing, and it means adding someone to the
    allowlist takes effect on their very next attempt instead of whenever a
    warm container happens to age out.
    """
    resp = _ssm.get_parameter(Name=ALLOWLIST_PARAM, WithDecryption=True)
    raw = resp["Parameter"]["Value"]
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def handler(event, context):
    trigger = event.get("triggerSource", "")
    email = (event.get("request", {}).get("userAttributes", {}).get("email") or "").strip().lower()

    # No email means the Google scopes or the attribute mapping are wrong. Deny
    # rather than guess: an unidentifiable user cannot be checked against a list
    # of identities.
    if not email:
        log.error("Sign-up denied: no email attribute present (trigger=%s).", trigger)
        raise Exception("Your identity provider did not supply an email address.")

    try:
        allowed = _allowed_emails()
    except Exception as e:
        # Log the cause for us, tell the user nothing about our infrastructure.
        log.exception("Sign-up denied: could not read allowlist %s (%s)", ALLOWLIST_PARAM, e)
        raise Exception("Access is restricted. Contact the site owner.") from None

    if not allowed:
        log.error("Sign-up denied: allowlist %s is empty.", ALLOWLIST_PARAM)
        raise Exception("Access is restricted. Contact the site owner.")

    if email not in allowed:
        # The address is logged so you can see who tried and add them if you want.
        log.warning("Sign-up denied for %s (not on the allowlist).", email)
        raise Exception("This account is not authorised to use this application.")

    # Federated users arrive unconfirmed. Left that way they can never actually
    # sign in, which looks identical to the allowlist rejecting them.
    if trigger == "PreSignUp_ExternalProvider":
        event["response"]["autoConfirmUser"] = True
        event["response"]["autoVerifyEmail"] = True

    log.info("Sign-up allowed for %s (trigger=%s).", email, trigger)
    return event
