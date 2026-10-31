from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import observations
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.observation_read import ObservationReadPurpose
from tests.integration.store.test_bafu_unit_read_filter import NOW, hold
from tests.integration.store.test_bafu_unit_read_filter import station as station

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.types.ids import StationId, TenantId


class TestObservationUnitReaders:
    def test_all_numeric_reads_and_latest_obey_hold(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        store = PgObservationStore(db_connection)
        row = store.fetch_observations(
            station[0], "discharge", NOW, NOW + timedelta(days=1)
        )[0]
        # Remove manual sibling only to make latest eligibility unambiguous.
        db_connection.execute(
            sa.delete(observations).where(observations.c.source == "manual_import")
        )
        hold(db_connection, station)
        assert (
            store.fetch_observations(
                station[0], "discharge", NOW, NOW + timedelta(days=1)
            )
            == []
        )
        assert store.fetch_observations_batch(
            [station[0]], "discharge", NOW, NOW + timedelta(days=1)
        ) == {station[0]: []}
        assert store.fetch_observation_by_id(row.id) is None
        assert store.fetch_latest_timestamp(station[0], "discharge") is None
        assert store.fetch_latest_capture_timestamp(station[0], "discharge") == NOW
        assert (
            store.qc_context_reader().fetch_observations(
                station[0], "discharge", NOW, NOW + timedelta(days=1)
            )
            == []
        )

    @pytest.mark.parametrize(
        "change", [{"value": 2.0}, {"rating_curve_correction_version": "new"}]
    )
    def test_cas_rejects_changed_evaluation(
        self,
        db_connection: sa.Connection,
        station: tuple[StationId, TenantId],
        change: dict[str, object],
    ) -> None:
        store = PgObservationStore(db_connection).qc_context_reader()
        row = store.fetch_observations(
            station[0], "discharge", NOW, NOW + timedelta(days=1)
        )[0]
        db_connection.execute(
            sa.update(observations).where(observations.c.id == row.id).values(**change)
        )
        assert not store.update_qc_if_current(row, QcStatus.QC_PASSED, [])
        assert store.fetch_observation_by_id(row.id).qc_status is QcStatus.RAW

    def test_cas_rejects_changed_unit_context_at_zero(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        store = PgObservationStore(db_connection).qc_context_reader()
        db_connection.execute(sa.update(observations).values(value=0.0))
        row = store.fetch_observations(
            station[0], "discharge", NOW, NOW + timedelta(days=1)
        )[0]
        hold(db_connection, station)
        assert not store.update_qc_if_current(row, QcStatus.QC_PASSED, [])
        assert (
            db_connection.scalar(
                sa.select(observations.c.qc_status).where(observations.c.id == row.id)
            )
            == "raw"
        )

    def test_successful_cas_does_not_change_value(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        store = PgObservationStore(db_connection).qc_context_reader()
        row = store.fetch_observations(
            station[0], "discharge", NOW, NOW + timedelta(days=1)
        )[0]
        assert store.update_qc_if_current(row, QcStatus.QC_PASSED, [], "v1")
        current = store.fetch_observation_by_id(row.id)
        assert (current.value, current.qc_status) == (row.value, QcStatus.QC_PASSED)

    def test_interior_uncertain_row_splits_context(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        db_connection.execute(
            sa.delete(observations).where(observations.c.source == "manual_import")
        )
        for offset in (1, 2):
            db_connection.execute(
                sa.insert(observations).values(
                    id=uuid4(),
                    station_id=station[0],
                    timestamp=NOW + timedelta(hours=offset),
                    parameter="discharge",
                    source="measured",
                    value=1.0,
                    qc_status="raw",
                )
            )
        hold(
            db_connection,
            station,
            start=NOW + timedelta(hours=1),
            end=NOW + timedelta(hours=2),
        )
        rows = (
            PgObservationStore(db_connection)
            .qc_context_reader()
            .fetch_observations(station[0], "discharge", NOW, NOW + timedelta(days=1))
        )
        assert len(rows) == 2
        assert rows[0].qc_context_segment != rows[1].qc_context_segment

    def test_audit_purpose_fails_closed(self, db_connection: sa.Connection) -> None:
        with pytest.raises(ValueError, match="restricted D2"):
            PgObservationStore(
                db_connection, purpose=ObservationReadPurpose.SOURCE_AUDIT
            )


class TestAbsentUnitBoundary:
    def test_quarantined_capture_without_row_segments_context(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        db_connection.execute(
            sa.delete(observations).where(observations.c.source == "manual_import")
        )
        db_connection.execute(
            sa.insert(observations).values(
                id=uuid4(),
                station_id=station[0],
                timestamp=NOW + timedelta(hours=2),
                parameter="discharge",
                source="measured",
                value=1.0,
                qc_status="raw",
            )
        )
        hold(
            db_connection,
            station,
            start=NOW + timedelta(hours=1),
            end=NOW + timedelta(hours=1, microseconds=1),
        )
        rows = (
            PgObservationStore(db_connection)
            .qc_context_reader()
            .fetch_observations(station[0], "discharge", NOW, NOW + timedelta(days=1))
        )
        assert len(rows) == 2
        assert rows[0].qc_context_segment != rows[1].qc_context_segment


class TestUnitReadIsolation:
    def test_alias_uses_same_source_scoped_disposition(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        from sapphire_flow.store.bafu_unit_read_filter import trusted_unit_predicate

        hold(db_connection, station)
        alias = observations.alias("reader_alias")
        rows = db_connection.execute(
            sa.select(alias.c.source, alias.c.parameter).where(
                alias.c.station_id == station[0],
                trusted_unit_predicate(alias),
            )
        ).all()
        assert set(rows) == {
            ("manual_import", "discharge"),
            ("measured", "water_level"),
        }

    def test_other_station_is_not_withheld(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        from sapphire_flow.store.station_store import PgStationStore
        from sapphire_flow.types.ids import StationId
        from tests.conftest import make_station_config

        other = make_station_config(station_id=StationId(uuid4()), code="2044")
        PgStationStore(db_connection).store_station(other)
        db_connection.execute(
            sa.insert(observations).values(
                id=uuid4(),
                station_id=other.id,
                timestamp=NOW,
                parameter="discharge",
                source="measured",
                value=7.0,
                qc_status="raw",
            )
        )
        hold(db_connection, station)
        rows = PgObservationStore(db_connection).fetch_observations(
            other.id, "discharge", NOW, NOW + timedelta(days=1)
        )
        assert [row.value for row in rows] == [7.0]


class TestPartialReleaseBoundary:
    def test_absent_uncertain_remainder_still_segments_context(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        from sapphire_flow.db.metadata import bafu_unit_hold_releases

        db_connection.execute(
            sa.delete(observations).where(observations.c.source == "manual_import")
        )
        db_connection.execute(
            sa.insert(observations).values(
                id=uuid4(),
                station_id=station[0],
                timestamp=NOW + timedelta(hours=4),
                parameter="discharge",
                source="measured",
                value=1.0,
                qc_status="raw",
            )
        )
        identity = hold(
            db_connection,
            station,
            start=NOW + timedelta(hours=1),
            end=NOW + timedelta(hours=3),
        )
        db_connection.execute(
            sa.insert(bafu_unit_hold_releases).values(
                id=uuid4(),
                hold_id=identity,
                starts_at=NOW + timedelta(hours=1),
                ends_at=NOW + timedelta(hours=2),
                approval_sha256="a" * 64,
            )
        )
        rows = (
            PgObservationStore(db_connection)
            .qc_context_reader()
            .fetch_observations(station[0], "discharge", NOW, NOW + timedelta(days=1))
        )
        assert len(rows) == 2
        assert rows[0].qc_context_segment != rows[1].qc_context_segment


class TestReviewRegressions:
    @pytest.mark.parametrize("persisted", [False, True])
    def test_manual_context_ignores_measured_boundaries(
        self,
        db_connection: sa.Connection,
        station: tuple[StationId, TenantId],
        persisted: bool,
    ) -> None:
        from sapphire_flow.types.enums import ObservationSource

        for offset in (1, 2):
            db_connection.execute(
                sa.insert(observations).values(
                    id=uuid4(),
                    station_id=station[0],
                    timestamp=NOW + timedelta(hours=offset),
                    parameter="discharge",
                    source="manual_import",
                    value=1.0,
                    qc_status="raw",
                )
            )
        if persisted:
            db_connection.execute(
                sa.insert(observations).values(
                    id=uuid4(),
                    station_id=station[0],
                    timestamp=NOW + timedelta(hours=1),
                    parameter="discharge",
                    source="measured",
                    value=1.0,
                    qc_status="raw",
                )
            )
        hold(
            db_connection,
            station,
            start=NOW + timedelta(hours=1),
            end=NOW + timedelta(hours=2),
        )
        store = PgObservationStore(db_connection).qc_context_reader()
        rows = store.fetch_observations(
            station[0],
            "discharge",
            NOW,
            NOW + timedelta(days=1),
            source=ObservationSource.MANUAL_IMPORT,
        )
        assert len(rows) == 3
        assert len({row.qc_context_segment for row in rows}) == 1
        from sapphire_flow.services.qc import Stage1QualityChecker, infer_time_step
        from sapphire_flow.types.domain import QcRuleParams, QcRuleSet

        assert infer_time_step(rows) == timedelta(hours=1)
        rules = QcRuleSet(
            version="test",
            rules=(
                QcRuleParams(
                    rule_id="rate_of_change",
                    rule_version="test",
                    parameter="discharge",
                    time_step=timedelta(hours=1),
                    thresholds={"max_rate": 1.0},
                ),
            ),
        )
        checked = Stage1QualityChecker().check_with_coverage(
            rows,
            rules,
            [],
            [],
            station_networks={station[0]: "bafu"},
        )
        assert checked.judged == frozenset(row.id for row in rows[1:])
        mixed = store.fetch_observations(
            station[0], "discharge", NOW, NOW + timedelta(days=1)
        )
        assert len({row.qc_context_segment for row in mixed}) == 2

    def test_empty_cas_flags_are_sql_null(
        self,
        db_connection: sa.Connection,
        station: tuple[StationId, TenantId],
    ) -> None:
        store = PgObservationStore(db_connection).qc_context_reader()
        row = store.fetch_observations(
            station[0], "discharge", NOW, NOW + timedelta(days=1)
        )[0]
        assert store.update_qc_if_current(row, QcStatus.QC_PASSED, [])
        assert db_connection.scalar(
            sa.select(observations.c.qc_flags.is_(None)).where(
                observations.c.id == row.id
            )
        )


class TestTrustedDashboardCoverage:
    def test_coverage_uses_same_trusted_rows_as_total(
        self,
        db_connection: sa.Connection,
        station: tuple[StationId, TenantId],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from sapphire_flow.api.routes import tables
        from tests.integration.api.test_dashboard_forecasts import (
            _client,
            app_overrides_clear,
        )

        db_connection.execute(
            sa.insert(observations).values(
                id=uuid4(),
                station_id=station[0],
                timestamp=NOW + timedelta(days=1),
                parameter="discharge",
                source="measured",
                value=99.0,
                qc_status="raw",
            )
        )
        hold(db_connection, station, start=NOW + timedelta(days=1))
        monkeypatch.setattr(tables, "_reflected", None)
        client = _client(db_connection)
        try:
            response = client.get("/")
            assert response.status_code == 200
            assert response.context["obs_count"] == 3
            assert response.context["obs_date_range"] == {
                "min": "2026-10-28",
                "max": "2026-10-28",
            }
            assert {
                p["parameter"]: p["count"] for p in response.context["obs_params"]
            } == {"discharge": 2, "water_level": 1}
        finally:
            client.close()
            app_overrides_clear()


class TestQcDependencyReview:
    def test_changed_manual_neighbour_rejects_evaluated_verdict(
        self,
        db_connection: sa.Connection,
        station: tuple[StationId, TenantId],
    ) -> None:
        from sapphire_flow.types.enums import ObservationSource

        db_connection.execute(
            sa.insert(observations).values(
                id=uuid4(),
                station_id=station[0],
                timestamp=NOW + timedelta(hours=1),
                parameter="discharge",
                source="manual_import",
                value=2.0,
                qc_status="raw",
            )
        )
        store = PgObservationStore(db_connection).qc_context_reader()
        rows = store.fetch_observations(
            station[0],
            "discharge",
            NOW,
            NOW + timedelta(days=1),
            source=ObservationSource.MANUAL_IMPORT,
        )
        db_connection.execute(
            sa.update(observations)
            .where(observations.c.id == rows[0].id)
            .values(value=100.0)
        )
        assert not store.update_qc_if_current(rows[1], QcStatus.QC_PASSED, [])

    @pytest.mark.parametrize("history_rows", [10000, 50000])
    def test_synthetic_history_query_plan(
        self,
        history_rows: int,
        db_connection: sa.Connection,
        station: tuple[StationId, TenantId],
        tmp_path: object,
    ) -> None:
        import json
        import os
        from pathlib import Path

        db_connection.execute(
            sa.insert(observations),
            [
                dict(
                    id=uuid4(),
                    station_id=station[0],
                    timestamp=NOW - timedelta(minutes=10 * offset),
                    parameter="discharge",
                    source="measured",
                    value=1.0,
                    qc_status="raw",
                )
                for offset in range(1, history_rows + 1)
            ],
        )
        db_connection.execute(sa.text("ANALYZE observations"))
        from sapphire_flow.types.observation_read import QcContextWindow

        store = PgObservationStore(db_connection).qc_context_reader()
        statement = store._select_qc_window(
            QcContextWindow(
                station_id=station[0],
                parameter="discharge",
                start=NOW - timedelta(days=1),
                end=NOW,
            )
        )
        sql = str(
            statement.compile(
                dialect=db_connection.dialect, compile_kwargs={"literal_binds": True}
            )
        )
        db_connection.execute(sa.text("SET LOCAL statement_timeout = '30s'"))
        plan = db_connection.exec_driver_sql(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql
        ).scalar_one()
        destination = Path(os.environ.get("BAFU_REVIEW_EVIDENCE", str(tmp_path)))
        destination.mkdir(parents=True, exist_ok=True)
        destination = destination / str(history_rows)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "history-plan.json").write_text(json.dumps(plan, indent=2))
        (destination / "history-query.sql").write_text(sql)
        assert plan[0]["Plan"]["Actual Rows"] == 144
        # EXPLAIN SQL-language bodies too: Function Scan alone hides loop counts.
        import re

        body = db_connection.scalar(
            sa.text(
                "SELECT prosrc FROM pg_proc WHERE oid = "
                "'public.observation_qc_window(uuid,text,timestamptz,timestamptz,text)'::regprocedure"
            )
        )
        assert isinstance(body, str)
        literals = {
            "station": "'" + str(station[0]) + "'::uuid",
            "param": "'discharge'",
            "src": "NULL::text",
            "starts": "'" + (NOW - timedelta(days=1)).isoformat() + "'::timestamptz",
            "ends": "'" + NOW.isoformat() + "'::timestamptz",
        }
        body = re.sub(
            r"\b(station|param|src|starts|ends)\b",
            lambda match: literals[match[0]],
            body,
        )
        internal = db_connection.exec_driver_sql(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + body
        ).scalar_one()
        (destination / "internal-plan.json").write_text(json.dumps(internal, indent=2))
        (destination / "internal-query.sql").write_text(body)
        assert internal[0]["Plan"]["Actual Rows"] == 144


class TestBoundedDependencyWindow:
    @pytest.mark.parametrize("change", ["outside", "other_source", "savepoint"])
    def test_unchanged_context_accepts_batch(
        self,
        db_connection: sa.Connection,
        station: tuple[StationId, TenantId],
        change: str,
    ) -> None:
        from sapphire_flow.types.enums import ObservationSource

        store = PgObservationStore(db_connection).qc_context_reader()
        rows = store.fetch_observations(
            station[0],
            "discharge",
            NOW,
            NOW + timedelta(hours=2),
            source=ObservationSource.MANUAL_IMPORT,
        )
        before = rows[0].qc_read_token
        if change == "outside":
            db_connection.execute(
                sa.insert(observations).values(
                    id=uuid4(),
                    station_id=station[0],
                    parameter="discharge",
                    timestamp=NOW - timedelta(days=30),
                    source="manual_import",
                    value=99.0,
                    qc_status="raw",
                )
            )
            hold(db_connection, station, start=NOW + timedelta(days=30))
        elif change == "other_source":
            db_connection.execute(
                sa.update(observations)
                .where(observations.c.source == "measured")
                .values(value=99.0)
            )
            hold(db_connection, station)
        else:
            with db_connection.begin_nested() as nested:
                db_connection.execute(
                    sa.update(observations)
                    .where(observations.c.id == rows[0].id)
                    .values(value=99.0)
                )
                hold(db_connection, station)
                db_connection.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
                nested.rollback()
        fresh = store.fetch_observations(
            station[0],
            "discharge",
            NOW,
            NOW + timedelta(hours=2),
            source=ObservationSource.MANUAL_IMPORT,
        )
        assert fresh[0].qc_read_token == before
        assert store.update_qc_batch_if_current(
            [(row, QcStatus.QC_PASSED, []) for row in rows],
            "v1",
        )

    def test_passed_neighbour_is_part_of_raw_target_dependency(
        self,
        db_connection: sa.Connection,
        station: tuple[StationId, TenantId],
    ) -> None:
        from sapphire_flow.services.qc import Stage1QualityChecker
        from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
        from sapphire_flow.types.enums import ObservationSource

        db_connection.execute(
            sa.update(observations)
            .where(observations.c.source == "manual_import")
            .values(qc_status="qc_passed")
        )
        db_connection.execute(
            sa.insert(observations).values(
                id=uuid4(),
                station_id=station[0],
                timestamp=NOW + timedelta(hours=1),
                parameter="discharge",
                source="manual_import",
                value=2.0,
                qc_status="raw",
            )
        )
        store = PgObservationStore(db_connection).qc_context_reader()
        rows = store.fetch_observations(
            station[0],
            "discharge",
            NOW,
            NOW + timedelta(days=1),
            source=ObservationSource.MANUAL_IMPORT,
        )
        checked = Stage1QualityChecker().check_with_coverage(
            rows,
            QcRuleSet(
                version="v1",
                rules=(
                    QcRuleParams(
                        rule_id="rate_of_change",
                        rule_version="v1",
                        parameter="discharge",
                        time_step=timedelta(hours=1),
                        thresholds={"max_rate": 10.0},
                    ),
                ),
            ),
            [],
            [],
            station_networks={station[0]: "bafu"},
        )
        assert rows[-1].id in checked.judged
        db_connection.execute(
            sa.update(observations)
            .where(observations.c.id == rows[0].id)
            .values(value=100.0)
        )
        assert not store.update_qc_batch_if_current(
            [(rows[-1], QcStatus.QC_PASSED, checked.flags[rows[-1].id])],
            "v1",
        )
        assert (
            db_connection.scalar(
                sa.select(observations.c.qc_status).where(
                    observations.c.id == rows[-1].id
                )
            )
            == "raw"
        )


class TestLatestVisibleObservation:
    def test_bulk_latest_respects_unit_hold(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        from sapphire_flow.types.enums import ObservationSource

        store = PgObservationStore(db_connection)
        visible = store.fetch_latest_observations(
            [station[0]],
            "discharge",
            NOW,
            NOW + timedelta(days=1),
            source=ObservationSource.MEASURED,
        )
        assert visible[station[0]] is not None
        hold(db_connection, station)
        assert store.fetch_latest_observations(
            [station[0]],
            "discharge",
            NOW,
            NOW + timedelta(days=1),
            source=ObservationSource.MEASURED,
        ) == {station[0]: None}

    def test_qc_context_reader_cannot_use_latest_projection(
        self, db_connection: sa.Connection, station: tuple[StationId, TenantId]
    ) -> None:
        from sapphire_flow.types.enums import ObservationSource

        with pytest.raises(ValueError, match="trusted read purpose"):
            PgObservationStore(
                db_connection
            ).qc_context_reader().fetch_latest_observations(
                [station[0]],
                "discharge",
                NOW,
                NOW + timedelta(days=1),
                source=ObservationSource.MEASURED,
            )


class TestLatestObservationQueryEvidence:
    def test_bounded_window_with_history_hidden_tails_and_absent_parameter(
        self,
        db_connection: sa.Connection,
        station: tuple[StationId, TenantId],
        tmp_path: Path,
    ) -> None:
        import json
        from typing import Any

        from sapphire_flow.types.dhm_delivery import DELIVERY_ID
        from sapphire_flow.types.enums import ObservationSource

        historical = [
            {
                "id": uuid4(),
                "station_id": station[0],
                "timestamp": NOW - timedelta(days=60) + timedelta(minutes=i),
                "parameter": "discharge",
                "source": "measured",
                "value": 1.0,
                "qc_status": "raw",
                "delivery_id": None,
            }
            for i in range(9000)
        ]
        recent = [
            {
                "id": uuid4(),
                "station_id": station[0],
                "timestamp": NOW + timedelta(minutes=i + 1),
                "parameter": "discharge" if i % 2 == 0 else "water_level",
                "source": "measured",
                "value": 1.0,
                "qc_status": "raw",
                "delivery_id": DELIVERY_ID if i >= 900 else None,
            }
            for i in range(1000)
        ]
        db_connection.execute(sa.insert(observations), historical + recent)
        db_connection.exec_driver_sql("ANALYZE observations")
        store = PgObservationStore(db_connection)
        plans: dict[str, Any] = {}

        def measure(name: str, parameter: str) -> dict[StationId, Any]:
            captured: list[tuple[str, Any]] = []

            def capture(
                conn: sa.Connection,
                cursor: Any,
                statement: str,
                parameters: Any,
                context: Any,
                executemany: bool,
            ) -> None:
                captured.append((statement, parameters))

            sa.event.listen(db_connection, "before_cursor_execute", capture)
            try:
                result = store.fetch_latest_observations(
                    [station[0]],
                    parameter,
                    NOW,
                    NOW + timedelta(days=1),
                    source=ObservationSource.MEASURED,
                    excluded_delivery_id=DELIVERY_ID,
                )
            finally:
                sa.event.remove(db_connection, "before_cursor_execute", capture)
            assert len(captured) == 1
            statement, parameters = captured[0]
            plans[name] = db_connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement,
                parameters,
            ).scalar_one()
            return result

        visible = measure("mixed_parameters_hidden_delivery_tail", "discharge")
        assert visible[station[0]].timestamp == NOW + timedelta(minutes=899)
        assert measure("absent_parameter", "not_measured")[station[0]] is None
        hold(
            db_connection,
            station,
            start=NOW + timedelta(minutes=1),
            end=NOW + timedelta(days=1),
        )
        held = measure("unit_held_tail", "discharge")
        assert held[station[0]].timestamp == NOW
        (tmp_path / "latest-query-plans.json").write_text(json.dumps(plans, indent=2))
        pending = [plans["unit_held_tail"][0]["Plan"]]
        observation_reads: list[dict[str, Any]] = []
        while pending:
            node = pending.pop()
            if node.get("Relation Name") == "observations":
                observation_reads.append(node)
            pending.extend(node.get("Plans", []))
        # Includes the trust-view lookup: no scan may enumerate the 9,000
        # historical rows outside the requested window.
        assert observation_reads
        assert all(node["Actual Rows"] <= 1001 for node in observation_reads)


class TestLatestObservationMaximumPage:
    def test_maximum_window_page_with_no_matching_parameter_and_other_source_tail(
        self,
        db_connection: sa.Connection,
        tmp_path: Path,
    ) -> None:
        import json
        import random
        from typing import Any

        from sapphire_flow.store.station_store import PgStationStore
        from sapphire_flow.types.datetime import ensure_utc
        from sapphire_flow.types.enums import ObservationSource
        from tests.conftest import make_station_config

        stations = [
            make_station_config(code=f"MAX-{i:03}", rng=random.Random(9000 + i))
            for i in range(200)
        ]
        station_store = PgStationStore(db_connection)
        for item in stations:
            station_store.store_station(item)
        ids = [item.id for item in stations]
        start = ensure_utc(NOW - timedelta(hours=720))
        db_connection.execute(
            sa.text("""
            INSERT INTO observations
                (id, station_id, timestamp, parameter, source, value, qc_status)
            SELECT md5(s.id::text || ':' || ticks.i::text || ':' || src.kind)::uuid,
                   s.id, CAST(:start AS timestamptz) + ticks.i * interval '10 minutes',
                   CASE WHEN src.kind='measured' THEN 'water_level'
                        ELSE 'discharge' END,
                   src.kind, 1.0, 'raw'
            FROM stations s CROSS JOIN generate_series(1, 4320) AS ticks(i)
            CROSS JOIN (VALUES ('measured'), ('manual_import')) AS src(kind)
            WHERE s.id=ANY(CAST(:ids AS uuid[]))
        """),
            {"start": start, "ids": ids},
        )
        db_connection.exec_driver_sql("ANALYZE observations")
        captured: list[tuple[str, Any]] = []

        def capture(
            conn: sa.Connection,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            captured.append((statement, parameters))

        sa.event.listen(db_connection, "before_cursor_execute", capture)
        try:
            result = PgObservationStore(db_connection).fetch_latest_observations(
                ids,
                "discharge",
                start,
                NOW,
                source=ObservationSource.MEASURED,
            )
        finally:
            sa.event.remove(db_connection, "before_cursor_execute", capture)
        assert result == dict.fromkeys(ids)
        assert len(captured) == 1
        statement, parameters = captured[0]
        plans = db_connection.exec_driver_sql(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement,
            parameters,
        ).scalar_one()
        (tmp_path / "maximum-page-query-plan.json").write_text(
            json.dumps(plans, indent=2)
        )
        pending = [plans[0]["Plan"]]
        observation_reads: list[dict[str, Any]] = []
        while pending:
            node = pending.pop()
            if node.get("Relation Name") == "observations":
                observation_reads.append(node)
            pending.extend(node.get("Plans", []))
        assert observation_reads
        assert all("Index" in node["Node Type"] for node in observation_reads)
        assert all(node["Actual Rows"] == 0 for node in observation_reads)
        root_plan = plans[0]["Plan"]
        assert (
            root_plan.get("Shared Hit Blocks", 0)
            + root_plan.get("Shared Read Blocks", 0)
            < 1_000_000
        )
