from __future__ import annotations

import random
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from sapphire_flow.api.provisional_forecasts import (
    ProvisionalForecastReader,
)
from sapphire_flow.api.security import Principal
from sapphire_flow.exceptions import StoreError
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.types.enums import AccessTokenRole, ForecastDataUse, ForecastStatus
from sapphire_flow.types.forecast_lineage import ForecastInputLineage
from sapphire_flow.types.ids import (
    AccessTokenId,
    ArtifactId,
    ForecastId,
    StationId,
    TenantId,
)
from sapphire_flow.types.rating_input_lineage import (
    RatingDaySupport,
    RatingInputLineage,
)
from tests.unit.api.test_api_forecasts import _make_operational_forecast


class Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def mappings(self) -> Result:
        return self

    def tuples(self) -> Result:
        return self

    def one_or_none(self) -> Any:
        return self.value

    def scalar_one(self) -> Any:
        return self.value

    def scalars(self) -> Any:
        return self.value

    def all(self) -> Any:
        return self.value


class Connection:
    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.calls: list[Any] = []
        self.engine = SimpleNamespace(begin=nullcontext)

    def execute(self, query: Any) -> Result:
        self.calls.append(query)
        value = self.results.pop(0)
        if isinstance(value, Exception):
            raise value
        return Result(value)


def case() -> tuple[Any, dict, Principal]:
    sid = StationId(uuid4())
    artifact = ArtifactId(uuid4())
    forecast = _make_operational_forecast(
        station_id=sid, model_artifact_id=artifact, rng=random.Random(4)
    )
    day = forecast.issued_at - timedelta(days=1)
    rating = RatingInputLineage(
        issue_time=forecast.issued_at,
        weather_cycle_time=day,
        artifact_id=artifact,
        days=(
            RatingDaySupport(
                day=day,
                fingerprints=("a" * 64,),
                accepted=1,
                rejected=0,
                extrapolated=0,
                assessed=0,
                unassessed=1,
                first=day,
                last=day,
                warnings=("stored", "stored"),
            ),
        ),
    )
    forecast = replace(
        forecast,
        data_use=ForecastDataUse.PROVISIONAL_RATING,
        input_lineage=ForecastInputLineage(
            transformation_versions=("test",), rating_input=rating
        ),
    )
    row = dict(
        id=forecast.id,
        station_id=sid,
        model_id=forecast.model_id,
        model_artifact_id=artifact,
        issued_at=forecast.issued_at,
        parameter=forecast.ensemble.parameter,
        representation=forecast.representation.value,
        status=forecast.status.value,
        qc_status=forecast.qc_status.value,
        nwp_cycle_source=forecast.nwp_cycle_source.value,
        created_at=forecast.created_at,
        input_quality=None,
        input_quality_flags=None,
        qc_flags=[],
        data_use="provisional_rating",
        rating_curve_id=None,
    )
    principal = Principal(
        token_id=AccessTokenId(uuid4()),
        role=AccessTokenRole.REVIEWER,
        tenant_id=TenantId(uuid4()),
        station_ids=frozenset({sid}),
    )
    return forecast, row, principal


class TestProvisionalForecastReader:
    def test_header_lookup_precedes_detail_and_uses_authorized_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        forecast, row, principal = case()
        conn = Connection(row)

        def fetch(store: object, identity: ForecastId) -> Any:
            assert len(conn.calls) == 1
            assert identity == row["id"]
            assert "native_rating_lineage" not in str(conn.calls[0])
            assert "input_lineage" not in str(conn.calls[0])
            return replace(forecast, status=ForecastStatus.SUPERSEDED, version=2)

        monkeypatch.setattr(PgForecastStore, "fetch_forecast", fetch)
        actual, metadata = ProvisionalForecastReader(conn, principal).detail(
            forecast.id
        )
        assert actual.status is ForecastStatus.SUPERSEDED
        assert actual.version == 2
        assert metadata["warnings"] == ["stored"]

    @pytest.mark.parametrize(
        "field",
        ["id", "station_id", "model_id", "issued_at", "model_artifact_id", "data_use"],
    )
    def test_poisoned_detail_refuses_without_values(
        self, monkeypatch: pytest.MonkeyPatch, field: str
    ) -> None:
        forecast, row, principal = case()
        changes = {
            "id": uuid4(),
            "station_id": uuid4(),
            "model_id": "different",
            "issued_at": forecast.issued_at + timedelta(days=1),
            "model_artifact_id": uuid4(),
            "data_use": ForecastDataUse.STANDARD,
        }
        # A hostile store return need not be a valid domain object.
        poisoned = SimpleNamespace(
            **{
                name: getattr(forecast, name)
                for name in (
                    "id",
                    "station_id",
                    "model_id",
                    "issued_at",
                    "model_artifact_id",
                    "data_use",
                    "input_lineage",
                )
            }
        )
        setattr(poisoned, field, changes[field])
        monkeypatch.setattr(PgForecastStore, "fetch_forecast", lambda *args: poisoned)
        with pytest.raises(HTTPException, match="503") as caught:
            ProvisionalForecastReader(Connection(row), principal).detail(forecast.id)
        assert caught.value.status_code == 503

    def test_missing_header_never_fetches_lineage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        forecast, _, principal = case()

        def forbidden(*args: object) -> None:
            pytest.fail("unauthorized protected read")

        monkeypatch.setattr(PgForecastStore, "fetch_forecast", forbidden)
        with pytest.raises(HTTPException, match="404"):
            ProvisionalForecastReader(Connection(None), principal).detail(forecast.id)

    @pytest.mark.parametrize(
        "error, status",
        [
            (StoreError("secret"), 503),
            (ValueError("secret"), 503),
            (RuntimeError("programming"), None),
        ],
    )
    def test_evidence_errors_are_narrowly_translated(
        self, monkeypatch: pytest.MonkeyPatch, error: Exception, status: int | None
    ) -> None:
        forecast, row, principal = case()

        def broken(*args: object) -> None:
            raise error

        monkeypatch.setattr(PgForecastStore, "fetch_forecast", broken)
        with pytest.raises(HTTPException if status else RuntimeError) as caught:
            ProvisionalForecastReader(Connection(row), principal).detail(forecast.id)
        if status:
            assert caught.value.status_code == status
            assert "secret" not in str(caught.value)

    @pytest.mark.parametrize("where", ["header", "lineage"])
    def test_null_artifact_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, where: str
    ) -> None:
        forecast, row, principal = case()
        if where == "header":
            row["model_artifact_id"] = None
        else:
            forecast = replace(
                forecast,
                input_lineage=replace(
                    forecast.input_lineage,
                    rating_input=replace(
                        forecast.input_lineage.rating_input, artifact_id=None
                    ),
                ),
            )
        monkeypatch.setattr(PgForecastStore, "fetch_forecast", lambda *args: forecast)
        with pytest.raises(HTTPException, match="503"):
            ProvisionalForecastReader(Connection(row), principal).detail(forecast.id)

    def test_page_binds_materialized_ids_and_rejected_false(self) -> None:
        forecast, row, principal = case()
        conn = Connection(1, [row], [(forecast.id, forecast.input_lineage.content)])
        result, total = ProvisionalForecastReader(conn, principal).summaries(
            forecast.station_id,
            forecast.issued_at,
            forecast.issued_at + timedelta(days=1),
            model_id=None,
            parameter=None,
            degraded_only=False,
            limit=200,
            offset=0,
        )
        assert total == len(result) == 1
        params = conn.calls[-1].compile().params
        assert [forecast.id] in params.values()
        assert False in params.values()
        assert len(conn.calls) == 3
        assert all("forecast_values" not in str(query) for query in conn.calls)

    @pytest.mark.parametrize("content", [None, "{}", "not-json"])
    def test_missing_or_corrupt_page_lineage_is_not_partial_success(
        self, content: str | None
    ) -> None:
        forecast, row, principal = case()
        conn = Connection(1, [row], [(forecast.id, content)])
        with pytest.raises(HTTPException, match="503"):
            ProvisionalForecastReader(conn, principal).summaries(
                forecast.station_id,
                forecast.issued_at,
                forecast.issued_at + timedelta(days=1),
                model_id=None,
                parameter=None,
                degraded_only=False,
                limit=200,
                offset=0,
            )

    @pytest.mark.parametrize("size", [0, 1, 200])
    def test_availability_one_scoped_batch(self, size: int) -> None:
        _, _, principal = case()
        ids = [StationId(uuid4()) for _ in range(size)]
        conn = Connection(ids[:1])
        answer = ProvisionalForecastReader(conn, principal).availability(ids)
        assert len(answer) == size
        assert len(conn.calls) == (1 if size else 0)
        if size:
            query = conn.calls[0]
            assert "native_rating_lineage" not in str(query)
            assert "tenant_id" in str(query)
            assert ids in query.compile().params.values()
            assert sum(answer.values()) == 1

    def test_consumer_marker_is_null_without_query_and_values_forbidden(self) -> None:
        forecast, _, principal = case()
        principal = replace(principal, role=AccessTokenRole.CONSUMER)
        conn = Connection()
        reader = ProvisionalForecastReader(conn, principal)
        assert reader.availability([forecast.station_id]) == {forecast.station_id: None}
        with pytest.raises(HTTPException, match="403"):
            reader.detail(forecast.id)
        assert conn.calls == []

    def test_availability_database_failure_is_not_false(self) -> None:
        forecast, _, principal = case()
        conn = Connection(OperationalError("private", {}, Exception("private")))
        with pytest.raises(HTTPException, match="503"):
            ProvisionalForecastReader(conn, principal).availability(
                [forecast.station_id]
            )


class TestProvisionalContract:
    def test_generated_schema_keeps_required_nullable_and_provisional_fields(
        self,
    ) -> None:
        from sapphire_flow.api.map_contract import build_map_openapi

        document = build_map_openapi()
        schemas = document["components"]["schemas"]
        for name in ("StationSummary", "StationDetail"):
            schema = schemas[name]
            assert "has_provisional_forecast" in schema["required"]
            assert {
                x["type"]
                for x in schema["properties"]["has_provisional_forecast"]["anyOf"]
            } == {"boolean", "null"}
        for name in ("ProvisionalForecastSummary", "ProvisionalForecastDetail"):
            schema = schemas[name]
            for field in (
                "data_use",
                "badge",
                "warnings",
                "rating_curves",
                "rating_provenance_status",
                "publication_state",
            ):
                assert field in schema["required"]
            assert schema["properties"]["publication_state"]["type"] == "null"
            assert schema["properties"]["rating_curves"]["maxItems"] == 0
        for item in document["paths"].values():
            for operation in item.values():
                for security in operation.get("security", []):
                    assert set(security) <= set(
                        document["components"]["securitySchemes"]
                    )

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/stations/not-uuid",
            "/api/v1/forecasts/not-uuid",
            "/api/v1/stations/not-uuid/forecasts",
        ],
    )
    def test_actual_malformed_identifier_retains_500_with_no_store(
        self, client: Any, path: str
    ) -> None:
        from fastapi.testclient import TestClient

        from sapphire_flow.api import app

        probe = TestClient(app, raise_server_exceptions=False)
        try:
            response = probe.get(path)
            assert response.status_code == 500
            assert response.headers["cache-control"] == "no-store"
            assert response.json() == {"error": "internal_server_error", "detail": None}
        finally:
            probe.close()


class TestProvisionalBoundaryRepairs:
    @pytest.mark.parametrize("error_type", [TypeError, KeyError])
    def test_programming_bug_is_actual_http_500(
        self, client: Any, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
    ) -> None:
        from fastapi.testclient import TestClient

        from sapphire_flow.api import app
        from sapphire_flow.api.provisional_forecasts import get_provisional_reader

        forecast, row, principal = case()
        app.dependency_overrides[get_provisional_reader] = lambda: (
            ProvisionalForecastReader(Connection(row), principal)
        )

        def broken(*args: object) -> None:
            raise error_type("private canary")

        monkeypatch.setattr(PgForecastStore, "fetch_forecast", broken)
        probe = TestClient(app, raise_server_exceptions=False)
        try:
            response = probe.get(
                f"/api/v1/forecasts/{forecast.id}?data_use=provisional_rating"
            )
        finally:
            probe.close()
        assert response.status_code == 500
        assert response.headers["cache-control"] == "no-store"
        assert "private canary" not in response.text

    @pytest.mark.parametrize(
        "sqlstate, expected",
        [("42501", "42501"), ("secret arbitrary text", None), (12345, None)],
    )
    def test_database_diagnostic_is_fixed_and_sanitized(
        self, sqlstate: object, expected: str | None
    ) -> None:
        from sqlalchemy.exc import ProgrammingError
        from structlog.testing import capture_logs

        forecast, _, principal = case()
        original = Exception("private original canary")
        original.sqlstate = sqlstate
        error = ProgrammingError(
            "private SQL canary", {"private": "parameters"}, original
        )
        with capture_logs() as logs, pytest.raises(HTTPException, match="503"):
            ProvisionalForecastReader(Connection(error), principal).availability(
                [forecast.station_id]
            )
        assert logs == [
            {
                "event": "provisional_read_unavailable",
                "operation": "availability",
                "failure_category": "database",
                "error_class": "ProgrammingError",
                "sqlstate": expected,
                "log_level": "error",
            }
        ]
        assert "private" not in str(logs)

    @pytest.mark.parametrize("fault", ["issue", "artifact", "header_curve"])
    def test_valid_but_wrong_lineage_or_header_refused(
        self, monkeypatch: pytest.MonkeyPatch, fault: str
    ) -> None:
        forecast, row, principal = case()
        if fault == "header_curve":
            row["rating_curve_id"] = uuid4()
        else:
            changes = (
                {"issue_time": forecast.issued_at + timedelta(hours=1)}
                if fault == "issue"
                else {"artifact_id": ArtifactId(uuid4())}
            )
            forecast = replace(
                forecast,
                input_lineage=replace(
                    forecast.input_lineage,
                    rating_input=replace(
                        forecast.input_lineage.rating_input, **changes
                    ),
                ),
            )
        monkeypatch.setattr(PgForecastStore, "fetch_forecast", lambda *args: forecast)
        with pytest.raises(HTTPException, match="503"):
            ProvisionalForecastReader(Connection(row), principal).detail(forecast.id)

    @pytest.mark.parametrize("fault", ["missing", "unexpected", "mixed_wrong_artifact"])
    def test_entire_page_refused_for_identity_or_one_bad_lineage(
        self, fault: str
    ) -> None:
        forecast, row, principal = case()
        second_id = ForecastId(uuid4())
        rows = [row, dict(row, id=second_id)]
        contents = [
            (forecast.id, forecast.input_lineage.content),
            (second_id, forecast.input_lineage.content),
        ]
        if fault == "missing":
            contents.pop()
        elif fault == "unexpected":
            contents.append((ForecastId(uuid4()), forecast.input_lineage.content))
        else:
            wrong = replace(
                forecast.input_lineage,
                rating_input=replace(
                    forecast.input_lineage.rating_input, artifact_id=ArtifactId(uuid4())
                ),
            )
            contents[1] = (second_id, wrong.content)
        reader = ProvisionalForecastReader(Connection(2, rows, contents), principal)
        with pytest.raises(HTTPException, match="503"):
            reader.summaries(
                forecast.station_id,
                forecast.issued_at,
                forecast.issued_at + timedelta(days=1),
                model_id=None,
                parameter=None,
                degraded_only=False,
                limit=2,
                offset=0,
            )


class TestOriginalForecastCallShape:
    def test_positional_standard_dependencies_still_work(self) -> None:
        from sapphire_flow.api.publication_gate import PublicationGate
        from sapphire_flow.api.routes.api_forecasts import get_forecast
        from tests.fakes.fake_stores import FakeForecastStore

        forecast = _make_operational_forecast(
            station_id=StationId(uuid4()), rng=random.Random(7)
        )
        store = FakeForecastStore()
        store.store_forecast(forecast)
        principal = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        response = get_forecast(
            str(forecast.id), {"forecast_store": store}, principal, PublicationGate()
        )
        assert response.id == str(forecast.id)
        assert response.weather_use is None
