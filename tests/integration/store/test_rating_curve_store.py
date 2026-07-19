from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.enums import InterpolationMethod
from sapphire_flow.types.ids import RatingCurveId, StationId
from sapphire_flow.types.rating_curve import RatingCurve
from tests.conftest import make_station_config

_NOW = datetime(2025, 1, 1, tzinfo=UTC)

_POINTS = [
    {"water_level": 1.0, "discharge": 10.0},
    {"water_level": 2.0, "discharge": 45.0},
]


def _seed_station(conn: sa.Connection, *, code: str = "STA-001") -> StationId:
    station = make_station_config(
        station_id=StationId(uuid.uuid4()), code=code, network="bafu"
    )
    PgStationStore(conn).store_station(station)
    return station.id


def _make_curve(
    *,
    station_id: StationId,
    version: int = 1,
    valid_from: datetime = _NOW,
    valid_to: datetime | None = None,
    interpolation: InterpolationMethod = InterpolationMethod.LINEAR,
    curve_id: RatingCurveId | None = None,
) -> RatingCurve:
    return RatingCurve(
        id=curve_id or RatingCurveId(uuid.uuid4()),
        station_id=station_id,
        version=version,
        valid_from=valid_from,
        valid_to=valid_to,
        points=list(_POINTS),
        interpolation=interpolation,
        uploaded_by=None,
        created_at=valid_from,
    )


class TestStoreAndFetchActiveCurve:
    def test_round_trip_preserves_interpolation_enum(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        store = PgRatingCurveStore(db_connection)
        curve = _make_curve(
            station_id=station_id, interpolation=InterpolationMethod.LOG_LINEAR
        )

        returned_id = store.store_rating_curve(curve)
        assert returned_id == curve.id

        fetched = store.fetch_active_curve(station_id)
        assert fetched is not None
        assert fetched.id == curve.id
        assert fetched.station_id == station_id
        assert fetched.version == 1
        assert fetched.valid_to is None
        assert fetched.points == _POINTS
        assert fetched.interpolation is InterpolationMethod.LOG_LINEAR
        assert isinstance(fetched.interpolation, InterpolationMethod)

    def test_fetch_active_curve_returns_none_when_absent(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        store = PgRatingCurveStore(db_connection)
        assert store.fetch_active_curve(station_id) is None


class TestSupersedeCurve:
    def test_supersede_sets_valid_to_and_clears_active(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        store = PgRatingCurveStore(db_connection)
        curve = _make_curve(station_id=station_id, valid_from=_NOW)
        store.store_rating_curve(curve)

        supersede_at = _NOW + timedelta(days=30)
        store.supersede_curve(curve.id, supersede_at)

        assert store.fetch_active_curve(station_id) is None
        fetched_at_old_time = store.fetch_curve_at(station_id, _NOW + timedelta(days=1))
        assert fetched_at_old_time is not None
        assert fetched_at_old_time.id == curve.id
        assert fetched_at_old_time.valid_to == supersede_at

    def test_partial_unique_index_blocks_two_active_curves_per_station(
        self, db_connection: sa.Connection
    ) -> None:
        """The DB-level partial UNIQUE (station_id) WHERE valid_to IS NULL is the
        only thing preventing two simultaneously-active curves per station — the
        store layer performs no such check itself. Without the index this insert
        would succeed silently (Postgres allows duplicate rows for a nullable/no
        -constraint column), so this test is discriminating against a regression
        that drops or narrows the index.
        """
        station_id = _seed_station(db_connection)
        store = PgRatingCurveStore(db_connection)
        store.store_rating_curve(
            _make_curve(station_id=station_id, version=1, valid_from=_NOW)
        )

        with pytest.raises(
            sa.exc.IntegrityError, match="uq_rating_curves_station_active"
        ):
            store.store_rating_curve(
                _make_curve(
                    station_id=station_id,
                    version=2,
                    valid_from=_NOW + timedelta(days=1),
                )
            )


class TestFetchCurveAt:
    def test_picks_curve_active_at_timestamp(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        store = PgRatingCurveStore(db_connection)
        old_curve = _make_curve(
            station_id=station_id,
            version=1,
            valid_from=_NOW,
            valid_to=_NOW + timedelta(days=10),
        )
        new_curve = _make_curve(
            station_id=station_id,
            version=2,
            valid_from=_NOW + timedelta(days=10),
            valid_to=None,
        )
        store.store_rating_curve(old_curve)
        store.store_rating_curve(new_curve)

        before = store.fetch_curve_at(station_id, _NOW + timedelta(days=5))
        assert before is not None
        assert before.id == old_curve.id

        after = store.fetch_curve_at(station_id, _NOW + timedelta(days=20))
        assert after is not None
        assert after.id == new_curve.id

    def test_returns_none_before_any_curve_valid(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        store = PgRatingCurveStore(db_connection)
        store.store_rating_curve(_make_curve(station_id=station_id, valid_from=_NOW))

        assert store.fetch_curve_at(station_id, _NOW - timedelta(days=1)) is None


class TestFetchCurvesInRange:
    def test_returns_curves_overlapping_range(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        store = PgRatingCurveStore(db_connection)
        curve_1 = _make_curve(
            station_id=station_id,
            version=1,
            valid_from=_NOW,
            valid_to=_NOW + timedelta(days=10),
        )
        curve_2 = _make_curve(
            station_id=station_id,
            version=2,
            valid_from=_NOW + timedelta(days=10),
            valid_to=_NOW + timedelta(days=20),
        )
        curve_3 = _make_curve(
            station_id=station_id,
            version=3,
            valid_from=_NOW + timedelta(days=100),
            valid_to=None,
        )
        store.store_rating_curve(curve_1)
        store.store_rating_curve(curve_2)
        store.store_rating_curve(curve_3)

        result = store.fetch_curves_in_range(
            station_id, _NOW + timedelta(days=5), _NOW + timedelta(days=15)
        )
        result_ids = {c.id for c in result}
        assert result_ids == {curve_1.id, curve_2.id}
        assert curve_3.id not in result_ids

    def test_returns_empty_list_when_no_overlap(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        store = PgRatingCurveStore(db_connection)
        store.store_rating_curve(
            _make_curve(
                station_id=station_id,
                valid_from=_NOW,
                valid_to=_NOW + timedelta(days=10),
            )
        )

        result = store.fetch_curves_in_range(
            station_id, _NOW + timedelta(days=100), _NOW + timedelta(days=110)
        )
        assert result == []

    def test_boundary_touching_curves_do_not_overlap(
        self, db_connection: sa.Connection
    ) -> None:
        # Half-open [start, end): a curve ending exactly at `start`, or starting
        # exactly at `end`, does NOT overlap (repo range convention).
        station_id = _seed_station(db_connection)
        store = PgRatingCurveStore(db_connection)
        before = _make_curve(
            station_id=station_id,
            version=1,
            valid_from=_NOW,
            valid_to=_NOW + timedelta(days=10),
        )
        target = _make_curve(
            station_id=station_id,
            version=2,
            valid_from=_NOW + timedelta(days=10),
            valid_to=_NOW + timedelta(days=20),
        )
        after = _make_curve(
            station_id=station_id,
            version=3,
            valid_from=_NOW + timedelta(days=20),
            valid_to=None,
        )
        for c in (before, target, after):
            store.store_rating_curve(c)

        result = store.fetch_curves_in_range(
            station_id, _NOW + timedelta(days=10), _NOW + timedelta(days=20)
        )
        assert {c.id for c in result} == {target.id}, (
            "boundary-touching curves must be excluded under half-open [start, end)"
        )


class TestFetchActiveCurvesBatch:
    def test_returns_active_curve_per_station(
        self, db_connection: sa.Connection
    ) -> None:
        station_a = _seed_station(db_connection, code="STA-A")
        station_b = _seed_station(db_connection, code="STA-B")
        store = PgRatingCurveStore(db_connection)

        curve_a = _make_curve(station_id=station_a, valid_from=_NOW)
        curve_b = _make_curve(station_id=station_b, valid_from=_NOW)
        store.store_rating_curve(curve_a)
        store.store_rating_curve(curve_b)

        result = store.fetch_active_curves_batch([station_a, station_b])
        assert set(result.keys()) == {station_a, station_b}
        assert result[station_a].id == curve_a.id
        assert result[station_b].id == curve_b.id

    def test_excludes_stations_without_active_curve(
        self, db_connection: sa.Connection
    ) -> None:
        station_a = _seed_station(db_connection, code="STA-A")
        station_b = _seed_station(db_connection, code="STA-B")
        store = PgRatingCurveStore(db_connection)

        curve_a = _make_curve(station_id=station_a, valid_from=_NOW)
        store.store_rating_curve(curve_a)

        result = store.fetch_active_curves_batch([station_a, station_b])
        assert set(result.keys()) == {station_a}

    def test_empty_input_returns_empty_dict(self, db_connection: sa.Connection) -> None:
        store = PgRatingCurveStore(db_connection)
        assert store.fetch_active_curves_batch([]) == {}
