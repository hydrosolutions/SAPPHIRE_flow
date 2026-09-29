# pyright: reportUnknownMemberType=false
"""Guarded import of the restricted DHM historical delivery."""

from __future__ import annotations

import argparse
import os
import tomllib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from sapphire_flow.adapters.dhm_files import (
    DhmFileFormatError,
    parse_daily_flow,
    parse_rating_tables,
)
from sapphire_flow.adapters.nepal_local_day import nepal_day_start, nepal_local_date
from sapphire_flow.config.deployment_identity import (
    DeploymentIdentityConfig,
    load_deployment_identity_config,
)
from sapphire_flow.config.onboarding import load_onboarding_config
from sapphire_flow.config.qc_rules import load_qc_rules
from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.exceptions import (
    ConfigurationError,
    DeliveryCollisionError,
    DeliveryCurveDependencyError,
)
from sapphire_flow.services.qc import Stage1QualityChecker, resolve_selection
from sapphire_flow.services.qc_datum import obs_qc_rule_version
from sapphire_flow.services.rating_conversion import (
    RatingConversionError,
    RatingConverter,
)
from sapphire_flow.services.station_qc_overrides import resolve_station_qc_overrides
from sapphire_flow.services.write_principal import (
    enforce_tenant_isolation,
    resolve_run_principal,
)
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.dhm_delivery import DELIVERY_ID
from sapphire_flow.types.domain import (
    GeoCoord,
    QcRuleSet,
    StationQcOverride,
    aggregate_qc_status,
)
from sapphire_flow.types.enums import (
    AuditEventType,
    GaugingStatus,
    InterpolationMethod,
    ObservationSource,
    QcStatus,
    StationKind,
    StationOwnership,
    StationStatus,
)
from sapphire_flow.types.ids import ObservationId, RatingCurveId, StationId, TenantId
from sapphire_flow.types.observation import Observation, RawObservation
from sapphire_flow.types.rating_curve import RatingCurve
from sapphire_flow.types.station import StationConfig

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.adapters.dhm_files import DailyFlowFile, RatingTableFile
    from sapphire_flow.protocols.stores import (
        AuditLogStore,
        ObservationStore,
        RatingCurveStore,
        StationStore,
        TenantStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)
DELIVERY_TENANT_CODE = "chwrr"
DELIVERY_TENANT_NAME = "CHWRR Nepal"
_STATION_CODES = frozenset({"447", "450", "604.5", "647", "670", "684"})

ImportCommand = Literal["stations", "replace", "qc"]
_TENANT_MISSING = (
    "CHWRR tenant does not exist: it is created at deploy time from the host's "
    "[tenants.chwrr] declaration (Plan 513), never by an import command"
)


def _audit_import(
    audit_log_store: AuditLogStore,
    *,
    command: ImportCommand,
    tenant_id: TenantId,
    counts: dict[str, object],
    now: UtcDatetime,
) -> None:
    """One system-actor row per successful command, written on the caller's
    connection so it commits or rolls back with the mutation. Rejections stay
    log-only: a rejection row would roll back with the failed transaction."""
    audit_log_store.append_entry(
        AuditEntry.system(
            event_type=AuditEventType.DELIVERY_IMPORTED,
            target_type="tenant",
            target_id=str(tenant_id),
            detail={"command": command, "delivery_id": DELIVERY_ID, **counts},
            ip_address=None,
            created_at=now,
        )
    )


class _TenantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    display_name: str


class _StationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    published_area_km2: float = Field(gt=0)
    station_kind: StationKind
    network: str
    timezone: str
    measured_parameters: list[str]
    station_status: StationStatus
    ownership: StationOwnership
    gauging_status: GaugingStatus


class _MetadataInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: _TenantInput
    stations: list[_StationInput]


def load_station_metadata(path: Path) -> _MetadataInput:
    metadata = _MetadataInput.model_validate(tomllib.loads(path.read_text()))
    if (metadata.tenant.code, metadata.tenant.display_name) != (
        DELIVERY_TENANT_CODE,
        DELIVERY_TENANT_NAME,
    ):
        raise ConfigurationError("DHM station artifact has the wrong tenant identity")
    if (
        len(metadata.stations) != 6
        or frozenset(s.code for s in metadata.stations) != _STATION_CODES
    ):
        raise ConfigurationError(
            "DHM station artifact must contain the six approved codes"
        )
    if any(
        s.network != "dhm"
        or s.station_kind is not StationKind.RIVER
        or s.station_status is not StationStatus.ONBOARDING
        or s.measured_parameters != ["discharge"]
        or s.timezone != "Asia/Kathmandu"
        or s.ownership is not StationOwnership.FOREIGN
        or s.gauging_status is not GaugingStatus.GAUGED
        for s in metadata.stations
    ):
        raise ConfigurationError("DHM station artifact has unsupported station fields")
    return metadata


def _one_overlay(config_path: Path) -> Path:
    raw = os.environ.get("SAPPHIRE_CONFIG_OVERLAY", "")
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    if len(entries) != 1:
        raise ConfigurationError("DHM import requires exactly one deployment overlay")
    path = Path(entries[0]).resolve()
    if not path.is_file():
        raise ConfigurationError("DHM deployment overlay is missing")
    return path


def _require_chwrr_identity(config_path: Path) -> DeploymentIdentityConfig:
    overlay = _one_overlay(config_path)
    expected = config_path.resolve().parent / "config/overlays/chwrr-import.toml"
    if overlay != expected.resolve() or tomllib.loads(overlay.read_text()) != {
        "deployment": {"writable_tenants": [DELIVERY_TENANT_CODE]}
    }:
        raise ConfigurationError(
            "DHM import requires the checked-in CHWRR deployment overlay"
        )
    identity = load_deployment_identity_config(config_path)
    _assert_chwrr_scoped(identity)
    return identity


def _assert_chwrr_scoped(identity: DeploymentIdentityConfig) -> None:
    if identity.global_admin or identity.writable_tenants != frozenset(
        {DELIVERY_TENANT_CODE}
    ):
        raise ConfigurationError("DHM import requires CHWRR-scoped write authority")


def _station_matches(existing: StationConfig, expected: StationConfig) -> bool:
    return (
        existing.tenant_id == expected.tenant_id
        and existing.code == expected.code
        and existing.name == expected.name
        and existing.location == expected.location
        and existing.station_kind == expected.station_kind
        and existing.timezone == expected.timezone
        and existing.network == expected.network
        and existing.measured_parameters == expected.measured_parameters
        and existing.station_status == expected.station_status
        and existing.ownership == expected.ownership
        and existing.gauging_status == expected.gauging_status
        and existing.forecast_targets is None
    )


def register_stations(
    tenant_store: TenantStore,
    station_store: StationStore,
    identity: DeploymentIdentityConfig,
    metadata: _MetadataInput,
    *,
    audit_log_store: AuditLogStore,
    now: UtcDatetime,
) -> int:
    _assert_chwrr_scoped(identity)
    tenant = tenant_store.fetch_tenant_by_code(DELIVERY_TENANT_CODE)
    if tenant is None:
        raise ConfigurationError(_TENANT_MISSING)
    principal = resolve_run_principal(
        tenant_store, identity, tenant_code=DELIVERY_TENANT_CODE
    )
    if principal.tenant_id is None:
        raise ConfigurationError("DHM station import requires a scoped principal")
    enforce_tenant_isolation(
        principal=principal,
        target_tenant_id=tenant.id,
        audit_log_store=None,
        event_type=AuditEventType.STATION_ONBOARDED,
        target_type="tenant",
        target_id=str(tenant.id),
        detail={"station_count": len(metadata.stations)},
        now=now,
    )
    expected_stations = [
        StationConfig(
            id=StationId(uuid4()),
            tenant_id=tenant.id,
            code=spec.code,
            name=spec.name,
            location=GeoCoord(lon=spec.longitude, lat=spec.latitude),
            station_kind=spec.station_kind,
            basin_id=None,
            timezone=spec.timezone,
            regulation_type=None,
            forecast_targets=None,
            measured_parameters=frozenset(spec.measured_parameters),
            station_status=spec.station_status,
            created_at=now,
            updated_at=now,
            network=spec.network,
            ownership=spec.ownership,
            wigos_id=None,
            gauging_status=spec.gauging_status,
        )
        for spec in metadata.stations
    ]
    new_stations: list[StationConfig] = []
    for expected in expected_stations:
        existing = station_store.fetch_station_by_code(expected.code, expected.network)
        if existing is None:
            new_stations.append(expected)
        elif existing.tenant_id != tenant.id:
            raise ConfigurationError(
                f"DHM station {expected.code} already belongs to another tenant"
            )
        elif not _station_matches(existing, expected):
            raise ConfigurationError(
                f"DHM station {expected.code} differs from approved metadata"
            )
    for station in new_stations:
        station_store.store_station(station)
    _audit_import(
        audit_log_store,
        command="stations",
        tenant_id=tenant.id,
        counts={"created": len(new_stations), "declared": len(expected_stations)},
        now=now,
    )
    return len(new_stations)


def build_delivery_curves(
    station_id: StationId,
    source: RatingTableFile,
    *,
    highest_surviving_version: int,
    now: UtcDatetime,
    day_start: Callable[[date], UtcDatetime] = nepal_day_start,
) -> list[RatingCurve]:
    curves: list[RatingCurve] = []
    for ordinal, block in enumerate(
        sorted(source.blocks, key=lambda item: item.from_date),
        start=highest_surviving_version + 1,
    ):
        curve = RatingCurve(
            id=RatingCurveId(uuid4()),
            station_id=station_id,
            version=ordinal,
            valid_from=day_start(block.from_date),
            valid_to=day_start(block.to_date + timedelta(days=1)),
            points=[
                {"water_level": point.stage_m, "discharge": point.discharge_m3s}
                for point in block.points
            ],
            interpolation=InterpolationMethod.LINEAR,
            uploaded_by=None,
            created_at=now,
            delivery_id=DELIVERY_ID,
            rating_type_label=block.rating_type_label,
        )
        try:
            RatingConverter.from_curve(curve)
        except RatingConversionError:
            raise DhmFileFormatError(
                f"station {source.station_code} rating block {ordinal} "
                "is invalid for linear conversion"
            ) from None
        curves.append(curve)
    return curves


def build_delivery_observations(
    station_id: StationId,
    source: DailyFlowFile,
    curves: list[RatingCurve],
    *,
    day_start: Callable[[date], UtcDatetime] = nepal_day_start,
) -> list[RawObservation]:
    observations: list[RawObservation] = []
    for item in source.values:
        timestamp = day_start(item.day)
        matching = [
            curve
            for curve in curves
            if curve.valid_from <= timestamp
            and (curve.valid_to is None or timestamp < curve.valid_to)
        ]
        selected = max(
            matching,
            key=lambda curve: (curve.valid_from, curve.version),
            default=None,
        )
        observations.append(
            RawObservation(
                station_id=station_id,
                timestamp=timestamp,
                parameter="discharge",
                value=item.discharge_m3s,
                source=ObservationSource.MANUAL_IMPORT,
                rating_curve_id=selected.id if selected is not None else None,
                delivery_id=DELIVERY_ID,
            )
        )
    return observations


def load_delivery_files(
    input_dir: Path, metadata: _MetadataInput
) -> dict[str, tuple[DailyFlowFile, RatingTableFile]]:
    files: dict[str, tuple[DailyFlowFile, RatingTableFile]] = {}
    for spec in metadata.stations:
        daily_path = input_dir / f"DFL_{spec.code}.txt"
        rating_path = input_dir / f"RT_{spec.code}.txt"
        daily = parse_daily_flow(daily_path.read_text())
        rating = parse_rating_tables(rating_path.read_text())
        if daily.station_code != spec.code or rating.station_code != spec.code:
            raise ConfigurationError(
                f"DHM delivery headers disagree with station {spec.code}"
            )
        files[spec.code] = (daily, rating)
    return files


def replace_delivery(
    tenant_store: TenantStore,
    station_store: StationStore,
    rating_store: RatingCurveStore,
    observation_store: ObservationStore,
    identity: DeploymentIdentityConfig,
    files: dict[str, tuple[DailyFlowFile, RatingTableFile]],
    *,
    audit_log_store: AuditLogStore,
    now: UtcDatetime,
    day_start: Callable[[date], UtcDatetime] = nepal_day_start,
) -> tuple[int, int]:
    _assert_chwrr_scoped(identity)
    tenant = tenant_store.fetch_tenant_by_code(DELIVERY_TENANT_CODE)
    if tenant is None:
        raise ConfigurationError(_TENANT_MISSING)
    principal = resolve_run_principal(
        tenant_store, identity, tenant_code=DELIVERY_TENANT_CODE
    )
    enforce_tenant_isolation(
        principal=principal,
        target_tenant_id=tenant.id,
        audit_log_store=None,
        event_type=AuditEventType.STATION_ONBOARDED,
        target_type="tenant",
        target_id=str(tenant.id),
        detail={"delivery_id": DELIVERY_ID},
        now=now,
    )
    tenant_store.lock_tenant(tenant.id)
    if frozenset(files) != _STATION_CODES:
        raise ConfigurationError("DHM delivery must contain exactly six stations")
    station_ids: list[StationId] = []
    new_curves: list[RatingCurve] = []
    new_observations: list[RawObservation] = []
    for code in sorted(files):
        station = station_store.fetch_station_by_code(code, "dhm")
        if station is None or station.tenant_id != tenant.id:
            raise ConfigurationError(f"DHM station {code} is not registered for CHWRR")
        station_ids.append(station.id)
        existing = rating_store.fetch_all_curves_for_station(station.id)
        survivors = [curve for curve in existing if curve.delivery_id != DELIVERY_ID]
        highest = max((curve.version for curve in survivors), default=0)
        daily, ratings = files[code]
        curves = build_delivery_curves(
            station.id,
            ratings,
            highest_surviving_version=highest,
            now=now,
            day_start=day_start,
        )
        new_curves.extend(curves)
        new_observations.extend(
            build_delivery_observations(
                station.id, daily, [*survivors, *curves], day_start=day_start
            )
        )
    intended_keys = {
        (row.station_id, row.timestamp, row.parameter, row.source)
        for row in new_observations
    }
    if len(intended_keys) != len(new_observations):
        raise ConfigurationError("DHM delivery has duplicate observation keys")
    observation_store.delete_delivery_observations(DELIVERY_ID, station_ids)
    for station_id in station_ids:
        rating_store.delete_delivery_curves(DELIVERY_ID, [station_id])
    for curve in new_curves:
        rating_store.store_rating_curve(curve)
    for offset in range(0, len(new_observations), 5000):
        observation_store.store_raw_observations(
            new_observations[offset : offset + 5000]
        )
    written = observation_store.fetch_delivery_observations(DELIVERY_ID, station_ids)
    written_keys = {
        (row.station_id, row.timestamp, row.parameter, row.source) for row in written
    }
    if written_keys != intended_keys or len(written) != len(new_observations):
        raise ConfigurationError("DHM delivery read-back differs from staged rows")
    _audit_import(
        audit_log_store,
        command="replace",
        tenant_id=tenant.id,
        counts={
            "stations": len(station_ids),
            "curves": len(new_curves),
            "observations": len(new_observations),
        },
        now=now,
    )
    return len(new_curves), len(new_observations)


_DHM_CEILINGS = {
    "447": 20000.0,
    "450": 100000.0,
    "604.5": 25000.0,
    "647": 10000.0,
    "670": 50000.0,
    "684": 20000.0,
}
_QC_SKIPPED = frozenset({"gross_outlier"})


def _resolve_delivery_qc(
    config_path: Path, stations: list[StationConfig], tenant_id: TenantId
) -> tuple[QcRuleSet, list[StationQcOverride]]:
    base = tomllib.loads(config_path.read_text())
    if "qc_rules" not in base:
        raise ConfigurationError("DHM QC requires explicit configured rules")
    rules = load_qc_rules(config_path=config_path)
    onboarding = load_onboarding_config(config_path=config_path)
    if onboarding is None:
        raise ConfigurationError("DHM QC requires onboarding configuration")
    specs = [
        spec
        for spec in onboarding.station_qc_thresholds
        if spec.tenant_code == DELIVERY_TENANT_CODE and spec.network == "dhm"
    ]
    if len(specs) != 6 or frozenset(spec.code for spec in specs) != _STATION_CODES:
        raise ConfigurationError("DHM QC requires exactly six station ceilings")
    if any(
        spec.rule_id != "range_check"
        or spec.parameter != "discharge"
        or spec.time_step != timedelta(days=1)
        or spec.thresholds != {"value_max": _DHM_CEILINGS[spec.code]}
        for spec in specs
    ):
        raise ConfigurationError("DHM QC station ceilings differ from D14")
    selected = rules.rules_for("discharge", timedelta(days=1), network="dhm")
    expected = {
        "range_check": {"value_min": 0.0, "value_max": 100000.0},
        "rate_of_change": {"max_rate": 100000.0},
        "spike": {"tolerance": 25.0},
    }
    selected_dhm = [rule for rule in selected if rule.network == "dhm"]
    if (
        len(selected_dhm) != 3
        or {rule.rule_id: rule.thresholds for rule in selected_dhm} != expected
    ):
        raise ConfigurationError("DHM QC rules differ from D14")
    if {rule.rule_id for rule in selected if rule.network is None} != {"gross_outlier"}:
        raise ConfigurationError("DHM QC generic fallback differs from D16")

    station_ids = {station.id for station in stations}

    def applicable(station: StationConfig, rule_id: str, parameter: str) -> bool:
        return (
            station.id in station_ids
            and station.network == "dhm"
            and rule_id in expected
            and parameter == "discharge"
        )

    resolution = resolve_station_qc_overrides(
        specs, stations, rules, applicable, tenant_id=tenant_id
    )
    if (
        resolution.rejected
        or resolution.not_applicable
        or resolution.fan_out
        or len(resolution.overrides) != 6
    ):
        raise ConfigurationError("DHM QC ceilings did not resolve one-to-one")
    return rules, list(resolution.overrides)


def _local_day_segments(observations: list[Observation]) -> list[list[Observation]]:
    segments: list[list[Observation]] = []
    for observation in sorted(observations, key=lambda row: row.timestamp):
        if (
            not segments
            or (
                nepal_local_date(observation.timestamp)
                - nepal_local_date(segments[-1][-1].timestamp)
            ).days
            != 1
        ):
            segments.append([])
        segments[-1].append(observation)
    return segments


def run_delivery_qc(
    tenant_store: TenantStore,
    station_store: StationStore,
    observation_store: ObservationStore,
    identity: DeploymentIdentityConfig,
    config_path: Path,
    *,
    audit_log_store: AuditLogStore,
    now: UtcDatetime,
) -> dict[QcStatus, int]:
    _assert_chwrr_scoped(identity)
    tenant = tenant_store.fetch_tenant_by_code(DELIVERY_TENANT_CODE)
    if tenant is None:
        raise ConfigurationError(_TENANT_MISSING)
    principal = resolve_run_principal(
        tenant_store, identity, tenant_code=DELIVERY_TENANT_CODE
    )
    enforce_tenant_isolation(
        principal=principal,
        target_tenant_id=tenant.id,
        audit_log_store=None,
        event_type=AuditEventType.STATION_ONBOARDED,
        target_type="tenant",
        target_id=str(tenant.id),
        detail={"delivery_id": DELIVERY_ID},
        now=now,
    )
    tenant_store.lock_tenant(tenant.id)
    stations = [
        station_store.fetch_station_by_code(code, "dhm")
        for code in sorted(_STATION_CODES)
    ]
    if any(station is None or station.tenant_id != tenant.id for station in stations):
        raise ConfigurationError("DHM QC requires six registered CHWRR stations")
    cohort = [station for station in stations if station is not None]
    rules, overrides = _resolve_delivery_qc(config_path, cohort, tenant.id)
    network_map = {station.id: station.network for station in cohort}
    station_ids = list(network_map)
    rows = observation_store.fetch_delivery_observations(DELIVERY_ID, station_ids)
    if not rows or any(
        row.parameter != "discharge"
        or row.source is not ObservationSource.MANUAL_IMPORT
        for row in rows
    ):
        raise ConfigurationError("DHM QC cohort is empty or has unexpected rows")
    grouped: dict[StationId, list[Observation]] = {
        station_id: [] for station_id in station_ids
    }
    for row in rows:
        grouped[row.station_id].append(row)
    checker = Stage1QualityChecker()
    outcomes: dict[ObservationId, QcStatus] = {}
    flags_raised = 0
    for station_id, station_rows in grouped.items():
        if not station_rows:
            raise ConfigurationError(
                f"DHM QC station {station_id} has no delivery rows"
            )
        for segment in _local_day_segments(station_rows):
            selection = resolve_selection(
                segment,
                rules,
                station_networks=network_map,
                skipped_rule_ids=_QC_SKIPPED,
            )
            runnable = selection[(station_id, "discharge")][1] > 0
            if not runnable:
                log.info(
                    "dhm_import.qc_unchecked_segment",
                    station_id=str(station_id),
                    start_day=nepal_local_date(segment[0].timestamp).isoformat(),
                    end_day=nepal_local_date(segment[-1].timestamp).isoformat(),
                    count=len(segment),
                )
            flags_by_id = checker.check(
                segment,
                rules,
                overrides,
                [],
                station_networks=network_map,
                skipped_rule_ids=_QC_SKIPPED,
            )
            for row in segment:
                flags = flags_by_id[row.id]
                flags_raised += len(flags)
                status = (
                    aggregate_qc_status(flags) if runnable else QcStatus.QC_UNCHECKED
                )
                if not observation_store.update_delivery_qc(
                    row.id,
                    DELIVERY_ID,
                    status,
                    flags,
                    obs_qc_rule_version(row.parameter, None),
                ):
                    raise ConfigurationError(
                        "DHM QC delivery row changed during update"
                    )
                outcomes[row.id] = status
    persisted = observation_store.fetch_delivery_observations(DELIVERY_ID, station_ids)
    if len(persisted) != len(outcomes) or any(
        row.id not in outcomes or row.qc_status is not outcomes[row.id]
        for row in persisted
    ):
        raise ConfigurationError("DHM QC read-back differs from intended statuses")
    log.info(
        "dhm_import.qc_coverage",
        rows_evaluated=len(outcomes),
        flags_raised=flags_raised,
        unchecked=sum(status is QcStatus.QC_UNCHECKED for status in outcomes.values()),
    )
    tally = {
        status: list(outcomes.values()).count(status)
        for status in set(outcomes.values())
    }
    _audit_import(
        audit_log_store,
        command="qc",
        tenant_id=tenant.id,
        counts={
            "rows_evaluated": len(outcomes),
            "statuses": {status.value: count for status, count in tally.items()},
        },
        now=now,
    )
    return tally


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import restricted DHM history")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("stations", "replace", "qc"):
        command = commands.add_parser(name)
        command.add_argument("--tenant", required=True)
        command.add_argument("--dry-run", action="store_true")
        if name == "replace":
            command.add_argument("--input-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    from sapphire_flow.logging import configure_cli_logging

    configure_cli_logging()
    args = _parser().parse_args(argv)
    config_raw = os.environ.get("SAPPHIRE_CONFIG")
    if not config_raw:
        raise ConfigurationError("SAPPHIRE_CONFIG is required for DHM import")
    config_path = Path(config_raw)
    if args.tenant != DELIVERY_TENANT_CODE:
        raise ConfigurationError("DHM import tenant must be chwrr")
    identity = _require_chwrr_identity(config_path)
    metadata_path = config_path.resolve().parent / "tests/fixtures/dhm/stations.toml"
    metadata = load_station_metadata(metadata_path)
    files = None
    if args.command == "replace":
        if args.input_dir.resolve().is_relative_to(config_path.resolve().parent):
            raise ConfigurationError(
                "restricted DHM input directory must be outside checkout"
            )
        files = load_delivery_files(args.input_dir, metadata)
    engine = create_engine_from_env()
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                tenant_store = PgTenantStore(conn)
                audit_log_store = PgAuditLogStore(conn)
                now = ensure_utc(datetime.now(UTC))
                if args.command == "stations":
                    created = register_stations(
                        tenant_store,
                        PgStationStore(conn),
                        identity,
                        metadata,
                        audit_log_store=audit_log_store,
                        now=now,
                    )
                    log.info("dhm_import.stations", created=created)
                elif args.command == "replace":
                    if files is None:
                        raise ConfigurationError("DHM delivery files were not loaded")
                    curves, observations = replace_delivery(
                        tenant_store,
                        PgStationStore(conn),
                        PgRatingCurveStore(conn),
                        PgObservationStore(conn),
                        identity,
                        files,
                        audit_log_store=audit_log_store,
                        now=now,
                    )
                    log.info(
                        "dhm_import.delivery",
                        curves=curves,
                        observations=observations,
                    )
                else:
                    statuses = run_delivery_qc(
                        tenant_store,
                        PgStationStore(conn),
                        PgObservationStore(conn),
                        identity,
                        config_path,
                        audit_log_store=audit_log_store,
                        now=now,
                    )
                    log.info(
                        "dhm_import.qc",
                        statuses={
                            status.value: count for status, count in statuses.items()
                        },
                        skipped_rule_ids=sorted(_QC_SKIPPED),
                        rate_of_change_discriminating=False,
                    )
                if args.dry_run:
                    transaction.rollback()
                else:
                    transaction.commit()
            except Exception:
                transaction.rollback()
                raise
    finally:
        engine.dispose()
    return 0


def _safe_failure_reason(exc: Exception) -> str:
    if isinstance(
        exc,
        (
            DhmFileFormatError,
            ConfigurationError,
            DeliveryCollisionError,
            DeliveryCurveDependencyError,
        ),
    ):
        return str(exc)
    return f"{type(exc).__name__}; details withheld to protect delivered values"


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log.error("dhm_import.failed", reason=_safe_failure_reason(exc))
        raise SystemExit(1) from None
