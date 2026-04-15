from __future__ import annotations

import os
from pathlib import Path

import platformdirs

_SUBDIRS = ("raw", "artifacts", "cache")


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
        (root / subdir).mkdir(parents=True, exist_ok=True, mode=0o750)
    return root


def resolve_artifact_dir(config_data_dir: str | None = None) -> Path:
    return resolve_data_dir(config_data_dir) / "artifacts"
