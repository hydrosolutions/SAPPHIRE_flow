from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sapphire_flow.config._overlay import load_merged_toml
from sapphire_flow.config.dhm import DhmConfig, parse_dhm_config
from sapphire_flow.exceptions import ConfigurationError

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, kw_only=True, slots=True)
class HydroScraperConfig:
    endpoint: str = "https://lindas.admin.ch/query"


class _SourcePayload(BaseModel):
    model_config = ConfigDict(strict=True)
    type: Literal["hydro_scraper", "dhm"] = "hydro_scraper"
    endpoint: str = "https://lindas.admin.ch/query"


class _AdaptersPayload(BaseModel):
    model_config = ConfigDict(strict=True)
    river_stations: dict[str, object] = Field(default_factory=dict)


class _DeploymentPayload(BaseModel):
    model_config = ConfigDict(strict=True)
    adapters: _AdaptersPayload = Field(default_factory=_AdaptersPayload)


def load_river_station_config(
    path: Path | None,
    overlay_paths: list[Path],
) -> HydroScraperConfig | DhmConfig:
    if path is None:
        return HydroScraperConfig()
    raw = load_merged_toml(path, overlay_paths)
    try:
        table = _DeploymentPayload.model_validate(raw).adapters.river_stations
        source = _SourcePayload.model_validate(table)
    except ValidationError:
        raise ConfigurationError("Invalid river station adapter selection") from None
    if source.type == "dhm":
        return parse_dhm_config(table)
    return HydroScraperConfig(endpoint=source.endpoint)
