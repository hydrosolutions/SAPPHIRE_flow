from __future__ import annotations

import errno
import os
from pathlib import Path

import platformdirs
import structlog

log = structlog.get_logger(__name__)

_SUBDIRS = ("raw", "artifacts", "cache")

# Plan 307 T1: the operator staging root for artifact imports. Deliberately
# NOT in `_SUBDIRS` — it must be a read-only MOUNT supplied by the
# deployment's compose overlay, never a directory this process creates. A
# `mkdir` here would silently manufacture an empty staging root on a host
# whose overlay forgot the bind, turning a misconfiguration into a
# file-not-found much later. `resolve_incoming_dir` therefore resolves the
# path and does not touch the filesystem.
_INCOMING_SUBDIR = "incoming"


def _ensure_subdir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o750)
    except OSError as exc:
        if exc.errno != errno.EROFS:
            raise
        log.debug(
            "data_dir.subdir_skipped_read_only",
            path=str(path),
            reason="read-only root filesystem (EROFS); caller must not rely on this",
        )


def resolve_data_dir(config_data_dir: str | None = None) -> Path:
    env_val = os.environ.get("SAPPHIRE_DATA_DIR")
    if env_val:
        root = Path(env_val)
    elif config_data_dir:
        root = Path(config_data_dir)
    else:
        root = Path(platformdirs.user_data_dir("sapphire-flow"))

    root = root.expanduser().resolve()

    for subdir in _SUBDIRS:
        _ensure_subdir(root / subdir)
    return root


def resolve_artifact_dir(config_data_dir: str | None = None) -> Path:
    return resolve_data_dir(config_data_dir) / "artifacts"


def resolve_incoming_dir(config_data_dir: str | None = None) -> Path:
    """The read-only staging root an operator drops an artifact into
    (Plan 307 T1). Resolved from configuration, never hardcoded, and never
    created — see `_INCOMING_SUBDIR`.

    `SAPPHIRE_INCOMING_DIR` wins when set, so a deployment can bind the
    staging root anywhere without also moving its data dir; the compose files
    set it explicitly rather than relying on the derived default, and this
    function is what makes that declaration live rather than decorative.
    """
    env_val = os.environ.get("SAPPHIRE_INCOMING_DIR")
    if env_val:
        return Path(env_val).expanduser().resolve()
    return resolve_data_dir(config_data_dir) / _INCOMING_SUBDIR
