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
import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa

from sapphire_flow.db.metadata import basin_static_packages
from sapphire_flow.services.basin_importer import (
    build_assigned_model_features_resolver,
    import_basin_package_from_directory,
    import_loaded_basin_package,
)
from sapphire_flow.services.basin_package_loader import load_basin_package
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.model_store import PgModelStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import ArtifactScope, ModelAssignmentStatus
from sapphire_flow.types.ids import BasinId, ModelId, StationGroupId, StationId
from sapphire_flow.types.model import ModelRecord
from sapphire_flow.types.station import (
    GroupModelAssignment,
    ModelAssignment,
    StationGroup,
)
from tests.conftest import make_station_config
from tests.fakes.fake_models import FakeStationForecastModel

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


@contextmanager
def _nested_txn(conn: sa.Connection):  # type: ignore[return] # noqa: ANN201
    """A SAVEPOINT-scoped ``transaction_factory`` for ``PgStationGroupStore``
    — its default opens a brand-new ``engine.begin()`` transaction that would
    commit independently of ``db_connection``'s outer, per-test-rollback
    transaction (see ``tests/integration/store/test_station_group_store.py``
    for the same pattern)."""
    with conn.begin_nested():
        yield conn


def _seed_model(conn: sa.Connection, model_id: ModelId) -> None:
    PgModelStore(conn).register_model(
        ModelRecord(
            id=model_id,
            display_name=str(model_id),
            artifact_scope=ArtifactScope.STATION,
            description="",
            created_at=_CLOCK_VALUE,
        )
    )


def _fake_model(static_features: frozenset[str]) -> FakeStationForecastModel:
    model = FakeStationForecastModel()
    model.data_requirements = dataclasses.replace(
        model.data_requirements, static_features=static_features
    )
    return model


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

        # Fixer round (blocker, 2026-07-23): a run whose accepted set is
        # EMPTY writes zero basins, so it reports "already_imported" (never
        # "imported" — that mislabel is exactly what let a fully-held
        # package permanently "lock" its package_id under the old
        # package-level gate) and writes NO `basin_static_packages`
        # provenance row (see `TestReimportPicksUpFormerlyHeldBasins` below).
        assert report.outcome == "already_imported"
        assert report.accepted == ()
        assert len(report.onboarding_held) == 1
        held = report.onboarding_held[0]
        assert held.basin_code == "123"
        assert any("unmatched" in reason for reason in held.hold_reasons)
        # A run with zero accepted basins still writes no basin/version rows
        # — but the run itself is NOT a rejection.
        assert report.imported_basins == ()
        provenance_count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_static_packages)
            .where(basin_static_packages.c.package_id == "nepal-dhm-basins")
        ).scalar_one()
        assert provenance_count == 0

    def test_gauge_id_divergence_rejects_at_acceptance_before_any_write(
        self, db_connection: sa.Connection
    ) -> None:
        """A stale/diverged loaded package (an accepted basin whose
        ``static_attributes`` row has gone missing) must come back as
        ``outcome="rejected"`` with a clear reason — never as a silently
        "successful" import with synthesized/empty attributes (contract
        04:670-672). NOTE: this rejection fires in Task 1B's
        ``_validate_gauge_id_join``, BEFORE ``import_loaded_basin_package``
        ever opens the write-boundary SAVEPOINT — it does not exercise
        ``savepoint.rollback()``. See
        ``test_write_boundary_failure_after_partial_write_rolls_back_whole_package``
        below for the genuine write-boundary/rollback case (fixer round,
        major finding: the two are NOT the same code path)."""
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

        count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_static_packages)
            .where(basin_static_packages.c.package_id == "nepal-dhm-basins")
        ).scalar_one()
        assert count == 0

        still_alive = db_connection.execute(sa.select(sa.literal(1))).scalar_one()
        assert still_alive == 1

    def test_write_boundary_failure_after_partial_write_rolls_back_whole_package(
        self, db_connection: sa.Connection
    ) -> None:
        """A genuine POST-provenance-insert write-boundary failure (Task
        2A/2C, inside ``store.basin_importer._assign_station_basin``) must
        roll back EVERYTHING the package wrote — including a basin whose
        write already completed before the failing one — proving the
        SAVEPOINT (``conn.begin_nested()``, ``basin_importer.py:95``) is
        load-bearing.

        Two basins in one package: the first basin's write completes
        (provenance row + basin row inserted) before the second basin's
        write fails — its resolved station is already bound to a DIFFERENT,
        pre-existing basin, a conflict ``_assign_station_basin`` rejects.
        This check has no equivalent in the pre-write-loop
        ``_reenforce_write_invariants`` pass, so — unlike the
        ``static_attributes``-divergence case above — it genuinely cannot be
        short-circuited before the SAVEPOINT opens (fixer round, major
        finding)."""
        station_a = _seed_station(db_connection, code="123", network="dhm")
        station_b = _seed_station(db_connection, code="456", network="dhm")

        loaded = load_basin_package(FIXTURE_DIR)
        basin_a = loaded.basins[0]
        basin_b = dataclasses.replace(
            basin_a, station_code="456", basin_code="456", gauge_id="nepal_456"
        )

        # A pre-existing basin, unrelated to this package, already bound to
        # station_b — this is what makes basin_b's write fail.
        other_basin_id = BasinId(uuid.uuid4())
        PgBasinStore(db_connection).store_basin(
            Basin(
                id=other_basin_id,
                code="other-basin",
                name="other",
                geometry=basin_a.geometry,
                area_km2=1.0,
                attributes={},
                band_geometries=None,
                created_at=_clock(),
                network="dhm",
            )
        )
        PgStationStore(db_connection).assign_basin(station_b, other_basin_id)

        diverged = dataclasses.replace(
            loaded,
            basins=(basin_a, basin_b),
            static_attributes={
                **loaded.static_attributes,
                "nepal_456": dict(loaded.static_attributes["nepal_123"]),
            },
        )

        def resolve(code: str, network: str) -> StationId | None:
            return {("123", "dhm"): station_a, ("456", "dhm"): station_b}.get(
                (code, network)
            )

        report = import_loaded_basin_package(
            db_connection, diverged, resolve_station=resolve, clock=_clock
        )

        assert report.outcome == "rejected"
        assert report.rejection_reason is not None
        assert "already bound to basin" in report.rejection_reason
        assert report.imported_basins == ()

        # The whole package rolled back — including basin_a, whose write
        # completed BEFORE basin_b's failed.
        provenance_count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_static_packages)
            .where(basin_static_packages.c.package_id == "nepal-dhm-basins")
        ).scalar_one()
        assert provenance_count == 0
        assert PgBasinStore(db_connection).fetch_basin_by_code("123", "dhm") is None

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
        # Idempotency is BASIN-aware (fixer round, blocker, 2026-07-23) — this
        # basin's current version already carries this package_id, so it is
        # skipped. The accept/hold PARTITION is still reported (Task 1B ran
        # again) even though nothing wrote — see
        # `TestReimportPicksUpFormerlyHeldBasins` for the case where a rerun
        # DOES import a (formerly held) basin under an otherwise-unchanged,
        # already-provenanced package.
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


class TestAssignedModelFeaturesProductionWiring:
    """Fixer round, major finding: the CLI must build and pass a REAL
    ``assigned_model_features`` resolver (``build_assigned_model_features_
    resolver``) — the default ``None`` seam treats every basin as
    unassigned, downgrading a null required-static-feature to a warning
    instead of an onboarding hold. These tests exercise the resolver wired
    through the full ``import_loaded_basin_package`` pipeline against a real
    DB — station-level assignment and group-level assignment, each proving
    the held basin is NOT persisted."""

    def _diverged_with_null_required_feature(self):  # noqa: ANN202 - test helper
        """The fixture package with ``ele_mt_sav`` promoted to
        catalog-``required_by_models`` and nulled out for the one basin."""
        loaded = load_basin_package(FIXTURE_DIR)
        catalog = tuple(
            dataclasses.replace(entry, required_by_models=("nulls_required_model",))
            if entry.name == "ele_mt_sav"
            else entry
            for entry in loaded.feature_catalog
        )
        static_attributes = {
            **loaded.static_attributes,
            "nepal_123": {
                **loaded.static_attributes["nepal_123"],
                "ele_mt_sav": None,
            },
        }
        return dataclasses.replace(
            loaded, feature_catalog=catalog, static_attributes=static_attributes
        )

    def test_active_station_assignment_holds_and_does_not_persist(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        model_id = ModelId("nulls_required_model")
        _seed_model(db_connection, model_id)
        PgStationStore(db_connection).store_model_assignment(
            ModelAssignment(
                station_id=station_id,
                model_id=model_id,
                time_step=timedelta(hours=24),
                status=ModelAssignmentStatus.ACTIVE,
                priority=1,
                created_at=_CLOCK_VALUE,
            )
        )
        resolver = build_assigned_model_features_resolver(
            PgStationStore(db_connection),
            PgStationGroupStore(db_connection),
            resolve_station=_resolver(station_id),
            models={model_id: _fake_model(frozenset({"ele_mt_sav"}))},
        )

        report = import_loaded_basin_package(
            db_connection,
            self._diverged_with_null_required_feature(),
            resolve_station=_resolver(station_id),
            assigned_model_features=resolver,
            clock=_clock,
        )

        assert report.accepted == ()
        assert len(report.onboarding_held) == 1
        held = report.onboarding_held[0]
        assert any("ele_mt_sav" in reason for reason in held.hold_reasons)
        # A run with zero accepted basins writes no basin/version rows for
        # THIS basin — and (fixer round, blocker, 2026-07-23) no
        # `basin_static_packages` provenance row either, matching
        # test_unmatched_station_is_held_in_onboarding_not_rejected above.
        assert report.imported_basins == ()
        assert PgBasinStore(db_connection).fetch_basin_by_code("123", "dhm") is None

    def test_active_group_assignment_holds_and_does_not_persist(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        model_id = ModelId("nulls_required_model")
        _seed_model(db_connection, model_id)
        group_id = StationGroupId(uuid.uuid4())
        group_store = PgStationGroupStore(
            db_connection, transaction_factory=lambda: _nested_txn(db_connection)
        )
        group_store.store_group(
            StationGroup(
                id=group_id,
                name="group-1",
                station_ids=frozenset({station_id}),
                created_at=_CLOCK_VALUE,
            )
        )
        group_store.store_group_model_assignment(
            GroupModelAssignment(
                group_id=group_id,
                model_id=model_id,
                time_step=timedelta(hours=24),
                status=ModelAssignmentStatus.ACTIVE,
                priority=1,
                created_at=_CLOCK_VALUE,
            )
        )
        resolver = build_assigned_model_features_resolver(
            PgStationStore(db_connection),
            group_store,
            resolve_station=_resolver(station_id),
            models={model_id: _fake_model(frozenset({"ele_mt_sav"}))},
        )

        report = import_loaded_basin_package(
            db_connection,
            self._diverged_with_null_required_feature(),
            resolve_station=_resolver(station_id),
            assigned_model_features=resolver,
            clock=_clock,
        )

        assert report.accepted == ()
        assert len(report.onboarding_held) == 1
        held = report.onboarding_held[0]
        assert any("ele_mt_sav" in reason for reason in held.hold_reasons)
        # A run with zero accepted basins writes no basin/version rows for
        # THIS basin — and (fixer round, blocker, 2026-07-23) no
        # `basin_static_packages` provenance row either, matching
        # test_unmatched_station_is_held_in_onboarding_not_rejected above.
        assert report.imported_basins == ()
        assert PgBasinStore(db_connection).fetch_basin_by_code("123", "dhm") is None


class TestReimportPicksUpFormerlyHeldBasins:
    """Fixer round (blocker, 2026-07-23): idempotency must be BASIN-aware,
    not package-aware. Under the old package-level gate, a first run that
    wrote `basin_static_packages` provenance for ANY reason (even zero
    accepted basins) permanently short-circuited every later run over the
    identical (unchanged) package, so a formerly onboarding-held basin could
    never be picked up once its hold cleared — contrary to the runbook.
    Locking tests for the reviewer's three required scenarios."""

    def test_all_held_first_run_then_resolved_rerun_imports_the_basin(
        self, db_connection: sa.Connection
    ) -> None:
        """(1) All-held first run -> resolve the hold -> an identical rerun
        imports the basin. Against the buggy package-level gate, the first
        run's ``proceed`` branch still writes a `basin_static_packages` row
        unconditionally (before looping over the empty `accepted` set), so
        the second run's identical fingerprint hits `no_op` immediately and
        NEVER imports basin "123" even though its station now resolves."""
        loaded = load_basin_package(FIXTURE_DIR)

        first = import_loaded_basin_package(
            db_connection, loaded, resolve_station=_resolver(None), clock=_clock
        )
        assert first.outcome == "already_imported"
        assert first.imported_basins == ()
        assert first.onboarding_held[0].basin_code == "123"
        provenance_after_first = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_static_packages)
            .where(basin_static_packages.c.package_id == "nepal-dhm-basins")
        ).scalar_one()
        assert provenance_after_first == 0

        station_id = _seed_station(db_connection)
        second = import_loaded_basin_package(
            db_connection, loaded, resolve_station=_resolver(station_id), clock=_clock
        )

        assert second.outcome == "imported"
        assert len(second.imported_basins) == 1
        assert second.imported_basins[0].basin_code == "123"
        assert second.imported_basins[0].outcome == "inserted"
        assert PgBasinStore(db_connection).fetch_basin_by_code("123", "dhm") is not None

    def _two_basin_loaded_package(self):  # noqa: ANN202 - test helper
        """The real fixture's one basin ("123") plus an in-memory-only
        duplicate ("456") under a different station — Task 2A/2C operate
        purely on the loaded domain objects, never re-reading files (same
        pattern as `TestImporterAcceptanceReport`'s write-boundary-rollback
        test above). `compute_package_fingerprint` does not read
        `loaded.basins`/`static_attributes` (only manifest fields + computed
        checksums), so adding a second basin leaves the fingerprint — and
        therefore idempotency across the three runs below — unchanged."""
        loaded = load_basin_package(FIXTURE_DIR)
        basin_a = loaded.basins[0]
        basin_b = dataclasses.replace(
            basin_a, station_code="456", basin_code="456", gauge_id="nepal_456"
        )
        return dataclasses.replace(
            loaded,
            basins=(basin_a, basin_b),
            static_attributes={
                **loaded.static_attributes,
                "nepal_456": dict(loaded.static_attributes["nepal_123"]),
            },
        )

    def test_mixed_first_run_then_rerun_imports_only_the_formerly_held_basin(
        self, db_connection: sa.Connection
    ) -> None:
        """(2) Mixed accepted/held first run -> rerun imports ONLY the
        formerly held basin, leaving the already-imported one untouched
        (never re-corrected). (3) A third, fully-identical run is a TRUE
        no-op — proving the provenance row is written exactly ONCE (a
        second unconditional insert would crash on the provenance primary
        key)."""
        station_a = _seed_station(db_connection, code="123", network="dhm")
        loaded = self._two_basin_loaded_package()

        def resolve_only_a(code: str, network: str) -> StationId | None:
            return station_a if (code, network) == ("123", "dhm") else None

        first = import_loaded_basin_package(
            db_connection, loaded, resolve_station=resolve_only_a, clock=_clock
        )
        assert first.outcome == "imported"
        assert {b.basin_code for b in first.imported_basins} == {"123"}
        assert {d.basin_code for d in first.onboarding_held} == {"456"}

        station_b = _seed_station(db_connection, code="456", network="dhm")

        def resolve_both(code: str, network: str) -> StationId | None:
            return {"123": station_a, "456": station_b}.get(code)

        second = import_loaded_basin_package(
            db_connection, loaded, resolve_station=resolve_both, clock=_clock
        )

        assert second.outcome == "imported"
        assert {b.basin_code for b in second.imported_basins} == {"456"}
        assert second.imported_basins[0].outcome == "inserted"

        third = import_loaded_basin_package(
            db_connection, loaded, resolve_station=resolve_both, clock=_clock
        )
        assert third.outcome == "already_imported"
        assert third.imported_basins == ()

        pkg_row_count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_static_packages)
            .where(basin_static_packages.c.package_id == "nepal-dhm-basins")
        ).scalar_one()
        assert pkg_row_count == 1


class TestCliEntrypointRealTransaction:
    """Fixer round (minor, 2026-07-23): ``import_basin_package_from_directory``'s
    happy path was previously exercised only via its Task 1A load-rejection
    short-circuit (`test_whole_package_load_rejection_short_circuits_before_
    any_write` above, which never opens a connection) — the CLI's own unit
    tests mock the function out entirely. This proves the REAL ``engine.
    begin()`` transaction it opens and hands to ``import_loaded_basin_
    package`` actually wires up and commits, on a DEDICATED, throwaway
    Postgres container (same pattern as ``test_e2e_pipeline.py``'s
    ``e2e_engine``) — never against the SHARED, session-scoped ``db_engine``
    every other test in this file uses, since a real commit there would leak
    ``(network="dhm", basin_code="123")`` state across the rest of the
    session (see the module docstring)."""

    def test_end_to_end_from_directory_commits_through_a_real_engine_begin(
        self,
    ) -> None:
        from alembic.config import Config
        from testcontainers.postgres import PostgresContainer

        from alembic import command

        with PostgresContainer(
            image="postgis/postgis:16-3.4",
            username="test",
            password="test",
            dbname="sapphire_cli_entrypoint_test",
        ) as pg:
            url = pg.get_connection_url().replace("+psycopg2", "+psycopg")
            os.environ["DATABASE_URL"] = url
            engine = sa.create_engine(url)
            try:
                alembic_cfg = Config("alembic.ini")
                alembic_cfg.set_main_option("sqlalchemy.url", url)
                command.upgrade(alembic_cfg, "head")

                with engine.begin() as conn:
                    station_id = _seed_station(conn)

                report = import_basin_package_from_directory(
                    FIXTURE_DIR,
                    engine,
                    resolve_station=_resolver(station_id),
                    clock=_clock,
                )

                assert report.outcome == "imported"
                assert report.package_id == "nepal-dhm-basins"
                assert len(report.imported_basins) == 1
                assert report.imported_basins[0].basin_code == "123"

                # A FRESH connection proves the write actually COMMITTED
                # through the real `engine.begin()` — not merely visible
                # within the transaction that wrote it.
                with engine.connect() as verify_conn:
                    stored = PgBasinStore(verify_conn).fetch_basin_by_code("123", "dhm")
                    assert stored is not None
                    provenance_count = verify_conn.execute(
                        sa.select(sa.func.count()).select_from(basin_static_packages)
                    ).scalar_one()
                    assert provenance_count == 1
            finally:
                engine.dispose()
