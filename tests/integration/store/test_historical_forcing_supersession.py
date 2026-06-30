"""Acceptance tests for the latest-version supersession filter on
``HistoricalForcingStore.fetch_forcing``.

Milestone 071-reanalysis-core, criterion 4: given two content-hash versions
of the same logical key ``(station_id, valid_time, parameter, source)``,
``fetch_forcing`` (without an explicit ``version=``) returns only the latest
version — no duplicate rows. The explicit ``version=`` audit path is
preserved.

LOCKED acceptance test. ``created_at`` is set explicitly on direct table
inserts so "latest" is deterministic (the store's server-default ``NOW()``
is transaction-constant and cannot distinguish two rows inserted in one
transaction).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from sapphire_flow.db.metadata import historical_forcing
from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
from sapphire_flow.types.ids import StationId
from tests.conftest import make_station_config


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _seed_station(conn: sa.Connection) -> StationId:
    from sapphire_flow.store.station_store import PgStationStore

    station = make_station_config(
        station_id=StationId(uuid.uuid4()),
        code=f"HF-{uuid.uuid4().hex[:6]}",
        network="camels",
    )
    PgStationStore(conn).store_station(station)
    return station.id


def _insert_row(
    conn: sa.Connection,
    *,
    station_id: StationId,
    source: str,
    version: str,
    valid_time: datetime,
    parameter: str,
    value: float,
    created_at: datetime,
) -> None:
    conn.execute(
        sa.insert(historical_forcing).values(
            id=uuid.uuid4(),
            station_id=station_id,
            source=source,
            version=version,
            valid_time=valid_time,
            parameter=parameter,
            spatial_type="basin_average",
            band_id=None,
            member_id=None,
            value=value,
            created_at=created_at,
        )
    )


class TestFetchForcingSupersession:
    def test_two_versions_returns_only_latest_no_duplicate_rows(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        store = PgHistoricalForcingStore(db_connection)
        vt = _utc(2026, 4, 10)
        # Same logical key (station, source, valid_time, parameter), two
        # distinct content-hash versions republished at different times.
        _insert_row(
            db_connection,
            station_id=sid,
            source="meteoswiss_rprelimd",
            version="old0000000000000",
            valid_time=vt,
            parameter="precipitation",
            value=3.0,
            created_at=_utc(2026, 4, 10, 6),
        )
        _insert_row(
            db_connection,
            station_id=sid,
            source="meteoswiss_rprelimd",
            version="new0000000000000",
            valid_time=vt,
            parameter="precipitation",
            value=4.0,
            created_at=_utc(2026, 4, 11, 6),
        )

        records = store.fetch_forcing(
            sid,
            "meteoswiss_rprelimd",
            _utc(2026, 4, 10),
            _utc(2026, 4, 11),
        )

        assert len(records) == 1
        assert records[0].version == "new0000000000000"
        assert records[0].value == 4.0

    def test_explicit_version_returns_exact_non_latest_row(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        store = PgHistoricalForcingStore(db_connection)
        vt = _utc(2026, 4, 10)
        _insert_row(
            db_connection,
            station_id=sid,
            source="meteoswiss_rprelimd",
            version="old0000000000000",
            valid_time=vt,
            parameter="precipitation",
            value=3.0,
            created_at=_utc(2026, 4, 10, 6),
        )
        _insert_row(
            db_connection,
            station_id=sid,
            source="meteoswiss_rprelimd",
            version="new0000000000000",
            valid_time=vt,
            parameter="precipitation",
            value=4.0,
            created_at=_utc(2026, 4, 11, 6),
        )

        records = store.fetch_forcing(
            sid,
            "meteoswiss_rprelimd",
            _utc(2026, 4, 10),
            _utc(2026, 4, 11),
            version="old0000000000000",
        )

        assert len(records) == 1
        assert records[0].version == "old0000000000000"
        assert records[0].value == 3.0

    def test_single_version_unaffected(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgHistoricalForcingStore(db_connection)
        _insert_row(
            db_connection,
            station_id=sid,
            source="meteoswiss_tabsd",
            version="only000000000000",
            valid_time=_utc(2026, 4, 10),
            parameter="temperature",
            value=12.5,
            created_at=_utc(2026, 4, 10, 6),
        )

        records = store.fetch_forcing(
            sid,
            "meteoswiss_tabsd",
            _utc(2026, 4, 10),
            _utc(2026, 4, 11),
        )

        assert len(records) == 1
        assert records[0].version == "only000000000000"

    def test_distinct_logical_keys_each_keep_their_latest(
        self, db_connection: sa.Connection
    ) -> None:
        # Supersession collapses per logical key, not across the whole query:
        # two different valid_times must each survive (two rows back), each at
        # its own latest version.
        sid = _seed_station(db_connection)
        store = PgHistoricalForcingStore(db_connection)
        for day in (10, 11):
            _insert_row(
                db_connection,
                station_id=sid,
                source="meteoswiss_rprelimd",
                version="old0000000000000",
                valid_time=_utc(2026, 4, day),
                parameter="precipitation",
                value=1.0,
                created_at=_utc(2026, 4, day, 6),
            )
            _insert_row(
                db_connection,
                station_id=sid,
                source="meteoswiss_rprelimd",
                version="new0000000000000",
                valid_time=_utc(2026, 4, day),
                parameter="precipitation",
                value=2.0,
                created_at=_utc(2026, 4, day, 18),
            )

        records = store.fetch_forcing(
            sid,
            "meteoswiss_rprelimd",
            _utc(2026, 4, 10),
            _utc(2026, 4, 12),
        )

        assert len(records) == 2
        assert {r.version for r in records} == {"new0000000000000"}
        assert {r.valid_time.day for r in records} == {10, 11}
