from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import event

from sapphire_flow.db.metadata import observations
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.types.enums import ObservationSource, QcStatus
from tests.integration.store.test_bafu_unit_read_filter import NOW, hold
from tests.integration.store.test_bafu_unit_read_filter import station as station

if TYPE_CHECKING:
    from sapphire_flow.types.ids import StationId, TenantId


class TestBoundedObservationRead:
    def test_actual_reader_keeps_trust_check_in_correlated_subplan(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        statements: list[tuple[str, Any]] = []

        def capture(
            conn: sa.Connection,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            statements.append((statement, parameters))

        event.listen(db_connection, "before_cursor_execute", capture)
        try:
            rows = PgObservationStore(db_connection).fetch_observations(
                station[0],
                "discharge",
                NOW,
                NOW + timedelta(hours=1),
                source=ObservationSource.MEASURED,
            )
        finally:
            event.remove(db_connection, "before_cursor_execute", capture)

        assert len(rows) == 1
        assert len(statements) == 1
        sql, parameters = statements[0]
        assert "WHERE (SELECT EXISTS (SELECT" in sql
        plan = db_connection.exec_driver_sql(
            "EXPLAIN (FORMAT JSON) " + sql, parameters
        ).scalar_one()[0]["Plan"]

        def outer_scan_has_scalar_filter(node: dict[str, Any]) -> bool:
            return (
                node.get("Relation Name") == observations.name
                and node.get("Alias") == observations.name
                and "SubPlan" in node.get("Filter", "")
            ) or any(
                outer_scan_has_scalar_filter(child) for child in node.get("Plans", [])
            )

        assert outer_scan_has_scalar_filter(plan)

    def test_window_source_qc_and_holds_preserve_chronological_results(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        # Deliberately insert out of time order, with both window boundaries.
        for hour in (4, 2, -1, 3, 1):
            db_connection.execute(
                sa.insert(observations).values(
                    id=uuid4(),
                    station_id=station[0],
                    timestamp=NOW + timedelta(hours=hour),
                    parameter="discharge",
                    source="measured",
                    value=float(hour),
                    qc_status="qc_passed" if hour != 3 else "raw",
                )
            )
        hold(
            db_connection,
            station,
            start=NOW + timedelta(hours=1),
            end=NOW + timedelta(hours=2),
        )
        store = PgObservationStore(db_connection)
        rows = store.fetch_observations(
            station[0],
            "discharge",
            NOW,
            NOW + timedelta(hours=4),
            source=ObservationSource.MEASURED,
        )
        assert [row.timestamp for row in rows] == [
            NOW + timedelta(hours=hour) for hour in (0, 2, 3)
        ]
        passed = store.fetch_observations(
            station[0],
            "discharge",
            NOW,
            NOW + timedelta(hours=4),
            qc_status=QcStatus.QC_PASSED,
            source=ObservationSource.MEASURED,
        )
        assert [row.timestamp for row in passed] == [NOW + timedelta(hours=2)]
        manual = store.fetch_observations(
            station[0],
            "discharge",
            NOW,
            NOW + timedelta(hours=4),
            source=ObservationSource.MANUAL_IMPORT,
        )
        assert [row.timestamp for row in manual] == [NOW]
