from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import cast

from sapphire_flow.config.deployment import (
    _resolve_env_vars,  # pyright: ignore[reportPrivateUsage]
)

_OVERLAY_ENV_VAR = "SAPPHIRE_CONFIG_OVERLAY"


def load_merged_toml(
    base_path: Path,
    overlay_paths: list[Path],
) -> dict[str, object]:
    """Parse base TOML and deep-merge overlays on top (left-to-right)."""
    merged: dict[str, object] = _parse_toml_file(base_path)
    for overlay_path in overlay_paths:
        overlay_data: dict[str, object] = _parse_toml_file(overlay_path)
        merged = _deep_merge(merged, overlay_data)
    return merged


def _resolve_overlay_paths() -> list[Path]:  # pyright: ignore[reportUnusedFunction]
    """Parse SAPPHIRE_CONFIG_OVERLAY env var into an overlay path list."""
    raw = os.environ.get(_OVERLAY_ENV_VAR)
    if raw is None or raw == "":
        return []
    items = [item.strip() for item in raw.split(",")]
    return [Path(item) for item in items if item]


def _parse_toml_file(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    text = path.read_text()
    resolved = _resolve_env_vars(text)
    return cast("dict[str, object]", tomllib.loads(resolved))


def _deep_merge(
    base: dict[str, object], overlay: dict[str, object]
) -> dict[str, object]:
    result: dict[str, object] = dict(base)
    for key, overlay_value in overlay.items():
        base_value = result.get(key)
        if isinstance(base_value, dict) and isinstance(overlay_value, dict):
            result[key] = _deep_merge(
                cast("dict[str, object]", base_value),
                cast("dict[str, object]", overlay_value),
            )
        else:
            result[key] = overlay_value
    return result
