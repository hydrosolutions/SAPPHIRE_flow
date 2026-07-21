from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field, field_validator, model_validator

from sapphire_flow.config._overlay import (
    _resolve_overlay_paths,  # pyright: ignore[reportPrivateUsage]
    load_merged_toml,
)


@dataclass(frozen=True, kw_only=True, slots=True)
class ComponentSpec:
    code: str
    weight: float


@dataclass(frozen=True, kw_only=True, slots=True)
class CalculatedStationSpec:
    """A calculated (component-derived) station to onboard (Plan 015 §5.C).

    Parsed from a ``[[onboarding.calculated]]`` TOML block. Components are referenced by
    ``code`` and must already exist as gauged+operational stations in the DB.
    ``effective_from`` (ISO 8601) is optional — it defaults to the earliest component
    observation timestamp at configuration time.
    """

    code: str
    name: str
    network: str
    parameter: str
    lon: float
    lat: float
    components: tuple[ComponentSpec, ...]
    timezone: str = "UTC"
    basin_code: str | None = None
    effective_from: str | None = None


class _ComponentModel(BaseModel):
    code: str = Field(min_length=1)
    weight: float

    @field_validator("weight")
    @classmethod
    def _finite_nonzero(cls, v: float) -> float:
        # Mirrors ComponentWeight.__post_init__ so a bad weight fails at the boundary.
        if not (v != 0 and math.isfinite(v) and abs(v) < 1e6):
            raise ValueError(
                f"weight must be nonzero and finite (|w| < 1e6), got {v!r}"
            )
        return v


class _CalculatedStationModel(BaseModel):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    network: str = Field(min_length=1)
    parameter: str = Field(min_length=1)
    lon: float = Field(ge=-180.0, le=180.0)
    lat: float = Field(ge=-90.0, le=90.0)
    components: list[_ComponentModel] = Field(min_length=1)
    timezone: str = "UTC"
    basin_code: str | None = None
    effective_from: str | None = None

    @field_validator("effective_from")
    @classmethod
    def _parseable_datetime(cls, v: str | None) -> str | None:
        if v is not None:
            datetime.fromisoformat(v)  # raises ValueError on a malformed timestamp
        return v

    @model_validator(mode="after")
    def _no_duplicate_or_self_component(self) -> _CalculatedStationModel:
        codes = [c.code for c in self.components]
        if len(codes) != len(set(codes)):
            raise ValueError(
                f"duplicate component codes for calculated station {self.code!r}"
            )
        if self.code in codes:
            raise ValueError(
                f"calculated station {self.code!r} lists itself as a component"
            )
        return self


@dataclass(frozen=True, kw_only=True, slots=True)
class OnboardingConfig:
    data_source: str = "camels-ch"
    basin_ids: tuple[str, ...] = ()
    water_level_datums_masl: dict[str, float] | None = None
    water_level_units: dict[str, str] | None = None
    calculated: tuple[CalculatedStationSpec, ...] = ()


def _parse_calculated(section: dict[str, Any]) -> tuple[CalculatedStationSpec, ...]:
    raw = section.get("calculated", [])
    specs: list[CalculatedStationSpec] = []
    for entry in raw:
        model = _CalculatedStationModel.model_validate(entry)
        specs.append(
            CalculatedStationSpec(
                code=model.code,
                name=model.name,
                network=model.network,
                parameter=model.parameter,
                lon=model.lon,
                lat=model.lat,
                components=tuple(
                    ComponentSpec(code=c.code, weight=c.weight)
                    for c in model.components
                ),
                timezone=model.timezone,
                basin_code=model.basin_code,
                effective_from=model.effective_from,
            )
        )
    return tuple(specs)


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
    datums_raw = section.get("water_level_datums_masl", {})
    units_raw = section.get("water_level_units", {})

    return OnboardingConfig(
        data_source=section.get("data_source", "camels-ch"),
        basin_ids=basin_ids,
        water_level_datums_masl={
            str(code): float(datum) for code, datum in datums_raw.items()
        },
        water_level_units={str(code): str(unit) for code, unit in units_raw.items()},
        calculated=_parse_calculated(section),
    )
