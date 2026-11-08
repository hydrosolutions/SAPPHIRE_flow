from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from sapphire_flow.api import app
from sapphire_flow.api.deps import get_connection
from sapphire_flow.api.provisional_forecasts import ProvisionalForecastReader
from sapphire_flow.api.security import Principal, require_principal
from sapphire_flow.db.metadata import forecasts, stations
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.types.domain import InputQualityFlag, QcFlag
from sapphire_flow.types.enums import (
    AccessTokenRole,
    InputQualityCategory,
    InputQualityLevel,
    QcStatus,
)
from sapphire_flow.types.ids import AccessTokenId, TenantId
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.integration.store.test_native_rating_forecasts import native_pair
from tests.integration.store.test_rejected_forecast_store import savepoint_factory

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sapphire_flow.types.forecast import OperationalForecast
    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness


@pytest.fixture
def native_read_case(
    role_harness: _RoleBootstrapHarness,
    record_testsuite_property: Callable[[str, object], None],
    request: pytest.FixtureRequest,
) -> Iterator[tuple]:
    record_testsuite_property("owned_container_id", role_harness._container_id)
    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.connect() as conn:
        transaction = conn.begin()
        ordinary, native = native_pair(conn)
        native = replace(
            native,
            input_quality=InputQualityLevel.PARTIAL,
            input_quality_flags=(
                InputQualityFlag(
                    category=InputQualityCategory.OBSERVATION,
                    level=InputQualityLevel.PARTIAL,
                    detail="stored input warning",
                ),
            ),
            qc_flags=(
                QcFlag(
                    rule_id="range_check",
                    rule_version="1",
                    status=QcStatus.QC_SUSPECT,
                    detail="stored qc warning",
                ),
            ),
        )
        if getattr(request, "param", "legacy") == "selected":
            from tests.integration.store.test_selected_weather_evidence import selected

            native = selected(native)
        PgForecastStore(
            conn, transaction_factory=savepoint_factory(conn)
        ).store_forecast(ordinary)
        PgForecastStore(
            conn, data_use=native.data_use, transaction_factory=savepoint_factory(conn)
        ).store_forecast(native)
        tenant = conn.scalar(
            sa.select(stations.c.tenant_id).where(stations.c.id == native.station_id)
        )
        principal = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.REVIEWER,
            tenant_id=TenantId(tenant),
            station_ids=frozenset({native.station_id}),
        )
        previous = app.dependency_overrides.copy()
        app.dependency_overrides[get_connection] = lambda: conn
        app.dependency_overrides[require_principal] = lambda: principal
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        client = TestClient(app, raise_server_exceptions=False)
        try:
            yield conn, client, native, ordinary, principal
        finally:
            client.close()
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
            transaction.rollback()


def list_url(native: OperationalForecast) -> str:
    return (
        f"/api/v1/stations/{native.station_id}/forecasts?data_use=provisional_rating"
        f"&start={(native.issued_at - timedelta(days=1)).isoformat()}"
        f"&end={(native.issued_at + timedelta(days=1)).isoformat()}"
    ).replace("+", "%2B")


class TestProvisionalReadApiRole:
    def test_summary_detail_flags_and_explicit_stage1_contract(
        self, native_read_case: tuple
    ) -> None:
        _, client, native, ordinary, _ = native_read_case
        detail = client.get(
            f"/api/v1/forecasts/{native.id}?data_use=provisional_rating"
        )
        page = client.get(list_url(native))
        assert detail.status_code == page.status_code == 200
        assert page.json()["total"] == 1
        for body in (detail.json(), page.json()["items"][0]):
            assert body["data_use"] == "provisional_rating"
            assert body["badge"] == "PROVISIONAL"
            assert body["rating_curves"] == []
            assert body["rating_provenance_status"] == "not_exposed"
            assert body["publication_applicability"] == "not_applicable"
            assert body["publication_state"] is None
            assert "rating_expired_at_issue" in body["warnings"]
            assert body["input_quality"] == "partial"
            assert body["input_quality_flags"][0]["detail"] == "stored input warning"
            assert body["qc_flags"][0]["detail"] == "stored qc warning"
            assert "input_lineage" not in body and "rating_input" not in body
        assert client.get(f"/api/v1/forecasts/{native.id}").status_code == 404
        assert (
            client.get(
                f"/api/v1/forecasts/{ordinary.id}?data_use=provisional_rating"
            ).status_code
            == 404
        )
        assert detail.headers["cache-control"] == "no-store"

    def test_marker_false_does_not_hide_superseded_history(
        self, native_read_case: tuple
    ) -> None:
        conn, client, native, _, _ = native_read_case
        path = f"/api/v1/stations/{native.station_id}"
        assert client.get(path).json()["has_provisional_forecast"] is True
        conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
        conn.execute(
            sa.update(forecasts)
            .where(forecasts.c.id == native.id)
            .values(status="superseded")
        )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        assert client.get(path).json()["has_provisional_forecast"] is False
        assert client.get(list_url(native)).json()["items"][0]["status"] == "superseded"
        detail = client.get(
            f"/api/v1/forecasts/{native.id}?data_use=provisional_rating"
        )
        assert detail.status_code == 200 and detail.json()["status"] == "superseded"

    @pytest.mark.parametrize(
        "role", [AccessTokenRole.CONSUMER, AccessTokenRole.REVIEWER]
    )
    def test_denied_requests_do_not_call_lineage(
        self, native_read_case: tuple, role: AccessTokenRole
    ) -> None:
        conn, client, native, _, principal = native_read_case
        app.dependency_overrides[require_principal] = lambda: replace(
            principal, role=role, station_ids=frozenset()
        )
        statements: list[str] = []

        def observed(
            conn: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            statements.append(statement)

        sa.event.listen(conn, "before_cursor_execute", observed)
        try:
            response = client.get(
                f"/api/v1/forecasts/{native.id}?data_use=provisional_rating"
            )
            assert response.status_code == (
                403 if role is AccessTokenRole.CONSUMER else 404
            )
            assert not any(
                "native_rating_lineage" in sql or "forecast_values" in sql
                for sql in statements
            )
        finally:
            sa.event.remove(conn, "before_cursor_execute", observed)

    def test_consumer_null_marker_has_no_forecast_query(
        self, native_read_case: tuple
    ) -> None:
        conn, client, native, _, principal = native_read_case
        app.dependency_overrides[require_principal] = lambda: replace(
            principal, role=AccessTokenRole.CONSUMER
        )
        statements: list[str] = []

        def observed(
            conn: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            statements.append(statement)

        sa.event.listen(conn, "before_cursor_execute", observed)
        try:
            response = client.get("/api/v1/stations")
            assert response.status_code == 200
            assert response.json()["items"][0]["has_provisional_forecast"] is None
            assert not any("FROM forecasts" in sql for sql in statements)
        finally:
            sa.event.remove(conn, "before_cursor_execute", observed)

    def test_native_summary_query_count_and_no_ensemble(
        self, native_read_case: tuple
    ) -> None:
        conn, _, native, _, principal = native_read_case
        statements: list[str] = []

        def observed(
            conn: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            statements.append(statement)

        sa.event.listen(conn, "before_cursor_execute", observed)
        try:
            rows, total = ProvisionalForecastReader(conn, principal).summaries(
                native.station_id,
                native.issued_at - timedelta(days=1),
                native.issued_at + timedelta(days=1),
                model_id=None,
                parameter=None,
                degraded_only=True,
                limit=200,
                offset=0,
            )
            assert len(rows) == total == 1
            assert len(statements) == 3
            assert sum("native_rating_lineage" in sql for sql in statements) == 1
            assert not any("forecast_values" in sql for sql in statements)
        finally:
            sa.event.remove(conn, "before_cursor_execute", observed)

    @pytest.mark.parametrize("status", ["reviewed", "published"])
    def test_nonstandard_publication_status_remains_refused(
        self, native_read_case: tuple, status: str
    ) -> None:
        conn, _, native, _, _ = native_read_case
        conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
        with (
            pytest.raises(sa.exc.IntegrityError, match="ck_forecasts_test_publication"),
            conn.begin_nested(),
        ):
            conn.execute(
                sa.update(forecasts)
                .where(forecasts.c.id == native.id)
                .values(status=status)
            )

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT input_lineage FROM forecasts",
            "SELECT content FROM rating_derived_discharge",
        ],
    )
    def test_protected_tables_still_denied(
        self, native_read_case: tuple, sql: str
    ) -> None:
        conn, _, _, _, _ = native_read_case
        with (
            pytest.raises(sa.exc.ProgrammingError, match="permission denied"),
            conn.begin_nested(),
        ):
            conn.execute(sa.text(sql))


class TestProvisionalSharedRouteAcceptance:
    @pytest.mark.parametrize("native_read_case", ["selected"], indirect=True)
    def test_selected_weather_v1_actual_role(self, native_read_case: tuple) -> None:
        _, client, native, _, _ = native_read_case
        response = client.get(
            f"/api/v1/forecasts/{native.id}?data_use=provisional_rating"
        )
        assert response.status_code == 200
        weather = response.json()["weather_use"]
        assert weather["station_id"] == str(native.station_id)
        assert weather["past"]["freshness"] == "unused"
        assert weather["future"]["freshness"] == "unused"
        assert response.json()["data_use"] == "provisional_rating"

    def test_legacy_shared_route_never_fetches_weather(
        self, native_read_case: tuple, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, client, native, _, _ = native_read_case

        def forbidden(*args: object) -> None:
            pytest.fail("legacy fetched weather evidence")

        monkeypatch.setattr(PgForecastStore, "fetch_evidence", forbidden)
        response = client.get(
            f"/api/v1/forecasts/{native.id}?data_use=provisional_rating"
        )
        assert response.status_code == 200 and response.json()["weather_use"] is None

    @pytest.mark.parametrize("native_read_case", ["selected"], indirect=True)
    @pytest.mark.parametrize(
        "fault", ["missing", "hash", "version", "purpose", "station"]
    )
    def test_shared_selected_weather_refuses_bad_evidence(
        self, native_read_case: tuple, monkeypatch: pytest.MonkeyPatch, fault: str
    ) -> None:
        import json

        conn, client, native, _, _ = native_read_case
        evidence = PgForecastStore(conn, data_use=native.data_use).fetch_evidence(
            native.id
        )
        assert evidence is not None
        if fault == "missing":
            evidence = None
        elif fault == "hash":
            evidence = replace(evidence, snapshot_sha256="0" * 64)
        else:
            manifest = json.loads(evidence.manifest_json)
            key, value = {
                "version": ("weather_evidence_version", 2),
                "purpose": ("data_use", "standard"),
                "station": ("station_id", str(uuid4())),
            }[fault]
            manifest[key] = value
            evidence = replace(evidence, manifest_json=json.dumps(manifest))
        monkeypatch.setattr(PgForecastStore, "fetch_evidence", lambda *args: evidence)
        response = client.get(
            f"/api/v1/forecasts/{native.id}?data_use=provisional_rating"
        )
        assert response.status_code == 503
        assert response.json()["error"] == "Selected weather evidence is unavailable"
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.parametrize("native_read_case", ["selected"], indirect=True)
    @pytest.mark.parametrize("denial", ["consumer", "station", "tenant"])
    @pytest.mark.parametrize("endpoint", ["detail", "list"])
    def test_denied_shared_reads_never_query_protected_data(
        self,
        native_read_case: tuple,
        monkeypatch: pytest.MonkeyPatch,
        denial: str,
        endpoint: str,
    ) -> None:
        conn, client, native, _, principal = native_read_case
        if denial == "consumer":
            principal = replace(principal, role=AccessTokenRole.CONSUMER)
        elif denial == "station":
            principal = replace(principal, station_ids=frozenset())
        else:
            # Keep the real nonempty station grant. Only the tenant is wrong.
            principal = replace(principal, tenant_id=TenantId(uuid4()))
        app.dependency_overrides[require_principal] = lambda: principal

        def forbidden(*args: object) -> None:
            pytest.fail("denied request fetched selected weather")

        monkeypatch.setattr(PgForecastStore, "fetch_evidence", forbidden)
        statements: list[str] = []

        def observed(
            conn: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            statements.append(statement)

        sa.event.listen(conn, "before_cursor_execute", observed)
        try:
            path = (
                list_url(native)
                if endpoint == "list"
                else f"/api/v1/forecasts/{native.id}?data_use=provisional_rating"
            )
            response = client.get(path)
        finally:
            sa.event.remove(conn, "before_cursor_execute", observed)
        assert response.status_code == (403 if denial == "consumer" else 404)
        assert response.headers["cache-control"] == "no-store"
        assert not any(
            any(
                term in sql
                for term in (
                    "native_rating_lineage",
                    "forecast_values",
                    "forecast_evidence",
                    "count(",
                )
            )
            for sql in statements
        )
        if endpoint == "list":
            assert not any("FROM forecasts" in sql for sql in statements)

    def test_admin_reads_real_sql_with_no_station_grants(
        self, native_read_case: tuple
    ) -> None:
        _, client, native, _, principal = native_read_case
        principal = replace(
            principal,
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        app.dependency_overrides[require_principal] = lambda: principal
        assert (
            client.get(
                f"/api/v1/forecasts/{native.id}?data_use=provisional_rating"
            ).status_code
            == 200
        )
        assert client.get(list_url(native)).json()["total"] == 1
        assert (
            client.get(f"/api/v1/stations/{native.station_id}").json()[
                "has_provisional_forecast"
            ]
            is True
        )

    def test_standard_only_marker_false(self, native_read_case: tuple) -> None:
        from tests.integration.store.test_forecast_store import (
            _make_forecast,
            _seed_artifact,
            _seed_model,
            _seed_station,
        )

        conn, client, _, _, principal = native_read_case
        conn.execute(sa.text("RESET ROLE"))
        sid = _seed_station(conn)
        mid = _seed_model(conn)
        aid = _seed_artifact(conn, sid, mid)
        ordinary = _make_forecast(sid, mid, aid)
        PgForecastStore(
            conn, transaction_factory=savepoint_factory(conn)
        ).store_forecast(ordinary)
        tenant = conn.scalar(
            sa.select(stations.c.tenant_id).where(stations.c.id == sid)
        )
        principal = replace(
            principal, tenant_id=TenantId(tenant), station_ids=frozenset({sid})
        )
        app.dependency_overrides[require_principal] = lambda: principal
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        response = client.get(f"/api/v1/stations/{sid}")
        assert response.status_code == 200
        assert response.json()["has_provisional_forecast"] is False

    @pytest.mark.parametrize("endpoint", ["detail", "list"])
    def test_unsupported_purpose_is_422(
        self, native_read_case: tuple, endpoint: str
    ) -> None:
        _, client, native, _, _ = native_read_case
        path = (
            list_url(native)
            if endpoint == "list"
            else f"/api/v1/forecasts/{native.id}?data_use=provisional_rating"
        )
        response = client.get(path.replace("provisional_rating", "expired_rating_test"))
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"

    def test_station_page_batches_only_authorized_page_ids(
        self, native_read_case: tuple, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn, client, native, _, principal = native_read_case
        ids = list(
            conn.scalars(
                sa.select(stations.c.id).where(
                    stations.c.tenant_id == principal.tenant_id
                )
            )
        )
        assert len(ids) >= 3
        allowed = frozenset(ids[:2])
        principal = replace(principal, station_ids=allowed)
        app.dependency_overrides[require_principal] = lambda: principal
        captured: list[list] = []
        actual = ProvisionalForecastReader.availability

        def observed(reader: ProvisionalForecastReader, station_ids: list) -> dict:
            captured.append(list(station_ids))
            return actual(reader, station_ids)

        monkeypatch.setattr(ProvisionalForecastReader, "availability", observed)
        response = client.get("/api/v1/stations?limit=1&offset=1")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2 and len(body["items"]) == 1
        assert len(captured) == 1 and len(captured[0]) == 1
        assert str(captured[0][0]) == body["items"][0]["id"]
        assert set(captured[0]) <= allowed
        assert len(set(ids) - allowed) >= 1

    def test_multirow_forecast_page_one_lineage_query_exact_ids(
        self, native_read_case: tuple
    ) -> None:
        from sapphire_flow.types.ids import ForecastId

        conn, client, native, _, _ = native_read_case
        conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
        store = PgForecastStore(
            conn, data_use=native.data_use, transaction_factory=savepoint_factory(conn)
        )
        created = []
        for index in range(3):
            issue = native.issued_at + timedelta(minutes=index + 1)
            lineage = replace(
                native.input_lineage,
                rating_input=replace(
                    native.input_lineage.rating_input, issue_time=issue
                ),
            )
            extra = replace(
                native, id=ForecastId(uuid4()), issued_at=issue, input_lineage=lineage
            )
            store.store_forecast(extra)
            created.append(extra)
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        calls: list[tuple] = []

        def observed(
            conn: object,
            clause: object,
            multiparams: object,
            params: object,
            execution_options: object,
        ) -> None:
            if "native_rating_lineage" in str(clause):
                calls.append((str(clause), clause.compile().params))

        sa.event.listen(conn, "before_execute", observed)
        try:
            response = client.get(list_url(native) + "&limit=2&offset=1")
        finally:
            sa.event.remove(conn, "before_execute", observed)
        assert response.status_code == 200
        body = response.json()
        expected = {str(created[0].id), str(created[1].id)}
        assert body["total"] == 4
        assert {item["id"] for item in body["items"]} == expected
        assert len(calls) == 1
        id_batches = [
            value for value in calls[0][1].values() if isinstance(value, (list, tuple))
        ]
        assert any({str(value) for value in batch} == expected for batch in id_batches)
