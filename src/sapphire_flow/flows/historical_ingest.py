# pyright: reportPrivateUsage=false
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import structlog
from prefect import task
from prefect.cache_policies import NO_CACHE
from prefect.runtime import flow_run

from sapphire_flow.adapters.dhm import DhmAdapter, validate_dhm_station_binding
from sapphire_flow.config.deployment import DeploymentConfig, load_config
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.config.dhm import DhmConfig
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.native_ingest import (
    discover_native_recovery,
    dispatch_native_captures,
)
from sapphire_flow.protocols.native_capture import CaptureQcRecoveryReader
from sapphire_flow.services.station_qc_overrides import is_ingest_station_judged
from sapphire_flow.services.write_principal import resolve_run_principal
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcRuleSet
from sapphire_flow.types.enums import FetchOutcomeCause, StationKind
from sapphire_flow.types.historical_ingest import (
    HistoricalCaptureRequest,
    NativeCaptureScope,
)
from sapphire_flow.types.native_capture import (
    CanonicalProviderIdentity,
    CapturedLevelReference,
)
from sapphire_flow.types.observation import HydroScraperBatchResult, StationFetchOutcome
from sapphire_flow.types.rating_evidence import RatingEvidenceMode

if TYPE_CHECKING:
    import sqlalchemy as sa

    from sapphire_flow.flows.ingest_observations import IngestResult
    from sapphire_flow.protocols.native_capture import (
        NativeCaptureReader,
        NativeCaptureWriter,
    )
    from sapphire_flow.protocols.stores import (
        ObservationStore,
        StationStore,
        TenantStore,
    )
    from sapphire_flow.store.clim_baseline_store import PgClimBaselineStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.station import StationConfig
    from sapphire_flow.types.write_principal import WritePrincipal

log = structlog.get_logger(__name__)


def _historical_now() -> UtcDatetime:
    return ensure_utc(datetime.now(UTC))


def _load_identity_config() -> DeploymentIdentityConfig:
    from sapphire_flow.config._overlay import (
        _resolve_overlay_paths,  # pyright: ignore[reportPrivateUsage]
        load_merged_toml,
    )

    path = os.environ.get("SAPPHIRE_CONFIG")
    if not path:
        raise ConfigurationError("Historical capture requires native configuration")
    data = load_merged_toml(Path(path), _resolve_overlay_paths())
    section = data.get("deployment", {})
    if not isinstance(section, dict):
        raise ConfigurationError("Invalid historical deployment identity")
    writable = section.get("writable_tenants", [])
    admin = section.get("global_admin", False)
    operator = section.get("operator")
    if (
        not isinstance(writable, list)
        or any(
            not isinstance(code, str) or not code or code.strip() != code
            for code in writable
        )
        or type(admin) is not bool
        or (operator is not None and not isinstance(operator, str))
    ):
        raise ConfigurationError("Invalid historical deployment identity")
    return DeploymentIdentityConfig(
        writable_tenants=frozenset(writable), global_admin=admin, operator=operator
    )


def _load_operational_config() -> DeploymentConfig:
    return load_config(os.environ["SAPPHIRE_CONFIG"])


def _load_rules() -> QcRuleSet:
    from sapphire_flow.flows.ingest_observations import _load_qc_rules

    return _load_qc_rules()


def _load_dhm_config() -> DhmConfig:
    from sapphire_flow.flows.ingest_observations import _load_adapter_config

    config = _load_adapter_config()
    if not isinstance(config, DhmConfig):
        raise ConfigurationError("Historical capture requires configured DHM source")
    return config


def _bootstrap() -> tuple[sa.Connection | None, dict[str, object]]:
    from sqlalchemy.engine import make_url

    from sapphire_flow.flows._db import setup_production_stores
    from sapphire_flow.store.native_capture_store import PgNativeCaptureStore
    from sapphire_flow.store.rating_derived_store import rating_transaction_factory

    url = os.environ["DATABASE_URL"]
    conn, stores = setup_production_stores(url)
    try:
        stores["native_capture_store"] = PgNativeCaptureStore(
            rating_transaction_factory(make_url(url)),
            clock=_historical_now,
            id_gen=uuid4,
        )
        return conn, stores
    except Exception:
        conn.close()
        raise


def _validate_native_inputs(
    operational: object,
    rules: object,
    dhm: object,
) -> tuple[DeploymentConfig, QcRuleSet, DhmConfig]:
    if (
        not isinstance(operational, DeploymentConfig)
        or not isinstance(rules, QcRuleSet)
        or not isinstance(dhm, DhmConfig)
    ):
        raise ConfigurationError("Historical native configuration unavailable")
    return operational, rules, dhm


def _require_methods(value: object, names: tuple[str, ...]) -> None:
    if any(not callable(getattr(value, name, None)) for name in names):
        raise ConfigurationError("Historical capture prerequisite unavailable")


@task(name="fetch-historical-observations", cache_policy=NO_CACHE)
def _fetch_window(
    config: DhmConfig, stations: list[StationConfig], request: HistoricalCaptureRequest
) -> HydroScraperBatchResult:
    from sapphire_flow.flows.ingest_observations import _dhm_http_client

    with _dhm_http_client(config) as client:
        adapter = DhmAdapter(
            config=config,
            http_client=client,
            clock=_historical_now,
            request_clock=_historical_now,
        )
        return adapter.fetch_observations_window(
            stations, start=request.start, end=request.end
        )


def run_historical_ingest(
    *, tenant_code: str, station_ids: list[str], start: datetime, end: datetime
) -> IngestResult:
    from sapphire_flow.flows import ingest_observations as flow

    conn = None
    try:
        try:
            now = _historical_now()
            request = HistoricalCaptureRequest.parse(
                tenant_code=tenant_code,
                station_ids=station_ids,
                start=start,
                end=end,
                now=now,
            )
            identity = _load_identity_config()
            if (
                identity.global_admin
                or request.tenant_code not in identity.writable_tenants
            ):
                raise ConfigurationError("Historical tenant is not writable")
            operational = _load_operational_config()
            rules = _load_rules()
            dhm = _load_dhm_config()
            operational, rules, dhm = _validate_native_inputs(operational, rules, dhm)
            conn, stores = _bootstrap()
            station_store = cast("StationStore", stores.get("station_store"))
            obs_store = cast("ObservationStore", stores.get("obs_store"))
            tenant_store = cast("TenantStore", stores.get("tenant_store"))
            baseline = cast("PgClimBaselineStore", stores.get("baseline_store"))
            capture = cast("NativeCaptureWriter", stores.get("native_capture_store"))
            _require_methods(station_store, ("fetch_station", "fetch_all_stations"))
            _require_methods(
                obs_store, ("qc_context_reader", "fetch_latest_capture_timestamp")
            )
            _require_methods(tenant_store, ("fetch_tenant_by_code",))
            _require_methods(baseline, ("fetch_baselines",))
            _require_methods(capture, ("commit_batch", "resolve", "pending_qc"))
            qc_reader = obs_store.qc_context_reader()
            _require_methods(
                qc_reader, ("fetch_capture_qc_window", "update_qc_batch_if_current")
            )
            if not isinstance(qc_reader, CaptureQcRecoveryReader):
                raise ConfigurationError("Historical bounded QC reader unavailable")
            principal = cast(
                "WritePrincipal | None",
                resolve_run_principal(
                    tenant_store, identity, tenant_code=request.tenant_code
                ),
            )
            if principal is None or principal.tenant_id is None:
                raise ConfigurationError("Historical capture requires scoped identity")
            stations: list[StationConfig] = []
            scopes: list[NativeCaptureScope] = []
            for station_id in request.station_ids:
                station = station_store.fetch_station(station_id)
                if (
                    station is None
                    or station.id != station_id
                    or station.tenant_id != principal.tenant_id
                    or station.network != "dhm"
                    or station.station_kind is not StationKind.RIVER
                    or not is_ingest_station_judged(station)
                ):
                    raise ConfigurationError("Historical station scope is unavailable")
                binding = validate_dhm_station_binding(dhm, station)
                if (
                    sum(
                        item.api_station_id == binding.api_station_id
                        for item in dhm.bindings
                    )
                    != 1
                ):
                    raise ConfigurationError("Historical provider mapping is ambiguous")
                cursor = obs_store.fetch_latest_capture_timestamp(
                    station.id, flow._cursor_parameter_for_station(station)
                )
                if cursor is None or cursor < request.end:
                    raise ConfigurationError(
                        "Historical request requires a newer stored cursor"
                    )
                stations.append(station)
                scopes.append(
                    NativeCaptureScope(
                        tenant_id=principal.tenant_id,
                        station_id=station.id,
                        station_code=station.code,
                        network=station.network,
                        provider=CanonicalProviderIdentity.parse(
                            dhm.endpoint, binding.api_station_id
                        ),
                        level_reference=CapturedLevelReference(binding.level_reference),
                    )
                )
            for configured in operational.provisional_ratings:
                binding = configured.to_domain()
                if (
                    binding.station_id in request.station_ids
                    and binding.evidence_mode is RatingEvidenceMode.LEGACY_CUSTODY
                    and binding.custody_start is not None
                    and binding.custody_end is not None
                    and binding.custody_start < request.end
                    and request.start < binding.custody_end
                ):
                    raise ConfigurationError(
                        "Historical request overlaps enabled legacy custody"
                    )
            ordinary_stations = [
                station
                for kind in (StationKind.RIVER, StationKind.LAKE, StationKind.WEATHER)
                for station in station_store.fetch_all_stations(kind=kind)
            ]
            thresholds = flow._configured_station_qc(
                ordinary_stations, rules, tenant_store
            )
            run_id = UUID(str(flow_run.id)) if flow_run.id else None
        except Exception:
            raise ConfigurationError("Historical capture preflight failed") from None

        log.info(
            "ingest.historical_capture",
            tenant_code=request.tenant_code,
            station_count=len(stations),
            start=request.start.isoformat(),
            end=request.end.isoformat(),
            flow_run_id=str(run_id) if run_id else None,
        )
        try:
            batch = _fetch_window(dhm, stations, request)
        except Exception:
            batch = HydroScraperBatchResult(
                outcomes=tuple(
                    StationFetchOutcome(
                        station_id=station.id,
                        observations=(),
                        failure_cause=FetchOutcomeCause.TRANSPORT_ERROR,
                        failure_detail="Historical fetch unavailable",
                    )
                    for station in stations
                )
            )
        dispatched = dispatch_native_captures(
            batch, tuple(scopes), dhm, capture, flow_run_id=run_id
        )
        failed = {
            item.station_id for item in batch.outcomes if item.failure_cause is not None
        }
        errors = [
            f"fetch failed for {item.station_id}: {item.failure_cause.value}"
            for item in batch.outcomes
            if item.failure_cause is not None
        ]
        failed.update(dispatched.failed_station_ids)
        errors.extend(dispatched.errors)
        recovery = discover_native_recovery(
            cast("NativeCaptureReader", capture),
            tuple(scopes),
            now=now,
            start=request.start,
            end=request.end,
        )
        failed.update(recovery.failed_station_ids)
        errors.extend(recovery.errors)
        totals = flow._empty_qc_counts()
        networks = {station.id: station.network for station in stations}
        for station in stations:
            windows = tuple(
                window for window in recovery.windows if window.station_id == station.id
            )
            if not windows:
                continue
            try:
                outcome = flow._run_qc_task(
                    qc_reader,
                    baseline,
                    station.id,
                    "water_level",
                    rules,
                    now,
                    station_networks=networks,
                    datum=station.water_level_datum_masl,
                    context_window_hours=2.0,
                    overrides=thresholds.overrides,
                    recovery_windows=windows,
                    window_mode=flow.QcWindowMode.RECOVERY_ONLY,
                )
                for key in totals:
                    totals[key] += outcome.counts[key]
                if outcome.recovery_holds:
                    failed.add(station.id)
                    errors.extend(
                        f"native_qc_recovery_held:{station.id}:{hold.reason.value}"
                        for hold in outcome.recovery_holds
                    )
            except Exception:
                failed.add(station.id)
                errors.append(f"native_qc_recovery_held:{station.id}:storage_error")
        requested_keys = {(station.network, station.code) for station in stations}
        result = flow.IngestResult(
            stations_polled=len(stations),
            observations_fetched=len(batch.observations),
            observations_stored=dispatched.observations_written,
            observations_skipped=dispatched.observations_unchanged,
            qc_passed=totals["passed"],
            qc_failed=totals["failed"],
            qc_suspect=totals["suspect"],
            qc_unchecked=totals["unchecked"],
            qc_unjudged=totals["unjudged"],
            qc_rechecked=totals["rechecked"],
            qc_newly_checked=totals["newly_checked"],
            qc_stale_evaluation=totals["stale_evaluation"],
            stations_failed=len(failed),
            errors=tuple(errors),
            qc_threshold_pending=sum(
                key in requested_keys for key in thresholds.pending_station_keys
            ),
            qc_threshold_rejected=sum(
                key in requested_keys for key in thresholds.rejected_station_keys
            ),
            qc_threshold_not_applicable=sum(
                key in requested_keys for key in thresholds.not_applicable_station_keys
            ),
            native_capture_backlog=recovery.backlog,
        )
        log.info(
            "ingest.historical_complete",
            stations_failed=result.stations_failed,
            observations_stored=result.observations_stored,
            qc_unchecked=result.qc_unchecked,
            qc_unjudged=result.qc_unjudged,
            errors=result.errors,
        )
        return result
    finally:
        if conn is not None:
            conn.close()
