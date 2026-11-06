# ruff: noqa: F811
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from sapphire_flow.api.routes import api_swiss_observation_history as route
from sapphire_flow.api.security import require_principal
from sapphire_flow.types.enums import AccessTokenRole
from tests.unit.api.test_swiss_overview import api, case  # noqa: F401

PATH = "/api/v1/swiss/observation-history"


@pytest.fixture
def history(api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    api.app.include_router(route.router)
    api.config.swiss_observation_history = True
    monkeypatch.setattr(route, "load_swiss_roster", lambda _: api.roster)
    return api


class TestSwissObservationHistory:
    def test_default_empty_schema_and_no_store(self, history: SimpleNamespace) -> None:
        response = history.client.get(PATH)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["window_hours"] == 168
        assert body["items"][0]["native"] == []
        assert body["items"][0]["coverage"] == {"state": "unknown"}
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.parametrize(
        "query",
        [
            "window_hours=12",
            "window_hours=72&window_hours=168",
            "station_id=x",
            "window_hours=072",
        ],
    )
    def test_strict_queries(self, history: SimpleNamespace, query: str) -> None:
        response = history.client.get(PATH + "?" + query)
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], list)
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_precedes_query(self, history: SimpleNamespace, status: int) -> None:
        if status == 403:
            history.principal = replace(
                history.principal, role=AccessTokenRole.CONSUMER
            )
        else:

            def deny() -> None:
                raise HTTPException(401, "unauthorized")

            history.app.dependency_overrides[require_principal] = deny
        response = history.client.get(PATH + "?window_hours=bad&tenant=x")
        assert response.status_code == status
        assert response.headers["cache-control"] == "no-store"

    def test_disabled_independent_of_overview(self, history: SimpleNamespace) -> None:
        history.config.swiss_observation_history = False
        response = history.client.get(PATH)
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"

    def test_method_failure_no_store(self, history: SimpleNamespace) -> None:
        response = history.client.post(PATH)
        assert response.status_code == 405
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.parametrize(
        "method,origin,status",
        [
            ("GET", "https://review.example", 200),
            ("POST", "https://review.example", 400),
            ("GET", "https://evil.example", 400),
        ],
    )
    def test_preflight(
        self, history: SimpleNamespace, method: str, origin: str, status: int
    ) -> None:
        response = history.client.options(
            PATH, headers={"Origin": origin, "Access-Control-Request-Method": method}
        )
        assert response.status_code == status
        assert response.headers["cache-control"] == "no-store"

    def test_unexpected_error_is_sanitized_no_store(
        self, history: SimpleNamespace
    ) -> None:
        def broken(request: object) -> None:
            raise RuntimeError("private diagnostic")

        history.obs.fetch_observation_history = broken
        response = history.client.get(PATH)
        assert response.status_code == 500
        assert "private diagnostic" not in response.text
        assert response.headers["cache-control"] == "no-store"

    def test_byte_overflow_fails_whole_response(
        self, history: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(route, "HISTORY_BYTE_CAP", 1)
        response = history.client.get(PATH)
        assert response.status_code == 503
        assert response.json() == {
            "error": "Swiss observation history is unavailable",
            "detail": None,
        }

    def test_foreign_scope_empty_and_admin_roster_only(
        self, history: SimpleNamespace
    ) -> None:
        from uuid import UUID

        from sapphire_flow.types.ids import TenantId

        history.principal = replace(
            history.principal, tenant_id=TenantId(UUID(int=999))
        )
        assert history.client.get(PATH).json()["items"] == []
        history.principal = replace(history.principal, role=AccessTokenRole.ADMIN)
        assert history.client.get(PATH).json()["station_count"] == 1

    def test_response_validates_against_published_contract(
        self, history: SimpleNamespace
    ) -> None:
        import jsonschema

        from sapphire_flow.api.map_contract import build_map_openapi

        doc = build_map_openapi()
        schema = doc["paths"][PATH]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        response = history.client.get(PATH + "?window_hours=72")
        assert response.status_code == 200
        jsonschema.validate(
            response.json(), {**schema, "components": doc["components"]}
        )

    @pytest.mark.parametrize(
        "field", ["window_start", "native_start", "expires_at", "generated_at"]
    )
    def test_inconsistent_time_metadata_refused(
        self, history: SimpleNamespace, field: str
    ) -> None:
        from pydantic import ValidationError

        from sapphire_flow.api.swiss_observation_history_schemas import (
            SwissObservationHistoryResponse,
        )

        body = history.client.get(PATH).json()
        body[field] = "2020-01-01T00:00:00Z"
        with pytest.raises(ValidationError, match="inconsistent"):
            SwissObservationHistoryResponse.model_validate(body)


class TestSingletonContract:
    @pytest.mark.parametrize("statistics", [(2.0, 1.0, 3.0), (1.0, 1.0, 2.0)])
    def test_singleton_requires_equal_statistics(
        self, history: SimpleNamespace, statistics: tuple[float, float, float]
    ) -> None:
        from pydantic import ValidationError

        from sapphire_flow.api.swiss_observation_history_schemas import (
            SwissObservationHistoryResponse,
        )

        body = history.client.get(PATH).json()
        body["items"][0]["coarse"][0] = [*statistics, 1]
        with pytest.raises(ValidationError, match="singleton"):
            SwissObservationHistoryResponse.model_validate(body)

    @pytest.mark.parametrize("statistics", [(2.0, 1.0, 3.0), (1.0, 1.0, 2.0)])
    def test_domain_singleton_requires_equal_statistics(
        self, statistics: tuple[float, float, float]
    ) -> None:
        from sapphire_flow.types.observation_history import BlockSummary

        with pytest.raises(ValueError, match="singleton"):
            BlockSummary(
                mean=statistics[0],
                minimum=statistics[1],
                maximum=statistics[2],
                count=1,
            )

    def test_signed_zero_singleton_uses_numeric_equality(
        self, history: SimpleNamespace
    ) -> None:
        from sapphire_flow.api.swiss_observation_history_schemas import (
            SwissObservationHistoryResponse,
        )
        from sapphire_flow.types.observation_history import BlockSummary

        BlockSummary(mean=0.0, minimum=-0.0, maximum=-0.0, count=1)
        body = history.client.get(PATH).json()
        body["items"][0]["coarse"][0] = [0.0, -0.0, -0.0, 1]
        SwissObservationHistoryResponse.model_validate(body)


class TestHistoryWire:
    def test_route_fractional_microseconds_and_float_round_trip(
        self, history: SimpleNamespace
    ) -> None:
        import hashlib
        import json
        import os
        import random
        from datetime import UTC, datetime, timedelta
        from pathlib import Path
        from uuid import UUID

        from sapphire_flow.types.datetime import ensure_utc
        from sapphire_flow.types.enums import QcStatus
        from tests.conftest import make_observation

        history.now = ensure_utc(datetime(2026, 11, 5, 10, 20, 0, 123456, tzinfo=UTC))
        history.app.dependency_overrides[route.get_history_generation_id] = lambda: (
            UUID(int=123)
        )
        station = history.stations[0]
        rng = random.Random(42)
        history.obs.store_observations(
            [
                make_observation(
                    rng=rng,
                    station_id=station.id,
                    timestamp=ensure_utc(history.now - timedelta(hours=72)),
                    value=-0.0,
                    qc_status=QcStatus.QC_PASSED,
                ),
                make_observation(
                    rng=rng,
                    station_id=station.id,
                    timestamp=ensure_utc(history.now - timedelta(microseconds=1)),
                    value=-0.0,
                    qc_status=QcStatus.QC_PASSED,
                ),
                make_observation(
                    rng=rng,
                    station_id=station.id,
                    timestamp=ensure_utc(history.now - timedelta(hours=48)),
                    value=1.0000000000000002,
                    qc_status=QcStatus.QC_PASSED,
                ),
                make_observation(
                    rng=rng,
                    station_id=station.id,
                    timestamp=ensure_utc(history.now - timedelta(seconds=1)),
                    value=0.12345678901234568,
                    qc_status=QcStatus.QC_PASSED,
                ),
                make_observation(
                    rng=rng,
                    station_id=station.id,
                    timestamp=history.now,
                    value=2.0,
                    qc_status=QcStatus.QC_PASSED,
                ),
            ]
        )
        response = history.client.get(PATH + "?window_hours=72")
        assert response.status_code == 200, response.text
        body = json.loads(response.content)
        assert body["as_of"] == "2026-11-05T10:20:00.123456Z"
        assert body["items"][0]["coarse"][0] == [0.0, -0.0, -0.0, 1]
        native = body["items"][0]["native"]
        assert native == [
            ["2026-11-05T10:19:59.123456Z", 0.12345678901234568],
            ["2026-11-05T10:20:00.123455Z", -0.0],
            [body["as_of"], 2.0],
        ]
        assert native[0][1].hex() == (0.12345678901234568).hex()
        assert native[1][1].hex() == (-0.0).hex()
        assert body["items"][0]["coarse"][8] == [1.0000000000000002] * 3 + [1]
        assert b"2.0" in response.content and b"-0.0" in response.content
        golden = (
            Path(__file__).parents[2] / "fixtures/swiss_observation_history_wire.json"
        )
        wire_fixture = response.content + b"\n"
        assert wire_fixture == golden.read_bytes()
        if destination := os.environ.get("SAPPHIRE_HISTORY_WIRE_FIXTURE"):
            path = Path(destination)
            path.write_bytes(wire_fixture)
            path.with_suffix(".sha256").write_text(
                hashlib.sha256(wire_fixture).hexdigest() + "  " + path.name + "\n"
            )
