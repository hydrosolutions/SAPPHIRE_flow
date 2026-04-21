from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from sapphire_flow.config._overlay import (
    _resolve_overlay_paths,  # pyright: ignore[reportPrivateUsage]
    load_merged_toml,
)


@dataclass(frozen=True, kw_only=True, slots=True)
class OnboardingConfig:
    data_source: str = "camels-ch"
    basin_ids: tuple[str, ...] = ()


def load_onboarding_config(
    config_path: str | Path | None = None,
) -> OnboardingConfig | None:
    if config_path is None:
        env_path = os.environ.get("SAPPHIRE_CONFIG")
        if env_path is None:
            raise ValueError("No config path provided and SAPPHIRE_CONFIG is not set")
        config_path = env_path
    path = Path(config_path)
    # Cast to dict[str, Any] — post-parse code treats TOML values loosely
    # (same behaviour as the prior tomllib.loads return type).
    data = cast("dict[str, Any]", load_merged_toml(path, _resolve_overlay_paths()))

    section = data.get("onboarding")
    if section is None:
        return None

    basin_ids_raw = section.get("basin_ids", [])
    basin_ids = tuple(str(bid) for bid in basin_ids_raw)

    return OnboardingConfig(
        data_source=section.get("data_source", "camels-ch"),
        basin_ids=basin_ids,
    )
