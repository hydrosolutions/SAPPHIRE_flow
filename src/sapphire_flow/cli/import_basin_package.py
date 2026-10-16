"""Plan 120 Task 3A — CLI entrypoint for importing an accepted basin/static
package.

Run via: python -m sapphire_flow.cli.import_basin_package --package-dir <dir>

Manual/onboarding-time invocation only for v1 (plan Task 3A "Scope out": no
scheduling/Prefect flow wraps this). Resolves stations against the live
`stations` table (`(code, network)`, network-scoped per contract §9 — Task
1B) and prints the resulting acceptance report. Exits non-zero when the
package (or an accepted decision at the write boundary) was rejected.

Also builds and passes a PRODUCTION `assigned_model_features` resolver
(`services.basin_importer.build_assigned_model_features_resolver`) — the
union of every ACTIVE station/group-assigned model's declared static
requirements for a basin's station. Without it, `evaluate_basin_acceptance`
defaults to treating no basin as verifiably assigned, downgrading a null
required-static-feature to a warning instead of an onboarding hold (fixer
round, major finding).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

    import sqlalchemy as sa

    from sapphire_flow.types.basin_package import BasinPackageImportReport, BasinRecord
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId, TenantId

log = structlog.get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import an accepted basin/static package (Plan 120 Task 3A). "
            "Reads DATABASE_URL from the environment."
        )
    )
    parser.add_argument(
        "--nepal21-static-only",
        action="store_true",
        help=(
            "Use the Plan 520 Nepal21 static-only gate. This validates the "
            "package identity/HRU pin and refuses weather/model activation; it "
            "does not import real DHM delivery data."
        ),
    )
    parser.add_argument(
        "--expected-package-id",
        help="Required package_id pin for --nepal21-static-only.",
    )
    parser.add_argument(
        "--expected-hru",
        help="Required Gateway HRU pin for --nepal21-static-only.",
    )
    parser.add_argument(
        "--tenant-id",
        help="Required tenant UUID for --nepal21-static-only.",
    )
    parser.add_argument(
        "--package-dir",
        required=True,
        type=Path,
        help=(
            "Path to the package directory (manifest.json, basins.gpkg, "
            "static_attributes.parquet, feature_catalog.json, "
            "validation_report.json, and optionally bands.gpkg)."
        ),
    )
    args = parser.parse_args()

    from sapphire_flow.logging import configure_cli_logging

    configure_cli_logging()

    if args.nepal21_static_only:
        if not args.tenant_id:
            raise ValueError("--nepal21-static-only requires --tenant-id")
        from uuid import UUID

        from sapphire_flow.db.engine import create_engine_from_env
        from sapphire_flow.types.ids import TenantId

        engine = create_engine_from_env()
        with engine.begin() as conn:
            report = run_nepal_static_import_transaction(
                conn,
                tenant_id=TenantId(UUID(args.tenant_id)),
                package_dir=args.package_dir,
                expected_package_id=args.expected_package_id,
                expected_hru=args.expected_hru,
            )
        _log_report(report)
        if report.outcome == "rejected":
            sys.exit(1)
        return

    report = _run_import(args.package_dir)
    _log_report(report)
    if report.outcome == "rejected":
        sys.exit(1)


@dataclass(frozen=True, kw_only=True, slots=True)
class NepalStaticResolverSnapshot:
    station_id: str
    nwp_source: str
    role: str
    hru_name: str | None
    polygon_name: str | None
    spatial_type: str | None
    band_id: int | None


def _fetch_nepal_static_resolver_snapshots(
    conn: sa.Connection,
) -> tuple[NepalStaticResolverSnapshot, ...]:
    import sqlalchemy as sa

    from sapphire_flow.db.metadata import (
        recap_gateway_polygon_bindings,
        station_weather_sources,
    )

    active_nepal_sources = (
        ("era5_land", "reanalysis"),
        ("ifs_ecmwf", "forecast"),
    )
    source_filter = sa.or_(
        *(
            sa.and_(
                station_weather_sources.c.nwp_source == nwp_source,
                station_weather_sources.c.role == role,
            )
            for nwp_source, role in active_nepal_sources
        )
    )
    basin_average = (
        sa.select(
            recap_gateway_polygon_bindings.c.station_id,
            recap_gateway_polygon_bindings.c.gateway_hru_name,
            recap_gateway_polygon_bindings.c.name,
            recap_gateway_polygon_bindings.c.spatial_type,
            recap_gateway_polygon_bindings.c.band_id,
        )
        .where(recap_gateway_polygon_bindings.c.spatial_type == "basin_average")
        .subquery()
    )
    rows = (
        conn.execute(
            sa.select(
                station_weather_sources.c.station_id,
                station_weather_sources.c.nwp_source,
                station_weather_sources.c.role,
                basin_average.c.gateway_hru_name,
                basin_average.c.name,
                basin_average.c.spatial_type,
                basin_average.c.band_id,
            )
            .select_from(
                station_weather_sources.outerjoin(
                    basin_average,
                    basin_average.c.station_id == station_weather_sources.c.station_id,
                )
            )
            .where(
                sa.and_(
                    station_weather_sources.c.status == "active",
                    source_filter,
                )
            )
            .order_by(
                station_weather_sources.c.station_id,
                station_weather_sources.c.nwp_source,
                station_weather_sources.c.role,
            )
        )
        .mappings()
        .all()
    )
    return tuple(
        NepalStaticResolverSnapshot(
            station_id=str(row["station_id"]),
            nwp_source=row["nwp_source"],
            role=row["role"],
            hru_name=row["gateway_hru_name"],
            polygon_name=row["name"],
            spatial_type=row["spatial_type"],
            band_id=row["band_id"],
        )
        for row in rows
    )


def _raise_if_nepal_static_resolver_changed(
    *,
    before: tuple[NepalStaticResolverSnapshot, ...],
    after: tuple[NepalStaticResolverSnapshot, ...],
) -> None:
    if before == after:
        return
    before_by_key = {(s.station_id, s.nwp_source, s.role): s for s in before}
    after_by_key = {(s.station_id, s.nwp_source, s.role): s for s in after}
    added = sorted(after_by_key.keys() - before_by_key.keys())
    removed = sorted(before_by_key.keys() - after_by_key.keys())
    changed = sorted(
        key
        for key in before_by_key.keys() & after_by_key.keys()
        if before_by_key[key] != after_by_key[key]
    )
    raise RuntimeError(
        "Nepal21 static import would change active ERA5/IFS resolver outcomes "
        f"(added={added!r}, removed={removed!r}, changed={changed!r})"
    )


def _validate_nepal21_static_only_args(
    package_dir: Path, expected_package_id: str | None, expected_hru: str | None
) -> None:
    if not expected_package_id or not expected_hru:
        raise ValueError(
            "--nepal21-static-only requires --expected-package-id and --expected-hru"
        )
    import json

    manifest = json.loads((package_dir / "manifest.json").read_text())
    if manifest.get("network") != "dhm":
        raise ValueError("Nepal21 static package must declare DHM network")
    if manifest.get("package_id") != expected_package_id:
        raise ValueError("Nepal21 static package_id does not match the release pin")
    hru_names = manifest.get("gateway_hru_names")
    if hru_names != [expected_hru]:
        raise ValueError("Nepal21 static HRU does not match the release pin")


def run_nepal_static_import_transaction(
    conn: sa.Connection,
    *,
    tenant_id: TenantId,
    package_dir: Path,
    expected_package_id: str,
    expected_hru: str,
    clock: Callable[[], UtcDatetime] | None = None,
) -> BasinPackageImportReport:
    from uuid import uuid5

    import sqlalchemy as sa

    from sapphire_flow.services.basin_importer import (
        build_assigned_model_features_resolver,
        import_loaded_basin_package,
    )
    from sapphire_flow.services.basin_package_loader import load_basin_package
    from sapphire_flow.services.model_registry import discover_models
    from sapphire_flow.services.nepal21_delivery_authority import (
        NEPAL21_STATION_CODES,
        OLD6_STATION_CODES,
    )
    from sapphire_flow.store._helpers import require_real_transaction
    from sapphire_flow.store.station_group_store import PgStationGroupStore
    from sapphire_flow.store.station_store import PgStationStore
    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.domain import GeoCoord

    require_real_transaction(conn, caller="run_nepal_static_import_transaction")
    _validate_nepal21_static_only_args(package_dir, expected_package_id, expected_hru)
    loaded = load_basin_package(package_dir)
    if (
        len(loaded.basins) != len(NEPAL21_STATION_CODES)
        or {basin.station_code for basin in loaded.basins} != set(NEPAL21_STATION_CODES)
        or any(
            basin.network != "dhm" or basin.gateway_hru_name != expected_hru
            for basin in loaded.basins
        )
    ):
        raise ValueError(
            "Nepal21 static package must contain exactly the "
            "approved 21 station codes and pinned HRU"
        )
    conn.execute(sa.text(_nepal21_static_guard_sql()), {"tenant_id": tenant_id})
    with conn.begin_nested():
        before = _fetch_nepal_static_resolver_snapshots(conn)
        station_store = PgStationStore(conn)

        def resolve_station(code: str, network: str) -> StationId | None:
            station = station_store.fetch_station_by_code(code, network)
            if station is None:
                return None
            if station.tenant_id != tenant_id:
                raise ValueError("Nepal21 static station belongs to a different tenant")
            return station.id

        resolved = {
            code: resolve_station(code, "dhm") for code in NEPAL21_STATION_CODES
        }
        if any(resolved[code] is None for code in OLD6_STATION_CODES):
            raise ValueError(
                "Nepal21 registration requires all old6 in the target tenant"
            )
        import_clock = clock or (lambda: ensure_utc(datetime.now(UTC)))
        for basin in loaded.basins:
            if resolved[basin.station_code] is not None:
                continue
            location = GeoCoord(lon=basin.outlet_lon, lat=basin.outlet_lat)
            conn.execute(
                sa.text(
                    "SELECT public.sf_register_nepal21_candidate("
                    ":id, :tenant, :code, :lon, :lat, :package, :created)"
                ),
                {
                    "id": uuid5(
                        tenant_id, f"dhm-test-package-candidate:{basin.station_code}"
                    ),
                    "tenant": tenant_id,
                    "code": basin.station_code,
                    "lon": location.lon,
                    "lat": location.lat,
                    "package": expected_package_id,
                    "created": import_clock(),
                },
            )

        report = import_loaded_basin_package(
            conn,
            loaded,
            resolve_station=resolve_station,
            assigned_model_features=build_assigned_model_features_resolver(
                station_store,
                PgStationGroupStore(conn),
                resolve_station,
                discover_models(),
            ),
            clock=import_clock,
        )
        if (
            report.outcome == "rejected"
            or report.onboarding_held
            or len(report.accepted) != 21
        ):
            raise ValueError(
                "Nepal21 static import requires all 21 registered "
                "stations without onboarding holds"
            )
        after = _fetch_nepal_static_resolver_snapshots(conn)
        _raise_if_nepal_static_resolver_changed(before=before, after=after)
    log.info(
        "basin_importer.nepal21_static_only_transaction_imported",
        package_dir=str(package_dir),
        package_id=expected_package_id,
        gateway_hru=expected_hru,
        outcome=report.outcome,
        imported_basins=len(report.imported_basins),
        active_resolver_consumers=len(after),
        activation="refused",
    )
    return report


def _nepal21_static_guard_sql() -> str:
    return "SELECT public.sf_lock_nepal_static_guard(:tenant_id)"


def _run_import(package_dir: Path) -> BasinPackageImportReport:
    from sapphire_flow.db.engine import create_engine_from_env
    from sapphire_flow.services.basin_importer import (
        build_assigned_model_features_resolver,
        import_basin_package_from_directory,
    )
    from sapphire_flow.services.model_registry import discover_models
    from sapphire_flow.store.station_group_store import PgStationGroupStore
    from sapphire_flow.store.station_store import PgStationStore
    from sapphire_flow.types.datetime import ensure_utc

    engine = create_engine_from_env()
    # Discovered ONCE (entry-point scan, no DB) and reused for every basin —
    # see build_assigned_model_features_resolver for why the default (None)
    # seam is never acceptable in production (Task 3A fixer round, major
    # finding).
    models = discover_models()

    def resolve_station(code: str, network: str) -> StationId | None:
        with engine.connect() as conn:
            station = PgStationStore(conn).fetch_station_by_code(code, network)
        return station.id if station is not None else None

    def assigned_model_features(basin: BasinRecord) -> frozenset[str]:
        with engine.connect() as conn:
            resolver = build_assigned_model_features_resolver(
                PgStationStore(conn),
                PgStationGroupStore(conn),
                resolve_station,
                models,
            )
            return resolver(basin)

    return import_basin_package_from_directory(
        package_dir,
        engine,
        resolve_station=resolve_station,
        assigned_model_features=assigned_model_features,
        clock=lambda: ensure_utc(datetime.now(UTC)),
    )


def _log_report(report: BasinPackageImportReport) -> None:
    log.info(
        "basin_importer.cli.report",
        package_id=report.package_id,
        outcome=report.outcome,
        accepted=len(report.accepted),
        onboarding_held=len(report.onboarding_held),
        imported_basins=len(report.imported_basins),
        rejection_reason=report.rejection_reason,
    )
    for decision in report.onboarding_held:
        log.warning(
            "basin_importer.cli.onboarding_held",
            network=decision.network,
            station_code=decision.station_code,
            basin_code=decision.basin_code,
            hold_reasons=decision.hold_reasons,
            warnings=decision.warnings,
        )
    for decision in report.accepted:
        if decision.warnings:
            log.warning(
                "basin_importer.cli.accepted_with_warnings",
                network=decision.network,
                station_code=decision.station_code,
                basin_code=decision.basin_code,
                warnings=decision.warnings,
            )
    for basin in report.imported_basins:
        if basin.material_change:
            log.warning(
                "basin_importer.cli.material_change",
                network=basin.network,
                basin_code=basin.basin_code,
                affected_artifact_ids=[str(a) for a in basin.affected_artifact_ids],
            )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("basin_importer.cli.failed")
        sys.exit(1)
