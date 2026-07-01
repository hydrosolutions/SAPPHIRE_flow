from __future__ import annotations

import math
import os
import random
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import uuid4

import structlog
import structlog.contextvars
from prefect import flow, task
from prefect import runtime as prefect_runtime
from prefect.cache_policies import NO_CACHE

from sapphire_flow.exceptions import ConfigurationError, StoreError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    ModelAssignmentStatus,
    NwpCycleSource,
    SpatialRepresentation,
    StationKind,
    StationStatus,
)

if TYPE_CHECKING:
    from collections.abc import Callable

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
        StationStore,
        WeatherForecastStore,
    )
    from sapphire_flow.types.basin import Basin
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import ForecastQcRuleSet
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.ids import ModelId, StationId
    from sapphire_flow.types.station import StationConfig, StationWeatherSource

log = structlog.get_logger(__name__)

# = MeteoSwissNwpAdapter.NWP_SOURCE. The operational NWP forcing path.
_ICON_NWP_SOURCE = "icon_ch2_eps"


def _select_nwp_source(weather_sources: list[StationWeatherSource]) -> str:
    """Deterministically pick the operational ICON / BASIN_AVERAGE NWP source.

    Independent of ``fetch_weather_sources`` ordering and two-pass so an EXACT
    ICON binding always wins over any other ``BASIN_AVERAGE`` source (e.g. a
    reanalysis binding that is also basin-average): Phase A only stores ICON
    grid records, so selecting a non-ICON basin-average source in Phase B would
    read the wrong ``nwp_source`` and skip the station.

    First pass: any source whose ``nwp_source`` is exactly ICON. Second pass:
    any ``BASIN_AVERAGE`` source (the binding onboarding Step 4b creates). Else
    fall back to the ICON source string.
    """
    for ws in weather_sources:
        if ws.nwp_source == _ICON_NWP_SOURCE:
            return ws.nwp_source
    for ws in weather_sources:
        if ws.extraction_type is SpatialRepresentation.BASIN_AVERAGE:
            return ws.nwp_source
    return _ICON_NWP_SOURCE


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastCycleResult:
    cycle_time: UtcDatetime
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
    stac_base_url: str | None
    stac_collection: str | None
    scratch_path: Path | None
    max_files: int | None
    grid_extractor: str


_GRID_EXTRACTOR_CHOICES: tuple[str, ...] = ("mesh", "exactextract")
_DEFAULT_GRID_EXTRACTOR: Literal["mesh"] = "mesh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_weather_forecast_adapter_config() -> _WeatherForecastAdapterConfig:
    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is None:
        return _WeatherForecastAdapterConfig(
            enabled=False,
            stac_base_url="https://data.geo.admin.ch/api/stac/v1",
            stac_collection="ch.meteoschweiz.ogd-forecasting-icon-ch2",
            scratch_path=Path("/tmp/sapphire_nwp"),
            max_files=None,
            grid_extractor=_DEFAULT_GRID_EXTRACTOR,
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

    enabled_value = weather_forecast.get("enabled", False)
    if not isinstance(enabled_value, bool):
        raise ConfigurationError(
            "[adapters.weather_forecast].enabled must be a TOML boolean"
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

    if enabled_value:
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

    return _WeatherForecastAdapterConfig(
        enabled=enabled_value,
        stac_base_url=stac_base_url if isinstance(stac_base_url, str) else None,
        stac_collection=stac_collection if isinstance(stac_collection, str) else None,
        scratch_path=Path(scratch_path_value)
        if isinstance(scratch_path_value, str)
        else None,
        max_files=max_files_value,
        grid_extractor=grid_extractor_value,
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
) -> UtcDatetime | None:
    """Fetch NWP forecast and store weather records.

    Returns ``cycle_time`` on success OR when no extraction occurred (no
    matching sources / no extractor configured — both considered successful
    no-op NWP phases, because the downstream per-station forecast step does
    not require NWP input for models with zero NWP features).

    Returns ``None`` only when a true failure occurred (adapter raise,
    extraction raise, store raise, unexpected return type). The caller
    treats ``None`` as a flow-fatal abort condition.
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
                    cycle_time=str(cycle_time),
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
            return cycle_time

        # Filter configs to only those matching this grid's NWP source
        configs_for_source = [
            ws for ws in station_configs if ws.nwp_source == result_object.nwp_source
        ]
        if not configs_for_source:
            log.warning(
                "nwp.extraction_skipped",
                reason="no_matching_sources",
                nwp_source=result_object.nwp_source,
            )
            return cycle_time

        extract_t0 = time.perf_counter()
        try:
            extracted = grid_extractor.extract(
                grid=result_object.values,
                configs=configs_for_source,
                basins=station_basins or {},
                cycle_time=cycle_time,
                nwp_source=result_object.nwp_source,
            )
        except Exception as exc:
            log.error(
                "extraction.failed",
                nwp_source=result_object.nwp_source,
                cycle_time=str(cycle_time),
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
        return cycle_time

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
    return cycle_time


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
            baseline_store = cast("ClimBaselineStore", stores["baseline_store"])
            basin_store = cast("BasinStore", stores["basin_store"])
            group_store = stores["group_store"]
            forcing_store = cast("HistoricalForcingStore", stores["forcing_store"])

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
        if adapter is None:
            weather_forecast_config = _load_weather_forecast_adapter_config()
            nwp_enabled = weather_forecast_config.enabled
            if weather_forecast_config.enabled:
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

        # Build priority index for alert checker
        all_priorities: dict[StationId, dict[ModelId, int]] = {
            s.id: {a.model_id: a.priority for a in model_assignments[s.id]}
            for s in operational
        }

        # Batch pre-fetch weather sources (eliminates per-station queries in Phase B)
        all_weather_sources: dict[StationId, list] = {
            s.id: station_store.fetch_weather_sources(s.id)  # type: ignore[union-attr]
            for s in operational
        }
        flat_weather_configs = [
            ws for sources in all_weather_sources.values() for ws in sources
        ]

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

        # --- Phase A: fetch NWP forcing (submit as task) ---
        nwp_future: Any = None
        nwp_cycle: UtcDatetime | None = None
        if runoff_only_mode:
            log.info(
                "forecast_cycle.nwp_disabled",
                mode="runoff_only",
                cycle_time=resolved_cycle_time.isoformat(),
            )
            nwp_cycle = resolved_cycle_time
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
            )

        # --- Step 1.6: observation timestamps (parallel with Phase A) ---
        obs_ts_future = _fetch_obs_timestamps_task.submit(
            obs_store=obs_store,
            stations=operational,
        )

        # Collect Phase A result
        if not runoff_only_mode:
            nwp_cycle = nwp_future.result()
        if nwp_cycle is None:
            log.error("forecast_cycle.nwp_fetch_failed_aborting")
            return ForecastCycleResult(
                cycle_time=resolved_cycle_time,
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

        # Determine nwp_cycle_source (v0: always PRIMARY)
        nwp_cycle_source = NwpCycleSource.PRIMARY

        # --- Phase B: per-station forecast loop ---
        from datetime import timedelta

        from sapphire_flow.services.forecast_combination import build_combined_forecasts
        from sapphire_flow.services.operational_inputs import (
            assemble_station_operational_inputs,
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

        stations_succeeded = 0
        stations_failed = 0
        forecasts_stored = 0
        errors: list[str] = []

        # Accumulate for Phase C
        all_ensembles: dict[StationId, dict[ModelId, dict[str, ForecastEnsemble]]] = {}

        for station in operational:
            sid = station.id
            structlog.contextvars.bind_contextvars(station_id=str(sid))
            station_t0 = time.perf_counter()

            assignments = model_assignments[sid]
            if not assignments:
                log.debug("forecast_cycle.no_assignments")
                structlog.contextvars.unbind_contextvars("station_id")
                continue

            # Use time_step from first active assignment (priority-sorted)
            sorted_assignments = sorted(assignments, key=lambda a: a.priority)
            time_step: timedelta = sorted_assignments[0].time_step
            first_model = models.get(sorted_assignments[0].model_id)
            if first_model is None:
                log.error(
                    "forecast_cycle.station_skipped_model_not_loaded",
                    model_id=str(sorted_assignments[0].model_id),
                )
                errors.append(
                    "Configured model "
                    f"{sorted_assignments[0].model_id} missing for {sid}"
                )
                stations_failed += 1
                structlog.contextvars.unbind_contextvars("station_id")
                continue
            forecast_horizon_steps: int = (
                first_model.data_requirements.forecast_horizon_steps
            )

            # Determine nwp_source for this station (deterministic ICON selection)
            weather_sources = all_weather_sources.get(sid, [])
            nwp_source: str = _select_nwp_source(weather_sources)

            try:
                inputs_result = assemble_station_operational_inputs(
                    station_id=sid,
                    model=first_model,
                    model_id=sorted_assignments[0].model_id,
                    issue_time=resolved_cycle_time,
                    cycle_time=resolved_cycle_time,
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
                        nwp_cycle_reference_time=resolved_cycle_time,
                        nwp_cycle_source=nwp_cycle_source,
                        config=config,
                        clock=clock,
                        id_gen=uuid4,
                        rng=rng,
                    )

                    if fc_result is None:
                        log.warning("forecast_cycle.all_models_failed")
                        stations_failed += 1
                        structlog.contextvars.unbind_contextvars("station_id")
                        continue

                    for fc in fc_result.forecasts:
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
                        nwp_cycle_reference_time=resolved_cycle_time,
                        nwp_cycle_source=nwp_cycle_source,
                        config=config,
                        clock=clock,
                        id_gen=uuid4,
                        rng=rng,
                    )

                    if multi_result.primary_model_id is None:
                        log.warning("forecast_cycle.all_models_failed")
                        stations_failed += 1
                        structlog.contextvars.unbind_contextvars("station_id")
                        continue

                    # Store all individual model forecasts
                    for mid, result in multi_result.results.items():
                        for fc in result.forecasts:
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
                        nwp_cycle_reference_time=resolved_cycle_time,
                        nwp_cycle_source=nwp_cycle_source,
                        clock=clock,
                        uuid_factory=uuid4,
                    )
                    if combined_forecasts:
                        for fc in combined_forecasts:
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
                        (
                            assignment
                            for assignment in group_assignments
                            if assignment.model_id == model_id
                            and assignment.status == ModelAssignmentStatus.ACTIVE
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
                    nwp_source_by_station = {
                        sid: _select_nwp_source(all_weather_sources.get(sid, []))
                        for sid in member_ids
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
                            cycle_time=resolved_cycle_time,
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
                        nwp_cycle_reference_time=resolved_cycle_time,
                        nwp_cycle_source=nwp_cycle_source,
                        config=config,  # type: ignore[arg-type]
                        clock=clock,  # type: ignore[arg-type]
                        id_gen=uuid4,
                        rng=rng,  # type: ignore[arg-type]
                    )

                    for sid, result in group_results.items():
                        for fc in result.forecasts:
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

        # --- Phase C: alert checking ---
        alerts_checked = False
        if config.enable_forecast_alerts and all_ensembles:
            from sapphire_flow.services.alert_checker import check_station_alerts

            alert_t0 = time.perf_counter()
            try:
                check_station_alerts(
                    all_ensembles=all_ensembles,
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
            forecasts_stored=result.forecasts_stored,
            alerts_checked=result.alerts_checked,
            duration_ms=result.duration_ms,
        )

        return result
    finally:
        if created_http_client is not None:
            created_http_client.close()
