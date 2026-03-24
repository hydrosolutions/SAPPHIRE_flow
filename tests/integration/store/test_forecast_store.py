from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import sqlalchemy as sa

from sapphire_flow.db.metadata import model_artifacts, models
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
    rng: random.Random | None = None,
) -> OperationalForecast:
    rng = rng or random.Random(42)
    ensemble = make_forecast_ensemble(
        station_id=station_id,
        representation=representation,
        n_members=n_members,
        n_steps=n_steps,
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
        store = PgForecastStore(db_connection)

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
        store = PgForecastStore(db_connection)

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
        store = PgForecastStore(db_connection)

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
        store = PgForecastStore(db_connection)

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
        store = PgForecastStore(db_connection)

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
        store = PgForecastStore(db_connection)

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
        store = PgForecastStore(db_connection)

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
        store = PgForecastStore(db_connection)

        fc_b = _make_forecast(sid, mid, aid, issued_at=_ISSUED_B, rng=random.Random(11))
        store.store_forecast(fc_b)

        # ISSUED_B is the end boundary, so it should be excluded
        results = store.fetch_forecasts_in_range(sid, _ISSUED_A, _ISSUED_B)
        assert results == []

    def test_filter_by_status(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(db_connection)

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
        store = PgForecastStore(db_connection)

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
        store = PgForecastStore(db_connection)

        fc = _make_forecast(sid, mid, aid)
        store.store_forecast(fc)

        import pytest

        with pytest.raises(ConflictError):
            store.transition_status(fc.id, 99, ForecastStatus.REVIEWED)
