from __future__ import annotations

import math
import os
import random
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeVar, cast
from uuid import uuid4

import structlog
import structlog.contextvars
from prefect import flow, task
from prefect import runtime as prefect_runtime
from prefect.cache_policies import NO_CACHE

from sapphire_flow.adapters.meteoswiss_nwp import (
    DEFAULT_DISK_GUARD_ARCHIVE_HARD_GB,
    DEFAULT_DISK_GUARD_ARCHIVE_SOFT_GB,
    DEFAULT_DISK_GUARD_SCRATCH_HARD_GB,
    DEFAULT_DISK_GUARD_SCRATCH_SOFT_GB,
)
from sapphire_flow.adapters.recap_gateway import (
    SNOW_CANONICAL_PARAMETERS,
    GatewayResolutionError,
    RecapAuthError,
    RecapConfigurationError,
    RecapDataUnavailableError,
)
from sapphire_flow.exceptions import (
    ConfigurationError,
    DiskHardLimitError,
    DiskSoftLimitError,
    NoCycleAvailableError,
    StoreError,
)
from sapphire_flow.protocols.adapters import SnowForecastSource
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    AlertEligibility,
    ForecastCycleHealth,
    ModelAssignmentStatus,
    NwpCycleSource,
    PipelineCheckType,
    PipelineHealthStatus,
    StationKind,
    StationStatus,
    WeatherSourceRole,
)
from sapphire_flow.types.ids import (
    ALERT_ELIGIBILITIES,
    FALLBACK_MODEL_IDS,
    FALLBACK_PRIORITY_THRESHOLD,
    ModelId,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from sapphire_flow.adapters.recap_gateway import (
        GatewayPolygonBindingStoreLike,
        RecapClientLike,
    )
    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.protocols.adapters import WeatherForecastSource
    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.grid_extractor import GridExtractor
    from sapphire_flow.protocols.stores import (
        AlertStore,
        BasinStore,
        ClimBaselineStore,
        ForecastStore,
        HistoricalForcingStore,
        ModelArtifactStore,
        ModelStateStore,
        NwpGridStore,
        ObservationStore,
        RatingCurveStore,
        StationGroupStore,
        StationStore,
        WeatherForecastStore,
    )
    from sapphire_flow.types.basin import Basin
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import ForecastQcRuleSet
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.rating_curve import RatingCurve
    from sapphire_flow.types.station import StationConfig, StationWeatherSource

log = structlog.get_logger(__name__)


def _bind_rating_curve(
    fc: OperationalForecast,
    active_curves: dict[StationId, RatingCurve] | None,
) -> OperationalForecast:
    """Bind the station's rating curve (active at the forecast's issue time) to a
    forecast before storage (Plan 035 Task 4). ``active_curves`` is ``None`` when
    the feature is off (no ``rating_curve_store`` injected, e.g. v0) — a pure
    no-op with no logging. An empty dict means the feature is on but the station
    reports discharge directly (no curve)."""
    if active_curves is None:
        return fc
    curve = active_curves.get(fc.station_id)
    if curve is None:
        log.debug(
            "rating_curve.bind_skipped",
            station_id=str(fc.station_id),
            forecast_id=str(fc.id),
        )
        return fc
    log.info(
        "rating_curve.bound",
        station_id=str(fc.station_id),
        forecast_id=str(fc.id),
        rating_curve_id=str(curve.id),
    )
    return replace(fc, rating_curve_id=curve.id)


# = MeteoSwissNwpAdapter.NWP_SOURCE. Used only by the grid-staleness check
# below — NOT a selection fallback (that heuristic was retired; forecast-source
# selection now goes exclusively through StationStore.fetch_forecast_binding).
_ICON_NWP_SOURCE = "icon_ch2_eps"
# = RecapGatewayForecastAdapter.NWP_SOURCE. Plan 082 Task 2G: the grid-
# staleness check is parameterized on the ACTIVE forecast source so an
# IFS-only Nepal deploy checks weather_forecasts freshness for "ifs_ecmwf",
# never the (permanently absent) "icon_ch2_eps" Zarr grid.
_IFS_NWP_SOURCE = "ifs_ecmwf"
_NWP_CADENCE_HOURS = 6.0
_DEFAULT_EXPECTED_DELIVERY_OFFSET_HOURS = 5.0


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastCycleResult:
    cycle_time: UtcDatetime
    health: ForecastCycleHealth
    stations_attempted: int
    stations_succeeded: int
    stations_failed: int
    forecasts_stored: int
    alerts_checked: bool
    duration_ms: float
    errors: tuple[str, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class _WeatherForecastAdapterConfig:
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


@dataclass(frozen=True, kw_only=True, slots=True)
class _NwpFetchOutcome:
    """Successful NWP-phase result threaded back to the forecast loop.

    ``cycle_time`` is the adapter-RESOLVED published cycle (the grid's own
    ``cycle_time`` on the gridded path), NOT the nominal request — on a fallback
    this is an older cycle. The loop uses it for BOTH the Phase-B readback and
    the ``nwp_cycle_reference_time`` provenance so records/readback/provenance
    stay on one cycle. ``fallback_used`` is True iff the adapter walked back
    >=1 cycle step; the loop maps it to ``NwpCycleSource.FALLBACK``.

    ``nwp_unavailable`` (Plan 090 D3) is True when NO adequate cycle exists this
    run (the adapter exhausted its fallback budget → ``NoCycleAvailableError``).
    This is NOT a flow-fatal error: the loop falls to runoff-only for THIS cycle
    (NWP-consuming models produce nothing; native/fallback models still forecast).

    ``snow_unavailable`` (Plan 145 D3.2c) is True when the snow-forecast fetch
    (capability-gated, station-scoped) contained >=1 ``(hru, variable)`` gap this
    cycle. Distinct from ``nwp_unavailable`` — a snow outage never trips the
    cycle-wide NWP degrade; it suppresses only the snow-fed model (via the
    per-model ``assess_future_coverage`` gate) while surfacing the cycle as
    DEGRADED rather than silently HEALTHY.
    """

    cycle_time: UtcDatetime
    fallback_used: bool
    nwp_unavailable: bool = False
    snow_unavailable: bool = False


_GRID_EXTRACTOR_CHOICES: tuple[str, ...] = ("mesh", "exactextract")
_DEFAULT_GRID_EXTRACTOR: Literal["mesh"] = "mesh"

# Plan 082 Task 2C: [adapters.weather_forecast].type selector.
_WEATHER_FORECAST_TYPES: tuple[str, ...] = ("meteoswiss_nwp", "recap_gateway")
_DEFAULT_WEATHER_FORECAST_TYPE = "meteoswiss_nwp"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_env_bool(name: str) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return False
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(
        f"{name} must be a boolean-like value (1/0, true/false, yes/no, on/off)"
    )


def _parse_expected_delivery_offset_hours(
    weather_forecast: dict[str, object],
) -> float:
    monitoring = weather_forecast.get("monitoring", {})
    if not isinstance(monitoring, dict):
        return _DEFAULT_EXPECTED_DELIVERY_OFFSET_HOURS
    value = monitoring.get(
        "expected_delivery_offset_hours",
        _DEFAULT_EXPECTED_DELIVERY_OFFSET_HOURS,
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


def _load_weather_forecast_adapter_config() -> _WeatherForecastAdapterConfig:
    require_nwp = _parse_env_bool("SAPPHIRE_REQUIRE_NWP")
    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is None:
        return _WeatherForecastAdapterConfig(
            enabled=False,
            require_nwp=require_nwp,
            type=_DEFAULT_WEATHER_FORECAST_TYPE,
            stac_base_url="https://data.geo.admin.ch/api/stac/v1",
            stac_collection="ch.meteoschweiz.ogd-forecasting-icon-ch2",
            scratch_path=Path("/tmp/sapphire_nwp"),
            max_files=None,
            grid_extractor=_DEFAULT_GRID_EXTRACTOR,
            expected_delivery_offset_hours=_DEFAULT_EXPECTED_DELIVERY_OFFSET_HOURS,
        )

    from sapphire_flow.config._overlay import (
        _resolve_overlay_paths,  # pyright: ignore[reportPrivateUsage]
        load_merged_toml,
    )

    data = cast(
        "dict[str, Any]",
        load_merged_toml(Path(config_path), _resolve_overlay_paths()),
    )
    adapters = data.get("adapters", {})
    weather_forecast = (
        adapters.get("weather_forecast", {}) if isinstance(adapters, dict) else {}
    )
    if not isinstance(weather_forecast, dict):
        weather_forecast = {}
    weather_forecast = cast("dict[str, object]", weather_forecast)

    enabled_value = weather_forecast.get("enabled", False)
    if not isinstance(enabled_value, bool):
        raise ConfigurationError(
            "[adapters.weather_forecast].enabled must be a TOML boolean"
        )

    type_value = weather_forecast.get("type", _DEFAULT_WEATHER_FORECAST_TYPE)
    if not isinstance(type_value, str) or type_value not in _WEATHER_FORECAST_TYPES:
        raise ConfigurationError(
            f"[adapters.weather_forecast].type must be one of {_WEATHER_FORECAST_TYPES}"
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
        "grid_extractor", _DEFAULT_GRID_EXTRACTOR
    )
    if (
        not isinstance(grid_extractor_value, str)
        or grid_extractor_value not in _GRID_EXTRACTOR_CHOICES
    ):
        raise ConfigurationError(
            "[adapters.weather_forecast].grid_extractor must be one of "
            f"{_GRID_EXTRACTOR_CHOICES}"
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
        from sapphire_flow.config.recap_gateway import load_recap_gateway_config

        load_recap_gateway_config(Path(config_path))

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

    return _WeatherForecastAdapterConfig(
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
        expected_delivery_offset_hours=_parse_expected_delivery_offset_hours(
            weather_forecast
        ),
        disk_guard_scratch_soft_gb=disk_guard_scratch_soft_gb,
        disk_guard_scratch_hard_gb=disk_guard_scratch_hard_gb,
        disk_guard_archive_soft_gb=disk_guard_archive_soft_gb,
        disk_guard_archive_hard_gb=disk_guard_archive_hard_gb,
    )


def _build_recap_forecast_adapter(
    *,
    config_path: str | None,
    gateway_polygon_store: GatewayPolygonBindingStoreLike | None,
    recap_client: object | None,
) -> object:
    """Plan 082 Task 2D Flow-1 dispatch: build the Nepal v1 Recap adapter.

    Config validity (a valid ``[adapters.recap_gateway]`` section) was
    already enforced by :func:`_load_weather_forecast_adapter_config` (Task
    2C) — this only wires the already-validated pieces together. Extracted
    from ``run_forecast_cycle_flow`` so the dispatch decision is unit-testable
    without running the full flow.
    """
    from sapphire_flow.adapters.recap_gateway import (
        RecapGatewayForecastAdapter,
        StoreBackedGatewayPolygonResolver,
    )
    from sapphire_flow.config.recap_gateway import (
        build_recap_client_config,
        load_recap_api_key,
        load_recap_gateway_config,
    )

    if gateway_polygon_store is None:
        raise ConfigurationError(
            "recap Gateway forecast dispatch (type=recap_gateway) requires a "
            "gateway_polygon_store (§5a table reader) but none was available"
        )
    if recap_client is None:
        if config_path is None:
            # Pyright narrowing: unreachable — enabled=true with
            # type=recap_gateway requires SAPPHIRE_CONFIG to have been
            # readable (Task 2C validated it before this is called).
            raise ConfigurationError(
                "[adapters.weather_forecast].type is recap_gateway but "
                "SAPPHIRE_CONFIG is unset"
            )
        from recap_client import RecapClient

        recap_gateway_config = load_recap_gateway_config(Path(config_path))
        client_config = build_recap_client_config(
            api_key=load_recap_api_key(), config=recap_gateway_config
        )
        recap_client = RecapClient(client_config)
        return RecapGatewayForecastAdapter(
            client=cast("RecapClientLike", recap_client),
            resolver=StoreBackedGatewayPolygonResolver(gateway_polygon_store),
            max_cycle_age_hours=recap_gateway_config.max_cycle_age_hours,
        )
    return RecapGatewayForecastAdapter(
        client=cast("RecapClientLike", recap_client),
        resolver=StoreBackedGatewayPolygonResolver(gateway_polygon_store),
    )


def _load_grid_extractor_choice() -> Literal["mesh", "exactextract"]:
    """Read ONLY the ``[adapters.weather_forecast].grid_extractor`` selector.

    Decoupled from :func:`_load_weather_forecast_adapter_config` so the selector
    is honored for injected adapters without triggering MeteoSwiss-only field
    validation. Returns the default (``"mesh"``) when ``SAPPHIRE_CONFIG`` is
    unset or the key is absent. Raises ``ConfigurationError`` only when the
    value is present but not a recognized choice — no MeteoSwiss-only field is
    read or validated here.
    """
    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is None:
        return _DEFAULT_GRID_EXTRACTOR

    from sapphire_flow.config._overlay import (
        _resolve_overlay_paths,  # pyright: ignore[reportPrivateUsage]
        load_merged_toml,
    )

    data = cast(
        "dict[str, Any]",
        load_merged_toml(Path(config_path), _resolve_overlay_paths()),
    )
    adapters = data.get("adapters", {})
    weather_forecast = (
        adapters.get("weather_forecast", {}) if isinstance(adapters, dict) else {}
    )
    if not isinstance(weather_forecast, dict):
        weather_forecast = {}

    value = weather_forecast.get("grid_extractor", _DEFAULT_GRID_EXTRACTOR)
    if value not in _GRID_EXTRACTOR_CHOICES:
        raise ConfigurationError(
            "[adapters.weather_forecast].grid_extractor must be one of "
            f"{_GRID_EXTRACTOR_CHOICES}"
        )
    return cast('Literal["mesh", "exactextract"]', value)


def _load_expected_delivery_offset_hours() -> float:
    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is None:
        return _DEFAULT_EXPECTED_DELIVERY_OFFSET_HOURS

    from sapphire_flow.config._overlay import (
        _resolve_overlay_paths,  # pyright: ignore[reportPrivateUsage]
        load_merged_toml,
    )

    data = cast(
        "dict[str, Any]",
        load_merged_toml(Path(config_path), _resolve_overlay_paths()),
    )
    adapters = data.get("adapters", {})
    weather_forecast = (
        adapters.get("weather_forecast", {}) if isinstance(adapters, dict) else {}
    )
    if not isinstance(weather_forecast, dict):
        return _DEFAULT_EXPECTED_DELIVERY_OFFSET_HOURS
    return _parse_expected_delivery_offset_hours(
        cast("dict[str, object]", weather_forecast)
    )


def _load_recap_staleness_threshold_hours(config_path: str | None) -> float | None:
    """Read ``RecapGatewayConfig.staleness_threshold_hours`` for the watchdog.

    Plan 082 Task 2G (Codex review Finding 2): for a Recap (``ifs_ecmwf``)
    deployment, ``[adapters.recap_gateway].staleness_threshold_hours`` is the
    SAP3-side staleness threshold — it must be used DIRECTLY as the max grid
    age, not derived from the MeteoSwiss ``expected_delivery_offset_hours *
    6h`` cadence heuristic (that formula is meaningless for Recap and was
    silently overriding the configured value with the MeteoSwiss default).
    Returns ``None`` when there is no config path to read (mirrors the other
    ``_load_*`` helpers' unset-``SAPPHIRE_CONFIG`` default).
    """
    if config_path is None:
        return None
    from sapphire_flow.config.recap_gateway import load_recap_gateway_config

    return load_recap_gateway_config(Path(config_path)).staleness_threshold_hours


def _load_forecast_qc_rules() -> ForecastQcRuleSet:
    from sapphire_flow.config.forecast_qc_rules import (
        _default_swiss_forecast_qc_rules,  # pyright: ignore[reportPrivateUsage]
        load_forecast_qc_rules,
    )

    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is not None:
        return load_forecast_qc_rules(config_path)
    return _default_swiss_forecast_qc_rules()


def _resolve_cycle_time(
    cycle_time_str: str | None,
    clock: Callable[[], UtcDatetime],
) -> UtcDatetime:
    if cycle_time_str is not None:
        return ensure_utc(datetime.fromisoformat(cycle_time_str).replace(tzinfo=UTC))
    return clock()


def _model_alert_eligibility(
    model_id: ModelId,
    models: dict[ModelId, ForecastModel],
) -> AlertEligibility:
    if model_id in ALERT_ELIGIBILITIES:
        return ALERT_ELIGIBILITIES[model_id]
    model = models.get(model_id)
    declared = getattr(model, "alert_eligibility", None)
    if isinstance(declared, AlertEligibility):
        return declared
    raise ConfigurationError(f"model {model_id} has no declared AlertEligibility")


def _append_pipeline_health_record(
    pipeline_health_store: object | None,
    *,
    check_type: PipelineCheckType,
    checked_at: UtcDatetime,
    status: PipelineHealthStatus,
    subject: str,
    detail: dict[str, object],
    cycle_time: UtcDatetime | None,
) -> None:
    if pipeline_health_store is None:
        return
    append = getattr(pipeline_health_store, "append_health_record", None)
    if not callable(append):
        return

    from sapphire_flow.types.pipeline import PipelineHealthRecord

    try:
        append(
            PipelineHealthRecord(
                check_type=check_type,
                checked_at=checked_at,
                status=status,
                subject=subject,
                detail=detail,
                cycle_time=cycle_time,
                created_at=checked_at,
            )
        )
    except Exception as exc:
        log.warning(
            "pipeline.health_record_write_failed",
            check_type=check_type.value,
            subject=subject,
            error=str(exc),
        )


def _record_station_dark(
    pipeline_health_store: object | None,
    *,
    station_id: StationId,
    reason: str,
    assigned_models: list[ModelId],
    nwp_enabled: bool,
    checked_at: UtcDatetime,
    cycle_time: UtcDatetime,
) -> None:
    detail = {
        "reason": reason,
        "assigned_models": [str(model_id) for model_id in assigned_models],
        "nwp_enabled": nwp_enabled,
    }
    log.error("forecast_cycle.station_dark", **detail)
    _append_pipeline_health_record(
        pipeline_health_store,
        check_type=PipelineCheckType.FORECAST_STATION_DARK,
        checked_at=checked_at,
        status=PipelineHealthStatus.CRITICAL,
        subject=str(station_id),
        detail=cast("dict[str, object]", detail),
        cycle_time=cycle_time,
    )


def _partition_alert_eligible_ensembles(
    all_ensembles: dict[StationId, dict[ModelId, dict[str, ForecastEnsemble]]],
    models: dict[ModelId, ForecastModel],
    pipeline_health_store: object | None,
    *,
    checked_at: UtcDatetime,
    cycle_time: UtcDatetime,
) -> tuple[dict[StationId, dict[ModelId, dict[str, ForecastEnsemble]]], bool]:
    eligible: dict[StationId, dict[ModelId, dict[str, ForecastEnsemble]]] = {}
    suppressed = False

    for station_id, model_ensembles in all_ensembles.items():
        station_eligible: dict[ModelId, dict[str, ForecastEnsemble]] = {}
        suppressed_eligibilities: set[str] = set()
        suppressed_parameters: set[str] = set()

        for model_id, param_ensembles in model_ensembles.items():
            eligibility = _model_alert_eligibility(model_id, models)
            if eligibility is AlertEligibility.SKILL_FORECAST:
                station_eligible[model_id] = param_ensembles
                continue
            suppressed_eligibilities.add(eligibility.value)
            suppressed_parameters.update(param_ensembles.keys())

        if station_eligible:
            eligible[station_id] = station_eligible
            continue

        if model_ensembles:
            suppressed = True
            detail = {
                "alert_eligibility": sorted(suppressed_eligibilities),
                "parameter": sorted(suppressed_parameters),
            }
            log.warning(
                "alert.suppressed_fallback_only",
                station_id=str(station_id),
                **detail,
            )
            _append_pipeline_health_record(
                pipeline_health_store,
                check_type=PipelineCheckType.ALERT_SUPPRESSED_FALLBACK,
                checked_at=checked_at,
                status=PipelineHealthStatus.WARNING,
                subject=str(station_id),
                detail=cast("dict[str, object]", detail),
                cycle_time=cycle_time,
            )

    return eligible, suppressed


def _check_nwp_grid_staleness(
    weather_forecast_store: object,
    pipeline_health_store: object | None,
    *,
    expected_delivery_offset_hours: float,
    checked_at: UtcDatetime,
    cycle_time: UtcDatetime,
    forecast_source: str = _ICON_NWP_SOURCE,
    staleness_max_age_hours: float | None = None,
) -> bool:
    """Check whether the latest stored ``forecast_source`` grid is stale.

    ``staleness_max_age_hours``, when given, is used DIRECTLY as the max
    allowed age (Plan 082 Task 2G, Codex review Finding 2) — this is how a
    Recap (``ifs_ecmwf``) deployment's ``RecapGatewayConfig.
    staleness_threshold_hours`` overrides the MeteoSwiss ``expected_delivery_
    offset_hours * 6h`` cadence heuristic, which does not apply to Recap.
    ``None`` (the MeteoSwiss/default call shape) preserves the existing
    offset*cadence computation unchanged.
    """
    fetch_latest = getattr(weather_forecast_store, "fetch_latest_cycle_time", None)
    if not callable(fetch_latest):
        return False

    latest_cycle_time = cast("UtcDatetime | None", fetch_latest(forecast_source))
    max_age_hours = (
        staleness_max_age_hours
        if staleness_max_age_hours is not None
        else expected_delivery_offset_hours * _NWP_CADENCE_HOURS
    )
    if latest_cycle_time is None:
        detail: dict[str, object] = {
            "last_grid_age_hours": None,
            "expected_offset_hours": expected_delivery_offset_hours,
        }
    else:
        age_hours = (checked_at - latest_cycle_time).total_seconds() / 3600.0
        if age_hours <= max_age_hours:
            return False
        detail = {
            "last_grid_age_hours": round(age_hours, 3),
            "expected_offset_hours": expected_delivery_offset_hours,
        }

    log.error("nwp.grid_stale", **detail)
    _append_pipeline_health_record(
        pipeline_health_store,
        check_type=PipelineCheckType.NWP_DELIVERY,
        checked_at=checked_at,
        status=PipelineHealthStatus.CRITICAL,
        subject="nwp_grid",
        detail=detail,
        cycle_time=cycle_time,
    )
    return True


class _StatusBearingAssignment(Protocol):
    """Structural shape shared by `ModelAssignment` and `GroupModelAssignment`."""

    status: ModelAssignmentStatus


_AssignmentT = TypeVar("_AssignmentT", bound=_StatusBearingAssignment)


def _active_only(assignments: list[_AssignmentT]) -> list[_AssignmentT]:
    """Filter station/group model-assignments down to ACTIVE only.

    This is the single shared definition of "active-only" for the
    operational (forecasting / input-assembly / alert-priority) consumers.
    It must NOT be applied to `_check_fallback_priority_drift`, which is a
    Plan 100 all-status DB-drift detector by design.
    """
    return [a for a in assignments if a.status == ModelAssignmentStatus.ACTIVE]


def _compute_required_snow(
    active_model_assignments: dict[StationId, list],
    models: dict[ModelId, ForecastModel],
) -> dict[StationId, frozenset[str]]:
    """Per-station required future-snow variables (Plan 145 D3.1, station-level v1).

    Built from ACTIVE station assignments only (`active_model_assignments`, already
    filtered by `_active_only`) and their resolved models' OWN
    `future_dynamic_features`, intersected with `SNOW_CANONICAL_PARAMETERS`. A
    station contributes NOTHING (no map entry) unless >=1 active assignment
    resolves to a model requiring >=1 snow variable — this IS the opt-in gate (no
    per-HRU "JSNOW subscribed" config flag exists): an inactive assignment or an
    unresolved/missing model contributes nothing, and a station whose only snow
    need is `past_dynamic_features` (the antecedent channel, Plan 146) is excluded
    too. Group-model snow scoping is a deferred follow-up (group assignments only
    resolve in Phase B2, structurally after this pre-fetch computation).
    """
    required: dict[StationId, frozenset[str]] = {}
    for station_id, assignments in active_model_assignments.items():
        needed: set[str] = set()
        for assignment in assignments:
            model = models.get(assignment.model_id)
            if model is None:
                continue
            needed |= model.data_requirements.future_dynamic_features & (
                SNOW_CANONICAL_PARAMETERS
            )
        if needed:
            required[station_id] = frozenset(needed)
    return required


def _check_fallback_priority_drift(
    model_assignments: dict[StationId, list],
    group_store: object | None,
) -> bool:
    drifted: list[dict[str, object]] = [
        {
            "scope": "station",
            "subject": str(station_id),
            "model_id": str(assignment.model_id),
            "priority": assignment.priority,
        }
        for station_id, assignments in model_assignments.items()
        for assignment in assignments
        if assignment.model_id in FALLBACK_MODEL_IDS
        and assignment.priority < FALLBACK_PRIORITY_THRESHOLD
    ]

    if group_store is not None:
        typed_group_store = cast("StationGroupStore", group_store)
        for model_id in FALLBACK_MODEL_IDS:
            for group in typed_group_store.fetch_groups_for_model(model_id):
                for assignment in typed_group_store.fetch_group_model_assignments(
                    group.id
                ):
                    if (
                        assignment.model_id == model_id
                        and assignment.priority < FALLBACK_PRIORITY_THRESHOLD
                    ):
                        drifted.append(
                            {
                                "scope": "group",
                                "subject": str(group.id),
                                "model_id": str(model_id),
                                "priority": assignment.priority,
                            }
                        )

    if not drifted:
        return False
    log.error("forecast_cycle.fallback_priority_drift", assignments=drifted)
    return True


def _forecast_cycle_health(
    *,
    stations_attempted: int,
    stations_failed: int,
    alert_suppressed: bool,
    nwp_grid_stale: bool,
    fallback_priority_drift: bool,
    snow_unavailable: bool = False,
) -> ForecastCycleHealth:
    if stations_attempted > 0 and stations_failed >= stations_attempted:
        return ForecastCycleHealth.FAILED
    if (
        stations_failed > 0
        or alert_suppressed
        or nwp_grid_stale
        or fallback_priority_drift
        or snow_unavailable
    ):
        return ForecastCycleHealth.DEGRADED
    return ForecastCycleHealth.HEALTHY


# ---------------------------------------------------------------------------
# Phase A task — fetch NWP and store weather records
# ---------------------------------------------------------------------------


@task(
    name="fetch-nwp-forcing",
    persist_result=False,
    log_prints=False,
    task_run_name="fetch-nwp-{cycle_time:%Y-%m-%dT%H}",
    cache_policy=NO_CACHE,
)
def _fetch_nwp_task(
    adapter: WeatherForecastSource,
    station_configs: list[StationWeatherSource],
    cycle_time: UtcDatetime,
    weather_forecast_store: object,
    clock: Callable[[], UtcDatetime],
    grid_store: NwpGridStore | None = None,
    grid_extractor: GridExtractor | None = None,
    station_basins: dict[StationId, Basin] | None = None,
    grid_archive_base_path: str | None = None,
    pipeline_health_store: object | None = None,
    # NOTE: pipeline_health_store is passed by reference (thread-based task
    # runner). If the runner is ever switched to process-based execution (e.g.
    # for task.map parallelisation deferred from Phase 8), this and the other
    # object params (weather_forecast_store, grid_store) would fail to
    # serialize at .submit() time — revisit all of them at that point.
    required_snow: Mapping[StationId, frozenset[str]] | None = None,
) -> _NwpFetchOutcome | None:
    """Fetch NWP forecast and store weather records.

    Returns a ``_NwpFetchOutcome`` (carrying ``cycle_time`` and the adapter's
    ``fallback_used`` fact) on success OR when no extraction occurred (no
    matching sources / no extractor configured — both considered successful
    no-op NWP phases, because the downstream per-station forecast step does
    not require NWP input for models with zero NWP features).

    Returns ``None`` only when a true failure occurred (adapter raise,
    extraction raise, store raise, unexpected return type). The caller
    treats ``None`` as a flow-fatal abort condition.

    ``required_snow`` (Plan 145 D3.1/D6) is the pre-computed per-station
    required-future-snow-variable map. When the adapter satisfies
    ``SnowForecastSource`` (capability-gated — never an
    ``isinstance(RecapGatewayForecastAdapter)`` import) AND >=1 station in
    ``station_configs`` has a non-empty entry, the snow-forecast channel is
    fetched too, under the SAME resolved cycle the IFS fetch used (D4 — never a
    second ``_resolve_effective_cycle`` probe), stored, and folded into the
    returned outcome's ``snow_unavailable`` flag (D3.2c). An adapter that does
    NOT satisfy the capability, or a batch with no snow-requiring station,
    skips this entirely — zero ``snow.forecast`` calls, unchanged outcome.
    """
    from sapphire_flow.preprocessing.converters import (
        basin_avg_to_records,
        point_forecast_to_records,
    )
    from sapphire_flow.types.weather import (
        BasinAverageForecast,
        GriddedForecast,
        PointForecast,
    )

    t0 = time.perf_counter()
    try:
        result = adapter.fetch_forecasts(station_configs, cycle_time)
    except DiskSoftLimitError as exc:
        # Plan 105 D2: disk below soft threshold — degrade to runoff-only for
        # this cycle (native/fallback models still forecast). DiskSoftLimitError
        # subclasses AdapterError directly (NOT NoCycleAvailableError) so this
        # clause MUST explicitly return the runoff-only outcome.
        log.warning(
            "nwp.disk_soft_limit",
            path=exc.path,
            free_gb=exc.free_gb,
            threshold_gb=exc.threshold_gb,
            subject=exc.subject,
        )
        _append_pipeline_health_record(
            pipeline_health_store,
            check_type=PipelineCheckType.DISK_USAGE,
            checked_at=clock(),
            status=PipelineHealthStatus.WARNING,
            subject=exc.subject,
            detail={
                "path": exc.path,
                "free_gb": exc.free_gb,
                "threshold_gb": exc.threshold_gb,
            },
            cycle_time=cycle_time,
        )
        return _NwpFetchOutcome(
            cycle_time=cycle_time, fallback_used=False, nwp_unavailable=True
        )
    except DiskHardLimitError as exc:
        # Plan 105 D2: disk below hard threshold — fail-closed (CRITICAL record,
        # return None so the existing nwp_outcome is None abort at ~:1210 fires).
        log.error(
            "nwp.disk_hard_limit",
            path=exc.path,
            free_gb=exc.free_gb,
            threshold_gb=exc.threshold_gb,
            subject=exc.subject,
        )
        _append_pipeline_health_record(
            pipeline_health_store,
            check_type=PipelineCheckType.DISK_USAGE,
            checked_at=clock(),
            status=PipelineHealthStatus.CRITICAL,
            subject=exc.subject,
            detail={
                "path": exc.path,
                "free_gb": exc.free_gb,
                "threshold_gb": exc.threshold_gb,
            },
            cycle_time=cycle_time,
        )
        return None
    except NoCycleAvailableError as exc:
        # Plan 090 D3: no adequate cycle within the fallback budget. This is NOT
        # a flow-fatal failure — signal NWP-unavailable so the caller falls to
        # runoff-only for THIS cycle (native/fallback models still forecast).
        log.warning("nwp.no_cycle_available", error=str(exc))
        return _NwpFetchOutcome(
            cycle_time=cycle_time, fallback_used=False, nwp_unavailable=True
        )
    except RecapConfigurationError as exc:
        # Plan 082 Task 2G: config/metadata error (HRU/variable rejected) —
        # HARD-ABORT, not degrade. Distinct from the generic catch-all so it
        # is recorded (and alertable) rather than silently swallowed.
        log.error("nwp.recap_config_error", error=str(exc), field=exc.field)
        _append_pipeline_health_record(
            pipeline_health_store,
            check_type=PipelineCheckType.NWP_DELIVERY,
            checked_at=clock(),
            status=PipelineHealthStatus.CRITICAL,
            subject="recap_gateway",
            detail={"reason": "config_error", "field": exc.field},
            cycle_time=cycle_time,
        )
        return None
    except GatewayResolutionError as exc:
        # Plan 082 Task 2G: every station in the batch was unmappable to a
        # Gateway polygon — a caller/config error, HARD-ABORT.
        log.error("nwp.recap_all_unmappable", error=str(exc))
        _append_pipeline_health_record(
            pipeline_health_store,
            check_type=PipelineCheckType.NWP_DELIVERY,
            checked_at=clock(),
            status=PipelineHealthStatus.CRITICAL,
            subject="recap_gateway",
            detail={"reason": "all_unmappable"},
            cycle_time=cycle_time,
        )
        return None
    except RecapAuthError as exc:
        # Plan 082 Task 2G: Gateway rejected the request as unauthorized —
        # HARD-ABORT (an expired/misconfigured API key needs operator action,
        # not a per-station skip).
        log.error("nwp.recap_auth_error", error=str(exc), status_code=exc.status_code)
        _append_pipeline_health_record(
            pipeline_health_store,
            check_type=PipelineCheckType.NWP_DELIVERY,
            checked_at=clock(),
            status=PipelineHealthStatus.CRITICAL,
            subject="recap_gateway",
            detail={"reason": "auth", "status_code": exc.status_code},
            cycle_time=cycle_time,
        )
        return None
    except RecapDataUnavailableError as exc:
        # Plan 082 Task 2G: Gateway reports the requested cycle's source data
        # is not yet published — retriable, matching the NoCycleAvailableError
        # precedent: degrade to runoff-only for THIS cycle.
        log.warning("nwp.recap_data_unavailable", error=str(exc), code=exc.code)
        _append_pipeline_health_record(
            pipeline_health_store,
            check_type=PipelineCheckType.NWP_DELIVERY,
            checked_at=clock(),
            status=PipelineHealthStatus.WARNING,
            subject="recap_gateway",
            detail={"reason": "source_data_missing"},
            cycle_time=cycle_time,
        )
        return _NwpFetchOutcome(
            cycle_time=cycle_time, fallback_used=False, nwp_unavailable=True
        )
    except Exception as exc:
        log.error("nwp.fetch_failed", error=str(exc))
        return None

    result_object: object = result
    if isinstance(result_object, GriddedForecast):
        # Step 1.2: Archive raw grid to Zarr (non-fatal — archiving is auxiliary)
        if grid_store is not None and grid_archive_base_path is not None:
            archive_t0 = time.perf_counter()
            try:
                grid_store.archive(result_object, Path(grid_archive_base_path))
            except Exception as exc:
                log.warning(
                    "nwp.archive_failed",
                    nwp_source=result_object.nwp_source,
                    cycle_time=str(result_object.cycle_time),
                    error=str(exc),
                )
            else:
                log.info(
                    "nwp.archive_completed",
                    nwp_source=result_object.nwp_source,
                    duration_ms=round((time.perf_counter() - archive_t0) * 1000, 1),
                )

        # Step 1.3: Extract basin averages
        if grid_extractor is None:
            log.warning(
                "nwp.extraction_skipped", reason="grid_extractor_not_configured"
            )
            # Provenance carries the adapter-RESOLVED published cycle (which may
            # be an older fallback cycle), never the nominal request. No records
            # were stored on this no-extraction path, but the reference time must
            # still reflect the true NWP cycle so age is not understated.
            return _NwpFetchOutcome(
                cycle_time=result_object.cycle_time,
                fallback_used=result_object.fallback_used,
            )

        # Filter configs to only the FORECAST bindings matching this grid's NWP
        # source. A REANALYSIS binding that happens to share the same
        # nwp_source string must never be handed to the forecast extractor.
        configs_for_source = [
            ws
            for ws in station_configs
            if ws.nwp_source == result_object.nwp_source
            and ws.role == WeatherSourceRole.FORECAST
        ]
        if not configs_for_source:
            log.warning(
                "nwp.extraction_skipped",
                reason="no_matching_sources",
                nwp_source=result_object.nwp_source,
            )
            return _NwpFetchOutcome(
                cycle_time=result_object.cycle_time,
                fallback_used=result_object.fallback_used,
            )

        extract_t0 = time.perf_counter()
        try:
            # Tag extracted basin-average records with the adapter-RESOLVED
            # published cycle (result_object.cycle_time), NOT the nominal
            # request. This keeps the stored records, the Phase-B readback, and
            # the provenance reference time all on the same cycle — a fallback
            # forecast is stored, read back, and reported at the true older
            # cycle, so the station is not skipped and NWP age is not understated.
            extracted = grid_extractor.extract(
                grid=result_object.values,
                configs=configs_for_source,
                basins=station_basins or {},
                cycle_time=result_object.cycle_time,
                nwp_source=result_object.nwp_source,
            )
        except Exception as exc:
            log.error(
                "extraction.failed",
                nwp_source=result_object.nwp_source,
                cycle_time=str(result_object.cycle_time),
                error=str(exc),
            )
            return None

        # Step 1.4: Convert to records and store
        all_records = []
        for station_id, forecast in extracted.items():
            if isinstance(forecast, BasinAverageForecast):
                all_records.extend(
                    basin_avg_to_records(station_id, forecast, clock, uuid4)
                )
            else:
                # ElevationBandForecast — deferred to v1 (Nepal)
                log.warning(
                    "nwp.unknown_forecast_type",
                    station_id=str(station_id),
                    type=type(forecast).__name__,
                )

        if all_records:
            weather_forecast_store.store_weather_forecasts(all_records)  # type: ignore[union-attr]

        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        log.info(
            "nwp.fetch_completed",
            records_stored=len(all_records),
            stations=len(extracted),
            extraction_duration_ms=round((time.perf_counter() - extract_t0) * 1000, 1),
            duration_ms=duration_ms,
        )
        return _NwpFetchOutcome(
            cycle_time=result_object.cycle_time,
            fallback_used=result_object.fallback_used,
        )

    if not isinstance(result_object, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        log.error("nwp.unexpected_return_type", type=type(result_object).__name__)
        return None

    all_records = []
    for station_id, forecast in result_object.items():
        if isinstance(forecast, PointForecast):
            all_records.extend(
                point_forecast_to_records(station_id, forecast, clock, uuid4)
            )
        elif isinstance(forecast, BasinAverageForecast):
            all_records.extend(basin_avg_to_records(station_id, forecast, clock, uuid4))
        else:
            log.warning(
                "nwp.unknown_forecast_type",
                station_id=str(station_id),
                type=type(forecast).__name__,
            )

    if all_records:
        weather_forecast_store.store_weather_forecasts(all_records)  # type: ignore[union-attr]

    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    log.info(
        "nwp.fetch_completed",
        records_stored=len(all_records),
        stations=len(result_object),
        duration_ms=duration_ms,
    )
    # Records are persisted under each forecast's own cycle_time (a pre-extracted
    # adapter may have snapped/fallen back to a published cycle). Report that
    # resolved cycle so Phase B's readback + provenance stay consistent with the
    # stored records (else the forecast is skipped / mis-recorded as the request).
    resolved_cycle = (
        next(iter(result_object.values())).cycle_time if result_object else cycle_time
    )
    # Provenance (Codex round 2 Finding): a pre-extracted/dict adapter (Recap)
    # may have walked back to an OLDER published cycle when the nominal cycle was
    # unpublished. That is a FALLBACK, not PRIMARY — the downstream loop maps
    # `fallback_used` to `NwpCycleSource`. Detect it by comparing the resolved
    # cycle against the nominal cycle, BOTH floored to the IFS publication
    # cadence the same way `resolve_latest_cycle` floors, so a non-cadence-
    # aligned nominal request (e.g. 12:30) is never a false-positive fallback.
    from sapphire_flow.adapters.recap_gateway import floor_to_ifs_cadence

    fallback_used = floor_to_ifs_cadence(resolved_cycle) != floor_to_ifs_cadence(
        cycle_time
    )

    # Plan 145 D1/D3/D4/D6: capability-gated, station-scoped snow-forecast fetch,
    # riding the SAME resolved cycle the IFS fetch just used (never a second
    # `_resolve_effective_cycle` probe). A non-capable adapter (MeteoSwiss/replay/
    # an ordinary injected WeatherForecastSource) or a batch with zero
    # snow-requiring stations skips this entirely -- zero `snow.forecast` calls,
    # unchanged outcome. Any snow-boundary failure (including one NOT already
    # contained per-(hru,variable) inside `fetch_snow_forecast`) degrades this
    # cycle's snow channel only -- it never aborts the whole NWP phase, since a
    # snow outage must never blind non-snow models for the station (Problem §3).
    snow_unavailable = False
    if required_snow and isinstance(adapter, SnowForecastSource):
        snow_station_ids = frozenset(required_snow)
        scoped_snow_configs = [
            ws for ws in station_configs if ws.station_id in snow_station_ids
        ]
        # A station requiring snow but with NO matching forecast binding in
        # this batch (config gap — e.g. no active FORECAST-role weather
        # source) must not silently degrade to HEALTHY: it never got a
        # chance to fetch what it needs. Surface it as snow_unavailable and
        # log the affected stations (review fold-in — minor finding).
        bound_station_ids = frozenset(ws.station_id for ws in scoped_snow_configs)
        unbound_required = snow_station_ids - bound_station_ids
        if unbound_required:
            log.warning(
                "nwp.snow_required_station_missing_binding",
                station_ids=[str(s) for s in sorted(unbound_required, key=str)],
            )
            snow_unavailable = True
        if scoped_snow_configs:
            try:
                # required_snow is threaded through so the adapter can scope its
                # OWN Gateway calls to the variables each station actually needs
                # (review fold-in) — a swe-only station must never trigger an
                # hs/rof call whose unavailability would falsely degrade it.
                snow_result = adapter.fetch_snow_forecast(
                    scoped_snow_configs, resolved_cycle, required_snow=required_snow
                )
            except Exception as exc:  # snow-scoped guard: never cycle-fatal
                log.error("nwp.snow_fetch_failed", error=str(exc))
                snow_unavailable = True
            else:
                snow_records = []
                for station_id, forecast in snow_result.forecasts.items():
                    if isinstance(forecast, BasinAverageForecast):
                        snow_records.extend(
                            basin_avg_to_records(station_id, forecast, clock, uuid4)
                        )
                    else:
                        log.warning(
                            "nwp.snow_unknown_forecast_type",
                            station_id=str(station_id),
                            type=type(forecast).__name__,
                        )
                if snow_records:
                    weather_forecast_store.store_weather_forecasts(  # type: ignore[union-attr]
                        snow_records
                    )
                # OR (never overwrite) — an unbound-required-station gap
                # detected above must survive even when the BOUND stations'
                # fetch fully succeeds.
                snow_unavailable = snow_unavailable or bool(snow_result.unavailable)
                log.info(
                    "nwp.snow_fetch_completed",
                    records_stored=len(snow_records),
                    stations=len(snow_result.forecasts),
                    unavailable_hru_count=len(snow_result.unavailable),
                )

    return _NwpFetchOutcome(
        cycle_time=resolved_cycle,
        fallback_used=fallback_used,
        snow_unavailable=snow_unavailable,
    )


# ---------------------------------------------------------------------------
# Step 1.6 task — fetch latest observation timestamps
# ---------------------------------------------------------------------------


@task(
    name="fetch-observation-timestamps",
    log_prints=False,
    task_run_name="fetch-obs-ts",
    cache_policy=NO_CACHE,
)
def _fetch_obs_timestamps_task(
    obs_store: object,
    stations: list[StationConfig],
) -> dict[StationId, UtcDatetime | None]:
    result: dict[StationId, UtcDatetime | None] = {}
    for station in stations:
        for param in station.measured_parameters or frozenset():
            ts = obs_store.fetch_latest_timestamp(station.id, param)  # type: ignore[union-attr]
            if ts is not None and (
                station.id not in result or result[station.id] is None
            ):
                result[station.id] = ts
                break
        if station.id not in result:
            result[station.id] = None
    return result


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------


def _resolve_forecast_cycle_run_name() -> str:
    params = prefect_runtime.flow_run.parameters or {}  # pyright: ignore[reportAttributeAccessIssue]
    cycle_time = params.get("cycle_time")
    if cycle_time is None:
        cycle_time = prefect_runtime.flow_run.scheduled_start_time  # pyright: ignore[reportAttributeAccessIssue]
    if isinstance(cycle_time, str):
        try:
            cycle_time = datetime.fromisoformat(cycle_time)
        except ValueError:
            return "forecast-cycle"
    if cycle_time is None:
        return "forecast-cycle"
    return f"forecast-{cycle_time:%Y-%m-%dT%H}"


@flow(
    name="forecast-cycle",
    log_prints=False,
    flow_run_name=_resolve_forecast_cycle_run_name,
)
def run_forecast_cycle_flow(
    station_store: object = None,
    obs_store: object = None,
    weather_forecast_store: object = None,
    forecast_store: object = None,
    model_state_store: object = None,
    artifact_store: object = None,
    alert_store: object = None,
    pipeline_health_store: object | None = None,
    baseline_store: object = None,
    basin_store: object = None,
    group_store: object | None = None,
    forcing_store: object = None,
    adapter: object = None,
    models: object | None = None,
    config: object | None = None,
    qc_rules: object | None = None,
    clock: object | None = None,
    rng: object | None = None,
    grid_store: object | None = None,
    grid_extractor: object | None = None,
    cycle_time: str | None = None,
    # Plan 082 Task 2D: §5a-table-backed store for the Recap Gateway
    # forecast dispatch resolver. Unused (and unbuilt) on Swiss deployments.
    gateway_polygon_store: object | None = None,
    # Plan 082 Task 2D: injectable recap-dg-client RecapClient, for tests.
    # None on the production path constructs a real RecapClient from config.
    recap_client: object | None = None,
    # Plan 035 Task 4: optional rating-curve store. When None (v0 — callers omit
    # it, and the production bootstrap does not inject it) forecast curve-binding
    # is a pure no-op. A v1 caller passes a RatingCurveStore to enable binding.
    rating_curve_store: object | None = None,
) -> ForecastCycleResult:
    flow_t0 = time.perf_counter()

    created_http_client: Any = None
    try:
        station_store = cast("StationStore | None", station_store)
        obs_store = cast("ObservationStore | None", obs_store)
        weather_forecast_store = cast(
            "WeatherForecastStore | None", weather_forecast_store
        )
        forecast_store = cast("ForecastStore | None", forecast_store)
        model_state_store = cast("ModelStateStore | None", model_state_store)
        artifact_store = cast("ModelArtifactStore | None", artifact_store)
        alert_store = cast("AlertStore | None", alert_store)
        baseline_store = cast("ClimBaselineStore | None", baseline_store)
        basin_store = cast("BasinStore | None", basin_store)
        forcing_store = cast("HistoricalForcingStore | None", forcing_store)
        adapter = cast("WeatherForecastSource | None", adapter)
        models = cast("dict[ModelId, ForecastModel] | None", models)
        config = cast("DeploymentConfig | None", config)
        qc_rules = cast("ForecastQcRuleSet | None", qc_rules)
        clock = cast("Callable[[], UtcDatetime] | None", clock)
        rng = cast("random.Random | None", rng)
        grid_store = cast("NwpGridStore | None", grid_store)
        grid_extractor = cast("GridExtractor | None", grid_extractor)
        gateway_polygon_store = cast(
            "GatewayPolygonBindingStoreLike | None", gateway_polygon_store
        )

        if clock is None:
            clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731
        if rng is None:
            rng = random.Random()

        # --- Production setup ---
        _conn: object = None
        if station_store is None:
            from sapphire_flow.flows._db import setup_production_stores

            database_url = os.environ["DATABASE_URL"]
            _conn, stores = setup_production_stores(database_url)
            station_store = cast("StationStore", stores["station_store"])
            obs_store = cast("ObservationStore", stores["obs_store"])
            weather_forecast_store = cast(
                "WeatherForecastStore", stores["weather_forecast_store"]
            )
            forecast_store = cast("ForecastStore", stores["forecast_store"])
            model_state_store = cast("ModelStateStore", stores["model_state_store"])
            artifact_store = cast("ModelArtifactStore", stores["artifact_store"])
            alert_store = cast("AlertStore", stores["alert_store"])
            pipeline_health_store = stores["pipeline_health_store"]
            baseline_store = cast("ClimBaselineStore", stores["baseline_store"])
            basin_store = cast("BasinStore", stores["basin_store"])
            group_store = stores["group_store"]
            forcing_store = cast("HistoricalForcingStore", stores["forcing_store"])
            gateway_polygon_store = cast(
                "GatewayPolygonBindingStoreLike", stores["gateway_polygon_store"]
            )

        if config is None:
            config_path = os.environ.get("SAPPHIRE_CONFIG")
            if config_path is not None:
                from sapphire_flow.config.deployment import load_config

                config = load_config(config_path)
            else:
                from sapphire_flow.config.deployment import DeploymentConfig

                config = DeploymentConfig(max_retention_days=600)

        config_path_for_adapter = os.environ.get("SAPPHIRE_CONFIG")
        # The grid_extractor selector is honored whether or not an adapter is
        # injected, via a lightweight read that does NOT validate MeteoSwiss-only
        # fields. Full adapter-config validation (MeteoSwiss-only fields) is
        # gated on `adapter is None` so an injected adapter bypasses it.
        grid_extractor_choice = _load_grid_extractor_choice()
        nwp_enabled = adapter is not None
        expected_delivery_offset_hours = _load_expected_delivery_offset_hours()
        # Plan 082 Task 2G: which forecast_source the grid-staleness check
        # queries `weather_forecasts` for. Defaults to the MeteoSwiss ICON
        # source (an injected adapter, e.g. tests, keeps this default).
        active_forecast_source = _ICON_NWP_SOURCE
        # Codex review Finding 2: for Recap this OVERRIDES
        # expected_delivery_offset_hours * 6h with the configured
        # RecapGatewayConfig.staleness_threshold_hours directly (None keeps
        # the MeteoSwiss offset*cadence computation).
        recap_staleness_max_age_hours: float | None = None
        if adapter is None:
            weather_forecast_config = _load_weather_forecast_adapter_config()
            nwp_enabled = weather_forecast_config.enabled
            if weather_forecast_config.type == "recap_gateway":
                active_forecast_source = _IFS_NWP_SOURCE
                if weather_forecast_config.enabled:
                    recap_staleness_max_age_hours = (
                        _load_recap_staleness_threshold_hours(config_path_for_adapter)
                    )
            expected_delivery_offset_hours = (
                weather_forecast_config.expected_delivery_offset_hours
            )
            if weather_forecast_config.require_nwp and not nwp_enabled:
                raise ConfigurationError(
                    "SAPPHIRE_REQUIRE_NWP is set but "
                    "[adapters.weather_forecast].enabled is false"
                )
            if (
                weather_forecast_config.enabled
                and weather_forecast_config.type == "recap_gateway"
            ):
                adapter = _build_recap_forecast_adapter(
                    config_path=config_path_for_adapter,
                    gateway_polygon_store=gateway_polygon_store,
                    recap_client=recap_client,
                )
            elif weather_forecast_config.enabled:
                import httpx

                from sapphire_flow.adapters.meteoswiss_nwp import MeteoSwissNwpAdapter

                if (
                    weather_forecast_config.stac_base_url is None
                    or weather_forecast_config.stac_collection is None
                    or weather_forecast_config.scratch_path is None
                ):
                    # Pyright narrowing: unreachable; helper validates enabled=true.
                    raise ConfigurationError(
                        "MeteoSwiss NWP adapter config is enabled but incomplete"
                    )
                created_http_client = httpx.Client(
                    timeout=httpx.Timeout(
                        connect=10.0,
                        read=300.0,
                        write=None,
                        pool=5.0,
                    )
                )
                adapter = MeteoSwissNwpAdapter(
                    stac_base_url=weather_forecast_config.stac_base_url,
                    stac_collection=weather_forecast_config.stac_collection,
                    scratch_path=weather_forecast_config.scratch_path,
                    http_client=created_http_client,
                    max_fallback_steps=math.ceil(
                        config.nwp_max_fallback_age_hours / 6.0
                    ),
                    max_files=weather_forecast_config.max_files,
                    cycle_min_age_minutes=config.nwp_cycle_min_age_minutes,
                    # Plan 105 D2: disk-guard thresholds from config; archive path
                    # from DeploymentConfig (NOT _WeatherForecastAdapterConfig).
                    disk_guard_scratch_soft_gb=weather_forecast_config.disk_guard_scratch_soft_gb,
                    disk_guard_scratch_hard_gb=weather_forecast_config.disk_guard_scratch_hard_gb,
                    disk_guard_archive_soft_gb=weather_forecast_config.disk_guard_archive_soft_gb,
                    disk_guard_archive_hard_gb=weather_forecast_config.disk_guard_archive_hard_gb,
                    nwp_grid_archive_path=Path(config.nwp_grid_archive_base_path)
                    if config.nwp_grid_archive_base_path is not None
                    else None,
                )
            elif config_path_for_adapter is None:
                log.warning(
                    "forecast_cycle.nwp_disabled_missing_config",
                    mode="runoff_only",
                    reason="sapphire_config_unset",
                )
        runoff_only_mode = not nwp_enabled

        if qc_rules is None:
            qc_rules = _load_forecast_qc_rules()

        if models is None:
            from sapphire_flow.services.model_registry import discover_models

            models = discover_models()

        build_grid = nwp_enabled and config.nwp_grid_archive_base_path is not None
        if build_grid:
            if grid_store is None:
                from sapphire_flow.store.zarr_nwp_grid_store import ZarrNwpGridStore

                grid_store = ZarrNwpGridStore()
            if grid_extractor is None:
                if grid_extractor_choice == "exactextract":
                    from sapphire_flow.preprocessing.exact_extract_grid_extractor import (  # noqa: E501
                        ExactExtractGridExtractor,
                    )

                    grid_extractor = ExactExtractGridExtractor()
                else:
                    from sapphire_flow.preprocessing.mesh_basin_extractor import (
                        MeshBasinExtractor,
                    )

                    grid_extractor = MeshBasinExtractor()

        if obs_store is None:
            raise ConfigurationError("obs_store is required but was not provided")
        if weather_forecast_store is None:
            raise ConfigurationError(
                "weather_forecast_store is required but was not provided"
            )
        if forecast_store is None:
            raise ConfigurationError("forecast_store is required but was not provided")
        if model_state_store is None:
            raise ConfigurationError(
                "model_state_store is required but was not provided"
            )
        if artifact_store is None:
            raise ConfigurationError("artifact_store is required but was not provided")
        if baseline_store is None:
            raise ConfigurationError("baseline_store is required but was not provided")
        if basin_store is None:
            raise ConfigurationError("basin_store is required but was not provided")
        if forcing_store is None:
            raise ConfigurationError("forcing_store is required but was not provided")

        resolved_cycle_time: UtcDatetime = _resolve_cycle_time(cycle_time, clock)

        # --- Batch pre-fetch: operational stations ---
        all_stations = station_store.fetch_all_stations(kind=StationKind.RIVER)  # type: ignore[union-attr]
        operational = [
            s for s in all_stations if s.station_status == StationStatus.OPERATIONAL
        ]

        if not operational:
            log.info("forecast_cycle.no_operational_stations")
            return ForecastCycleResult(
                cycle_time=resolved_cycle_time,
                health=ForecastCycleHealth.HEALTHY,
                stations_attempted=0,
                stations_succeeded=0,
                stations_failed=0,
                forecasts_stored=0,
                alerts_checked=False,
                duration_ms=round((time.perf_counter() - flow_t0) * 1000, 1),
                errors=(),
            )

        log.info("forecast_cycle.starting", stations=len(operational))

        # Batch pre-fetch per-station data
        model_assignments: dict[StationId, list] = {
            s.id: station_store.fetch_model_assignments(s.id)  # type: ignore[union-attr]
            for s in operational
        }
        # ALL-status view above stays as-is for `_check_fallback_priority_drift`
        # (Plan 100's locked all-status DB-drift contract). The ACTIVE-only view
        # below is what forecasting, input assembly, and alert-priority consume
        # (Plan 124 — station path must match the group path's active-only rule).
        active_model_assignments: dict[StationId, list] = {
            sid: _active_only(assignments)
            for sid, assignments in model_assignments.items()
        }
        all_thresholds: dict[StationId, list] = {
            s.id: station_store.fetch_thresholds(s.id)  # type: ignore[union-attr]
            for s in operational
        }
        all_baselines: dict[StationId, list] = {}
        for s in operational:
            params = list(s.measured_parameters or frozenset())
            combined: list = []
            for param in params:
                combined.extend(baseline_store.fetch_baselines(s.id, param))  # type: ignore[union-attr]
            all_baselines[s.id] = combined
        water_level_datums_masl: dict[StationId, float | None] = {
            s.id: s.water_level_datum_masl for s in operational
        }

        # Plan 035 Task 4: one batch lookup of the rating curve active at the
        # cycle's issue time, indexed by station. None = feature off (no store),
        # so per-forecast binding at each store site is a pure no-op. Covers both
        # the per-station and per-group store paths (group members ⊆ operational).
        active_rating_curves: dict[StationId, RatingCurve] | None = (
            cast("RatingCurveStore", rating_curve_store).fetch_active_curves_batch_at(
                [s.id for s in operational], resolved_cycle_time
            )
            if rating_curve_store is not None
            else None
        )

        # Build priority index for alert checker (active-only, Plan 124)
        all_priorities: dict[StationId, dict[ModelId, int]] = {
            s.id: {a.model_id: a.priority for a in active_model_assignments[s.id]}
            for s in operational
        }
        # Drift check reads the RAW all-status dict — Plan 100's locked
        # all-status DB-drift contract. Do NOT swap in active_model_assignments.
        fallback_priority_drift = _check_fallback_priority_drift(
            model_assignments,
            group_store,
        )

        # Batch pre-fetch FORECAST bindings (eliminates per-station queries in
        # Phase B). Resolution happens ONCE, up front, before Phase A (which
        # consumes flat_weather_configs and runs first) — with per-station
        # containment. A station whose binding resolution raises
        # ConfigurationError (0 or >=2 FORECAST bindings) is excluded from
        # flat_weather_configs so it cannot poison the shared NWP prefetch for
        # every other station, and is recorded exactly once as failed. Phase B
        # and the group loop below skip already-failed stations instead of
        # re-resolving and re-counting them.
        stations_failed = 0
        errors: list[str] = []
        failed_station_ids: set[StationId] = set()
        forecast_bindings: dict[StationId, StationWeatherSource] = {}
        for s in operational:
            try:
                forecast_bindings[s.id] = station_store.fetch_forecast_binding(s.id)  # type: ignore[union-attr]
            except ConfigurationError as exc:
                log.warning(
                    "forecast_cycle.station_skipped_bad_weather_source_config",
                    station_id=str(s.id),
                    error=str(exc),
                )
                errors.append(f"Bad weather-source config for {s.id}: {exc}")
                stations_failed += 1
                failed_station_ids.add(s.id)

        flat_weather_configs = list(forecast_bindings.values())

        # Build station→basin map for GridExtractor
        station_basins: dict[StationId, Basin] = {}
        for s in operational:
            if s.basin_id is not None:
                basin = basin_store.fetch_basin(s.basin_id)  # type: ignore[union-attr]
                if basin is not None:
                    station_basins[s.id] = basin
                else:
                    log.warning(
                        "nwp.basin_not_found", station_id=s.id, basin_id=s.basin_id
                    )

        # Instantiate reanalysis source for past_dynamic. The hybrid resolver
        # (Plan 072) is opt-in via DeploymentConfig.reanalysis_source; "single"
        # keeps the v0a per-station single-source path.
        from sapphire_flow.adapters.hybrid_reanalysis_factories import (
            select_reanalysis_source,
        )

        forcing_source = select_reanalysis_source(
            forcing_store=forcing_store, mode=config.reanalysis_source
        )

        # Instantiate forecast QC checker
        from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker

        qc_checker = ForecastOutputQualityChecker()

        # Plan 145 D3.1: per-station required future-snow variables, computed
        # BEFORE Phase A submission from the already-loaded ACTIVE assignments +
        # model registry (the flow, not `_fetch_nwp_task`, has both). Threaded
        # into the task below; excludes inactive assignments / unresolved models
        # / stations with no snow-requiring assignment by construction.
        required_snow: dict[StationId, frozenset[str]] = _compute_required_snow(
            active_model_assignments, models
        )

        # --- Phase A: fetch NWP forcing (submit as task) ---
        nwp_future: Any = None
        nwp_outcome: _NwpFetchOutcome | None = None
        # When EVERY operational station failed forecast-binding resolution
        # above, flat_weather_configs is empty. Submitting Phase A against an
        # empty station list reaches adapters (e.g. ReplayNwpAdapter) that
        # raise on empty station_configs; _fetch_nwp_task converts that raise
        # to None, which would otherwise take the fatal-abort return path
        # below and ERASE the per-station failure accounting already
        # recorded (stations_failed / errors / failed_station_ids). Treat
        # this the same as runoff_only_mode from Phase A's point of view: no
        # NWP fetch this cycle, fall through to the normal result path so
        # every operational station is accounted for exactly once.
        skip_nwp_fetch = runoff_only_mode or not flat_weather_configs
        if runoff_only_mode:
            log.info(
                "forecast_cycle.nwp_disabled",
                mode="runoff_only",
                cycle_time=resolved_cycle_time.isoformat(),
            )
        elif not flat_weather_configs:
            log.warning(
                "forecast_cycle.nwp_skipped_no_forecast_bindings",
                cycle_time=resolved_cycle_time.isoformat(),
                stations_failed=stations_failed,
            )
        else:
            nwp_future = _fetch_nwp_task.submit(
                adapter=cast("WeatherForecastSource", adapter),
                station_configs=flat_weather_configs,
                cycle_time=resolved_cycle_time,
                weather_forecast_store=weather_forecast_store,
                clock=clock,
                grid_store=grid_store,
                grid_extractor=grid_extractor,
                station_basins=station_basins,
                grid_archive_base_path=config.nwp_grid_archive_base_path,
                pipeline_health_store=pipeline_health_store,
                required_snow=required_snow,
            )

        # --- Step 1.6: observation timestamps (parallel with Phase A) ---
        obs_ts_future = _fetch_obs_timestamps_task.submit(
            obs_store=obs_store,
            stations=operational,
        )

        # Collect Phase A result
        if not skip_nwp_fetch:
            nwp_outcome = nwp_future.result()
            # Plan 095: bound the nwp_grids disk footprint by pruning grid-cube
            # zarrs from cycles older than the retention window. Age-only; the
            # permanent archive is the extracted values in weather_forecasts. A
            # prune failure must never abort the forecast cycle.
            if (
                nwp_outcome is not None
                and config.nwp_grid_archive_base_path is not None
            ):
                from sapphire_flow.store.zarr_nwp_grid_store import prune_old_cycles

                try:
                    prune_old_cycles(
                        Path(config.nwp_grid_archive_base_path),
                        config.nwp_grid_retention_days,
                        clock,
                    )
                except Exception:
                    log.warning(
                        "forecast_cycle.nwp_grid_prune_failed",
                        base_path=config.nwp_grid_archive_base_path,
                    )
            if nwp_outcome is None:
                log.error("forecast_cycle.nwp_fetch_failed_aborting")
                return ForecastCycleResult(
                    cycle_time=resolved_cycle_time,
                    health=ForecastCycleHealth.FAILED,
                    stations_attempted=0,
                    stations_succeeded=0,
                    stations_failed=0,
                    forecasts_stored=0,
                    alerts_checked=False,
                    duration_ms=round((time.perf_counter() - flow_t0) * 1000, 1),
                    errors=("NWP fetch failed",),
                )

        # Collect Step 1.6 result (we don't block on this — just use it for logging)
        _obs_timestamps: dict[StationId, UtcDatetime | None] = obs_ts_future.result()

        # Plan 090 D3: NWP configured but no adequate cycle this run → treat NWP
        # as unavailable FOR THIS CYCLE and fall to runoff-only (NWP-consuming
        # models produce nothing; native/fallback models still forecast). This is
        # distinct from a genuine fatal error (handled by the abort above).
        nwp_unavailable_runtime = (
            not skip_nwp_fetch
            and nwp_outcome is not None
            and nwp_outcome.nwp_unavailable
        )
        if nwp_unavailable_runtime:
            log.warning(
                "forecast_cycle.nwp_unavailable_runoff_only",
                cycle_time=resolved_cycle_time.isoformat(),
            )
        effective_runoff_only = skip_nwp_fetch or nwp_unavailable_runtime
        nwp_grid_stale = False
        if nwp_enabled:
            nwp_grid_stale = _check_nwp_grid_staleness(
                weather_forecast_store,
                pipeline_health_store,
                expected_delivery_offset_hours=expected_delivery_offset_hours,
                checked_at=clock(),
                cycle_time=resolved_cycle_time,
                forecast_source=active_forecast_source,
                staleness_max_age_hours=recap_staleness_max_age_hours,
            )

        # --- Honest NWP provenance (epic-088 M4) ---
        # Runoff-only (configured OR runtime-unavailable) has no NWP cycle at all
        # → RUNOFF_ONLY + null reference time (NOT a faked clock cycle). With NWP
        # on and available, the adapter's fallback fact decides PRIMARY vs
        # FALLBACK; the resolved cycle time is the reference.
        nwp_cycle_reference_time: UtcDatetime | None
        if effective_runoff_only:
            nwp_cycle_source = NwpCycleSource.RUNOFF_ONLY
            nwp_cycle_reference_time = None
        else:
            assert nwp_outcome is not None  # guarded by the abort above
            nwp_cycle_source = (
                NwpCycleSource.FALLBACK
                if nwp_outcome.fallback_used
                else NwpCycleSource.PRIMARY
            )
            nwp_cycle_reference_time = nwp_outcome.cycle_time

        # Phase-B readback cycle: the NWP records were STORED under the adapter-
        # resolved cycle, so they must be READ BACK under the same cycle or the
        # station is skipped (no records found). ``issue_time`` stays the nominal
        # cycle (the forecast is issued now, whatever NWP cycle it consumed).
        # Runoff-only has no NWP records; keep the nominal cycle there.
        nwp_readback_cycle_time: UtcDatetime = (
            nwp_cycle_reference_time
            if nwp_cycle_reference_time is not None
            else resolved_cycle_time
        )

        # --- Phase B: per-station forecast loop ---
        from sapphire_flow.services.forecast_combination import build_combined_forecasts
        from sapphire_flow.services.operational_inputs import (
            assemble_station_operational_inputs,
            build_superset_requirements,
        )
        from sapphire_flow.services.run_group_forecast import (
            assemble_group_operational_inputs,
            discover_group_runs,
            run_group_forecast,
        )
        from sapphire_flow.services.run_station_forecast import (
            run_all_station_forecasts,
            run_station_forecast,
        )
        from sapphire_flow.types.enums import ModelCombinationStrategy

        # stations_failed / errors accumulate from the up-front forecast-binding
        # resolution above (Phase A containment) as well as this loop.
        stations_succeeded = 0
        forecasts_stored = 0

        # Accumulate for Phase C
        all_ensembles: dict[StationId, dict[ModelId, dict[str, ForecastEnsemble]]] = {}

        for station in operational:
            sid = station.id
            if sid in failed_station_ids:
                # Already recorded as failed during the up-front forecast-binding
                # resolution (Phase A containment) — do not re-resolve or
                # double-count.
                continue
            structlog.contextvars.bind_contextvars(station_id=str(sid))
            station_t0 = time.perf_counter()

            assignments = active_model_assignments[sid]
            if not assignments:
                log.debug("forecast_cycle.no_assignments")
                structlog.contextvars.unbind_contextvars("station_id")
                continue

            # Use time_step from first active assignment (priority-sorted)
            sorted_assignments = sorted(assignments, key=lambda a: a.priority)
            assembly_assignment = sorted_assignments[0]
            if effective_runoff_only:
                assembly_assignment = next(
                    (
                        assignment
                        for assignment in sorted_assignments
                        if (model := models.get(assignment.model_id)) is not None
                        and not model.data_requirements.future_dynamic_features
                    ),
                    sorted_assignments[0],
                )
            time_step: timedelta = assembly_assignment.time_step
            first_model = models.get(assembly_assignment.model_id)
            if first_model is None:
                log.error(
                    "forecast_cycle.station_skipped_model_not_loaded",
                    model_id=str(assembly_assignment.model_id),
                )
                errors.append(
                    f"Configured model {assembly_assignment.model_id} missing for {sid}"
                )
                stations_failed += 1
                structlog.contextvars.unbind_contextvars("station_id")
                continue
            # Assemble inputs ONCE using a SUPERSET of every assigned model's
            # data requirements so heterogeneous model sets (e.g. NWP models
            # needing future forcing alongside native models that declare none)
            # each receive the data they declare. Using only the first (highest-
            # priority) model's requirements starved the others.
            assigned_models = [
                m
                for a in sorted_assignments
                if (m := models.get(a.model_id)) is not None
            ]
            superset_reqs = build_superset_requirements(
                [m.data_requirements for m in assigned_models]
            )
            # When NWP is unavailable (configured runoff-only or adapter exhausted
            # its cycle budget this run), assemble WITHOUT future features so a
            # declared-but-unfetchable NWP requirement does not make assembly
            # return None and starve native/fallback models. The per-model coverage
            # guard then skips NWP-consuming models while native/fallback models
            # still forecast.
            if effective_runoff_only:
                superset_reqs = replace(
                    superset_reqs, future_dynamic_features=frozenset()
                )
            forecast_horizon_steps: int = superset_reqs.forecast_horizon_steps

            assignment_time_steps = {a.time_step for a in sorted_assignments}
            if len(assignment_time_steps) > 1:
                log.warning(
                    "forecast_cycle.heterogeneous_assignment_time_steps",
                    time_steps=[str(ts) for ts in sorted(assignment_time_steps)],
                    used=str(time_step),
                )

            # sid is guaranteed present: failed_station_ids stations are skipped above.
            nwp_source: str = forecast_bindings[sid].nwp_source

            try:
                inputs_result = assemble_station_operational_inputs(
                    station_id=sid,
                    model=first_model,
                    model_id=assembly_assignment.model_id,
                    issue_time=resolved_cycle_time,
                    cycle_time=nwp_readback_cycle_time,
                    nwp_source=nwp_source,
                    forcing_source=forcing_source,
                    weather_forecast_store=weather_forecast_store,  # type: ignore[arg-type]
                    obs_store=obs_store,  # type: ignore[arg-type]
                    station_store=station_store,  # type: ignore[arg-type]
                    basin_store=basin_store,  # type: ignore[arg-type]
                    model_state_store=model_state_store,  # type: ignore[arg-type]
                    clock=clock,
                    forecast_horizon_steps=forecast_horizon_steps,
                    time_step=time_step,
                    requirements_override=superset_reqs,
                )
            except Exception as exc:
                log.warning("forecast_cycle.input_assembly_failed", error=str(exc))
                errors.append(f"Input assembly failed for {sid}: {exc}")
                stations_failed += 1
                structlog.contextvars.unbind_contextvars("station_id")
                continue

            if inputs_result is None:
                log.info("forecast_cycle.station_skipped_no_nwp")
                structlog.contextvars.unbind_contextvars("station_id")
                continue

            inputs, input_metadata = inputs_result

            try:
                if (
                    config.forecast_combination_strategy
                    == ModelCombinationStrategy.PRIMARY
                ):
                    # Existing behaviour: single model with fallback chain
                    fc_result = run_station_forecast(
                        station_id=sid,
                        inputs=inputs,
                        input_metadata=input_metadata,
                        assignments=sorted_assignments,
                        models=models,
                        artifact_store=artifact_store,  # type: ignore[arg-type]
                        qc_checker=qc_checker,
                        qc_rules=qc_rules,
                        qc_overrides=[],
                        baselines=all_baselines[sid],
                        nwp_cycle_reference_time=nwp_cycle_reference_time,
                        nwp_cycle_source=nwp_cycle_source,
                        config=config,
                        clock=clock,
                        id_gen=uuid4,
                        rng=rng,
                        water_level_datum_masl=water_level_datums_masl.get(sid),
                    )

                    if fc_result is None:
                        reason = "all_models_failed"
                        _record_station_dark(
                            pipeline_health_store,
                            station_id=sid,
                            reason=reason,
                            assigned_models=[
                                assignment.model_id for assignment in sorted_assignments
                            ],
                            nwp_enabled=nwp_enabled,
                            checked_at=clock(),
                            cycle_time=resolved_cycle_time,
                        )
                        errors.append(
                            f"Station {sid} produced zero forecasts: {reason}"
                        )
                        stations_failed += 1
                        structlog.contextvars.unbind_contextvars("station_id")
                        continue

                    for fc in fc_result.forecasts:
                        fc = _bind_rating_curve(fc, active_rating_curves)
                        try:
                            forecast_store.store_forecast(fc)  # type: ignore[union-attr]
                            forecasts_stored += 1
                        except Exception as exc:
                            log.warning(
                                "forecast_cycle.store_forecast_failed", error=str(exc)
                            )
                            errors.append(f"Store failed for {sid}: {exc}")

                    if fc_result.new_state is not None:
                        try:
                            model_state_store.store_state(  # type: ignore[union-attr]
                                sid,
                                fc_result.model_id,
                                resolved_cycle_time,
                                fc_result.new_state,
                            )
                        except Exception as exc:
                            log.warning(
                                "forecast_cycle.store_state_failed", error=str(exc)
                            )

                    all_ensembles[sid] = {fc_result.model_id: dict(fc_result.ensembles)}

                else:
                    # Combination mode: run all models and produce a combined forecast.
                    # TODO: use merge_data_requirements() once models with different
                    #       requirements are added. For now all models share the same
                    #       forcing so first-model inputs are sufficient.
                    multi_result = run_all_station_forecasts(
                        station_id=sid,
                        inputs=inputs,
                        input_metadata=input_metadata,
                        assignments=sorted_assignments,
                        models=models,
                        artifact_store=artifact_store,  # type: ignore[arg-type]
                        qc_checker=qc_checker,
                        qc_rules=qc_rules,
                        qc_overrides=[],
                        baselines=all_baselines[sid],
                        nwp_cycle_reference_time=nwp_cycle_reference_time,
                        nwp_cycle_source=nwp_cycle_source,
                        config=config,
                        clock=clock,
                        id_gen=uuid4,
                        rng=rng,
                        water_level_datum_masl=water_level_datums_masl.get(sid),
                    )

                    if multi_result.primary_model_id is None:
                        reason = "all_models_failed"
                        _record_station_dark(
                            pipeline_health_store,
                            station_id=sid,
                            reason=reason,
                            assigned_models=[
                                assignment.model_id for assignment in sorted_assignments
                            ],
                            nwp_enabled=nwp_enabled,
                            checked_at=clock(),
                            cycle_time=resolved_cycle_time,
                        )
                        errors.append(
                            f"Station {sid} produced zero forecasts: {reason}"
                        )
                        stations_failed += 1
                        structlog.contextvars.unbind_contextvars("station_id")
                        continue

                    # Store all individual model forecasts
                    for mid, result in multi_result.results.items():
                        for fc in result.forecasts:
                            fc = _bind_rating_curve(fc, active_rating_curves)
                            try:
                                forecast_store.store_forecast(fc)  # type: ignore[union-attr]
                                forecasts_stored += 1
                            except Exception as exc:
                                log.warning(
                                    "forecast_cycle.store_forecast_failed",
                                    error=str(exc),
                                )
                                errors.append(f"Store failed for {sid}: {exc}")

                        # Persist warm-up state for primary model only
                        if (
                            mid == multi_result.primary_model_id
                            and result.new_state is not None
                        ):
                            try:
                                model_state_store.store_state(  # type: ignore[union-attr]
                                    sid,
                                    mid,
                                    resolved_cycle_time,
                                    result.new_state,
                                )
                            except Exception as exc:
                                log.warning(
                                    "forecast_cycle.store_state_failed", error=str(exc)
                                )

                    # Build and store combined forecast
                    combined_forecasts = build_combined_forecasts(
                        station_id=sid,
                        multi_result=multi_result,
                        strategy=config.forecast_combination_strategy,
                        nwp_cycle_reference_time=nwp_cycle_reference_time,
                        nwp_cycle_source=nwp_cycle_source,
                        clock=clock,
                        uuid_factory=uuid4,
                    )
                    if combined_forecasts:
                        for fc in combined_forecasts:
                            fc = _bind_rating_curve(fc, active_rating_curves)
                            try:
                                forecast_store.store_forecast(fc)  # type: ignore[union-attr]
                                forecasts_stored += 1
                            except Exception as exc:
                                log.warning(
                                    "forecast_cycle.store_forecast_failed",
                                    error=str(exc),
                                )
                                errors.append(f"Store failed for {sid}: {exc}")
                        log.info(
                            "forecast_cycle.combined_forecast_stored",
                            n_models=len(multi_result.combinable_results),
                            strategy=config.forecast_combination_strategy.value,
                        )
                    else:
                        log.warning(
                            "forecast_cycle.combined_forecast_skipped",
                            reason="fewer than 2 combinable models",
                            n_models=len(multi_result.combinable_results),
                        )

                    # Accumulate all models' ensembles for Phase C alerting
                    all_ensembles[sid] = {
                        mid: dict(result.ensembles)
                        for mid, result in multi_result.results.items()
                    }

            except Exception as exc:
                log.warning("forecast_cycle.station_forecast_failed", error=str(exc))
                errors.append(f"Forecast failed for {sid}: {exc}")
                stations_failed += 1
                structlog.contextvars.unbind_contextvars("station_id")
                continue

            stations_succeeded += 1
            duration_ms = round((time.perf_counter() - station_t0) * 1000, 1)
            for mid, param_ensembles in all_ensembles.get(sid, {}).items():
                if not param_ensembles:
                    continue
                primary_ensemble = next(iter(param_ensembles.values()))
                ensemble_size = primary_ensemble.member_count
                lead_time_hours = (
                    primary_ensemble.forecast_horizon_steps
                    * primary_ensemble.time_step.total_seconds()
                    / 3600
                )
                structlog.contextvars.bind_contextvars(model_id=str(mid))
                try:
                    log.info(
                        "forecast.run_completed",
                        duration_ms=duration_ms,
                        ensemble_size=ensemble_size,
                        lead_time_hours=lead_time_hours,
                    )
                finally:
                    structlog.contextvars.unbind_contextvars("model_id")
            structlog.contextvars.unbind_contextvars("station_id")

        # --- Phase B2: per-group forecast loop ---
        if group_store is not None:
            operational_ids = {s.id for s in operational}
            group_produced_pairs: set[tuple[StationId, ModelId]] = set()
            for group, model_id in discover_group_runs(models, group_store):  # type: ignore[arg-type]
                group_t0 = time.perf_counter()
                structlog.contextvars.bind_contextvars(
                    group_id=str(group.id),
                    model_id=str(model_id),
                )
                try:
                    group_assignments = group_store.fetch_group_model_assignments(  # type: ignore[union-attr]
                        group.id
                    )
                    active_assignments = sorted(
                        _active_only(
                            [a for a in group_assignments if a.model_id == model_id]
                        ),
                        key=lambda assignment: assignment.priority,
                    )
                    if not active_assignments:
                        log.info(
                            "forecast_cycle.group_skipped_no_active_assignment",
                        )
                        continue
                    assignment = active_assignments[0]

                    member_ids = [
                        sid
                        for sid in sorted(group.station_ids, key=str)
                        if sid in operational_ids
                    ]
                    dropped_station_ids = sorted(
                        (
                            sid
                            for sid in group.station_ids
                            if sid not in operational_ids
                        ),
                        key=str,
                    )
                    if dropped_station_ids:
                        log.info(
                            "forecast_cycle.group_dropped_non_operational_members",
                            station_ids=[str(sid) for sid in dropped_station_ids],
                        )
                    if not member_ids:
                        log.info("forecast_cycle.group_skipped_no_operational_members")
                        continue

                    duplicate_member_ids = [
                        sid
                        for sid in member_ids
                        if (sid, model_id) in group_produced_pairs
                    ]
                    if duplicate_member_ids:
                        log.warning(
                            "forecast_cycle.group_duplicate_station_model_skipped",
                            station_ids=[str(sid) for sid in duplicate_member_ids],
                        )
                        member_ids = [
                            sid
                            for sid in member_ids
                            if (sid, model_id) not in group_produced_pairs
                        ]
                    if not member_ids:
                        log.warning(
                            "forecast_cycle.group_skipped_duplicate_station_model_members"
                        )
                        continue

                    model = models[model_id]  # type: ignore[index]
                    missing_binding_ids = [
                        sid for sid in member_ids if sid not in forecast_bindings
                    ]
                    if missing_binding_ids:
                        # A member with no valid FORECAST binding already failed
                        # (and was counted once) during the up-front resolution
                        # above — a group forecast needs all its members, so this
                        # group fails too. Do not double-count stations_failed.
                        raise ConfigurationError(
                            f"group {group.id} has member(s) with no valid "
                            f"FORECAST weather-source binding: {missing_binding_ids}"
                        )
                    nwp_source_by_station = {
                        sid: forecast_bindings[sid].nwp_source for sid in member_ids
                    }
                    baselines_by_station = {
                        sid: all_baselines.get(sid, []) for sid in member_ids
                    }
                    restricted_group = replace(group, station_ids=frozenset(member_ids))

                    try:
                        group_inputs_result = assemble_group_operational_inputs(
                            group=restricted_group,
                            model=model,  # type: ignore[arg-type]
                            model_id=model_id,
                            issue_time=resolved_cycle_time,
                            cycle_time=nwp_readback_cycle_time,
                            nwp_source_by_station=nwp_source_by_station,
                            forcing_source=forcing_source,
                            weather_forecast_store=weather_forecast_store,  # type: ignore[arg-type]
                            obs_store=obs_store,  # type: ignore[arg-type]
                            station_store=station_store,  # type: ignore[arg-type]
                            basin_store=basin_store,  # type: ignore[arg-type]
                            model_state_store=model_state_store,  # type: ignore[arg-type]
                            clock=clock,  # type: ignore[arg-type]
                            forecast_horizon_steps=(
                                model.data_requirements.forecast_horizon_steps
                            ),
                            time_step=assignment.time_step,
                        )
                    except StoreError:
                        raise
                    except Exception as exc:
                        log.warning(
                            "forecast_cycle.group_input_assembly_failed",
                            error=str(exc),
                        )
                        errors.append(
                            f"Group input assembly failed for {group.id}: {exc}"
                        )
                        continue

                    if group_inputs_result is None:
                        log.info("forecast_cycle.group_skipped_no_serviceable_stations")
                        continue

                    group_inputs, metadata_by_station = group_inputs_result
                    group_results = run_group_forecast(
                        group=restricted_group,
                        group_inputs=group_inputs,
                        metadata_by_station=metadata_by_station,
                        assignment=assignment,
                        model=model,  # type: ignore[arg-type]
                        artifact_store=artifact_store,  # type: ignore[arg-type]
                        qc_checker=qc_checker,
                        qc_rules=qc_rules,  # type: ignore[arg-type]
                        qc_overrides=[],
                        baselines_by_station=baselines_by_station,
                        nwp_cycle_reference_time=nwp_cycle_reference_time,
                        nwp_cycle_source=nwp_cycle_source,
                        config=config,  # type: ignore[arg-type]
                        clock=clock,  # type: ignore[arg-type]
                        id_gen=uuid4,
                        rng=rng,  # type: ignore[arg-type]
                        water_level_datums_masl=water_level_datums_masl,
                    )

                    for sid, result in group_results.items():
                        for fc in result.forecasts:
                            fc = _bind_rating_curve(fc, active_rating_curves)
                            try:
                                forecast_store.store_forecast(fc)  # type: ignore[union-attr]
                                forecasts_stored += 1
                            except StoreError:
                                raise
                            except Exception as exc:
                                log.warning(
                                    "forecast_cycle.store_forecast_failed",
                                    station_id=str(sid),
                                    error=str(exc),
                                )
                                errors.append(f"Store failed for {sid}: {exc}")

                        if result.new_state is not None:
                            try:
                                model_state_store.store_state(  # type: ignore[union-attr]
                                    sid,
                                    model_id,
                                    resolved_cycle_time,
                                    result.new_state,
                                )
                            except StoreError:
                                raise
                            except Exception as exc:
                                log.warning(
                                    "forecast_cycle.store_state_failed",
                                    station_id=str(sid),
                                    error=str(exc),
                                )

                        all_ensembles.setdefault(sid, {})[model_id] = dict(
                            result.ensembles
                        )
                        all_priorities.setdefault(sid, {})[model_id] = (
                            assignment.priority
                        )
                        group_produced_pairs.add((sid, model_id))

                    log.info(
                        "forecast_cycle.group_completed",
                        stations_forecast=len(group_results),
                        duration_ms=round((time.perf_counter() - group_t0) * 1000, 1),
                    )
                except StoreError:
                    raise
                except Exception as exc:
                    log.warning(
                        "forecast_cycle.group_forecast_failed",
                        error=str(exc),
                    )
                    errors.append(f"Group forecast failed for {group.id}: {exc}")
                    continue
                finally:
                    structlog.contextvars.unbind_contextvars("group_id", "model_id")

        alert_eligible_ensembles, alert_suppressed = (
            _partition_alert_eligible_ensembles(
                all_ensembles,
                models,
                pipeline_health_store,
                checked_at=clock(),
                cycle_time=resolved_cycle_time,
            )
            if all_ensembles
            else ({}, False)
        )

        # --- Phase C: alert checking ---
        alerts_checked = False
        if config.enable_forecast_alerts and alert_eligible_ensembles:
            from sapphire_flow.services.alert_checker import check_station_alerts

            alert_t0 = time.perf_counter()
            try:
                check_station_alerts(
                    all_ensembles=alert_eligible_ensembles,
                    all_thresholds=all_thresholds,
                    danger_levels=config.get_danger_level_definitions(),
                    all_priorities=all_priorities,
                    config=config,
                    alert_store=alert_store,  # type: ignore[arg-type]
                    clock=clock,
                )
                alerts_checked = True
            except Exception as exc:
                log.error("forecast_cycle.alert_check_failed", error=str(exc))
                errors.append(f"Alert check failed: {exc}")
            alert_duration_ms = round((time.perf_counter() - alert_t0) * 1000, 1)
            log.info("alerts.check_completed", duration_ms=alert_duration_ms)

        total_ms = round((time.perf_counter() - flow_t0) * 1000, 1)

        result = ForecastCycleResult(
            cycle_time=resolved_cycle_time,
            health=_forecast_cycle_health(
                stations_attempted=len(operational),
                stations_failed=stations_failed,
                alert_suppressed=alert_suppressed,
                nwp_grid_stale=nwp_grid_stale,
                fallback_priority_drift=fallback_priority_drift,
                snow_unavailable=(
                    nwp_outcome.snow_unavailable if nwp_outcome is not None else False
                ),
            ),
            stations_attempted=len(operational),
            stations_succeeded=stations_succeeded,
            stations_failed=stations_failed,
            forecasts_stored=forecasts_stored,
            alerts_checked=alerts_checked,
            duration_ms=total_ms,
            errors=tuple(errors),
        )

        log.info(
            "forecast_cycle.complete",
            cycle_time=str(resolved_cycle_time),
            stations_attempted=result.stations_attempted,
            stations_succeeded=result.stations_succeeded,
            stations_failed=result.stations_failed,
            health=result.health.value,
            forecasts_stored=result.forecasts_stored,
            alerts_checked=result.alerts_checked,
            duration_ms=result.duration_ms,
        )

        return result
    finally:
        if created_http_client is not None:
            created_http_client.close()
