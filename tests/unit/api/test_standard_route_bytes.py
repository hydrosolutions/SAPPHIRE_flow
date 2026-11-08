from __future__ import annotations

import json
import random
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from sapphire_flow.api import app
from sapphire_flow.api.deps import get_connection, get_stores
from sapphire_flow.api.security import require_principal
from sapphire_flow.types.ids import ForecastId, StationId
from tests.fakes.fake_stores import FakeForecastStore
from tests.unit.api.conftest import ADMIN_PRINCIPAL, _DummyConnection
from tests.unit.api.test_api_forecasts import _make_operational_forecast


def standard_route_bytes() -> dict[str, str]:
    station_id = StationId(UUID("00000000-0000-0000-0000-000000000021"))
    forecast = replace(
        _make_operational_forecast(station_id=station_id, rng=random.Random(2)),
        id=ForecastId(UUID("00000000-0000-0000-0000-000000000022")),
    )
    store = FakeForecastStore()
    store.store_forecast(forecast)
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_stores] = lambda: {"forecast_store": store}
    app.dependency_overrides[get_connection] = lambda: _DummyConnection()
    app.dependency_overrides[require_principal] = lambda: ADMIN_PRINCIPAL
    client = TestClient(app)
    try:
        paths = {
            "detail": f"/api/v1/forecasts/{forecast.id}",
            "nonempty": (
                f"/api/v1/stations/{station_id}/forecasts"
                "?start=2025-01-01&end=2025-01-02"
            ),
            "empty": (
                f"/api/v1/stations/{station_id}/forecasts"
                "?start=2024-01-01&end=2024-01-02"
            ),
        }
        result: dict[str, str] = {}
        for name, path in paths.items():
            for purpose in ("omitted", "explicit"):
                query = ("&" if "?" in path else "?") + "data_use=standard"
                response = client.get(path + (query if purpose == "explicit" else ""))
                assert response.status_code == 200
                result[f"{name}_{purpose}"] = response.content.decode()
        return result
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


class TestStandardRouteByteCompatibility:
    @pytest.mark.parametrize("name", ["detail", "nonempty", "empty"])
    def test_actual_standard_routes_match_prechange_bytes(self, name: str) -> None:
        baseline = json.loads(
            (
                Path(__file__).parent / "fixtures" / "standard_forecast_routes.json"
            ).read_text()
        )
        actual = standard_route_bytes()
        for purpose in ("omitted", "explicit"):
            key = f"{name}_{purpose}"
            assert actual[key] == baseline[key]
