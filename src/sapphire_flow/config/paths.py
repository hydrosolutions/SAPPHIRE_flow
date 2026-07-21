from __future__ import annotations

import errno
import os
from pathlib import Path

import platformdirs
import structlog

log = structlog.get_logger(__name__)

_SUBDIRS = ("raw", "artifacts", "cache")


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
