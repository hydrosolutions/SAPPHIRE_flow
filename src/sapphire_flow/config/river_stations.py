from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sapphire_flow.config._overlay import load_merged_toml
from sapphire_flow.config.dhm import DhmConfig, parse_dhm_config
from sapphire_flow.exceptions import ConfigurationError

if TYPE_CHECKING:
    from sapphire_flow.config.deployment import DeploymentConfig

FORECAST_PROVIDER_PATH = Path("/app/config/overlays/nhs-provider.toml")


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastConfigOverlay:
    paths: tuple[Path, ...]

    def __post_init__(self) -> None:
        if (
            len(self.paths) < 2
            or self.paths[-1] != FORECAST_PROVIDER_PATH
            or len(set(self.paths)) != len(self.paths)
        ):
            raise ValueError(
                "forecast overlays must end in the reviewed provider target"
            )
        if any(
            not path.is_absolute()
            or ".." in path.parts
            or any(ord(char) < 32 or ord(char) == 127 for char in str(path))
            or any(char in str(path) for char in ",${}")
            for path in self.paths
        ):
            raise ValueError("forecast overlays require absolute container paths")

    @classmethod
    def parse(cls, raw: str | None) -> ForecastConfigOverlay | None:
        if raw is None or raw == "":
            return None
        items = tuple(item.strip() for item in raw.split(","))
        paths = tuple(Path(item) for item in items)
        if any(
            not item or str(path) != item
            for item, path in zip(items, paths, strict=True)
        ):
            raise ValueError("forecast overlays require non-empty canonical paths")
        return cls(paths=paths)

    def environment_value(self) -> str:
        return ",".join(str(path) for path in self.paths)


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
    return parse_river_station_config(load_merged_toml(path, overlay_paths))


def parse_river_station_config(
    raw: dict[str, object],
) -> HydroScraperConfig | DhmConfig:
    try:
        table = _DeploymentPayload.model_validate(raw).adapters.river_stations
        source = _SourcePayload.model_validate(table)
    except ValidationError:
        raise ConfigurationError("Invalid river station adapter selection") from None
    if source.type == "dhm":
        return parse_dhm_config(table)
    return HydroScraperConfig(endpoint=source.endpoint)


def load_forecast_provider_config(
    path: Path,
    worker_overlay_paths: tuple[Path, ...],
    selection: ForecastConfigOverlay,
) -> tuple[DeploymentConfig, DhmConfig]:
    from sapphire_flow.config.deployment import parse_deployment_config

    if (
        not path.is_absolute()
        or selection.paths[:-1] != worker_overlay_paths
        or path in selection.paths
    ):
        raise ConfigurationError(
            "forecast provider selection does not match worker configuration"
        )
    try:
        baseline = load_merged_toml(path, list(worker_overlay_paths))
        provider = load_merged_toml(FORECAST_PROVIDER_PATH, [])
        if set(provider) != {"adapters"}:
            raise ValueError("provider scope")
        provider_adapters = provider["adapters"]
        if not isinstance(provider_adapters, dict) or set(
            cast("dict[str, object]", provider_adapters)
        ) != {"river_stations"}:
            raise ValueError("provider scope")
        table = cast("dict[str, object]", provider_adapters)["river_stations"]
        if not isinstance(table, dict):
            raise ValueError("provider table")
        candidate = deepcopy(baseline)
        adapters = candidate.setdefault("adapters", {})
        if not isinstance(adapters, dict):
            raise ValueError("adapter table")
        cast("dict[str, object]", adapters)["river_stations"] = table
        dhm = parse_river_station_config(candidate)
        if not isinstance(dhm, DhmConfig):
            raise ValueError("provider selector")
        config = parse_deployment_config(candidate)
    except (OSError, ValueError, ConfigurationError):
        raise ConfigurationError("invalid forecast provider configuration") from None
    return config, dhm
