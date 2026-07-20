from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.observation_version_store import PgObservationVersionStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import InterpolationMethod, ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, RatingCurveId, StationId
from sapphire_flow.types.observation import Observation
from sapphire_flow.types.rating_curve import RatingCurve
from tests.conftest import make_station_config

_NOW = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))


def _seed_station(conn: sa.Connection, *, code: str = "OV-001") -> StationId:
    station = make_station_config(
        station_id=StationId(uuid.uuid4()), code=code, network="bafu"
    )
    PgStationStore(conn).store_station(station)
    return station.id


def _make_curve(
    conn: sa.Connection, station_id: StationId, *, version: int = 1
) -> RatingCurveId:
    curve_id = RatingCurveId(uuid.uuid4())
    PgRatingCurveStore(conn).store_rating_curve(
        RatingCurve(
            id=curve_id,
            station_id=station_id,
            version=version,
            valid_from=_NOW,
            valid_to=None if version == 1 else _NOW + timedelta(days=version),
            points=[{"water_level": 1.0, "discharge": 10.0}],
            interpolation=InterpolationMethod.LINEAR,
            uploaded_by=None,
            created_at=_NOW,
        )
    )
    return curve_id


def _store_derived_obs(
    conn: sa.Connection,
    station_id: StationId,
    curve_id: RatingCurveId,
    *,
    hour: int = 0,
    value: float | None = 12.3,
    qc_status: QcStatus = QcStatus.QC_PASSED,
) -> Observation:
    obs = Observation(
        id=ObservationId(uuid.uuid4()),
        station_id=station_id,
        timestamp=_NOW + timedelta(hours=hour),
        parameter="discharge",
        value=value,
        source=ObservationSource.RATING_CURVE_DERIVED,
        rating_curve_id=curve_id,
        rating_curve_correction_version=None,
        qc_status=qc_status,
        qc_flags=[],
        qc_rule_version=None,
        created_at=_NOW,
    )
    PgObservationStore(conn).store_observations([obs])
    return obs


class TestArchiveAndFetch:
    def test_round_trip_preserves_fields(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        old_curve = _make_curve(db_connection, sid, version=1)
        new_curve = _make_curve(db_connection, sid, version=2)
        obs = _store_derived_obs(db_connection, sid, old_curve, hour=0, value=12.3)
        store = PgObservationVersionStore(db_connection)

        assert store.archive_observation_values([obs], new_curve) == 1

        fetched = store.fetch_archived_values(
            sid, "discharge", _NOW, _NOW + timedelta(hours=1)
        )
        assert len(fetched) == 1
        a = fetched[0]
        assert a.observation_id == obs.id
        assert a.station_id == sid
        assert a.value == 12.3
        assert a.rating_curve_id == old_curve
        assert a.superseded_by_curve_id == new_curve
        assert a.superseded_at is not None

    def test_archive_is_idempotent(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        old_curve = _make_curve(db_connection, sid, version=1)
        new_curve = _make_curve(db_connection, sid, version=2)
        obs = _store_derived_obs(db_connection, sid, old_curve)
        store = PgObservationVersionStore(db_connection)

        assert store.archive_observation_values([obs], new_curve) == 1
        # Re-archiving the same (observation, producing curve) inserts nothing.
        assert store.archive_observation_values([obs], new_curve) == 0

        fetched = store.fetch_archived_values(
            sid, "discharge", _NOW, _NOW + timedelta(hours=1)
        )
        assert len(fetched) == 1

    def test_missing_observation_archives_null_value(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        old_curve = _make_curve(db_connection, sid, version=1)
        new_curve = _make_curve(db_connection, sid, version=2)
        obs = _store_derived_obs(
            db_connection, sid, old_curve, value=None, qc_status=QcStatus.MISSING
        )
        store = PgObservationVersionStore(db_connection)

        assert store.archive_observation_values([obs], new_curve) == 1
        [a] = store.fetch_archived_values(
            sid, "discharge", _NOW, _NOW + timedelta(hours=1)
        )
        assert a.value is None


class TestArchiveValidation:
    def test_rejects_non_derived_observation(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        new_curve = _make_curve(db_connection, sid, version=1)
        # A MEASURED observation (rating_curve_id None) — not archivable.
        measured = Observation(
            id=ObservationId(uuid.uuid4()),
            station_id=sid,
            timestamp=_NOW,
            parameter="discharge",
            value=5.0,
            source=ObservationSource.MEASURED,
            rating_curve_id=None,
            rating_curve_correction_version=None,
            qc_status=QcStatus.QC_PASSED,
            qc_flags=[],
            qc_rule_version=None,
            created_at=_NOW,
        )
        store = PgObservationVersionStore(db_connection)
        with pytest.raises(ValueError, match="rating-curve-derived"):
            store.archive_observation_values([measured], new_curve)

    def test_rejects_measured_source_even_with_curve_id(
        self, db_connection: sa.Connection
    ) -> None:
        # Discriminates the source check specifically: rating_curve_id is set but
        # source is MEASURED — a null-check alone would let this through.
        sid = _seed_station(db_connection)
        new_curve = _make_curve(db_connection, sid, version=1)
        mislabelled = Observation(
            id=ObservationId(uuid.uuid4()),
            station_id=sid,
            timestamp=_NOW,
            parameter="discharge",
            value=5.0,
            source=ObservationSource.MEASURED,
            rating_curve_id=new_curve,  # set, but source is not derived
            rating_curve_correction_version=None,
            qc_status=QcStatus.QC_PASSED,
            qc_flags=[],
            qc_rule_version=None,
            created_at=_NOW,
        )
        store = PgObservationVersionStore(db_connection)
        with pytest.raises(ValueError, match="rating-curve-derived"):
            store.archive_observation_values([mislabelled], new_curve)


class TestFetchFilters:
    def test_half_open_window_and_curve_filter(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        curve_a = _make_curve(db_connection, sid, version=1)
        curve_b = _make_curve(db_connection, sid, version=2)
        new_curve = _make_curve(db_connection, sid, version=3)
        obs0 = _store_derived_obs(db_connection, sid, curve_a, hour=0)
        obs6 = _store_derived_obs(db_connection, sid, curve_b, hour=6)
        store = PgObservationVersionStore(db_connection)
        store.archive_observation_values([obs0, obs6], new_curve)

        # Half-open [0h, 6h): includes obs0, excludes obs6 at the boundary.
        window = store.fetch_archived_values(
            sid, "discharge", _NOW, _NOW + timedelta(hours=6)
        )
        assert {a.observation_id for a in window} == {obs0.id}

        # Filter by producing curve.
        only_b = store.fetch_archived_values(
            sid,
            "discharge",
            _NOW,
            _NOW + timedelta(hours=12),
            rating_curve_id=curve_b,
        )
        assert {a.observation_id for a in only_b} == {obs6.id}


class TestSameStationIntegrity:
    def test_cross_station_superseding_curve_rejected(
        self, db_connection: sa.Connection
    ) -> None:
        # The superseding curve must belong to the archived row's station:
        # composite FK (station_id, superseded_by_curve_id) -> rating_curves.
        station_a = _seed_station(db_connection, code="OV-A")
        station_b = _seed_station(db_connection, code="OV-B")
        curve_a = _make_curve(db_connection, station_a, version=1)
        curve_b = _make_curve(db_connection, station_b, version=1)  # other station
        obs = _store_derived_obs(db_connection, station_a, curve_a)
        store = PgObservationVersionStore(db_connection)

        with pytest.raises(sa.exc.IntegrityError):
            store.archive_observation_values([obs], curve_b)
