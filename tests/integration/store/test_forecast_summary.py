from __future__ import annotations

import random
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import sqlalchemy as sa

from sapphire_flow.db.metadata import model_artifacts, models
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    ForecastStatus,
    NwpCycleSource,
    QcStatus,
)
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.forecast_summary import ForecastSummaryRow
from sapphire_flow.types.ids import ArtifactId, ForecastId, ModelId, StationId
from tests.conftest import make_forecast_ensemble, make_station_config

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime


@contextmanager
def savepoint_txn(conn: sa.Connection):  # type: ignore[return]
    with conn.begin_nested():
        yield conn


def savepoint_factory(conn: sa.Connection):
    return lambda: savepoint_txn(conn)


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
    parameter: str = "discharge",
    rng: random.Random | None = None,
) -> OperationalForecast:
    rng = rng or random.Random(42)
    ensemble = make_forecast_ensemble(
        station_id=station_id,
        representation=EnsembleRepresentation.MEMBERS,
        n_members=3,
        n_steps=5,
        parameter=parameter,
        rng=rng,
    )
    return OperationalForecast(
        id=ForecastId(uuid4()),
        station_id=station_id,
        model_id=model_id,
        model_artifact_id=artifact_id,
        issued_at=issued_at,
        nwp_cycle_reference_time=_NWP_CYCLE,
        nwp_cycle_source=NwpCycleSource.PRIMARY,
        representation=EnsembleRepresentation.MEMBERS,
        status=ForecastStatus.RAW,
        version=1,
        warm_up_source=None,
        warm_up_state_age_hours=None,
        observation_staleness_hours=None,
        ensemble=ensemble,
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestFetchSummariesRoundTrip:
    def test_round_trip_two_forecasts(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        fc_a = _make_forecast(sid, mid, aid, issued_at=_ISSUED_A, rng=random.Random(1))
        fc_b = _make_forecast(sid, mid, aid, issued_at=_ISSUED_B, rng=random.Random(2))
        store.store_forecast(fc_a)
        store.store_forecast(fc_b)

        start = ensure_utc(datetime(2025, 1, 1, 0, tzinfo=UTC))
        end = ensure_utc(datetime(2025, 1, 2, 0, tzinfo=UTC))
        summaries, total = store.fetch_forecast_summaries(sid, start, end)

        assert total == 2
        s = summaries[0]
        assert s.id == fc_b.id
        assert s.station_id == sid
        assert s.model_id == ModelId("linreg_v1")
        assert s.issued_at == _ISSUED_B
        assert s.issued_at.tzinfo is not None
        assert s.parameter == "discharge"
        assert s.representation is EnsembleRepresentation.MEMBERS
        assert s.status is ForecastStatus.RAW
        assert s.qc_status is QcStatus.RAW
        assert s.nwp_cycle_source is NwpCycleSource.PRIMARY
        assert isinstance(s, ForecastSummaryRow)
        assert not hasattr(s, "ensemble")


class TestFetchSummariesFilterModel:
    def test_filter_by_model_id(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid_lr = _seed_model(db_connection, "linreg_v1")
        mid_ar = _seed_model(db_connection, "arima_v1")
        aid_lr = _seed_artifact(db_connection, sid, mid_lr)
        aid_ar = _seed_artifact(db_connection, sid, mid_ar)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        fc_lr = _make_forecast(
            sid, mid_lr, aid_lr, issued_at=_ISSUED_A, rng=random.Random(10)
        )
        fc_ar = _make_forecast(
            sid, mid_ar, aid_ar, issued_at=_ISSUED_B, rng=random.Random(11)
        )
        store.store_forecast(fc_lr)
        store.store_forecast(fc_ar)

        start = ensure_utc(datetime(2025, 1, 1, 0, tzinfo=UTC))
        end = ensure_utc(datetime(2025, 1, 2, 0, tzinfo=UTC))
        summaries, total = store.fetch_forecast_summaries(
            sid, start, end, model_id=ModelId("linreg_v1")
        )

        assert total == 1
        assert len(summaries) == 1
        assert summaries[0].id == fc_lr.id
        assert summaries[0].model_id == ModelId("linreg_v1")


class TestFetchSummariesFilterParam:
    def test_filter_by_parameter(self, db_connection: sa.Connection) -> None:
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

        start = ensure_utc(datetime(2025, 1, 1, 0, tzinfo=UTC))
        end = ensure_utc(datetime(2025, 1, 2, 0, tzinfo=UTC))
        summaries, total = store.fetch_forecast_summaries(
            sid, start, end, parameter="discharge"
        )

        assert total == 1
        assert len(summaries) == 1
        assert summaries[0].id == fc_discharge.id
        assert summaries[0].parameter == "discharge"


class TestFetchSummariesHalfOpen:
    def test_end_boundary_excluded(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        boundary = ensure_utc(datetime(2025, 1, 1, 12, tzinfo=UTC))
        fc = _make_forecast(sid, mid, aid, issued_at=boundary, rng=random.Random(30))
        store.store_forecast(fc)

        start = ensure_utc(datetime(2025, 1, 1, 0, tzinfo=UTC))
        end = boundary  # half-open: end == issued_at, so excluded
        summaries, total = store.fetch_forecast_summaries(sid, start, end)

        assert summaries == []
        assert total == 0


class TestFetchSummariesOrdering:
    def test_newest_first(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        t1 = ensure_utc(datetime(2025, 1, 1, 0, tzinfo=UTC))
        t2 = ensure_utc(datetime(2025, 1, 1, 6, tzinfo=UTC))
        t3 = ensure_utc(datetime(2025, 1, 1, 12, tzinfo=UTC))

        fc1 = _make_forecast(sid, mid, aid, issued_at=t1, rng=random.Random(40))
        fc2 = _make_forecast(sid, mid, aid, issued_at=t2, rng=random.Random(41))
        fc3 = _make_forecast(sid, mid, aid, issued_at=t3, rng=random.Random(42))
        store.store_forecast(fc1)
        store.store_forecast(fc2)
        store.store_forecast(fc3)

        start = ensure_utc(datetime(2025, 1, 1, 0, tzinfo=UTC))
        end = ensure_utc(datetime(2025, 1, 2, 0, tzinfo=UTC))
        summaries, total = store.fetch_forecast_summaries(sid, start, end)

        assert total == 3
        assert summaries[0].issued_at == t3
        assert summaries[1].issued_at == t2
        assert summaries[2].issued_at == t1


class TestFetchSummariesPagination:
    def test_pagination_limit_and_offset(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        forecasts_stored = []
        for i in range(5):
            issued = ensure_utc(datetime(2025, 1, 1, i, tzinfo=UTC))
            fc = _make_forecast(
                sid, mid, aid, issued_at=issued, rng=random.Random(50 + i)
            )
            store.store_forecast(fc)
            forecasts_stored.append(fc)

        start = ensure_utc(datetime(2025, 1, 1, 0, tzinfo=UTC))
        end = ensure_utc(datetime(2025, 1, 2, 0, tzinfo=UTC))

        page_0, total_0 = store.fetch_forecast_summaries(
            sid, start, end, limit=2, offset=0
        )
        assert len(page_0) == 2
        assert total_0 == 5

        page_2, total_2 = store.fetch_forecast_summaries(
            sid, start, end, limit=2, offset=4
        )
        assert len(page_2) == 1
        assert total_2 == 5

        all_ids: set[ForecastId] = set()
        offset = 0
        while True:
            page, total = store.fetch_forecast_summaries(
                sid, start, end, limit=2, offset=offset
            )
            if not page:
                break
            page_ids = {s.id for s in page}
            assert page_ids.isdisjoint(all_ids)
            all_ids.update(page_ids)
            offset += len(page)

        expected_ids = {fc.id for fc in forecasts_stored}
        assert all_ids == expected_ids


class TestFetchSummariesEmpty:
    def test_no_forecasts_returns_empty(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        start = ensure_utc(datetime(2025, 1, 1, 0, tzinfo=UTC))
        end = ensure_utc(datetime(2025, 1, 2, 0, tzinfo=UTC))
        summaries, total = store.fetch_forecast_summaries(sid, start, end)

        assert summaries == []
        assert total == 0
