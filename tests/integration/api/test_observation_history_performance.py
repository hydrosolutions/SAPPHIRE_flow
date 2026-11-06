# ruff: noqa: F811
import gzip
import json
import os
import resource
from pathlib import Path
from statistics import median, quantiles
from time import perf_counter
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.enums import AccessTokenRole
from sapphire_flow.types.ids import StationId
from tests.conftest import make_station_config
from tests.integration.api.test_access_token_auth import _auth, _make_token
from tests.integration.api.test_swiss_observation_history import (  # noqa: F401
    PATH,
    A,
    history_api,
)


@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("SAPPHIRE_RUN_HISTORY_QUALIFICATION") != "1",
    reason=(
        "Unadmitted history performance gate: expensive provisional diagnostic; "
        "opt in with SAPPHIRE_RUN_HISTORY_QUALIFICATION=1"
    ),
)
class TestObservationHistoryPerformance:
    @pytest.mark.parametrize(
        "fleet,cadence,window",
        [(134, 5, 168), (200, 5, 168), (134, 10, 72), (200, 15, 72)],
    )
    def test_dense_route_qualification(
        self, history_api: SimpleNamespace, fleet: int, cadence: int, window: int
    ) -> None:
        state = history_api
        stations = [
            make_station_config(
                station_id=StationId(uuid4()),
                code=f"dense-{uuid4()}",
                tenant_id=state.stations[0].tenant_id,
            )
            for _ in range(fleet)
        ]
        with state.engine.begin() as conn:
            for station in stations:
                PgStationStore(conn).store_station(station)
            token = _make_token(
                conn,
                role=AccessTokenRole.REVIEWER,
                tenant_id=stations[0].tenant_id,
                station_ids=frozenset(s.id for s in stations),
            )
            conn.execute(
                sa.text("""
                INSERT INTO observations
                    (id, station_id, timestamp, parameter, source, value, qc_status)
                SELECT gen_random_uuid(), sid,
                       :cutoff - n * interval '1 minute' * :cadence,
                       'discharge', 'measured',
                       123.45678901234567 + sin(n * 0.37) * 98.76543210987654,
                       'qc_passed'
                FROM unnest(CAST(:ids AS uuid[])) sid
                CROSS JOIN generate_series(0, :points - 1) n
            """),
                {
                    "cutoff": A,
                    "cadence": cadence,
                    "ids": [s.id for s in stations],
                    "points": window * 60 // cadence + 1,
                },
            )
            conn.execute(sa.text("ANALYZE observations"))
        state.stations = stations
        durations = []
        query_counts = []
        observation_sql = []
        statements = []

        def capture(
            conn: sa.Connection,
            cursor: Any,
            statement: str,
            params: Any,
            context: Any,
            many: bool,
        ) -> None:
            statements.append(statement)
            if "FROM observations" in statement:
                observation_sql.append((statement, params))

        sa.event.listen(state.engine, "before_cursor_execute", capture)
        responses = []
        try:
            for _ in range(7):
                statements.clear()
                start = perf_counter()
                response = state.client.get(
                    PATH + f"?window_hours={window}", headers=_auth(token)
                )
                durations.append(perf_counter() - start)
                query_counts.append(len(statements))
                responses.append(response)
        finally:
            sa.event.remove(state.engine, "before_cursor_execute", capture)
        response = responses[-1]
        report = {
            "fleet": fleet,
            "cadence_minutes": cadence,
            "window_hours": window,
            "rows_per_station": window * 60 // cadence + 1,
            "eligible_rows": fleet * (window * 60 // cadence + 1),
            "statuses": [r.status_code for r in responses],
            "durations_seconds": durations,
            "cold_seconds": durations[0],
            "warm_p50_seconds": median(durations[1:]),
            "warm_p95_seconds": quantiles(durations[1:], n=20, method="inclusive")[18],
            "queries": query_counts,
            "raw_bytes_by_request": [len(r.content) for r in responses],
            "gzip_bytes_by_request": [len(gzip.compress(r.content)) for r in responses],
            "process_peak_rss_native_units": resource.getrusage(
                resource.RUSAGE_SELF
            ).ru_maxrss,
            "provisional_only": True,
        }
        destination = Path(
            os.environ.get(
                "SAPPHIRE_HISTORY_BENCHMARK_DIR", "/tmp/sapphire-history-benchmark"
            )
        )
        destination.mkdir(parents=True, exist_ok=True)
        stem = f"dense-{fleet}-{cadence}-{window}"
        (destination / f"{stem}.json").write_text(json.dumps(report, indent=2))
        if observation_sql:
            statement, params = observation_sql[-1]
            with state.engine.begin() as conn:
                conn.execute(sa.text("SET LOCAL statement_timeout = '10s'"))
                plan = conn.exec_driver_sql(
                    "EXPLAIN (FORMAT JSON) " + statement, params
                ).scalar_one()
            (destination / f"{stem}-explain.json").write_text(
                json.dumps(plan, indent=2)
            )
        assert response.status_code == 200, report
        assert response.json()["station_count"] == fleet
        assert len(response.content) <= 2097152, report
        # Candidate latency/query figures are recorded, not silently approved.
