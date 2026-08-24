"""Tests for the Cognito PreSignUp allowlist trigger.

This function is the only thing standing between "sign in with Google" and
"anyone on earth may register", so the cases that matter most here are the
ones where something goes WRONG. A bug that denies a legitimate user is an
annoyance; a bug that admits everyone is the whole security model gone.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# boto3.client() reads a region at construction time, and the module builds its
# SSM client at import. Set one before loading so the import works on a machine
# with no AWS config at all.
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-southeast-2")

_MODULE_PATH = Path(__file__).parent / "infra" / "lambda" / "presignup" / "index.py"
_spec = importlib.util.spec_from_file_location("presignup_index", _MODULE_PATH)
presignup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(presignup)


def make_event(email, trigger="PreSignUp_ExternalProvider"):
    attrs = {} if email is None else {"email": email}
    return {
        "triggerSource": trigger,
        "request": {"userAttributes": attrs},
        "response": {},
    }


@pytest.fixture
def ssm(monkeypatch):
    """Replace the module-level SSM client with a stub."""
    client = MagicMock()
    monkeypatch.setattr(presignup, "_ssm", client)
    return client


def set_allowlist(ssm, value):
    ssm.get_parameter.return_value = {"Parameter": {"Value": value}}


# --- The happy path --------------------------------------------------------


def test_allowlisted_email_is_admitted(ssm):
    set_allowlist(ssm, "josephforanu@gmail.com,friend@example.com")
    out = presignup.handler(make_event("josephforanu@gmail.com"), None)
    assert out["response"]["autoConfirmUser"] is True
    assert out["response"]["autoVerifyEmail"] is True


def test_match_ignores_case_and_surrounding_space(ssm):
    set_allowlist(ssm, "  Joseph@Example.com , friend@example.com ")
    out = presignup.handler(make_event("JOSEPH@example.COM"), None)
    assert out["response"]["autoConfirmUser"] is True


def test_native_signup_is_not_auto_confirmed(ssm):
    """Auto-confirm is only correct for federated users, who have already
    proven the email to Google. A native sign-up should still be verified."""
    set_allowlist(ssm, "joseph@example.com")
    out = presignup.handler(
        make_event("joseph@example.com", trigger="PreSignUp_SignUp"), None
    )
    assert "autoConfirmUser" not in out["response"]


# --- Denials ---------------------------------------------------------------


def test_unlisted_email_is_denied(ssm):
    set_allowlist(ssm, "joseph@example.com")
    with pytest.raises(Exception, match="not authorised"):
        presignup.handler(make_event("stranger@example.com"), None)


def test_missing_email_is_denied(ssm):
    """No email means the Google scopes or attribute mapping are wrong. An
    unidentifiable user cannot be checked against a list of identities."""
    set_allowlist(ssm, "joseph@example.com")
    with pytest.raises(Exception, match="email"):
        presignup.handler(make_event(None), None)


def test_blank_email_is_denied(ssm):
    set_allowlist(ssm, "joseph@example.com")
    with pytest.raises(Exception):
        presignup.handler(make_event("   "), None)


# --- Fail closed -----------------------------------------------------------


def test_unreachable_ssm_denies(ssm):
    """The critical one. If the allowlist cannot be read we must deny, not
    shrug and let the request through."""
    ssm.get_parameter.side_effect = RuntimeError("ssm is down")
    with pytest.raises(Exception, match="restricted"):
        presignup.handler(make_event("joseph@example.com"), None)


def test_missing_parameter_denies(ssm):
    ssm.get_parameter.side_effect = Exception("ParameterNotFound")
    with pytest.raises(Exception, match="restricted"):
        presignup.handler(make_event("joseph@example.com"), None)


def test_empty_allowlist_denies_everyone(ssm):
    """An empty list means nobody is allowed, never everybody."""
    set_allowlist(ssm, "")
    with pytest.raises(Exception, match="restricted"):
        presignup.handler(make_event("joseph@example.com"), None)


def test_allowlist_of_only_separators_denies(ssm):
    set_allowlist(ssm, " , , ")
    with pytest.raises(Exception, match="restricted"):
        presignup.handler(make_event("joseph@example.com"), None)


def test_decryption_is_requested(ssm):
    """The parameter is a SecureString; reading it without decryption would
    silently compare against ciphertext and deny everyone."""
    set_allowlist(ssm, "joseph@example.com")
    presignup.handler(make_event("joseph@example.com"), None)
    assert ssm.get_parameter.call_args.kwargs["WithDecryption"] is True
