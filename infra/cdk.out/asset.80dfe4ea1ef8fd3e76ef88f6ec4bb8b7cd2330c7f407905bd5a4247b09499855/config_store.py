"""Read/write access to the editable config documents.

Deliberately the same shape as store.py: one Protocol, a local implementation
and an AWS one, and a make_* factory that picks between them. The caller never
learns which it is using.

The documents are profile.yaml (who you are) and settings.yaml (what to search
for, and the pre-filter thresholds). Both are editable from the website and
from VS Code, and this module is what makes those the SAME edit:

  local   CONFIG_BUCKET unset -> LocalConfigStore reads and writes the files
          sitting in the repo, so the settings page and your editor are
          touching one file.
  cloud   CONFIG_BUCKET set   -> S3ConfigStore reads and writes the versioned
          objects the scoring run downloads at the start of every invocation.

companies.yaml is deliberately NOT here. It holds ATS board tokens that are
only meaningful if they resolve to a real board, so it stays a checked-in file
edited alongside check_boards.py.
"""

from __future__ import annotations

import logging
import os
from typing import Optional, Protocol

log = logging.getLogger(__name__)

PROFILE_DOC = "profile.yaml"
SETTINGS_DOC = "settings.yaml"

EDITABLE_DOCS = (PROFILE_DOC, SETTINGS_DOC)


class ConfigStore(Protocol):
    """Minimal document interface. Text in, text out."""

    def read(self, name: str) -> Optional[str]: ...
    def write(self, name: str, body: str) -> None: ...


def _check_name(name: str) -> None:
    """Reject anything not on the fixed list.

    These names reach a filesystem path and an S3 key, so an unchecked value
    from an HTTP request is a path traversal ('../../etc/passwd') or an
    arbitrary object write. An allowlist of two constants is the cheapest
    correct answer; there is no case for accepting a third name.
    """
    if name not in EDITABLE_DOCS:
        raise ValueError(f"{name!r} is not an editable document")


class LocalConfigStore:
    """Documents as files on disk, relative to a base directory."""

    def __init__(self, base_dir: str = "."):
        self.base_dir = base_dir

    def _path(self, name: str) -> str:
        _check_name(name)
        return os.path.join(self.base_dir, name)

    def read(self, name: str) -> Optional[str]:
        path = self._path(name)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def write(self, name: str, body: str) -> None:
        path = self._path(name)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # Write then swap, so an interrupted save cannot leave a half-written
        # profile that the next scoring run would parse as garbage.
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, path)


class S3ConfigStore:
    """Documents as objects in the versioned config bucket.

    The bucket has versioning on, which is the rollback mechanism: a bad edit
    from the website is recoverable without this module needing any undo logic
    of its own.
    """

    def __init__(self, bucket: str):
        import boto3  # lazy, so local runs need no AWS deps

        self._bucket = bucket
        self._s3 = boto3.client("s3")

    def read(self, name: str) -> Optional[str]:
        _check_name(name)
        try:
            resp = self._s3.get_object(Bucket=self._bucket, Key=name)
        except Exception as e:
            # NoSuchKey is an expected state (nothing uploaded yet), not a
            # failure. Anything else is worth a log line but still reads as
            # absent, because a settings page that 500s is worse than one that
            # offers to create the document.
            log.warning("Could not read s3://%s/%s (%s)", self._bucket, name, e)
            return None
        return resp["Body"].read().decode("utf-8")

    def write(self, name: str, body: str) -> None:
        _check_name(name)
        self._s3.put_object(
            Bucket=self._bucket,
            Key=name,
            Body=body.encode("utf-8"),
            ContentType="application/yaml",
        )
        log.info("Wrote s3://%s/%s (%d bytes)", self._bucket, name, len(body))


def make_config_store() -> ConfigStore:
    """The store this process should use. One switch, two backends."""
    bucket = os.environ.get("CONFIG_BUCKET")
    if bucket:
        return S3ConfigStore(bucket)
    return LocalConfigStore(os.environ.get("CONFIG_DIR", "."))
