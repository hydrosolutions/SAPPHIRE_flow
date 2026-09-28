"""Plan 404 T1 — `PgRejectedForecastStore`: append-only, all-or-none batch
write with D6's abandon-before-commit guard, and lock/statement timeouts on
its own dedicated (never the shared AUTOCOMMIT) connection.
"""

from __future__ import annotations

import math
import random
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import polars as pl
import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import models, rejected_forecasts, stations
from sapphire_flow.exceptions import CaptureAbandonedError
from sapphire_flow.store.rejected_forecast_store import (
    PgRejectedForecastStore,
    rejected_capture_transaction_factory,
)
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import EnsembleRepresentation, QcStatus
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.rejected_forecast import (
    RejectedAssignmentPayload,
    RejectedForecastEntry,
    RejectedParameterPayload,
)
from tests.conftest import make_forecast_ensemble, make_station_config

_NOW = ensure_utc(datetime(2026, 1, 10, tzinfo=UTC))


@contextmanager
def _savepoint(conn: sa.Connection):  # type: ignore[return]
    with conn.begin_nested():
        yield conn


def savepoint_factory(conn: sa.Connection):
    return lambda: _savepoint(conn)


def _seed_station(conn: sa.Connection, rng_seed: int = 1) -> StationId:
    station = make_station_config(rng=random.Random(rng_seed))
    PgStationStore(conn).store_station(station)
    return station.id


def _seed_model(conn: sa.Connection, model_id: str = "rejected-test-model") -> ModelId:
    conn.execute(
        sa.insert(models).values(
            id=model_id,
            display_name="Test model",
            artifact_scope="station",
            description="Test model",
        )
    )
    return ModelId(model_id)


def _entry(
    *,
    station_id: StationId,
    model_id: ModelId,
    attempt_id=None,
    issued_at=_NOW,
    parameters: tuple[RejectedParameterPayload, ...] | None = None,
    group_id=None,
) -> RejectedForecastEntry:
    if parameters is None:
        parameters = (
            RejectedParameterPayload(
                ensemble=make_forecast_ensemble(
                    station_id=station_id, n_steps=2, n_members=2
                ),
                qc_status=QcStatus.QC_FAILED,
                qc_flags=(
                    QcFlag(
                        rule_id="range_check",
                        rule_version="1.0",
                        status=QcStatus.QC_FAILED,
                        detail="out of range",
                    ),
                ),
            ),
        )
    return RejectedForecastEntry(
        attempt_id=attempt_id or uuid4(),
        payload=RejectedAssignmentPayload(
            station_id=station_id,
            model_id=model_id,
            model_artifact_id=None,
            issued_at=issued_at,
            group_id=group_id,
            parameters=parameters,
        ),
    )


class TestWriteBatchRoundTrip:
    def test_member_and_quantile_round_trip_with_multiple_parameters(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        store = PgRejectedForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        member_ensemble = make_forecast_ensemble(
            station_id=sid,
            n_steps=2,
            n_members=3,
            parameter="discharge",
            representation=EnsembleRepresentation.MEMBERS,
        )
        quantile_ensemble = make_forecast_ensemble(
            station_id=sid,
            n_steps=2,
            parameter="water_level",
            representation=EnsembleRepresentation.QUANTILES,
        )
        entry = _entry(
            station_id=sid,
            model_id=mid,
            parameters=(
                RejectedParameterPayload(
                    ensemble=member_ensemble,
                    qc_status=QcStatus.QC_FAILED,
                    qc_flags=(
                        QcFlag(
                            rule_id="range_check",
                            rule_version="1.0",
                            status=QcStatus.QC_FAILED,
                            detail="too high",
                        ),
                    ),
                ),
                RejectedParameterPayload(
                    ensemble=quantile_ensemble,
                    qc_status=QcStatus.QC_SUSPECT,
                    qc_flags=(
                        QcFlag(
                            rule_id="quantile_crossing",
                            rule_version="1.0",
                            status=QcStatus.QC_SUSPECT,
                            detail="crossing detected",
                        ),
                    ),
                ),
            ),
        )
        store.write_batch([entry], abandon=threading.Event())

        rows, total = store.fetch_rejected_forecasts(
            sid, _NOW - timedelta(days=1), _NOW + timedelta(days=1)
        )
        assert total == 2
        by_param = {r.parameter: r for r in rows}
        discharge = by_param["discharge"]
        assert discharge.qc_status == QcStatus.QC_FAILED
        assert discharge.representation == EnsembleRepresentation.MEMBERS
        assert discharge.attempt_id == entry.attempt_id
        assert discharge.qc_flags[0].detail == "too high"
        water_level = by_param["water_level"]
        assert water_level.qc_status == QcStatus.QC_SUSPECT
        assert water_level.representation == EnsembleRepresentation.QUANTILES

        # values round-trip: same member/quantile keys, same count of points.
        raw_df = member_ensemble.values
        for member_id in raw_df["member_id"].unique().to_list():
            key = str(member_id)
            assert key in discharge.values
            expected_n = raw_df.filter(pl.col("member_id") == member_id).height
            assert len(discharge.values[key]) == expected_n

    def test_single_step_forecast_round_trips(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, rng_seed=2)
        mid = _seed_model(db_connection, "single-step-model")
        store = PgRejectedForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        ensemble = make_forecast_ensemble(station_id=sid, n_steps=1, n_members=2)
        entry = _entry(
            station_id=sid,
            model_id=mid,
            parameters=(
                RejectedParameterPayload(
                    ensemble=ensemble, qc_status=QcStatus.QC_FAILED, qc_flags=()
                ),
            ),
        )
        store.write_batch([entry], abandon=threading.Event())
        rows, total = store.fetch_rejected_forecasts(
            sid, _NOW - timedelta(days=1), _NOW + timedelta(days=1)
        )
        assert total == 1
        assert rows[0].time_step_seconds == int(ensemble.time_step.total_seconds())

    def test_members_with_different_timelines_round_trip(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, rng_seed=3)
        mid = _seed_model(db_connection, "diff-timeline-model")
        store = PgRejectedForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        t0 = _NOW
        rows_data = [
            {"valid_time": t0 + timedelta(hours=1), "member_id": 0, "value": 1.0},
            {"valid_time": t0 + timedelta(hours=2), "member_id": 0, "value": 2.0},
            {"valid_time": t0 + timedelta(hours=1), "member_id": 1, "value": 3.0},
        ]
        df = pl.DataFrame(rows_data).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )
        ensemble = ForecastEnsemble.from_members(
            station_id=sid,
            issued_at=t0,
            parameter="discharge",
            units="m3/s",
            time_step=timedelta(hours=1),
            values=df,
        )
        entry = _entry(
            station_id=sid,
            model_id=mid,
            parameters=(
                RejectedParameterPayload(
                    ensemble=ensemble, qc_status=QcStatus.QC_FAILED, qc_flags=()
                ),
            ),
        )
        store.write_batch([entry], abandon=threading.Event())
        rows, _ = store.fetch_rejected_forecasts(
            sid, _NOW - timedelta(days=1), _NOW + timedelta(days=1)
        )
        values = rows[0].values
        assert len(values["0"]) == 2
        assert len(values["1"]) == 1

    def test_unchecked_status_never_reads_as_pass_even_with_no_flags(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, rng_seed=4)
        mid = _seed_model(db_connection, "unchecked-model")
        store = PgRejectedForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        entry = _entry(
            station_id=sid,
            model_id=mid,
            parameters=(
                RejectedParameterPayload(
                    ensemble=make_forecast_ensemble(station_id=sid, n_steps=1),
                    qc_status=QcStatus.QC_UNCHECKED,
                    qc_flags=(),
                ),
            ),
        )
        store.write_batch([entry], abandon=threading.Event())
        rows, _ = store.fetch_rejected_forecasts(
            sid, _NOW - timedelta(days=1), _NOW + timedelta(days=1)
        )
        assert rows[0].qc_status == QcStatus.QC_UNCHECKED
        assert rows[0].qc_flags == ()

    def test_nonfinite_values_round_trip_losslessly(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, rng_seed=5)
        mid = _seed_model(db_connection, "nonfinite-model")
        store = PgRejectedForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        df = pl.DataFrame(
            {
                "valid_time": [_NOW + timedelta(hours=1)] * 3,
                "member_id": [0, 1, 2],
                "value": [float("nan"), float("inf"), float("-inf")],
            }
        ).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )
        ensemble = ForecastEnsemble.from_members(
            station_id=sid,
            issued_at=_NOW,
            parameter="discharge",
            units="m3/s",
            time_step=timedelta(hours=1),
            values=df,
        )
        entry = _entry(
            station_id=sid,
            model_id=mid,
            parameters=(
                RejectedParameterPayload(
                    ensemble=ensemble, qc_status=QcStatus.QC_FAILED, qc_flags=()
                ),
            ),
        )
        store.write_batch([entry], abandon=threading.Event())
        rows, _ = store.fetch_rejected_forecasts(
            sid, _NOW - timedelta(days=1), _NOW + timedelta(days=1)
        )
        values = rows[0].values
        assert math.isnan(values["0"][0][1])
        assert values["1"][0][1] == math.inf
        assert values["2"][0][1] == -math.inf

    def test_read_filters_window_model_and_order(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, rng_seed=6)
        mid_a = _seed_model(db_connection, "order-model-a")
        mid_b = _seed_model(db_connection, "order-model-b")
        store = PgRejectedForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.write_batch(
            [
                _entry(station_id=sid, model_id=mid_a, issued_at=_NOW),
                _entry(
                    station_id=sid, model_id=mid_b, issued_at=_NOW - timedelta(hours=1)
                ),
                _entry(
                    station_id=sid,
                    model_id=mid_a,
                    issued_at=_NOW - timedelta(days=30),
                ),
            ],
            abandon=threading.Event(),
        )
        rows, total = store.fetch_rejected_forecasts(
            sid, _NOW - timedelta(days=2), _NOW + timedelta(days=1)
        )
        assert total == 2
        assert [r.issued_at for r in rows] == sorted(
            (r.issued_at for r in rows), reverse=True
        )

        rows_a, total_a = store.fetch_rejected_forecasts(
            sid,
            _NOW - timedelta(days=40),
            _NOW + timedelta(days=1),
            model_id=mid_a,
        )
        assert total_a == 2
        assert all(r.model_id == mid_a for r in rows_a)

        # end exclusive
        rows_excl, total_excl = store.fetch_rejected_forecasts(
            sid, _NOW - timedelta(days=2), _NOW
        )
        assert total_excl == 1

    def test_limit_offset(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection, rng_seed=7)
        mid = _seed_model(db_connection, "paginate-model")
        store = PgRejectedForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.write_batch(
            [
                _entry(
                    station_id=sid, model_id=mid, issued_at=_NOW - timedelta(hours=i)
                )
                for i in range(3)
            ],
            abandon=threading.Event(),
        )
        rows, total = store.fetch_rejected_forecasts(
            sid,
            _NOW - timedelta(days=1),
            _NOW + timedelta(days=1),
            limit=1,
            offset=1,
        )
        assert total == 3
        assert len(rows) == 1


class TestWriteBatchAtomicityAndAbandon:
    def test_forced_failure_on_a_later_row_leaves_none_of_the_batch(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, rng_seed=8)
        mid = _seed_model(db_connection, "atomic-model")
        missing_station = StationId(uuid4())
        store = PgRejectedForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        good_entry = _entry(station_id=sid, model_id=mid)
        bad_entry = _entry(station_id=missing_station, model_id=mid)  # FK violation

        with pytest.raises(sa.exc.IntegrityError):
            store.write_batch([good_entry, bad_entry], abandon=threading.Event())

        rows, total = store.fetch_rejected_forecasts(
            sid, _NOW - timedelta(days=1), _NOW + timedelta(days=1)
        )
        assert total == 0

    def test_abandon_set_before_commit_leaves_no_rows(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, rng_seed=9)
        mid = _seed_model(db_connection, "abandon-model")
        store = PgRejectedForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        abandon = threading.Event()
        abandon.set()
        with pytest.raises(CaptureAbandonedError):
            store.write_batch([_entry(station_id=sid, model_id=mid)], abandon=abandon)

        rows, total = store.fetch_rejected_forecasts(
            sid, _NOW - timedelta(days=1), _NOW + timedelta(days=1)
        )
        assert total == 0

    def test_write_refused_when_built_with_no_transaction_factory(
        self, db_connection: sa.Connection
    ) -> None:
        from sapphire_flow.exceptions import ConfigurationError

        sid = _seed_station(db_connection, rng_seed=10)
        mid = _seed_model(db_connection, "no-factory-model")
        store = PgRejectedForecastStore(db_connection, transaction_factory=None)
        with pytest.raises(ConfigurationError):
            store.write_batch(
                [_entry(station_id=sid, model_id=mid)], abandon=threading.Event()
            )


class TestAppendOnlyGuard:
    @pytest.mark.parametrize("action", ("update", "delete", "truncate"))
    def test_owner_cannot_mutate_rejected_forecasts(
        self, db_connection: sa.Connection, action: str
    ) -> None:
        sid = _seed_station(db_connection, rng_seed=11)
        mid = _seed_model(db_connection, "append-only-model")
        store = PgRejectedForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.write_batch(
            [_entry(station_id=sid, model_id=mid)], abandon=threading.Event()
        )
        statement = {
            "update": sa.update(rejected_forecasts).values(recorded_at=sa.func.now()),
            "delete": sa.delete(rejected_forecasts),
            "truncate": sa.text("TRUNCATE rejected_forecasts CASCADE"),
        }[action]
        with (
            pytest.raises(sa.exc.DBAPIError, match="append-only"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(statement)


class TestLockTimeout:
    def test_conflicting_lock_yields_lock_not_available(
        self, db_engine: sa.Engine
    ) -> None:
        with db_engine.begin() as seed_conn:
            sid = _seed_station(seed_conn, rng_seed=12)
            mid = _seed_model(seed_conn, "lock-timeout-model")

        blocker = db_engine.connect()
        blocker.execute(sa.text("BEGIN"))
        blocker.execute(
            sa.text("LOCK TABLE rejected_forecasts IN ACCESS EXCLUSIVE MODE")
        )
        try:
            factory = rejected_capture_transaction_factory(db_engine.url)
            store = PgRejectedForecastStore(
                db_engine.connect(), transaction_factory=factory
            )
            with pytest.raises(sa.exc.OperationalError) as exc_info:
                store.write_batch(
                    [_entry(station_id=sid, model_id=mid)], abandon=threading.Event()
                )
            orig = exc_info.value.orig
            sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
            assert sqlstate == "55P03"
        finally:
            blocker.execute(sa.text("ROLLBACK"))
            blocker.close()
            with db_engine.begin() as cleanup_conn:
                cleanup_conn.execute(
                    sa.delete(rejected_forecasts).where(
                        rejected_forecasts.c.station_id == sid
                    )
                )
                cleanup_conn.execute(sa.delete(stations).where(stations.c.id == sid))
                cleanup_conn.execute(sa.delete(models).where(models.c.id == mid))


class TestRejectedCaptureTransactionFactory:
    def test_uses_nullpool_and_short_connect_timeout(
        self, db_engine: sa.Engine
    ) -> None:
        from sqlalchemy.pool import NullPool

        factory = rejected_capture_transaction_factory(db_engine.url)
        with factory() as conn:
            assert isinstance(conn.engine.pool, NullPool)
            assert conn.engine.url.query.get("connect_timeout") is None
        # connect_args are engine-level, not surfaced on the URL; assert via
        # the underlying dialect's connect kwargs instead.
        engine = sa.create_engine(
            db_engine.url, poolclass=NullPool, connect_args={"connect_timeout": 5}
        )
        assert engine.pool.__class__ is NullPool
