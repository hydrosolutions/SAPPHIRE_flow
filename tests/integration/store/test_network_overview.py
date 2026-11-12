import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.network_overview import (
    ObservationAvailability,
    ProtectedObservationVisibility,
)
from tests.conftest import make_observation, make_station_config
from tests.integration.store.test_bafu_unit_read_filter import hold
from tests.integration.store.test_observation_store import _seed_station

NOW = ensure_utc(datetime(2026, 11, 10, 10, 20, tzinfo=UTC))


class TestOverviewObservations:
    def test_qc_and_completed_slots(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgObservationStore(db_connection)
        rows = [
            make_observation(
                station_id=sid,
                timestamp=ensure_utc(NOW - timedelta(minutes=offset)),
                parameter="water_level",
                value=-float(offset),
                qc_status=qc,
                rng=random.Random(offset),
            )
            for offset, qc in [
                (10, QcStatus.QC_FAILED),
                (20, QcStatus.QC_UNCHECKED),
                (80, QcStatus.RAW),
                (140, QcStatus.QC_SUSPECT),
                (200, QcStatus.QC_PASSED),
            ]
        ]
        store.store_observations(rows)
        result = store.fetch_overview_observations(
            [sid],
            as_of=NOW,
            protected_visibility=ProtectedObservationVisibility.EXCLUDE,
        )[sid, "water_level"]
        assert result.latest.sample is not None
        assert result.latest.sample.qc_status is QcStatus.QC_UNCHECKED
        assert result.latest_failed_at == NOW - timedelta(minutes=10)
        assert len(result.hourly) == 72
        assert result.hourly[-1].sample == result.latest.sample
        assert result.hourly[-2].sample.qc_status is QcStatus.RAW

    def test_equal_time_sources_are_ambiguous(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        store = PgObservationStore(db_connection)
        first = make_observation(station_id=sid, timestamp=NOW, value=3)
        second = replace(
            make_observation(
                station_id=sid, timestamp=NOW, value=4, rng=random.Random(99)
            ),
            source=ObservationSource.MANUAL_IMPORT,
        )
        store.store_observations([first, second])
        result = store.fetch_overview_observations(
            [sid],
            as_of=NOW,
            protected_visibility=ProtectedObservationVisibility.DETAIL_AUTHORIZED,
        )[sid, "discharge"]
        assert result.latest.availability is ObservationAvailability.AMBIGUOUS_SOURCE
        assert result.latest.sample is None

    def test_latest_outside_chart_and_failed_only(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        store = PgObservationStore(db_connection)
        store.store_observations(
            [
                make_observation(
                    station_id=sid,
                    timestamp=ensure_utc(NOW - timedelta(hours=100)),
                    value=4,
                ),
                make_observation(
                    station_id=sid,
                    timestamp=NOW,
                    parameter="water_level",
                    value=5,
                    qc_status=QcStatus.QC_FAILED,
                    rng=random.Random(98),
                ),
            ]
        )
        result = store.fetch_overview_observations(
            [sid],
            as_of=NOW,
            protected_visibility=ProtectedObservationVisibility.EXCLUDE,
        )
        assert result[sid, "discharge"].latest.sample is not None
        assert all(s.sample is None for s in result[sid, "discharge"].hourly)
        assert result[sid, "water_level"].latest.sample is None
        assert result[sid, "water_level"].latest_failed_at == NOW

    def test_latest_inclusive_cutoff_and_hourly_exclusive_start(
        self, db_connection: sa.Connection
    ) -> None:
        import random

        sid = _seed_station(db_connection)
        store = PgObservationStore(db_connection)
        hour = ensure_utc(NOW.replace(minute=0))
        store.store_observations(
            [
                make_observation(
                    station_id=sid,
                    timestamp=ensure_utc(NOW - timedelta(hours=168)),
                    value=4,
                    rng=random.Random(31),
                ),
                make_observation(
                    station_id=sid,
                    parameter="water_level",
                    timestamp=ensure_utc(hour - timedelta(hours=72)),
                    value=5,
                    rng=random.Random(32),
                ),
            ]
        )
        result = store.fetch_overview_observations(
            [sid],
            as_of=NOW,
            protected_visibility=ProtectedObservationVisibility.EXCLUDE,
        )
        assert result[sid, "discharge"].latest.sample is not None
        assert result[sid, "water_level"].hourly[0].sample is None
        assert all(slot.sample is None for slot in result[sid, "water_level"].hourly)

    def test_derived_sources_never_substitute_measurements(
        self, db_connection: sa.Connection
    ) -> None:
        import random

        sid = _seed_station(db_connection)
        store = PgObservationStore(db_connection)
        store.store_observations(
            [
                replace(
                    make_observation(
                        station_id=sid, timestamp=NOW, value=99, rng=random.Random(71)
                    ),
                    source=ObservationSource.COMPONENT_DERIVED,
                )
            ]
        )
        result = store.fetch_overview_observations(
            [sid],
            as_of=NOW,
            protected_visibility=ProtectedObservationVisibility.DETAIL_AUTHORIZED,
        )
        assert result[sid, "discharge"].latest.sample is None

    def test_unit_held_newest_sample_falls_back_to_older_one(
        self, db_connection: sa.Connection
    ) -> None:
        station = make_station_config(code="HELD-001", rng=random.Random(81))
        PgStationStore(db_connection).store_station(station)
        store = PgObservationStore(db_connection)
        hour = ensure_utc(NOW.replace(minute=0))
        store.store_observations(
            [
                make_observation(
                    station_id=station.id,
                    timestamp=ensure_utc(hour - timedelta(minutes=minutes)),
                    value=float(minutes),
                    rng=random.Random(minutes),
                )
                for minutes in (10, 40)
            ]
        )
        hold(
            db_connection,
            (station.id, station.tenant_id),
            start=ensure_utc(hour - timedelta(minutes=20)),
        )
        result = store.fetch_overview_observations(
            [station.id],
            as_of=NOW,
            protected_visibility=ProtectedObservationVisibility.EXCLUDE,
        )[station.id, "discharge"]
        older = ensure_utc(hour - timedelta(minutes=40))
        assert result.latest.sample is not None
        assert result.latest.sample.timestamp == older
        assert result.hourly[-1].sample == result.latest.sample
