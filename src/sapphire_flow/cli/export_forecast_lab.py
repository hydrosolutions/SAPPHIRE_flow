"""Plan 198 T6 — CLI export of the Forecast Lab snapshot, mirroring
`cli/bafu_observation_audit.py`'s module-invocation convention (F10). The
other surface (T5's REST route) and this CLI both call the same
`services.forecast_lab.snapshot.build_snapshot()` (D1) and serialise the
same Pydantic model — no second implementation.

Run via (module invocation — F10, no console-script entry):

    python -m sapphire_flow.cli.export_forecast_lab \\
        --output /path/to/snapshot.json \\
        [--station-code 2009 --station-code 2091] \\
        [--observation-hours 168]

With no `--station-code`, every eligible (operational BAFU river) station
is exported (D17a) — this is an operator tool, not a scoped consumer, so
there is no principal/scoping concept here (compare T5's route).

Exit code: non-zero on total failure (unknown `--station-code`, a DB or
config error — all propagate as an uncaught exception, Python's default
exit code 1); zero on a successful export, even one carrying a `partial`
or `unavailable` `status.overall` — that is a data-availability fact
inside a validly-written document, not a CLI failure (D13's partial-vs-
failure distinction applies here too).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import jsonschema
import structlog

from sapphire_flow.api.forecast_lab_schemas import ForecastLabSnapshot
from sapphire_flow.config.deployment import load_config
from sapphire_flow.config.paths import resolve_artifact_dir
from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.services.forecast_lab.db_sources import (
    ForecastLabStores,
    fetch_eligible_station_by_code,
    fetch_eligible_stations,
)
from sapphire_flow.services.forecast_lab.snapshot import build_snapshot
from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable

    import sqlalchemy as sa

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger(__name__)

_DEFAULT_OBSERVATION_HOURS = 168


def _forecast_lab_stores(conn: sa.Connection) -> ForecastLabStores:
    from sapphire_flow.store.basin_store import PgBasinStore
    from sapphire_flow.store.forecast_store import PgForecastStore
    from sapphire_flow.store.model_artifact_provenance import (
        PgArtifactProvenanceStore,
    )
    from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
    from sapphire_flow.store.model_store import PgModelStore
    from sapphire_flow.store.observation_store import PgObservationStore
    from sapphire_flow.store.station_store import PgStationStore

    return ForecastLabStores(
        station_store=PgStationStore(conn),
        observation_store=PgObservationStore(conn),
        forecast_store=PgForecastStore(conn),
        model_store=PgModelStore(conn),
        artifact_store=PgModelArtifactStore(conn, resolve_artifact_dir()),
        provenance_store=PgArtifactProvenanceStore(conn),
        basin_store=PgBasinStore(conn),
    )


def _resolve_stations(
    stores: ForecastLabStores, station_codes: list[str]
) -> list[StationConfig]:
    if station_codes:
        resolved: list[StationConfig] = []
        for code in station_codes:
            # D17a — same eligible-set resolution as T5's route.
            station = fetch_eligible_station_by_code(stores, code)
            if station is None:
                raise SystemExit(
                    "export_forecast_lab: unknown or ineligible "
                    f"--station-code: {code!r}"
                )
            resolved.append(station)
        return resolved
    return fetch_eligible_stations(stores)


def _write_atomically(output_path: Path, payload: dict[str, object]) -> None:
    """AC17 — temp write -> schema validation -> `os.replace`. A failure at
    any point leaves no partial file at `output_path`: the temp file is
    removed on any exception and `output_path` itself is only ever touched
    by the final, all-or-nothing `os.replace`."""
    jsonschema.validate(
        instance=payload, schema=ForecastLabSnapshot.model_json_schema()
    )
    tmp_path = output_path.with_name(f".{output_path.name}.tmp")
    try:
        tmp_path.write_text(json.dumps(payload, indent=2))
        os.replace(tmp_path, output_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def export_forecast_lab_snapshot(
    stores: ForecastLabStores,
    *,
    archive_base_path: Path | None,
    station_codes: list[str],
    observation_hours: int,
    output_path: Path,
    clock: Callable[[], UtcDatetime],
) -> ForecastLabSnapshot:
    """The testable core (D1/D20): takes an already-constructed
    `ForecastLabStores` (fakes in tests, `_forecast_lab_stores(conn)` in
    `main()`) rather than a raw connection, so it needs no real database to
    exercise."""
    stations = _resolve_stations(stores, station_codes)
    snapshot = build_snapshot(
        stores,
        stations=stations,
        archive_base_path=archive_base_path,
        observation_hours=observation_hours,
        clock=clock,
    )
    _write_atomically(output_path, snapshot.model_dump(mode="json"))
    return snapshot


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Export a Forecast Lab snapshot (forecast-lab-snapshot/v1) to a "
            "JSON file (Plan 198 T6)."
        )
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--station-code",
        action="append",
        default=[],
        help="Repeatable. Omit to export every eligible station (D17a).",
    )
    parser.add_argument(
        "--observation-hours", type=int, default=_DEFAULT_OBSERVATION_HOURS
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from sapphire_flow.logging import configure_cli_logging

    configure_cli_logging()
    args = _parse_args(argv)
    config = load_config()
    engine = create_engine_from_env()
    with engine.begin() as conn:
        snapshot = export_forecast_lab_snapshot(
            _forecast_lab_stores(conn),
            archive_base_path=config.bafu_forecast_archive_path,
            station_codes=args.station_code,
            observation_hours=args.observation_hours,
            output_path=args.output,
            clock=lambda: ensure_utc(datetime.now(UTC)),
        )

    log.info(
        "export_forecast_lab.complete",
        output=str(args.output),
        snapshot_id=snapshot.snapshot_id,
        overall_status=snapshot.status.overall,
        station_count=len(snapshot.stations),
    )
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
