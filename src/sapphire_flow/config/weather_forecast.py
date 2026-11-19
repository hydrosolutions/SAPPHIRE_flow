from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from sapphire_flow.adapters.meteoswiss_nwp import (
    DEFAULT_DISK_GUARD_ARCHIVE_HARD_GB,
    DEFAULT_DISK_GUARD_ARCHIVE_SOFT_GB,
    DEFAULT_DISK_GUARD_SCRATCH_HARD_GB,
    DEFAULT_DISK_GUARD_SCRATCH_SOFT_GB,
)
from sapphire_flow.exceptions import ConfigurationError

DEFAULT_EXPECTED_DELIVERY_OFFSET_HOURS = 5.0
GRID_EXTRACTOR_CHOICES: tuple[str, ...] = ("mesh", "exactextract")
DEFAULT_GRID_EXTRACTOR: Literal["mesh"] = "mesh"
WEATHER_FORECAST_TYPES: tuple[str, ...] = ("meteoswiss_nwp", "recap_gateway")
DEFAULT_WEATHER_FORECAST_TYPE = "meteoswiss_nwp"


def parse_require_nwp(value: str | None) -> bool:
    if value is None or value == "":
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(
        "SAPPHIRE_REQUIRE_NWP must be a boolean-like value "
        "(1/0, true/false, yes/no, on/off)"
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherForecastAdapterConfig:
    enabled: bool
    require_nwp: bool
    # Plan 082 Task 2C: the [adapters.weather_forecast].type selector — the
    # single source of truth the Flow-1 dispatch (Task 2D) branches on.
    # "meteoswiss_nwp" (default, unchanged behavior) or "recap_gateway"
    # (Nepal v1). Determines which adapter's required-field set is validated
    # below: recap_gateway skips the MeteoSwiss-only fields and instead
    # requires a valid [adapters.recap_gateway] section.
    type: str
    stac_base_url: str | None
    stac_collection: str | None
    scratch_path: Path | None
    max_files: int | None
    grid_extractor: str
    expected_delivery_offset_hours: float
    # Plan 105 D2 — disk-guard thresholds (four TOML-configurable values;
    # defaults are shared constants from meteoswiss_nwp to avoid divergence).
    disk_guard_scratch_soft_gb: float = DEFAULT_DISK_GUARD_SCRATCH_SOFT_GB
    disk_guard_scratch_hard_gb: float = DEFAULT_DISK_GUARD_SCRATCH_HARD_GB
    disk_guard_archive_soft_gb: float = DEFAULT_DISK_GUARD_ARCHIVE_SOFT_GB
    disk_guard_archive_hard_gb: float = DEFAULT_DISK_GUARD_ARCHIVE_HARD_GB


def parse_expected_delivery_offset_hours(
    weather_forecast: dict[str, object],
) -> float:
    monitoring = weather_forecast.get("monitoring", {})
    if not isinstance(monitoring, dict):
        return DEFAULT_EXPECTED_DELIVERY_OFFSET_HOURS
    value = cast("dict[str, object]", monitoring).get(
        "expected_delivery_offset_hours",
        DEFAULT_EXPECTED_DELIVERY_OFFSET_HOURS,
    )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(
            "[adapters.weather_forecast.monitoring].expected_delivery_offset_hours "
            "must be a TOML number"
        )
    if value <= 0:
        raise ConfigurationError(
            "[adapters.weather_forecast.monitoring].expected_delivery_offset_hours "
            "must be > 0"
        )
    return float(value)


def parse_weather_forecast_adapter_config(
    data: dict[str, object], *, require_nwp: bool
) -> WeatherForecastAdapterConfig:
    adapters = data.get("adapters", {})
    weather_forecast: object = (
        cast("dict[str, object]", adapters).get("weather_forecast", {})
        if isinstance(adapters, dict)
        else dict[str, object]()
    )
    if not isinstance(weather_forecast, dict):
        weather_forecast = dict[str, object]()
    weather_forecast = cast("dict[str, object]", weather_forecast)

    enabled_value = weather_forecast.get("enabled", False)
    if not isinstance(enabled_value, bool):
        raise ConfigurationError(
            "[adapters.weather_forecast].enabled must be a TOML boolean"
        )

    type_value = weather_forecast.get("type", DEFAULT_WEATHER_FORECAST_TYPE)
    if not isinstance(type_value, str) or type_value not in WEATHER_FORECAST_TYPES:
        raise ConfigurationError(
            f"[adapters.weather_forecast].type must be one of {WEATHER_FORECAST_TYPES}"
        )

    stac_base_url = weather_forecast.get("stac_base_url")
    stac_collection = weather_forecast.get("stac_collection")
    scratch_path_value = weather_forecast.get("scratch_path")

    # Plan 086: optional hard cap on GRIB files fetched per cycle. Absent → None
    # (unlimited, production default). A bool is rejected explicitly because
    # ``bool`` is a subclass of ``int`` in Python.
    max_files_value = weather_forecast.get("max_files")
    if max_files_value is not None and (
        isinstance(max_files_value, bool) or not isinstance(max_files_value, int)
    ):
        raise ConfigurationError(
            "[adapters.weather_forecast].max_files must be a TOML integer or unset"
        )

    grid_extractor_value = weather_forecast.get(
        "grid_extractor", DEFAULT_GRID_EXTRACTOR
    )
    if (
        not isinstance(grid_extractor_value, str)
        or grid_extractor_value not in GRID_EXTRACTOR_CHOICES
    ):
        raise ConfigurationError(
            "[adapters.weather_forecast].grid_extractor must be one of "
            f"{GRID_EXTRACTOR_CHOICES}"
        )

    if enabled_value and type_value == "meteoswiss_nwp":
        missing = [
            key
            for key, value in (
                ("stac_base_url", stac_base_url),
                ("stac_collection", stac_collection),
                ("scratch_path", scratch_path_value),
            )
            if not isinstance(value, str) or value == ""
        ]
        if missing:
            joined = ", ".join(missing)
            raise ConfigurationError(
                "[adapters.weather_forecast] enabled=true requires "
                f"configured MeteoSwiss field(s): {joined}"
            )
    elif enabled_value and type_value == "recap_gateway":
        # Plan 082 Task 2C: validate the Recap-specific config instead — the
        # shared [adapters.recap_gateway] section (Task 2A), never the
        # MeteoSwiss-only fields above. Raises ConfigurationError (propagates)
        # when the section is missing/incomplete.
        from sapphire_flow.config.recap_gateway import parse_recap_gateway_config

        parse_recap_gateway_config(data)

    # Plan 105 D2 — parse disk_guard_*_gb thresholds (same pattern as max_files).
    def _parse_disk_guard_gb(key: str, default: float) -> float:
        val = weather_forecast.get(key)
        if val is None:
            return default
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ConfigurationError(
                f"[adapters.weather_forecast].{key} must be a TOML number"
            )
        if val <= 0:
            raise ConfigurationError(f"[adapters.weather_forecast].{key} must be > 0")
        return float(val)

    disk_guard_scratch_soft_gb = _parse_disk_guard_gb(
        "disk_guard_scratch_soft_gb", DEFAULT_DISK_GUARD_SCRATCH_SOFT_GB
    )
    disk_guard_scratch_hard_gb = _parse_disk_guard_gb(
        "disk_guard_scratch_hard_gb", DEFAULT_DISK_GUARD_SCRATCH_HARD_GB
    )
    disk_guard_archive_soft_gb = _parse_disk_guard_gb(
        "disk_guard_archive_soft_gb", DEFAULT_DISK_GUARD_ARCHIVE_SOFT_GB
    )
    disk_guard_archive_hard_gb = _parse_disk_guard_gb(
        "disk_guard_archive_hard_gb", DEFAULT_DISK_GUARD_ARCHIVE_HARD_GB
    )

    # Cross-field validation: hard must be less than soft for each mount.
    if disk_guard_scratch_hard_gb >= disk_guard_scratch_soft_gb:
        raise ConfigurationError(
            "[adapters.weather_forecast] disk_guard_scratch_hard_gb must be "
            "< disk_guard_scratch_soft_gb"
        )
    if disk_guard_archive_hard_gb >= disk_guard_archive_soft_gb:
        raise ConfigurationError(
            "[adapters.weather_forecast] disk_guard_archive_hard_gb must be "
            "< disk_guard_archive_soft_gb"
        )

    return WeatherForecastAdapterConfig(
        enabled=enabled_value,
        require_nwp=require_nwp,
        type=type_value,
        stac_base_url=stac_base_url if isinstance(stac_base_url, str) else None,
        stac_collection=stac_collection if isinstance(stac_collection, str) else None,
        scratch_path=Path(scratch_path_value)
        if isinstance(scratch_path_value, str)
        else None,
        max_files=max_files_value,
        grid_extractor=grid_extractor_value,
        expected_delivery_offset_hours=parse_expected_delivery_offset_hours(
            weather_forecast
        ),
        disk_guard_scratch_soft_gb=disk_guard_scratch_soft_gb,
        disk_guard_scratch_hard_gb=disk_guard_scratch_hard_gb,
        disk_guard_archive_soft_gb=disk_guard_archive_soft_gb,
        disk_guard_archive_hard_gb=disk_guard_archive_hard_gb,
    )
