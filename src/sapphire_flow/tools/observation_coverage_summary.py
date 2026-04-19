"""Daily per-station observation coverage summary.

Queries the observations table for a trailing window and reports
interval coverage (actual / expected polls) per (station, parameter).

This is a Flow 4 precursor — a band-aid for the 6-month LINDAS archive-
accumulation phase documented in docs/plans/058-bafu-lindas-archive-collection.md.
Once Flow 4 (pipeline monitoring) is implemented, this tool's scope is tracked as
a "precursor" in the Flow 4 design notes.

Operational threshold: if any station falls below 90% coverage for two
consecutive days, an ops check is due.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from sapphire_flow.logging import configure_cli_logging
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ids import StationId  # noqa: TC001

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import ObservationStore

log = structlog.get_logger(__name__)

_DEFAULT_PARAMETERS = ["discharge", "water_level", "water_temperature"]


@dataclass(frozen=True, kw_only=True, slots=True)
class StationParameterCoverage:
    station_id: StationId
    parameter: str
    actual_count: int
    expected_count: int
    coverage_pct: float


@dataclass(frozen=True, kw_only=True, slots=True)
class CoverageSummary:
    window_start: UtcDatetime
    window_end: UtcDatetime
    window_hours: int
    expected_cadence_minutes: int
    rows: list[StationParameterCoverage]

    @property
    def total_actual(self) -> int:
        return sum(r.actual_count for r in self.rows)

    @property
    def total_expected(self) -> int:
        return sum(r.expected_count for r in self.rows)

    @property
    def overall_coverage_pct(self) -> float:
        if self.total_expected == 0:
            return 0.0
        return 100.0 * self.total_actual / self.total_expected


def compute_coverage_summary(
    store: ObservationStore,
    station_ids: list[StationId],
    *,
    now: UtcDatetime,
    parameters: list[str] | None = None,
    window_hours: int = 24,
    expected_cadence_minutes: int = 10,
) -> CoverageSummary:
    """Compute per-(station, parameter) interval coverage over the trailing window.

    Only stations that have at least one observation within the window appear in
    the summary.  Stations with zero observations are silently omitted to avoid
    flooding logs with stations not yet onboarded.
    """
    if parameters is None:
        parameters = _DEFAULT_PARAMETERS

    window_end = now
    window_start = UtcDatetime(now - timedelta(hours=window_hours))
    expected_per_station = round((window_hours * 60) / expected_cadence_minutes)

    rows: list[StationParameterCoverage] = []

    for parameter in parameters:
        batch = store.fetch_observations_batch(
            station_ids=station_ids,
            parameter=parameter,
            start=window_start,
            end=window_end,
        )
        for station_id, observations in batch.items():
            count = len(observations)
            if count == 0:
                continue
            coverage_pct = 100.0 * count / expected_per_station
            rows.append(
                StationParameterCoverage(
                    station_id=station_id,
                    parameter=parameter,
                    actual_count=count,
                    expected_count=expected_per_station,
                    coverage_pct=round(coverage_pct, 1),
                )
            )
            log.info(
                "coverage.station.computed",
                station_id=str(station_id),
                parameter=parameter,
                actual_count=count,
                expected_count=expected_per_station,
                coverage_pct=round(coverage_pct, 1),
            )

    summary = CoverageSummary(
        window_start=window_start,
        window_end=window_end,
        window_hours=window_hours,
        expected_cadence_minutes=expected_cadence_minutes,
        rows=rows,
    )
    log.info(
        "coverage.summary.computed",
        window_hours=window_hours,
        station_count=len({r.station_id for r in rows}),
        total_actual=summary.total_actual,
        total_expected=summary.total_expected,
        overall_coverage_pct=summary.overall_coverage_pct,
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    configure_cli_logging()
    parser = argparse.ArgumentParser(
        description="Emit per-station observation coverage for the trailing window."
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL connection URL (falls back to DATABASE_URL env var)",
    )
    parser.add_argument(
        "--window-hours",
        type=int,
        default=24,
        help="Trailing window in hours (default: 24)",
    )
    parser.add_argument(
        "--expected-cadence-minutes",
        type=int,
        default=10,
        help="Expected poll cadence in minutes (default: 10)",
    )
    args = parser.parse_args(argv)

    database_url: str | None = args.database_url
    if not database_url:
        log.error(
            "coverage.cli.missing_database_url",
            hint="Pass --database-url or set DATABASE_URL",
        )
        return 1

    try:
        import sqlalchemy as sa

        from sapphire_flow.store.observation_store import PgObservationStore
        from sapphire_flow.store.station_store import PgStationStore

        engine = sa.create_engine(database_url)
        with engine.connect() as conn:
            station_store = PgStationStore(conn)
            station_configs = station_store.fetch_all_stations()
            station_ids = [s.id for s in station_configs]

            obs_store = PgObservationStore(conn)
            now = ensure_utc(datetime.now(UTC))
            summary = compute_coverage_summary(
                obs_store,
                station_ids,
                now=now,
                window_hours=args.window_hours,
                expected_cadence_minutes=args.expected_cadence_minutes,
            )
    except Exception as exc:
        log.error("coverage.cli.db_error", error=str(exc))
        return 1

    log.info(
        "coverage.cli.done",
        overall_coverage_pct=summary.overall_coverage_pct,
        station_count=len({r.station_id for r in summary.rows}),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
