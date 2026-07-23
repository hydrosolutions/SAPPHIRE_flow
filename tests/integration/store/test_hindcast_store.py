from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import structlog.testing

from sapphire_flow.db.metadata import (
    hindcast_forecasts,
    hindcast_values,
    model_artifacts,
    models,
    stations,
)
from sapphire_flow.store.hindcast_store import PgHindcastStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import EnsembleRepresentation, ForcingType, QcStatus
from sapphire_flow.types.forecast import HindcastForecast
from sapphire_flow.types.ids import ArtifactId, HindcastForecastId, ModelId, StationId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.conftest import make_forecast_ensemble


class _SpyConn:
    """Proxy that records every statement executed through it."""

    def __init__(self, real: sa.Connection) -> None:
        self._real = real
        self.executed: list[object] = []

    def execute(self, stmt: object, *a: object, **k: object) -> object:
        self.executed.append(stmt)
        return self._real.execute(stmt, *a, **k)  # type: ignore[arg-type]

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)


@contextmanager
def _savepoint_spy_factory(conn: sa.Connection):  # type: ignore[return]
    spy = _SpyConn(conn)
    with conn.begin_nested():
        yield spy


def savepoint_factory(conn: sa.Connection):
    return lambda: _savepoint_spy_factory(conn)


def _capturing_spy_factory(conn: sa.Connection) -> tuple[list[_SpyConn], object]:
    """Return (spies list, factory) so tests can inspect the captured spy."""
    spies: list[_SpyConn] = []

    @contextmanager
    def _factory():  # type: ignore[return]
        spy = _SpyConn(conn)
        spies.append(spy)
        with conn.begin_nested():
            yield spy

    return spies, _factory


_T0 = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_T1 = ensure_utc(datetime(2025, 6, 1, tzinfo=UTC))
_T2 = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return ensure_utc(datetime(year, month, day, hour, tzinfo=UTC))


def _seed_station(
    conn: sa.Connection, *, tenant_id: object | None = DEFAULT_TENANT_ID
) -> StationId:
    # ``tenant_id`` is included only when not None so this helper is reusable by
    # migration tests that seed at a PRE-tenant revision (no tenant_id column
    # yet) — they pass ``tenant_id=None``. On the current (head) schema the
    # column is NOT NULL with no default, so it must be named explicitly.
    sid = StationId(uuid4())
    values: dict[str, object] = {
        "id": sid,
        "code": f"HC-{sid.hex[:6]}",
        "name": "Hindcast Test Station",
        "location": "SRID=4326;POINT(8.5 47.4)",
        "station_kind": "river",
        "network": "bafu",
        "timezone": "Europe/Zurich",
        "measured_parameters": ["discharge"],
        "ownership": "own",
    }
    if tenant_id is not None:
        values["tenant_id"] = tenant_id
    conn.execute(sa.insert(stations).values(**values))
    return sid


def _seed_model(conn: sa.Connection) -> ModelId:
    mid = ModelId(f"hc_model_{uuid.uuid4().hex[:8]}")
    conn.execute(
        sa.insert(models).values(
            id=mid,
            display_name="Hindcast Test Model",
            artifact_scope="station",
            description="Integration test hindcast",
        )
    )
    return mid


def _seed_artifact(
    conn: sa.Connection, model_id: ModelId, station_id: StationId
) -> ArtifactId:
    aid = ArtifactId(uuid4())
    conn.execute(
        sa.insert(model_artifacts).values(
            id=aid,
            model_id=model_id,
            station_id=station_id,
            group_id=None,
            status="active",
            artifact_path=f"artifacts/{aid}.bin",
            sha256_hash="",
            training_period_start=_T0,
            training_period_end=_T1,
            trained_at=_T2,
        )
    )
    return aid


def _make_hindcast(
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    *,
    hindcast_step: object | None = None,
    forcing_type: ForcingType = ForcingType.NWP_ARCHIVE,
    hindcast_run_id: UUID | None = None,
    representation: EnsembleRepresentation = EnsembleRepresentation.MEMBERS,
    n_members: int = 3,
    n_steps: int = 5,
    created_at: datetime | None = None,
    parameter: str = "discharge",
    units: str = "m³/s",
) -> HindcastForecast:
    step = hindcast_step if hindcast_step is not None else _utc(2025, 3, 1)
    run_id = hindcast_run_id or uuid4()
    ensemble = make_forecast_ensemble(
        station_id=station_id,
        representation=representation,
        n_members=n_members,
        n_steps=n_steps,
        parameter=parameter,
        units=units,
    )
    return HindcastForecast(
        id=HindcastForecastId(uuid4()),
        station_id=station_id,
        model_id=model_id,
        model_artifact_id=artifact_id,
        hindcast_step=step,  # type: ignore[arg-type]
        forcing_type=forcing_type,
        representation=representation,
        hindcast_run_id=run_id,
        ensemble=ensemble,
        created_at=created_at if created_at is not None else _T0,
    )


class TestStoreAndFetch:
    def test_store_and_fetch(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        step = _utc(2025, 3, 1)
        hindcast = _make_hindcast(sid, mid, aid, hindcast_step=step)
        returned_id = store.store_hindcast(hindcast)

        assert returned_id == hindcast.id

        results = store.fetch_hindcasts(sid, mid, _utc(2025, 2, 28), _utc(2025, 3, 2))
        assert len(results) == 1
        fetched = results[0]

        assert fetched.id == hindcast.id
        assert fetched.station_id == sid
        assert fetched.model_id == mid
        assert fetched.model_artifact_id == aid
        assert fetched.hindcast_step == step
        assert fetched.forcing_type == ForcingType.NWP_ARCHIVE
        assert fetched.representation == EnsembleRepresentation.MEMBERS
        assert fetched.hindcast_run_id == hindcast.hindcast_run_id
        assert fetched.ensemble.parameter == hindcast.ensemble.parameter
        assert fetched.ensemble.units == hindcast.ensemble.units
        assert not fetched.ensemble.values.is_empty()


class TestFetchHalfOpenRange:
    def test_half_open_range(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        run_id = uuid4()

        step_before = _utc(2025, 4, 30)
        step_start = _utc(2025, 5, 1)
        step_mid = _utc(2025, 5, 2)
        step_end = _utc(2025, 5, 3)

        for step in [step_before, step_start, step_mid, step_end]:
            store.store_hindcast(
                _make_hindcast(
                    sid, mid, aid, hindcast_step=step, hindcast_run_id=run_id
                )
            )

        results = store.fetch_hindcasts(sid, mid, step_start, step_end)
        fetched_steps = {h.hindcast_step for h in results}

        assert step_before not in fetched_steps
        assert step_start in fetched_steps
        assert step_mid in fetched_steps
        assert step_end not in fetched_steps


class TestFetchWithForcingTypeFilter:
    def test_forcing_type_filter(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        step = _utc(2025, 6, 1)
        run_id = uuid4()

        store.store_hindcast(
            _make_hindcast(
                sid,
                mid,
                aid,
                hindcast_step=step,
                forcing_type=ForcingType.NWP_ARCHIVE,
                hindcast_run_id=run_id,
            )
        )
        store.store_hindcast(
            _make_hindcast(
                sid,
                mid,
                aid,
                hindcast_step=step,
                forcing_type=ForcingType.REANALYSIS,
                hindcast_run_id=run_id,
            )
        )

        nwp_results = store.fetch_hindcasts(
            sid,
            mid,
            _utc(2025, 5, 31),
            _utc(2025, 6, 2),
            forcing_type=ForcingType.NWP_ARCHIVE,
        )
        assert len(nwp_results) == 1
        assert nwp_results[0].forcing_type == ForcingType.NWP_ARCHIVE

        rean_results = store.fetch_hindcasts(
            sid,
            mid,
            _utc(2025, 5, 31),
            _utc(2025, 6, 2),
            forcing_type=ForcingType.REANALYSIS,
        )
        assert len(rean_results) == 1
        assert rean_results[0].forcing_type == ForcingType.REANALYSIS

        all_results = store.fetch_hindcasts(
            sid, mid, _utc(2025, 5, 31), _utc(2025, 6, 2)
        )
        assert len(all_results) == 2


class TestFetchWithRunIdFilter:
    def test_run_id_filter(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        step = _utc(2025, 7, 1)
        run_a = uuid4()
        run_b = uuid4()

        store.store_hindcast(
            _make_hindcast(sid, mid, aid, hindcast_step=step, hindcast_run_id=run_a)
        )
        store.store_hindcast(
            _make_hindcast(sid, mid, aid, hindcast_step=step, hindcast_run_id=run_b)
        )

        results_a = store.fetch_hindcasts(
            sid, mid, _utc(2025, 6, 30), _utc(2025, 7, 2), hindcast_run_id=run_a
        )
        assert len(results_a) == 1
        assert results_a[0].hindcast_run_id == run_a

        results_b = store.fetch_hindcasts(
            sid, mid, _utc(2025, 6, 30), _utc(2025, 7, 2), hindcast_run_id=run_b
        )
        assert len(results_b) == 1
        assert results_b[0].hindcast_run_id == run_b


class TestFetchEmpty:
    def test_fetch_returns_empty_list(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(db_connection)

        results = store.fetch_hindcasts(sid, mid, _utc(2024, 1, 1), _utc(2024, 12, 31))
        assert results == []


class TestFetchHindcastsByStation:
    def test_two_models_same_station(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid_a = _seed_model(db_connection)
        mid_b = _seed_model(db_connection)
        aid_a = _seed_artifact(db_connection, mid_a, sid)
        aid_b = _seed_artifact(db_connection, mid_b, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        step = _utc(2025, 3, 1)
        store.store_hindcast(_make_hindcast(sid, mid_a, aid_a, hindcast_step=step))
        store.store_hindcast(_make_hindcast(sid, mid_b, aid_b, hindcast_step=step))

        result = store.fetch_hindcasts_by_station(
            sid, "discharge", _utc(2025, 2, 28), _utc(2025, 3, 2)
        )

        assert set(result.keys()) == {mid_a, mid_b}
        assert len(result[mid_a]) == 1
        assert len(result[mid_b]) == 1

    def test_filter_by_parameter(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        step = _utc(2025, 4, 1)
        discharge_hc = _make_hindcast(sid, mid, aid, hindcast_step=step)

        # build a water_level hindcast manually to vary parameter
        wl_ensemble = make_forecast_ensemble(
            station_id=sid,
            representation=EnsembleRepresentation.MEMBERS,
            parameter="water_level",
        )
        wl_hc = HindcastForecast(
            id=HindcastForecastId(uuid4()),
            station_id=sid,
            model_id=mid,
            model_artifact_id=aid,
            hindcast_step=step,
            forcing_type=ForcingType.NWP_ARCHIVE,
            representation=EnsembleRepresentation.MEMBERS,
            hindcast_run_id=uuid4(),
            ensemble=wl_ensemble,
            created_at=_T0,
        )
        store.store_hindcast(discharge_hc)
        store.store_hindcast(wl_hc)

        result = store.fetch_hindcasts_by_station(
            sid, "discharge", _utc(2025, 3, 31), _utc(2025, 4, 2)
        )

        assert mid in result
        assert all(h.ensemble.parameter == "discharge" for h in result[mid])
        assert len(result[mid]) == 1

    def test_filter_by_period(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        step_in = _utc(2025, 5, 10)
        step_out = _utc(2025, 5, 20)
        store.store_hindcast(_make_hindcast(sid, mid, aid, hindcast_step=step_in))
        store.store_hindcast(_make_hindcast(sid, mid, aid, hindcast_step=step_out))

        result = store.fetch_hindcasts_by_station(
            sid, "discharge", _utc(2025, 5, 1), _utc(2025, 5, 15)
        )

        assert mid in result
        fetched_steps = {h.hindcast_step for h in result[mid]}
        assert step_in in fetched_steps
        assert step_out not in fetched_steps

    def test_empty_result(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgHindcastStore(db_connection)

        result = store.fetch_hindcasts_by_station(
            sid, "discharge", _utc(2024, 1, 1), _utc(2024, 12, 31)
        )

        assert result == {}


# ---------------------------------------------------------------------------
# Plan 038 locked atomicity tests
# ---------------------------------------------------------------------------


class TestStoreHindcastAtomicityDefaultFactory:
    def test_default_factory_is_engine_begin(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgHindcastStore(db_connection)
        # engine.begin is a bound method — new object each access;
        # compare via __self__/__func__ to avoid identity failure
        assert getattr(store._begin, "__self__", None) is db_connection.engine
        engine_cls = type(db_connection.engine)
        assert getattr(store._begin, "__func__", None) is engine_cls.begin


class TestStoreHindcastAtomicityRollback:
    def test_values_insert_failure_rolls_back_header(
        self, db_connection: sa.Connection
    ) -> None:
        """Prove the values insert fires AND the header is absent after rollback.

        The hit_values_insert flag rules out a pass caused by the header INSERT
        triggering a FK failure before hindcast_values is reached.
        """
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        hc = _make_hindcast(sid, mid, aid)

        hit_values_insert: dict[str, bool] = {"fired": False}

        @contextmanager
        def _failing_spy_factory():  # type: ignore[return]
            spy = _SpyConn(db_connection)
            real_spy_execute = spy.execute

            def _patched(stmt: object, *a: object, **k: object) -> object:
                if (
                    isinstance(stmt, sa.sql.dml.Insert)
                    and getattr(getattr(stmt, "table", None), "name", "")
                    == "hindcast_values"
                ):
                    hit_values_insert["fired"] = True
                    raise sa.exc.IntegrityError(
                        "forced hindcast_values failure", None, Exception()
                    )
                return real_spy_execute(stmt, *a, **k)

            spy.execute = _patched  # type: ignore[method-assign]
            with db_connection.begin_nested():
                yield spy

        store = PgHindcastStore(db_connection, transaction_factory=_failing_spy_factory)

        with pytest.raises(sa.exc.IntegrityError):
            store.store_hindcast(hc)

        # The values insert must have fired (rules out a header-FK short-circuit)
        assert hit_values_insert["fired"], "hindcast_values INSERT was never reached"

        # Both header and values must be absent (rollback was atomic)
        row = db_connection.execute(
            sa.select(hindcast_forecasts.c.id).where(hindcast_forecasts.c.id == hc.id)
        ).first()
        assert row is None

        values_row = db_connection.execute(
            sa.select(hindcast_values.c.hindcast_forecast_id).where(
                hindcast_values.c.hindcast_forecast_id == hc.id
            )
        ).first()
        assert values_row is None

    def test_conflict_values_insert_failure_rolls_back_keeping_original(
        self, db_connection: sa.Connection
    ) -> None:
        """On a conflict path, values-INSERT failure rolls back, preserving seeded data.

        Scenario:
          1. Seed a prior hindcast (seeded_id, original values).
          2. Attempt a re-insert with the SAME natural key, forcing the values
             INSERT to fail AFTER the DELETE of the old values has executed.
          3. Assert the savepoint rolled back: the original header and values survive.

        The spy intercepts Insert-on-hindcast_values only, letting the Delete pass
        through — so the test actually exercises the DELETE→INSERT→rollback path.
        """
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        # A second active artifact (distinct model) so the conflict can carry a
        # DIFFERENT mutable header field — proving the header upsert itself rolls
        # back, not just that the value rows are restored.
        mid_alt = _seed_model(db_connection)
        aid_alt = _seed_artifact(db_connection, mid_alt, sid)
        run_id = uuid4()

        # Seed the original hindcast (clean insert)
        hc_seed = _make_hindcast(
            sid, mid, aid, hindcast_step=_utc(2025, 10, 1), hindcast_run_id=run_id
        )
        seeded_store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        seeded_id = seeded_store.store_hindcast(hc_seed)

        # Confirm original value count
        original_count = db_connection.execute(
            sa.select(sa.func.count()).where(
                hindcast_values.c.hindcast_forecast_id == seeded_id
            )
        ).scalar_one()
        assert original_count > 0

        # Build a conflicting hindcast (same natural key, DIFFERENT artifact so a
        # leaked header update would be detectable)
        hc_conflict = _make_hindcast(
            sid, mid, aid_alt, hindcast_step=_utc(2025, 10, 1), hindcast_run_id=run_id
        )
        assert hc_conflict.id != seeded_id  # different object
        assert aid_alt != aid  # different mutable header field

        hit_values_insert: dict[str, bool] = {"fired": False}

        @contextmanager
        def _conflict_failing_factory():  # type: ignore[return]
            spy = _SpyConn(db_connection)
            real_spy_execute = spy.execute

            def _patched(stmt: object, *a: object, **k: object) -> object:
                # Intercept only Insert on hindcast_values; let Delete pass through.
                if (
                    isinstance(stmt, sa.sql.dml.Insert)
                    and getattr(getattr(stmt, "table", None), "name", "")
                    == "hindcast_values"
                ):
                    hit_values_insert["fired"] = True
                    raise sa.exc.IntegrityError(
                        "forced conflict-path hindcast_values failure",
                        None,
                        Exception(),
                    )
                return real_spy_execute(stmt, *a, **k)

            spy.execute = _patched  # type: ignore[method-assign]
            with db_connection.begin_nested():
                yield spy

        store = PgHindcastStore(
            db_connection, transaction_factory=_conflict_failing_factory
        )

        with pytest.raises(sa.exc.IntegrityError):
            store.store_hindcast(hc_conflict)

        assert hit_values_insert["fired"], "hindcast_values INSERT was never reached"

        # The original header must still exist AND retain its original mutable
        # field — the conflict-path header upsert (which would have set
        # model_artifact_id=aid_alt) must have rolled back with the values.
        header_row = db_connection.execute(
            sa.select(hindcast_forecasts.c.model_artifact_id).where(
                hindcast_forecasts.c.id == seeded_id
            )
        ).first()
        assert header_row is not None, (
            "original header was lost after conflict rollback"
        )
        assert header_row[0] == aid, (
            "header upsert leaked: model_artifact_id was not rolled back to the "
            "seeded value"
        )

        # The original value rows must be fully restored
        restored_count = db_connection.execute(
            sa.select(sa.func.count()).where(
                hindcast_values.c.hindcast_forecast_id == seeded_id
            )
        ).scalar_one()
        assert restored_count == original_count, (
            f"original {original_count} value rows not restored; got {restored_count}"
        )


class TestStoreHindcastAtomicitySuccess:
    def test_clean_insert_routes_through_txn(
        self, db_connection: sa.Connection
    ) -> None:
        """Upsert + DELETE + INSERT all route through the spy (clean insert path).

        A broken impl that bypasses the injected txn and writes directly on
        self._conn (= db_connection) would NOT appear in spy.executed.
        On a clean insert the DELETE is a no-op but must still fire through the txn.
        """
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)

        spies, factory = _capturing_spy_factory(db_connection)
        store = PgHindcastStore(db_connection, transaction_factory=factory)
        hc = _make_hindcast(sid, mid, aid)

        store.store_hindcast(hc)

        assert len(spies) == 1, "factory must have been called exactly once"
        spy = spies[0]

        # header upsert + values DELETE + values INSERT = at least 3 statements
        assert len(spy.executed) >= 3, (
            f"expected ≥3 statements via txn spy, got {len(spy.executed)}"
        )

        inserts = [
            s
            for s in spy.executed
            if isinstance(s, sa.sql.dml.Insert)
            and getattr(s, "table", None) is hindcast_values
        ]
        deletes = [
            s
            for s in spy.executed
            if isinstance(s, sa.sql.dml.Delete)
            and getattr(s, "table", None) is hindcast_values
        ]
        assert inserts, "hindcast_values INSERT missing from spy"
        assert deletes, "hindcast_values DELETE missing from spy"

        header_row = db_connection.execute(
            sa.select(hindcast_forecasts.c.id).where(hindcast_forecasts.c.id == hc.id)
        ).first()
        assert header_row is not None

        value_count = db_connection.execute(
            sa.select(sa.func.count()).where(
                hindcast_values.c.hindcast_forecast_id == hc.id
            )
        ).scalar_one()
        assert value_count > 0

    def test_conflict_upsert_routes_through_txn(
        self, db_connection: sa.Connection
    ) -> None:
        """Upsert + DELETE + INSERT all route through the spy (conflict path).

        A broken conflict-path implementation that bypasses the transaction
        (e.g. skips the DELETE or short-circuits the txn wrapper) would fail.
        """
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        run_id = uuid4()

        # Clean insert first (uses its own savepoint factory)
        clean_store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        hc_first = _make_hindcast(
            sid, mid, aid, hindcast_step=_utc(2025, 11, 1), hindcast_run_id=run_id
        )
        clean_store.store_hindcast(hc_first)

        # Second insert with the same natural key — triggers the conflict path
        hc_second = _make_hindcast(
            sid, mid, aid, hindcast_step=_utc(2025, 11, 1), hindcast_run_id=run_id
        )
        spies, factory = _capturing_spy_factory(db_connection)
        conflict_store = PgHindcastStore(db_connection, transaction_factory=factory)

        conflict_store.store_hindcast(hc_second)

        assert len(spies) == 1, "factory must have been called exactly once"
        spy = spies[0]

        n_stmts = len(spy.executed)
        assert n_stmts >= 3, (
            f"expected ≥3 statements via txn spy on conflict path, got {n_stmts}"
        )

        inserts = [
            s
            for s in spy.executed
            if isinstance(s, sa.sql.dml.Insert)
            and getattr(s, "table", None) is hindcast_values
        ]
        deletes = [
            s
            for s in spy.executed
            if isinstance(s, sa.sql.dml.Delete)
            and getattr(s, "table", None) is hindcast_values
        ]
        header_inserts = [
            s
            for s in spy.executed
            if isinstance(s, sa.sql.dml.Insert)
            and getattr(s, "table", None) is hindcast_forecasts
        ]
        assert header_inserts, "hindcast_forecasts upsert missing from conflict spy"
        assert deletes, "hindcast_values DELETE missing from conflict spy"
        assert inserts, "hindcast_values INSERT missing from conflict spy"


class TestStoreHindcastIsolationHolds:
    def test_rolled_back_savepoint_invisible_from_fresh_connection(
        self, db_connection: sa.Connection, db_engine: sa.Engine
    ) -> None:
        """Prove a rolled-back savepoint write is invisible from a fresh connection."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        hc = _make_hindcast(sid, mid, aid)

        # Write inside a savepoint then explicitly roll it back
        with db_connection.begin_nested() as sp:
            db_connection.execute(
                pg_insert(hindcast_forecasts).values(
                    id=hc.id,
                    station_id=hc.station_id,
                    model_id=hc.model_id,
                    model_artifact_id=hc.model_artifact_id,
                    hindcast_step=hc.hindcast_step,
                    forcing_type=hc.forcing_type.value,
                    representation=hc.representation.value,
                    hindcast_run_id=hc.hindcast_run_id,
                    parameter=hc.ensemble.parameter,
                    units=hc.ensemble.units,
                    created_at=hc.created_at,
                    qc_status=hc.qc_status.value,
                    qc_flags=[],
                )
            )
            sp.rollback()

        # Verify the write is invisible from a separate connection
        with db_engine.connect() as fresh_conn:
            row = fresh_conn.execute(
                sa.select(hindcast_forecasts.c.id).where(
                    hindcast_forecasts.c.id == hc.id
                )
            ).first()
        assert row is None, "rolled-back savepoint write leaked to a fresh connection"


class TestFetchHindcastsOrphanSkip:
    def test_orphan_header_skipped_valid_returned(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        # Seed a valid hindcast
        valid_hc = _make_hindcast(sid, mid, aid, hindcast_step=_utc(2025, 8, 1))
        store.store_hindcast(valid_hc)

        # Seed an orphan header with no hindcast_values
        orphan_id = HindcastForecastId(uuid4())
        db_connection.execute(
            sa.insert(hindcast_forecasts).values(
                id=orphan_id,
                station_id=sid,
                model_id=mid,
                model_artifact_id=aid,
                hindcast_step=_utc(2025, 8, 2),
                forcing_type=ForcingType.NWP_ARCHIVE.value,
                representation=EnsembleRepresentation.MEMBERS.value,
                hindcast_run_id=uuid4(),
                parameter="discharge",
                units="m³/s",
                created_at=_T0,
                qc_status="raw",
                qc_flags=[],
            )
        )

        with structlog.testing.capture_logs() as cap_logs:
            results = store.fetch_hindcasts(
                sid, mid, _utc(2025, 7, 31), _utc(2025, 8, 3)
            )

        assert len(results) == 1
        assert results[0].id == valid_hc.id

        warning_events = [
            e for e in cap_logs if e.get("event") == "hindcast.orphan_header_skipped"
        ]
        assert len(warning_events) == 1
        assert warning_events[0]["log_level"] == "warning"
        # D1: payload must carry the station_id and the orphan hindcast_forecast_id
        assert str(warning_events[0].get("station_id")) == str(sid)
        assert str(warning_events[0].get("hindcast_forecast_id")) == str(orphan_id)

    def test_orphan_header_skipped_by_fetch_hindcasts_by_station(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        # Seed one valid hindcast
        valid_hc = _make_hindcast(sid, mid, aid, hindcast_step=_utc(2025, 9, 1))
        store.store_hindcast(valid_hc)

        # Seed an orphan header (no values)
        orphan_id = HindcastForecastId(uuid4())
        db_connection.execute(
            sa.insert(hindcast_forecasts).values(
                id=orphan_id,
                station_id=sid,
                model_id=mid,
                model_artifact_id=aid,
                hindcast_step=_utc(2025, 9, 2),
                forcing_type=ForcingType.NWP_ARCHIVE.value,
                representation=EnsembleRepresentation.MEMBERS.value,
                hindcast_run_id=uuid4(),
                parameter="discharge",
                units="m³/s",
                created_at=_T0,
                qc_status="raw",
                qc_flags=[],
            )
        )

        with structlog.testing.capture_logs() as cap_logs:
            result = store.fetch_hindcasts_by_station(
                sid, "discharge", _utc(2025, 8, 31), _utc(2025, 9, 3)
            )

        assert mid in result
        assert len(result[mid]) == 1
        assert result[mid][0].id == valid_hc.id

        warning_events = [
            e for e in cap_logs if e.get("event") == "hindcast.orphan_header_skipped"
        ]
        assert len(warning_events) == 1
        assert str(warning_events[0].get("station_id")) == str(sid)
        assert str(warning_events[0].get("hindcast_forecast_id")) == str(orphan_id)


# ---------------------------------------------------------------------------
# Plan 040 locked deduplication tests
# ---------------------------------------------------------------------------


class TestStoreHindcastDedupIdempotent:
    def test_same_natural_key_same_data_returns_same_id(
        self, db_connection: sa.Connection
    ) -> None:
        """Re-inserting the SAME hindcast returns the SAME id, one row, no error."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        run_id = uuid4()
        hc = _make_hindcast(
            sid, mid, aid, hindcast_step=_utc(2026, 1, 1), hindcast_run_id=run_id
        )

        id_first = store.store_hindcast(hc)
        id_second = store.store_hindcast(hc)

        assert id_first == id_second, "idempotent re-insert must return the same id"

        count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(hindcast_forecasts)
            .where(
                hindcast_forecasts.c.station_id == sid,
                hindcast_forecasts.c.model_id == mid,
                hindcast_forecasts.c.hindcast_run_id == run_id,
            )
        ).scalar_one()
        assert count == 1, f"expected 1 header row, got {count}"


class TestStoreHindcastDedupFullReplace:
    def test_same_natural_key_different_payload_full_replace(
        self, db_connection: sa.Connection
    ) -> None:
        """DO UPDATE full-replace: same natural key, different payload.

        One header (second write's mutable fields), second write's value rows,
        and the EXISTING header id returned (not the new hindcast.id).
        """
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        # Two distinct models so we can seed two distinct active artifacts for
        # the same station — the unique index on model_artifacts allows one
        # active artifact per (station_id, model_id) pair.
        mid_alt = _seed_model(db_connection)
        aid_first = _seed_artifact(db_connection, mid, sid)
        aid_second = _seed_artifact(db_connection, mid_alt, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        run_id = uuid4()
        step = _utc(2026, 2, 1)

        hc_first = _make_hindcast(
            sid,
            mid,
            aid_first,
            hindcast_step=step,
            hindcast_run_id=run_id,
            n_members=3,
            n_steps=4,
        )
        id_first = store.store_hindcast(hc_first)

        hc_second = _make_hindcast(
            sid,
            mid,
            aid_second,  # different artifact — mutable header field
            hindcast_step=step,
            hindcast_run_id=run_id,
            n_members=5,  # different ensemble size — new values
            n_steps=2,
            created_at=_utc(2026, 6, 1),  # later than hc_first's _T0
            units="L/s",  # different units — mutable header field
        )
        id_second = store.store_hindcast(hc_second)

        # (d) method returns the EXISTING id
        assert id_second == id_first, (
            "conflict upsert must return the existing header id"
        )

        # (a) exactly one header row
        count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(hindcast_forecasts)
            .where(
                hindcast_forecasts.c.station_id == sid,
                hindcast_forecasts.c.model_id == mid,
                hindcast_forecasts.c.hindcast_run_id == run_id,
            )
        ).scalar_one()
        assert count == 1, f"expected 1 header after conflict, got {count}"

        # (b) mutable header field reflects second write
        header = db_connection.execute(
            sa.select(hindcast_forecasts.c.model_artifact_id).where(
                hindcast_forecasts.c.id == id_first
            )
        ).scalar_one()
        assert header == aid_second, "model_artifact_id must reflect the second write"

        # (b1) units is in set_: the second write's units overwrites the first.
        stored_units = db_connection.execute(
            sa.select(hindcast_forecasts.c.units).where(
                hindcast_forecasts.c.id == id_first
            )
        ).scalar_one()
        assert stored_units == "L/s", "units must reflect the second write (in set_)"

        # (b2) created_at is NOT in set_: it stays the FIRST write's value, tied
        # to the surviving header (not overwritten by the later second write).
        stored_created_at = db_connection.execute(
            sa.select(hindcast_forecasts.c.created_at).where(
                hindcast_forecasts.c.id == id_first
            )
        ).scalar_one()
        assert ensure_utc(stored_created_at) == _T0, (
            "created_at must remain the first write's value (not in set_)"
        )
        assert ensure_utc(stored_created_at) != hc_second.created_at, (
            "created_at must NOT be overwritten by the second write"
        )

        # (c) value rows are the second write's (5 members × 2 steps = 10 rows)
        expected_value_count = 5 * 2
        actual_count = db_connection.execute(
            sa.select(sa.func.count()).where(
                hindcast_values.c.hindcast_forecast_id == id_first
            )
        ).scalar_one()
        assert actual_count == expected_value_count, (
            f"expected {expected_value_count} value rows (2nd write),"
            f" got {actual_count}"
        )

    def test_representation_overwritten_on_conflict(
        self, db_connection: sa.Connection
    ) -> None:
        """representation is in set_: a MEMBERS→QUANTILES re-run overwrites it.

        Guards against set_ omitting `representation` (a MEMBERS header left
        behind quantile value rows would silently mis-describe the payload).
        """
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        run_id = uuid4()
        step = _utc(2026, 7, 1)

        id_members = store.store_hindcast(
            _make_hindcast(
                sid,
                mid,
                aid,
                hindcast_step=step,
                hindcast_run_id=run_id,
                representation=EnsembleRepresentation.MEMBERS,
                n_members=3,
                n_steps=2,
            )
        )
        id_quantiles = store.store_hindcast(
            _make_hindcast(
                sid,
                mid,
                aid,
                hindcast_step=step,
                hindcast_run_id=run_id,
                representation=EnsembleRepresentation.QUANTILES,
                n_steps=2,
            )
        )

        assert id_quantiles == id_members, "same natural key must upsert one header"

        stored_repr = db_connection.execute(
            sa.select(hindcast_forecasts.c.representation).where(
                hindcast_forecasts.c.id == id_members
            )
        ).scalar_one()
        assert stored_repr == EnsembleRepresentation.QUANTILES.value, (
            "representation must reflect the second write (in set_)"
        )

        # Full-replace: the surviving value rows are quantiles (member_id NULL).
        member_rows = db_connection.execute(
            sa.select(sa.func.count()).where(
                hindcast_values.c.hindcast_forecast_id == id_members,
                hindcast_values.c.member_id.isnot(None),
            )
        ).scalar_one()
        assert member_rows == 0, "old MEMBERS value rows must be fully replaced"

    def test_qc_fields_overwritten_on_conflict(
        self, db_connection: sa.Connection
    ) -> None:
        """QC fields in set_ — re-run's corrected verdict overwrites stale QC."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        run_id = uuid4()
        step = _utc(2026, 3, 1)

        hc_raw = HindcastForecast(
            id=HindcastForecastId(uuid4()),
            station_id=sid,
            model_id=mid,
            model_artifact_id=aid,
            hindcast_step=step,
            forcing_type=ForcingType.NWP_ARCHIVE,
            representation=EnsembleRepresentation.MEMBERS,
            hindcast_run_id=run_id,
            ensemble=make_forecast_ensemble(station_id=sid),
            created_at=_T0,
            qc_status=QcStatus.RAW,
            qc_flags=(),
        )
        id_raw = store.store_hindcast(hc_raw)

        hc_qc_passed = HindcastForecast(
            id=HindcastForecastId(uuid4()),
            station_id=sid,
            model_id=mid,
            model_artifact_id=aid,
            hindcast_step=step,
            forcing_type=ForcingType.NWP_ARCHIVE,
            representation=EnsembleRepresentation.MEMBERS,
            hindcast_run_id=run_id,
            ensemble=make_forecast_ensemble(station_id=sid),
            created_at=_T0,
            qc_status=QcStatus.QC_PASSED,
            qc_flags=(
                QcFlag(
                    rule_id="range_check",
                    rule_version="1",
                    status=QcStatus.QC_PASSED,
                    detail="within range",
                ),
            ),
        )
        id_updated = store.store_hindcast(hc_qc_passed)
        assert id_updated == id_raw

        row = (
            db_connection.execute(
                sa.select(
                    hindcast_forecasts.c.qc_status,
                    hindcast_forecasts.c.qc_flags,
                ).where(hindcast_forecasts.c.id == id_raw)
            )
            .mappings()
            .one()
        )
        assert row["qc_status"] == QcStatus.QC_PASSED.value, (
            "qc_status must be overwritten on conflict"
        )
        assert len(row["qc_flags"]) == 1, "qc_flags must reflect the second write"


class TestStoreHindcastDedupForcingTypeKey:
    def test_distinct_forcing_type_both_persist(
        self, db_connection: sa.Connection
    ) -> None:
        """forcing_type is a KEY column — distinct forcing_type hindcasts both persist.

        Guards the forcing_type-in-key decision
        (TestFetchWithForcingTypeFilter regression).
        """
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        run_id = uuid4()
        step = _utc(2026, 4, 1)

        id_nwp = store.store_hindcast(
            _make_hindcast(
                sid,
                mid,
                aid,
                hindcast_step=step,
                forcing_type=ForcingType.NWP_ARCHIVE,
                hindcast_run_id=run_id,
            )
        )
        id_rean = store.store_hindcast(
            _make_hindcast(
                sid,
                mid,
                aid,
                hindcast_step=step,
                forcing_type=ForcingType.REANALYSIS,
                hindcast_run_id=run_id,
            )
        )

        assert id_nwp != id_rean, "distinct forcing_type must produce distinct ids"

        count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(hindcast_forecasts)
            .where(
                hindcast_forecasts.c.station_id == sid,
                hindcast_forecasts.c.model_id == mid,
                hindcast_forecasts.c.hindcast_run_id == run_id,
            )
        ).scalar_one()
        assert count == 2, f"both forcing-type hindcasts must persist, got {count}"


class TestStoreHindcastDedupDistinctRunId:
    def test_distinct_run_id_both_persist(self, db_connection: sa.Connection) -> None:
        """Distinct run_ids for same station/model/step/parameter both persist."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        step = _utc(2026, 5, 1)
        run_a = uuid4()
        run_b = uuid4()

        id_a = store.store_hindcast(
            _make_hindcast(sid, mid, aid, hindcast_step=step, hindcast_run_id=run_a)
        )
        id_b = store.store_hindcast(
            _make_hindcast(sid, mid, aid, hindcast_step=step, hindcast_run_id=run_b)
        )

        assert id_a != id_b

        count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(hindcast_forecasts)
            .where(
                hindcast_forecasts.c.station_id == sid,
                hindcast_forecasts.c.model_id == mid,
                hindcast_forecasts.c.hindcast_step == step,
            )
        ).scalar_one()
        assert count == 2, f"both run-id hindcasts must persist, got {count}"


class TestStoreHindcastDedupDistinctParameter:
    def test_distinct_parameter_both_persist(
        self, db_connection: sa.Connection
    ) -> None:
        """parameter is a KEY column — same station/model/step/run/forcing but
        distinct parameter must produce two headers (not upsert into one)."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        step = _utc(2026, 8, 1)
        run_id = uuid4()

        id_discharge = store.store_hindcast(
            _make_hindcast(
                sid,
                mid,
                aid,
                hindcast_step=step,
                hindcast_run_id=run_id,
                parameter="discharge",
            )
        )
        id_water_level = store.store_hindcast(
            _make_hindcast(
                sid,
                mid,
                aid,
                hindcast_step=step,
                hindcast_run_id=run_id,
                parameter="water_level",
            )
        )

        assert id_discharge != id_water_level, (
            "distinct parameter must produce distinct ids"
        )

        count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(hindcast_forecasts)
            .where(
                hindcast_forecasts.c.station_id == sid,
                hindcast_forecasts.c.model_id == mid,
                hindcast_forecasts.c.hindcast_step == step,
                hindcast_forecasts.c.hindcast_run_id == run_id,
            )
        ).scalar_one()
        assert count == 2, f"both parameter hindcasts must persist, got {count}"
