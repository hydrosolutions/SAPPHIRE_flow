from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import bafu_unit_write_scopes, observations
from sapphire_flow.services.observation_history import summarize_history
from sapphire_flow.store.bafu_unit_read_filter import unit_read_state
from sapphire_flow.store.bafu_unit_writer import PgBafuUnitWriter
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.bafu_capture_batch import BafuStationCaptureBatch
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.dhm_delivery import DELIVERY_ID
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.observation_history import (
    HistoryGrid,
    HistoryRead,
    HistoryRowLimitError,
    HistoryWindow,
)
from tests.conftest import make_station_config
from tests.integration.store.test_bafu_batch_journal import batch as batch
from tests.integration.store.test_bafu_unit_read_filter import hold
from tests.integration.store.test_bafu_unit_writer import QUOTA, mark_passed, normalized

A = ensure_utc(datetime(2026, 11, 5, 12, 59, 59, 999999, tzinfo=UTC))


@pytest.fixture
def history(db_connection: sa.Connection) -> tuple[Any, HistoryGrid, HistoryRead]:
    station = make_station_config(station_id=StationId(uuid4()), code="history-test")
    PgStationStore(db_connection).store_station(station)
    grid = HistoryGrid(as_of=A, window=HistoryWindow.SEVEN_DAYS)
    request = HistoryRead(
        station_ids=(station.id,),
        start=grid.start,
        as_of=A,
        excluded_delivery_id=DELIVERY_ID,
        station_row_cap=2,
        fleet_row_cap=2,
    )
    return station, grid, request


def insert(
    conn: sa.Connection, station: Any, timestamp: datetime, value: float, **extra: Any
) -> None:
    row = dict(
        id=uuid4(),
        station_id=station.id,
        timestamp=timestamp,
        value=value,
        parameter="discharge",
        source="measured",
        qc_status="qc_passed",
    )
    row.update(extra)
    conn.execute(sa.insert(observations).values(**row))


class TestObservationHistory:
    def test_exclusions_before_caps_and_refresh_corrections(
        self,
        db_connection: sa.Connection,
        history: tuple[Any, HistoryGrid, HistoryRead],
    ) -> None:
        station, grid, request = history
        insert(db_connection, station, grid.start, 0.1)
        insert(db_connection, station, A, -0.0)
        # NULL delivery is eligible; every other cohort must be inert before LIMIT.
        for i in range(15):
            insert(
                db_connection,
                station,
                grid.start + timedelta(minutes=i + 1),
                1e200,
                delivery_id=DELIVERY_ID,
            )
        insert(
            db_connection,
            station,
            grid.start + timedelta(hours=1),
            999,
            qc_status="qc_failed",
        )
        insert(
            db_connection,
            station,
            grid.start + timedelta(hours=2),
            999,
            source="manual_import",
        )
        insert(db_connection, station, grid.start + timedelta(hours=3), float("nan"))
        insert(db_connection, station, grid.start + timedelta(hours=4), float("inf"))
        insert(db_connection, station, grid.start + timedelta(hours=5), float("-inf"))
        store = PgObservationStore(db_connection)
        rows = store.fetch_observation_history(request)
        assert len(rows) == 2
        result = summarize_history(grid, request.station_ids, rows)[0]
        assert result.coarse[0].mean == 0.1
        assert result.native[0].value.hex() == (-0.0).hex()
        db_connection.execute(
            sa.update(observations)
            .where(
                observations.c.station_id == station.id,
                observations.c.timestamp == grid.start,
            )
            .values(value=0.2)
        )
        assert store.fetch_observation_history(request)[0].value == 0.2
        hold(db_connection, (station.id, station.tenant_id), start=grid.start, end=A)
        assert len(store.fetch_observation_history(request)) == 1

    @pytest.mark.parametrize("fleet", [True, False])
    def test_eligible_overflow_never_truncated_success(
        self,
        db_connection: sa.Connection,
        history: tuple[Any, HistoryGrid, HistoryRead],
        fleet: bool,
    ) -> None:
        station, grid, request = history
        for i in range(3):
            insert(db_connection, station, grid.start + timedelta(seconds=i), float(i))
        request = replace(request, fleet_row_cap=2 if fleet else 10)
        with pytest.raises(HistoryRowLimitError, match="exceeds bound") as exc:
            PgObservationStore(db_connection).fetch_observation_history(request)
        assert exc.value.eligible_rows == 3
        assert exc.value.station_id == (None if fleet else station.id)

    def test_one_snapshot_plus_local_timeout_and_trusted_purpose(
        self,
        db_connection: sa.Connection,
        history: tuple[Any, HistoryGrid, HistoryRead],
    ) -> None:
        station, grid, request = history
        insert(db_connection, station, grid.start, 1.0)
        statements = []

        def capture(
            conn: sa.Connection,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            many: bool,
        ) -> None:
            statements.append(statement)

        sa.event.listen(db_connection, "before_cursor_execute", capture)
        try:
            PgObservationStore(db_connection).fetch_observation_history(request)
        finally:
            sa.event.remove(db_connection, "before_cursor_execute", capture)
        assert len(statements) == 2
        assert statements[0] == "SET LOCAL statement_timeout = '5s'"
        assert "LIMIT" in statements[1]
        with pytest.raises(ValueError, match="trusted"):
            PgObservationStore(
                db_connection
            ).qc_context_reader().fetch_observation_history(request)

    def test_exact_outer_bounds_parameter_and_station_scope_before_caps(
        self,
        db_connection: sa.Connection,
        history: tuple[Any, HistoryGrid, HistoryRead],
    ) -> None:
        station, grid, request = history
        other = make_station_config(
            station_id=StationId(uuid4()), code="history-outside"
        )
        PgStationStore(db_connection).store_station(other)
        insert(db_connection, station, grid.start, 2.0)
        insert(db_connection, station, A, 3.0)
        insert(db_connection, station, grid.start - timedelta(microseconds=1), 999.0)
        insert(db_connection, station, A + timedelta(microseconds=1), 999.0)
        insert(db_connection, station, grid.start, 999.0, parameter="water_level")
        insert(db_connection, other, grid.start, 999.0)
        rows = PgObservationStore(db_connection).fetch_observation_history(request)
        assert [(r.station_id, r.timestamp, r.value) for r in rows] == [
            (station.id, grid.start, 2.0),
            (station.id, A, 3.0),
        ]


class TestHistoryUnitEvidence:
    @pytest.mark.parametrize("approved", [True, False])
    def test_evidence_required_rows_use_current_canonical_view(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
        approved: bool,
    ) -> None:
        # Existing unit fixture computes canonical registry/capture hashes and writes
        # through the real admitted writer. Never forge evidence or replace the view.
        engine = isolated_committed_db_engine
        candidate = normalized(batch)
        if approved:
            with engine.begin() as conn:
                conn.execute(
                    sa.insert(bafu_unit_write_scopes).values(
                        id=uuid4(),
                        tenant_id=candidate.tenant_id,
                        station_id=candidate.station_id,
                        network="bafu",
                        gauge_code=candidate.key.gauge.value,
                        lindas_kind=candidate.key.kind,
                        starts_at=candidate.received_at,
                        ends_at=candidate.received_at + timedelta(days=1),
                        registry_sha256=candidate.registry_sha256,
                        approval_sha256="a" * 64,
                    )
                )
        PgBafuUnitWriter(engine.begin, quota=QUOTA).write(candidate)
        mark_passed(engine, candidate)
        request = HistoryRead(
            station_ids=(candidate.station_id,),
            start=candidate.received_at,
            as_of=candidate.received_at,
            excluded_delivery_id=DELIVERY_ID,
            station_row_cap=1,
            fleet_row_cap=1,
        )
        with engine.begin() as conn:
            disposition = conn.execute(
                sa.select(
                    unit_read_state.c.evidence_required,
                    unit_read_state.c.canonical,
                    unit_read_state.c.held,
                ).where(unit_read_state.c.station_id == candidate.station_id)
            ).one()
            assert disposition.evidence_required is True
            assert disposition.canonical is approved
            assert disposition.held is False
            rows = PgObservationStore(conn).fetch_observation_history(request)
        assert [row.value for row in rows] == ([0.022] if approved else [])
