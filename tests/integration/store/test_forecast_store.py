from __future__ import annotations

import random
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
import sqlalchemy.exc

from sapphire_flow.db.metadata import forecast_values, model_artifacts, models
from sapphire_flow.exceptions import ConflictError
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    ForecastStatus,
    NwpCycleSource,
)
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.ids import ArtifactId, ForecastId, ModelId, StationId
from tests.conftest import make_forecast_ensemble, make_station_config

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime


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


_NOW = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_ISSUED_A = ensure_utc(datetime(2025, 1, 1, 0, tzinfo=UTC))
_ISSUED_B = ensure_utc(datetime(2025, 1, 1, 6, tzinfo=UTC))
_NWP_CYCLE = ensure_utc(datetime(2024, 12, 31, 18, tzinfo=UTC))


def _seed_station(conn: sa.Connection) -> StationId:
    station = make_station_config(rng=random.Random(77))
    PgStationStore(conn).store_station(station)
    return station.id


def _seed_model(conn: sa.Connection, model_id: str = "linreg_v1") -> ModelId:
    conn.execute(
        sa.insert(models).values(
            id=model_id,
            display_name="Linear Regression v1",
            artifact_scope="station",
            description="Test model",
            created_at=_NOW,
        )
    )
    return ModelId(model_id)


def _seed_artifact(
    conn: sa.Connection,
    station_id: StationId,
    model_id: ModelId,
) -> ArtifactId:
    aid = ArtifactId(uuid4())
    conn.execute(
        sa.insert(model_artifacts).values(
            id=aid,
            model_id=model_id,
            station_id=station_id,
            group_id=None,
            status="active",
            artifact_path="artifacts/test.bin",
            sha256_hash="",
            training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
            training_period_end=ensure_utc(datetime(2024, 12, 31, tzinfo=UTC)),
            trained_at=_NOW,
            promoted_at=_NOW,
            promoted_by=None,
            superseded_at=None,
            created_at=_NOW,
        )
    )
    return aid


def _make_forecast(
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    *,
    issued_at: UtcDatetime = _ISSUED_A,
    representation: EnsembleRepresentation = EnsembleRepresentation.MEMBERS,
    status: ForecastStatus = ForecastStatus.RAW,
    n_members: int = 5,
    n_steps: int = 10,
    parameter: str = "discharge",
    nwp_cycle_source: NwpCycleSource = NwpCycleSource.PRIMARY,
    nwp_cycle_reference_time: UtcDatetime | None = _NWP_CYCLE,
    rng: random.Random | None = None,
) -> OperationalForecast:
    rng = rng or random.Random(42)
    ensemble = make_forecast_ensemble(
        station_id=station_id,
        representation=representation,
        n_members=n_members,
        n_steps=n_steps,
        parameter=parameter,
        rng=rng,
    )
    return OperationalForecast(
        id=ForecastId(uuid4()),
        station_id=station_id,
        model_id=model_id,
        model_artifact_id=artifact_id,
        issued_at=issued_at,
        nwp_cycle_reference_time=nwp_cycle_reference_time,  # type: ignore[arg-type]
        nwp_cycle_source=nwp_cycle_source,
        representation=representation,
        status=status,
        version=1,
        warm_up_source=None,
        warm_up_state_age_hours=None,
        observation_staleness_hours=None,
        ensemble=ensemble,
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestStoreAndFetchForecast:
    def test_store_and_fetch_forecast(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        fc = _make_forecast(sid, mid, aid)
        returned_id = store.store_forecast(fc)
        assert returned_id == fc.id

        fetched = store.fetch_forecast(fc.id)
        assert fetched is not None
        assert fetched.id == fc.id
        assert fetched.station_id == sid
        assert fetched.model_id == mid
        assert fetched.model_artifact_id == aid
        assert fetched.representation == EnsembleRepresentation.MEMBERS
        assert fetched.status == ForecastStatus.RAW
        assert fetched.version == 1

        assert fetched.ensemble.parameter == fc.ensemble.parameter
        assert fetched.ensemble.units == fc.ensemble.units
        assert len(fetched.ensemble.values) == len(fc.ensemble.values)
        assert (
            fetched.ensemble.forecast_horizon_steps
            == fc.ensemble.forecast_horizon_steps
        )

    def test_fetch_nonexistent_returns_none(self, db_connection: sa.Connection) -> None:
        store = PgForecastStore(db_connection)
        result = store.fetch_forecast(ForecastId(uuid4()))
        assert result is None

    def test_round_trip_quantiles(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection, "quantile_model")
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        fc = _make_forecast(
            sid,
            mid,
            aid,
            representation=EnsembleRepresentation.QUANTILES,
        )
        store.store_forecast(fc)
        fetched = store.fetch_forecast(fc.id)
        assert fetched is not None
        assert fetched.representation == EnsembleRepresentation.QUANTILES
        assert "quantile" in fetched.ensemble.values.columns
        assert "member_id" not in fetched.ensemble.values.columns


class TestFetchLatest:
    def test_fetch_latest_returns_most_recent(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        fc_old = _make_forecast(
            sid, mid, aid, issued_at=_ISSUED_A, rng=random.Random(1)
        )
        fc_new = _make_forecast(
            sid, mid, aid, issued_at=_ISSUED_B, rng=random.Random(2)
        )
        store.store_forecast(fc_old)
        store.store_forecast(fc_new)

        latest = store.fetch_latest_forecast(sid)
        assert latest is not None
        assert latest.id == fc_new.id
        assert latest.issued_at == _ISSUED_B

    def test_fetch_latest_with_model_filter(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid_a = _seed_model(db_connection, "model_a")
        mid_b = _seed_model(db_connection, "model_b")
        aid_a = _seed_artifact(db_connection, sid, mid_a)
        aid_b = _seed_artifact(db_connection, sid, mid_b)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        fc_a = _make_forecast(
            sid, mid_a, aid_a, issued_at=_ISSUED_A, rng=random.Random(3)
        )
        fc_b = _make_forecast(
            sid, mid_b, aid_b, issued_at=_ISSUED_B, rng=random.Random(4)
        )
        store.store_forecast(fc_a)
        store.store_forecast(fc_b)

        latest_a = store.fetch_latest_forecast(sid, model_id=mid_a)
        assert latest_a is not None
        assert latest_a.id == fc_a.id

    def test_fetch_latest_returns_none_when_empty(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgForecastStore(db_connection)
        result = store.fetch_latest_forecast(StationId(uuid4()))
        assert result is None


class TestFetchForecastsForCycle:
    def test_exact_match_on_issued_at(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        fc_a = _make_forecast(sid, mid, aid, issued_at=_ISSUED_A, rng=random.Random(5))
        fc_b = _make_forecast(sid, mid, aid, issued_at=_ISSUED_B, rng=random.Random(6))
        store.store_forecast(fc_a)
        store.store_forecast(fc_b)

        results = store.fetch_forecasts_for_cycle(_ISSUED_A)
        assert len(results) == 1
        assert results[0].id == fc_a.id

    def test_filters_by_station(self, db_connection: sa.Connection) -> None:
        sid_1 = _seed_station(db_connection)
        station2 = make_station_config(code="S-002", rng=random.Random(88))
        PgStationStore(db_connection).store_station(station2)
        sid_2 = station2.id

        mid = _seed_model(db_connection)
        aid_1 = _seed_artifact(db_connection, sid_1, mid)
        aid_2 = _seed_artifact(db_connection, sid_2, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        fc_1 = _make_forecast(
            sid_1, mid, aid_1, issued_at=_ISSUED_A, rng=random.Random(7)
        )
        fc_2 = _make_forecast(
            sid_2, mid, aid_2, issued_at=_ISSUED_A, rng=random.Random(8)
        )
        store.store_forecast(fc_1)
        store.store_forecast(fc_2)

        results = store.fetch_forecasts_for_cycle(_ISSUED_A, station_id=sid_1)
        assert len(results) == 1
        assert results[0].station_id == sid_1

    def test_returns_empty_for_unknown_cycle(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgForecastStore(db_connection)
        unknown = ensure_utc(datetime(2030, 1, 1, tzinfo=UTC))
        assert store.fetch_forecasts_for_cycle(unknown) == []


class TestFetchForecastsInRange:
    def test_half_open_range(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        fc_a = _make_forecast(sid, mid, aid, issued_at=_ISSUED_A, rng=random.Random(9))
        fc_b = _make_forecast(sid, mid, aid, issued_at=_ISSUED_B, rng=random.Random(10))
        store.store_forecast(fc_a)
        store.store_forecast(fc_b)

        # half-open [ISSUED_A, ISSUED_B) — excludes ISSUED_B
        results = store.fetch_forecasts_in_range(sid, _ISSUED_A, _ISSUED_B)
        ids = {r.id for r in results}
        assert fc_a.id in ids
        assert fc_b.id not in ids

    def test_includes_end_exclusive(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        fc_b = _make_forecast(sid, mid, aid, issued_at=_ISSUED_B, rng=random.Random(11))
        store.store_forecast(fc_b)

        # ISSUED_B is the end boundary, so it should be excluded
        results = store.fetch_forecasts_in_range(sid, _ISSUED_A, _ISSUED_B)
        assert results == []

    def test_filter_by_status(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        end = ensure_utc(datetime(2025, 1, 2, tzinfo=UTC))
        fc = _make_forecast(
            sid, mid, aid, issued_at=_ISSUED_A, status=ForecastStatus.RAW
        )
        store.store_forecast(fc)

        raw_results = store.fetch_forecasts_in_range(
            sid, _ISSUED_A, end, status=ForecastStatus.RAW
        )
        assert len(raw_results) == 1

        reviewed_results = store.fetch_forecasts_in_range(
            sid, _ISSUED_A, end, status=ForecastStatus.REVIEWED
        )
        assert reviewed_results == []


class TestTransitionStatus:
    def test_transition_increments_version(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        fc = _make_forecast(sid, mid, aid)
        store.store_forecast(fc)

        new_version = store.transition_status(fc.id, 1, ForecastStatus.REVIEWED)
        assert new_version == 2

        fetched = store.fetch_forecast(fc.id)
        assert fetched is not None
        assert fetched.status == ForecastStatus.REVIEWED
        assert fetched.version == 2

    def test_transition_conflict_raises(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        fc = _make_forecast(sid, mid, aid)
        store.store_forecast(fc)

        with pytest.raises(ConflictError):
            store.transition_status(fc.id, 99, ForecastStatus.REVIEWED)


class TestParameterFilter:
    def test_fetch_latest_with_parameter_filter(
        self, db_connection: sa.Connection
    ) -> None:

        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        fc_discharge = _make_forecast(
            sid,
            mid,
            aid,
            issued_at=_ISSUED_A,
            parameter="discharge",
            rng=random.Random(20),
        )
        fc_water_level = _make_forecast(
            sid,
            mid,
            aid,
            issued_at=_ISSUED_A,
            parameter="water_level",
            rng=random.Random(21),
        )
        store.store_forecast(fc_discharge)
        store.store_forecast(fc_water_level)

        result_discharge = store.fetch_latest_forecast(sid, parameter="discharge")
        assert result_discharge is not None
        assert result_discharge.id == fc_discharge.id
        assert result_discharge.ensemble.parameter == "discharge"

        result_water_level = store.fetch_latest_forecast(sid, parameter="water_level")
        assert result_water_level is not None
        assert result_water_level.id == fc_water_level.id
        assert result_water_level.ensemble.parameter == "water_level"

        result_any = store.fetch_latest_forecast(sid, parameter=None)
        assert result_any is not None

    def test_unique_constraint_allows_same_cycle_different_params(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        fc_discharge = _make_forecast(
            sid,
            mid,
            aid,
            issued_at=_ISSUED_A,
            parameter="discharge",
            rng=random.Random(22),
        )
        fc_water_level = _make_forecast(
            sid,
            mid,
            aid,
            issued_at=_ISSUED_A,
            parameter="water_level",
            rng=random.Random(23),
        )
        store.store_forecast(fc_discharge)
        store.store_forecast(fc_water_level)

        results = store.fetch_forecasts_for_cycle(_ISSUED_A, station_id=sid)
        assert len(results) == 2

    def test_unique_constraint_rejects_duplicate_param(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        fc_first = _make_forecast(
            sid,
            mid,
            aid,
            issued_at=_ISSUED_A,
            parameter="discharge",
            rng=random.Random(24),
        )
        fc_duplicate = _make_forecast(
            sid,
            mid,
            aid,
            issued_at=_ISSUED_A,
            parameter="discharge",
            rng=random.Random(25),
        )
        store.store_forecast(fc_first)

        with pytest.raises(sqlalchemy.exc.IntegrityError):
            store.store_forecast(fc_duplicate)


class TestRunoffOnlyProvenanceRoundTrip:
    """epic-088 M4: RUNOFF_ONLY source + null reference time survive persistence.

    RED until migration 0026 makes ``nwp_cycle_reference_time`` nullable and
    extends the ``nwp_cycle_source`` CHECK to include ``'runoff_only'``.
    """

    def test_round_trip_runoff_only_null_reference(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection, "runoff_only_model")
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        fc = _make_forecast(
            sid,
            mid,
            aid,
            nwp_cycle_source=NwpCycleSource.RUNOFF_ONLY,
            nwp_cycle_reference_time=None,
        )
        store.store_forecast(fc)

        fetched = store.fetch_forecast(fc.id)
        assert fetched is not None
        assert fetched.nwp_cycle_source == NwpCycleSource.RUNOFF_ONLY
        assert fetched.nwp_cycle_reference_time is None


class TestForecastProvenanceConstraints:
    """The DB CHECK accepts the three known sources and rejects anything else."""

    def test_check_accepts_runoff_only(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection, "runoff_only_check")
        from sapphire_flow.db.metadata import forecasts

        db_connection.execute(
            sa.insert(forecasts).values(
                id=uuid4(),
                station_id=sid,
                model_id=mid,
                model_artifact_id=None,
                issued_at=_ISSUED_A,
                nwp_cycle_reference_time=None,
                nwp_cycle_source="runoff_only",
                representation="members",
                status="raw",
                version=1,
                parameter="discharge",
                units="m³/s",
            )
        )

    def test_check_rejects_unknown_source(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection, "bogus_source_check")
        from sapphire_flow.db.metadata import forecasts

        with pytest.raises(sqlalchemy.exc.IntegrityError):
            db_connection.execute(
                sa.insert(forecasts).values(
                    id=uuid4(),
                    station_id=sid,
                    model_id=mid,
                    model_artifact_id=None,
                    issued_at=_ISSUED_A,
                    nwp_cycle_reference_time=None,
                    nwp_cycle_source="satellite",
                    representation="members",
                    status="raw",
                    version=1,
                    parameter="discharge",
                    units="m³/s",
                )
            )


# ---------------------------------------------------------------------------
# Plan 038 locked atomicity tests
# ---------------------------------------------------------------------------


class TestStoreforecastAtomicityDefaultFactory:
    def test_default_factory_is_engine_begin(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgForecastStore(db_connection)
        # engine.begin is a bound method — new object each access;
        # compare via __self__/__func__ to avoid identity failure
        assert getattr(store._begin, "__self__", None) is db_connection.engine
        engine_cls = type(db_connection.engine)
        assert getattr(store._begin, "__func__", None) is engine_cls.begin


class TestStoreForecastAtomicityRollback:
    def test_values_insert_failure_rolls_back_header(
        self, db_connection: sa.Connection
    ) -> None:
        """Prove the values insert fires AND both rows are absent after rollback.

        The spy factory yields a DISTINCT proxy from db_connection, so
        monkeypatching the spy's execute can't accidentally intercept reads
        via self._conn.  The hit_values_insert flag rules out a pass caused
        by the header INSERT triggering a FK failure before values are reached.
        """
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection, "atomic_rollback_fc")
        aid = _seed_artifact(db_connection, sid, mid)

        hit_values_insert: dict[str, bool] = {"fired": False}

        @contextmanager
        def _failing_spy_factory():  # type: ignore[return]
            spy = _SpyConn(db_connection)
            real_spy_execute = spy.execute

            def _patched(stmt: object, *a: object, **k: object) -> object:
                if (
                    isinstance(stmt, sa.sql.dml.Insert)
                    and getattr(getattr(stmt, "table", None), "name", "")
                    == "forecast_values"
                ):
                    hit_values_insert["fired"] = True
                    raise sa.exc.IntegrityError(
                        "forced forecast_values failure", None, Exception()
                    )
                return real_spy_execute(stmt, *a, **k)

            spy.execute = _patched  # type: ignore[method-assign]
            with db_connection.begin_nested():
                yield spy

        store = PgForecastStore(db_connection, transaction_factory=_failing_spy_factory)
        fc = _make_forecast(sid, mid, aid)

        with pytest.raises(sa.exc.IntegrityError):
            store.store_forecast(fc)

        # The values insert must have fired (rules out a header-FK short-circuit)
        assert hit_values_insert["fired"], "forecast_values INSERT was never reached"

        from sapphire_flow.db.metadata import forecasts as forecasts_table

        # Both header and values must be absent (rollback was atomic)
        header_row = db_connection.execute(
            sa.select(forecasts_table.c.id).where(forecasts_table.c.id == fc.id)
        ).first()
        assert header_row is None

        values_row = db_connection.execute(
            sa.select(forecast_values.c.forecast_id).where(
                forecast_values.c.forecast_id == fc.id
            )
        ).first()
        assert values_row is None


class TestStoreForecastAtomicitySuccess:
    def test_writes_routed_through_injected_txn(
        self, db_connection: sa.Connection
    ) -> None:
        """Prove both INSERTs go through the spy (not self._conn).

        A broken impl that bypasses the injected txn and writes directly on
        self._conn (= db_connection) would NOT appear in spy.executed.
        """
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection, "atomic_success_fc")
        aid = _seed_artifact(db_connection, sid, mid)

        spies, factory = _capturing_spy_factory(db_connection)
        store = PgForecastStore(db_connection, transaction_factory=factory)
        fc = _make_forecast(sid, mid, aid)

        store.store_forecast(fc)

        assert len(spies) == 1, "factory must have been called exactly once"
        spy = spies[0]

        # The spy must have recorded at least 2 statements: header + values
        assert len(spy.executed) >= 2, (
            f"expected ≥2 statements via txn spy, got {len(spy.executed)}"
        )

        table_names = {
            getattr(getattr(stmt, "table", None), "name", None) for stmt in spy.executed
        }
        assert "forecasts" in table_names, "forecasts header INSERT missing from spy"
        assert "forecast_values" in table_names, (
            "forecast_values INSERT missing from spy"
        )

        from sapphire_flow.db.metadata import forecasts as forecasts_table

        header_row = db_connection.execute(
            sa.select(forecasts_table.c.id).where(forecasts_table.c.id == fc.id)
        ).first()
        assert header_row is not None

        value_count = db_connection.execute(
            sa.select(sa.func.count()).where(forecast_values.c.forecast_id == fc.id)
        ).scalar_one()
        assert value_count > 0


class TestStoreForecastIsolationHolds:
    def test_rolled_back_savepoint_invisible_from_fresh_connection(
        self, db_connection: sa.Connection, db_engine: sa.Engine
    ) -> None:
        """Prove a rolled-back savepoint write is invisible from a fresh connection."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection, "isolation_holds_fc")
        aid = _seed_artifact(db_connection, sid, mid)
        fc = _make_forecast(sid, mid, aid)

        from sapphire_flow.db.metadata import forecasts as forecasts_table

        # Write inside a savepoint then explicitly roll it back
        with db_connection.begin_nested() as sp:
            db_connection.execute(
                sa.insert(forecasts_table).values(
                    id=fc.id,
                    station_id=fc.station_id,
                    model_id=fc.model_id,
                    model_artifact_id=fc.model_artifact_id,
                    issued_at=fc.issued_at,
                    nwp_cycle_reference_time=fc.nwp_cycle_reference_time,
                    nwp_cycle_source=fc.nwp_cycle_source.value,
                    representation=fc.representation.value,
                    status=fc.status.value,
                    version=fc.version,
                    parameter=fc.ensemble.parameter,
                    units=fc.ensemble.units,
                    created_at=fc.created_at,
                    updated_at=fc.updated_at,
                    qc_status=fc.qc_status.value,
                    qc_flags=[],
                )
            )
            sp.rollback()

        # Verify the write is invisible from a separate connection
        with db_engine.connect() as fresh_conn:
            row = fresh_conn.execute(
                sa.select(forecasts_table.c.id).where(forecasts_table.c.id == fc.id)
            ).first()
        assert row is None, "rolled-back savepoint write leaked to a fresh connection"
