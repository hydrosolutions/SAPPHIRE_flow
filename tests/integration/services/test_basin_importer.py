"""Plan 120 Task 3A — importer orchestration + acceptance report.

Red-first acceptance tests locked from ``docs/plans/120-basin-static-
importer.md`` Task 3A, exercised against the real, contract-compliant
fixture at ``tests/fixtures/basin_static/nepal-dhm-basins/`` (loaded via the
Phase-1 loader), wrapped by ``services/basin_importer.py`` (Task 3A) around
the already-merged Phase-2 writer (``store/basin_importer.py``).

DB-touching cases use ``import_loaded_basin_package`` directly on the
per-test-rollback ``db_connection`` fixture — the SAME isolation pattern
every other DB-backed test in this repo uses (`store/basin_importer.py`'s
own test files). ``import_basin_package_from_directory`` (the engine-based
CLI entrypoint) is exercised for its Task-1A load-rejection short-circuit,
which never opens a connection, so it is safe against the SHARED,
session-scoped Postgres container — deliberately NOT exercised end-to-end
against a raw ``engine.begin()`` commit here, since that would leak
committed ``(network="dhm", basin_code="123")`` state across every other
test in the session that imports the same fixture package. It is a thin
(two-line) wrapper around ``import_loaded_basin_package``, which IS fully
exercised end-to-end below.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from sapphire_flow.db.metadata import basin_static_packages
from sapphire_flow.services.basin_importer import (
    import_basin_package_from_directory,
    import_loaded_basin_package,
)
from sapphire_flow.services.basin_package_loader import load_basin_package
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ids import StationId
from tests.conftest import make_station_config

FIXTURE_DIR = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "basin_static"
    / "nepal-dhm-basins"
)

_CLOCK_VALUE = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


def _clock() -> UtcDatetime:
    return _CLOCK_VALUE


def _seed_station(
    conn: sa.Connection, *, code: str = "123", network: str = "dhm"
) -> StationId:
    station = make_station_config(
        station_id=StationId(uuid.uuid4()), code=code, network=network, basin_id=None
    )
    PgStationStore(conn).store_station(station)
    return station.id


def _resolver(station_id: StationId | None):  # noqa: ANN202 - test helper
    return lambda code, network: (
        station_id if (code, network) == ("123", "dhm") else None
    )


class TestImporterAcceptanceReport:
    def test_end_to_end_fixture_produces_accepted_report_with_provenance(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded = load_basin_package(FIXTURE_DIR)

        report = import_loaded_basin_package(
            db_connection,
            loaded,
            resolve_station=_resolver(station_id),
            clock=_clock,
        )

        assert report.outcome == "imported"
        assert report.package_id == "nepal-dhm-basins"
        assert report.rejection_reason is None
        assert len(report.accepted) == 1
        assert report.accepted[0].basin_code == "123"
        assert report.onboarding_held == ()
        assert len(report.imported_basins) == 1
        assert report.imported_basins[0].outcome == "inserted"
        assert report.imported_basins[0].material_change is False

        provenance = db_connection.execute(
            sa.select(basin_static_packages.c.package_id).where(
                basin_static_packages.c.package_id == "nepal-dhm-basins"
            )
        ).scalar_one_or_none()
        assert provenance == "nepal-dhm-basins"

    def test_unmatched_station_is_held_in_onboarding_not_rejected(
        self, db_connection: sa.Connection
    ) -> None:
        loaded = load_basin_package(FIXTURE_DIR)

        report = import_loaded_basin_package(
            db_connection,
            loaded,
            resolve_station=_resolver(None),  # no station ever resolves
            clock=_clock,
        )

        assert report.outcome == "imported"
        assert report.accepted == ()
        assert len(report.onboarding_held) == 1
        held = report.onboarding_held[0]
        assert held.basin_code == "123"
        assert any("unmatched" in reason for reason in held.hold_reasons)
        # A package-level import with zero accepted basins still writes no
        # basin/version rows — but the run itself is NOT a rejection.
        assert report.imported_basins == ()

    def test_write_boundary_rejection_never_silently_completes(
        self, db_connection: sa.Connection
    ) -> None:
        """A stale/diverged loaded package (an accepted basin whose
        ``static_attributes`` row has gone missing) must come back as
        ``outcome="rejected"`` with a clear reason — never as a silently
        "successful" import with synthesized/empty attributes (contract
        04:670-672)."""
        station_id = _seed_station(db_connection)
        loaded = load_basin_package(FIXTURE_DIR)
        diverged = dataclasses.replace(loaded, static_attributes={})

        report = import_loaded_basin_package(
            db_connection,
            diverged,
            resolve_station=_resolver(station_id),
            clock=_clock,
        )

        assert report.outcome == "rejected"
        assert report.rejection_reason is not None
        assert "static_attributes" in report.rejection_reason
        assert report.imported_basins == ()

        # Nothing persisted — the savepoint rolled back the provenance row
        # `import_basin_package` writes before reaching the per-basin
        # attribute check.
        count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_static_packages)
            .where(basin_static_packages.c.package_id == "nepal-dhm-basins")
        ).scalar_one()
        assert count == 0

        # The savepoint rollback must not poison the caller's OUTER
        # transaction — a further statement on the same connection must
        # still succeed.
        still_alive = db_connection.execute(sa.select(sa.literal(1))).scalar_one()
        assert still_alive == 1

    def test_reimporting_identical_package_is_idempotent(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded = load_basin_package(FIXTURE_DIR)

        first = import_loaded_basin_package(
            db_connection, loaded, resolve_station=_resolver(station_id), clock=_clock
        )
        assert first.outcome == "imported"

        second = import_loaded_basin_package(
            db_connection, loaded, resolve_station=_resolver(station_id), clock=_clock
        )

        assert second.outcome == "already_imported"
        assert second.imported_basins == ()
        # Idempotency is package-level (Task 2C) — the accept/hold PARTITION
        # is still reported (Task 1B ran again), even though nothing wrote.
        assert len(second.accepted) == 1

    def test_whole_package_load_rejection_short_circuits_before_any_write(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """A directory that fails Task 1A's whole-package load (mandatory
        file missing) must be reported ``rejected`` with ``package_id=None``
        (never parsed) — proven through ``import_basin_package_from_directory``
        end to end, since load failure means the DB is never touched (safe
        against the shared session engine)."""
        empty_package_dir = tmp_path / "empty-package"
        empty_package_dir.mkdir()

        report = import_basin_package_from_directory(
            empty_package_dir,
            db_connection.engine,
            resolve_station=_resolver(None),
            clock=_clock,
        )

        assert report.outcome == "rejected"
        assert report.package_id is None
        assert report.rejection_reason is not None
        assert "manifest.json" in report.rejection_reason
        assert report.accepted == ()
        assert report.onboarding_held == ()
