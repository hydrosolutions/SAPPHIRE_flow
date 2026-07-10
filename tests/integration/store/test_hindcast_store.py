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
from sapphire_flow.types.enums import EnsembleRepresentation, ForcingType
from sapphire_flow.types.forecast import HindcastForecast
from sapphire_flow.types.ids import ArtifactId, HindcastForecastId, ModelId, StationId
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


def _seed_station(conn: sa.Connection) -> StationId:
    sid = StationId(uuid4())
    conn.execute(
        sa.insert(stations).values(
            id=sid,
            code=f"HC-{sid.hex[:6]}",
            name="Hindcast Test Station",
            location="SRID=4326;POINT(8.5 47.4)",
            station_kind="river",
            network="bafu",
            timezone="Europe/Zurich",
            measured_parameters=["discharge"],
            ownership="own",
        )
    )
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
) -> HindcastForecast:
    step = hindcast_step if hindcast_step is not None else _utc(2025, 3, 1)
    run_id = hindcast_run_id or uuid4()
    ensemble = make_forecast_ensemble(
        station_id=station_id,
        representation=representation,
        n_members=n_members,
        n_steps=n_steps,
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
        created_at=_T0,
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


class TestStoreHindcastAtomicitySuccess:
    def test_writes_routed_through_injected_txn(
        self, db_connection: sa.Connection
    ) -> None:
        """Prove both INSERTs go through the spy (not self._conn).

        A broken impl that bypasses the injected txn and writes directly on
        self._conn (= db_connection) would NOT appear in spy.executed.
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

        # The spy must have recorded at least 2 statements: header + values
        assert len(spy.executed) >= 2, (
            f"expected ≥2 statements via txn spy, got {len(spy.executed)}"
        )

        table_names = {
            getattr(getattr(stmt, "table", None), "name", None) for stmt in spy.executed
        }
        assert "hindcast_forecasts" in table_names, (
            "hindcast_forecasts header INSERT missing from spy"
        )
        assert "hindcast_values" in table_names, (
            "hindcast_values INSERT missing from spy"
        )

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
