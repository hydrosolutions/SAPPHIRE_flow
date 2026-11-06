from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from sapphire_flow.api import deps
from sapphire_flow.api.errors import http_exception_handler, unhandled_exception_handler
from sapphire_flow.api.publication_cache import PublicationCacheMiddleware
from sapphire_flow.api.routes import api_swiss_observation_history as route
from sapphire_flow.api.security import load_access_token_pepper
from sapphire_flow.config.swiss_overview import SwissOverviewConfig
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AccessTokenRole
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.swiss_overview import SwissRosterEntry
from tests.conftest import make_station_config
from tests.integration.api.test_access_token_auth import _auth, _make_token
from tests.integration.history_fixtures import (
    capture_statements,
    observation_statements,
)
from tests.integration.store.test_bafu_unit_read_filter import hold
from tests.integration.store.test_observation_history import insert

A = ensure_utc(datetime(2026, 11, 5, 12, 59, 59, 999999, tzinfo=UTC))
PATH = "/api/v1/swiss/observation-history"


@pytest.fixture
def history_api(
    isolated_committed_db_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SimpleNamespace]:
    engine = isolated_committed_db_engine
    station = make_station_config(
        station_id=StationId(uuid4()), code=f"history-{uuid4()}"
    )
    with engine.begin() as conn:
        PgStationStore(conn).store_station(station)
        token = _make_token(
            conn,
            role=AccessTokenRole.REVIEWER,
            tenant_id=station.tenant_id,
            station_ids=frozenset({station.id}),
        )
    state = SimpleNamespace(stations=[station], now=A, token=token, engine=engine)
    state.config = SimpleNamespace(
        protected_backup_max_age_hours=24,
        publication_proof_window_hours=24,
        swiss_observation_history=True,
        swiss_overview=SwissOverviewConfig(roster_count=1, roster_sha256="a" * 64),
        evaluation_forecasts=SimpleNamespace(
            to_domain=lambda: SimpleNamespace(tenant_id=station.tenant_id)
        ),
    )
    monkeypatch.setattr(
        route,
        "load_swiss_roster",
        lambda _: tuple(
            SwissRosterEntry(station_id=s.id, network=s.network, code=s.code)
            for s in state.stations
        ),
    )
    app = FastAPI()
    app.state.engine = engine
    app.state.access_token_pepper = load_access_token_pepper()
    app.include_router(route.router)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(PublicationCacheMiddleware)
    app.dependency_overrides[deps.get_deployment_config] = lambda: state.config
    app.dependency_overrides[deps.get_evaluation_clock] = lambda: state.now
    with TestClient(app, raise_server_exceptions=False) as client:
        state.client = client
        yield state


class TestHistoryAuthorization:
    def test_actual_query_count_and_local_setting_restored(
        self, history_api: SimpleNamespace
    ) -> None:
        state = history_api
        queries = []

        def capture(
            conn: sa.Connection,
            cursor: Any,
            statement: str,
            params: Any,
            context: Any,
            many: bool,
        ) -> None:
            queries.append(statement)

        sa.event.listen(state.engine, "before_cursor_execute", capture)
        try:
            response = state.client.get(PATH, headers=_auth(state.token))
        finally:
            sa.event.remove(state.engine, "before_cursor_execute", capture)
        assert response.status_code == 200, response.text
        # Explicit station grants require a preexisting tenant-integrity query.
        assert len(queries) == 7
        assert sum("FROM observations" in query for query in queries) == 1
        with state.engine.connect() as conn:
            assert conn.execute(sa.text("SHOW statement_timeout")).scalar_one() == "0"

    @pytest.mark.parametrize(
        "query",
        ["window_hours=bogus", "window_hours=72&window_hours=168", "tenant=private"],
    )
    def test_unauthenticated_query_never_reads_observations(
        self, history_api: SimpleNamespace, query: str
    ) -> None:
        with capture_statements(history_api.engine) as statements:
            response = history_api.client.get(PATH + "?" + query)
        assert observation_statements(statements) == []
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"

    def test_database_timeout_rolls_back_auth_write(
        self, history_api: SimpleNamespace
    ) -> None:
        from sapphire_flow.db.metadata import access_tokens

        state = history_api

        # Cause a real server-side 57014 only on the observation statement.
        def delay(
            conn: sa.Connection,
            cursor: Any,
            statement: str,
            params: Any,
            context: Any,
            many: bool,
        ) -> tuple[str, Any]:
            if "FROM observations" in statement:
                return "SELECT pg_sleep(6)", {}
            return statement, params

        sa.event.listen(state.engine, "before_cursor_execute", delay, retval=True)
        try:
            response = state.client.get(PATH, headers=_auth(state.token))
        finally:
            sa.event.remove(state.engine, "before_cursor_execute", delay)
        assert response.status_code == 503, response.text
        assert response.headers["cache-control"] == "no-store"
        with state.engine.connect() as conn:
            assert (
                conn.execute(
                    sa.select(access_tokens.c.last_used_at).where(
                        access_tokens.c.key_prefix == state.token.split(".")[0]
                    )
                ).scalar_one()
                is None
            )
            assert conn.execute(sa.text("SHOW statement_timeout")).scalar_one() == "0"


class TestHistoryIsolation:
    @pytest.mark.parametrize("credential", ["malformed", "consumer"])
    def test_bad_auth_precedes_malformed_query_without_numeric_sql(
        self, history_api: SimpleNamespace, credential: str
    ) -> None:
        state = history_api
        token = "malformed"
        if credential == "consumer":
            with state.engine.begin() as conn:
                token = _make_token(
                    conn,
                    role=AccessTokenRole.CONSUMER,
                    tenant_id=state.stations[0].tenant_id,
                    station_ids=frozenset({state.stations[0].id}),
                )
        with capture_statements(state.engine) as statements:
            response = state.client.get(
                PATH + "?window_hours=bad&window_hours=72&tenant=private",
                headers=_auth(token),
            )
        assert response.status_code == (403 if credential == "consumer" else 401)
        assert response.headers["cache-control"] == "no-store"
        assert observation_statements(statements) == []

    @pytest.mark.parametrize("field", ["code", "network", "missing", "tenant"])
    def test_roster_drift_precedes_numeric_sql(
        self, history_api: SimpleNamespace, field: str
    ) -> None:
        state = history_api
        # Change manifest identity, leaving the stored station and credentials valid.
        station = state.stations[0]
        if field == "tenant":
            state.config.evaluation_forecasts = SimpleNamespace(
                to_domain=lambda: SimpleNamespace(tenant_id=uuid4())
            )
        elif field == "missing":
            state.stations = [replace(station, id=StationId(uuid4()))]
        else:
            state.stations = [replace(station, **{field: "drift"})]
        with capture_statements(state.engine) as statements:
            response = state.client.get(PATH, headers=_auth(state.token))
        assert response.status_code == 503
        assert observation_statements(statements) == []

    @pytest.mark.parametrize("role", [AccessTokenRole.REVIEWER, AccessTokenRole.ADMIN])
    def test_roster_scope_with_unlisted_and_ungranted_stations(
        self, history_api: SimpleNamespace, role: AccessTokenRole
    ) -> None:
        state = history_api
        first = state.stations[0]
        second = make_station_config(
            station_id=StationId(uuid4()),
            code=f"listed-{uuid4()}",
            tenant_id=first.tenant_id,
        )
        unlisted = make_station_config(
            station_id=StationId(uuid4()),
            code=f"unlisted-{uuid4()}",
            tenant_id=first.tenant_id,
        )
        with state.engine.begin() as conn:
            for station in [second, unlisted]:
                PgStationStore(conn).store_station(station)
            token = _make_token(
                conn,
                role=role,
                tenant_id=None if role is AccessTokenRole.ADMIN else first.tenant_id,
                station_ids=frozenset()
                if role is AccessTokenRole.ADMIN
                else frozenset({first.id, unlisted.id}),
            )
            for station in [first, second, unlisted]:
                insert(conn, station, A, 2.0)
        state.stations = [first, second]
        response = state.client.get(PATH, headers=_auth(token))
        assert response.status_code == 200, response.text
        expected = [str(first.id)] + (
            [str(second.id)] if role == AccessTokenRole.ADMIN else []
        )
        assert [item["station"]["id"] for item in response.json()["items"]] == expected
        assert str(unlisted.id) not in response.text
        assert all(
            item["native"] == [["2026-11-05T12:59:59.999999Z", 2.0]]
            for item in response.json()["items"]
        )

    def test_excluded_qc_held_values_leave_response_cohort_unchanged(
        self, history_api: SimpleNamespace
    ) -> None:
        from sapphire_flow.types.dhm_delivery import DELIVERY_ID

        state = history_api
        station = state.stations[0]
        coarse_time = A - timedelta(days=1)
        with state.engine.begin() as conn:
            insert(conn, station, coarse_time, 2.0)
            insert(conn, station, A, -0.0)
        before = state.client.get(PATH, headers=_auth(state.token))
        assert before.status_code == 200
        with state.engine.begin() as conn:
            insert(
                conn,
                station,
                coarse_time + timedelta(minutes=1),
                1e200,
                delivery_id=DELIVERY_ID,
            )
            insert(
                conn,
                station,
                coarse_time + timedelta(minutes=2),
                1e200,
                qc_status="qc_failed",
            )
            held = coarse_time + timedelta(minutes=3)
            insert(conn, station, held, 1e200)
            hold(
                conn,
                (station.id, station.tenant_id),
                start=held,
                end=held + timedelta(microseconds=1),
            )
        after = state.client.get(PATH, headers=_auth(state.token))
        assert after.status_code == 200, after.text
        assert before.json()["items"] == after.json()["items"]
        assert sum(block[3] for block in after.json()["items"][0]["coarse"]) == 1
        assert len(after.json()["items"][0]["native"]) == 1
