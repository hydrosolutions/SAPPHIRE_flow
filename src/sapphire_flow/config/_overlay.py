from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from sapphire_flow.config.deployment import (
    _ENV_VAR_PATTERN,  # pyright: ignore[reportPrivateUsage]
    _resolve_env_vars,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_OVERLAY_ENV_VAR = "SAPPHIRE_CONFIG_OVERLAY"


@dataclass(frozen=True, kw_only=True, slots=True)
class ConfigOrigin:
    document: int
    operation: Literal["set", "replace"]


@dataclass(frozen=True, kw_only=True, slots=True)
class TomlProvenance:
    """Internal merge evidence; keys/variables are untrusted, never display directly."""

    origins: dict[tuple[str, ...], ConfigOrigin] = field(
        default_factory=lambda: dict[tuple[str, ...], ConfigOrigin]()
    )
    variables: dict[int, tuple[str, ...]] = field(
        default_factory=lambda: dict[int, tuple[str, ...]]()
    )


def load_merged_toml(
    base_path: Path,
    overlay_paths: list[Path],
    *,
    environment: Mapping[str, str] | None = None,
    provenance: TomlProvenance | None = None,
) -> dict[str, object]:
    """Parse base TOML and deep-merge overlays on top (left-to-right)."""
    merged: dict[str, object] = {}
    for document, path in enumerate((base_path, *overlay_paths)):
        overlay_data = _parse_toml_file(path, environment, provenance, document)
        if document and "qc_rules" in overlay_data:
            raise ValueError(
                f"QC rules must be changed in the base config, not an overlay: {path}"
            )
        onboarding = overlay_data.get("onboarding")
        if document and isinstance(onboarding, dict):
            for key in ("station_qc_thresholds", "qc_pending_networks"):
                if key in onboarding:
                    raise ValueError(
                        f"onboarding.{key} must be changed in the base config, "
                        f"not an overlay: {path}"
                    )
        merged = _deep_merge(merged, overlay_data, provenance, document)
    return merged


def _resolve_overlay_paths() -> list[Path]:  # pyright: ignore[reportUnusedFunction]
    """Parse SAPPHIRE_CONFIG_OVERLAY env var into an overlay path list."""
    raw = os.environ.get(_OVERLAY_ENV_VAR)
    if raw is None or raw == "":
        return []
    items = [item.strip() for item in raw.split(",")]
    return [Path(item) for item in items if item]


def _parse_toml_file(
    path: Path,
    environment: Mapping[str, str] | None = None,
    provenance: TomlProvenance | None = None,
    document: int = 0,
) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    text = path.read_text()
    if provenance is not None:
        provenance.variables[document] = tuple(
            dict.fromkeys(_ENV_VAR_PATTERN.findall(text))
        )
    resolved = _resolve_env_vars(text, environment)
    return cast("dict[str, object]", tomllib.loads(resolved))


def _deep_merge(
    base: dict[str, object],
    overlay: dict[str, object],
    provenance: TomlProvenance | None = None,
    document: int = 0,
    prefix: tuple[str, ...] = (),
) -> dict[str, object]:
    result: dict[str, object] = dict(base)
    for key, overlay_value in overlay.items():
        base_value = result.get(key)
        if isinstance(base_value, dict) and isinstance(overlay_value, dict):
            if provenance is not None and overlay_value:
                provenance.origins.pop((*prefix, key), None)
            result[key] = _deep_merge(
                cast("dict[str, object]", base_value),
                cast("dict[str, object]", overlay_value),
                provenance,
                document,
                (*prefix, key),
            )
        else:
            if provenance is not None:
                key_path = (*prefix, key)
                for old in tuple(provenance.origins):
                    if old[: len(key_path)] == key_path:
                        del provenance.origins[old]
                _record_origin(
                    overlay_value,
                    key_path,
                    ConfigOrigin(
                        document=document,
                        operation="replace" if key in result else "set",
                    ),
                    provenance,
                )
            result[key] = overlay_value
    return result


def _record_origin(
    value: object,
    path: tuple[str, ...],
    origin: ConfigOrigin,
    provenance: TomlProvenance,
) -> None:
    if isinstance(value, dict) and value:
        for key, child in cast("dict[str, object]", value).items():
            _record_origin(child, (*path, key), origin, provenance)
    else:
        provenance.origins[path] = origin
