from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from sapphire_flow.api import app
from sapphire_flow.api.deps import get_observation_clock
from sapphire_flow.api.security import Principal, require_principal
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.dhm_delivery import DELIVERY_ID
from sapphire_flow.types.enums import AccessTokenRole, QcStatus
from sapphire_flow.types.ids import AccessTokenId
from tests.conftest import make_observation, make_station_config

_NOW = ensure_utc(datetime(2026, 10, 30, tzinfo=UTC))
_PATH = "/api/v1/observations/latest"


@pytest.fixture(autouse=True)
def fixed_clock() -> None:
    app.dependency_overrides[get_observation_clock] = lambda: _NOW


class TestListLatestObservations:
    def test_null_and_missing_items_match_maintained_contract(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        import json
        from pathlib import Path

        import jsonschema

        stations = [
            make_station_config(code=f"S-{i}", rng=random.Random(i)) for i in range(2)
        ]
        for station in stations:
            fake_stores["station_store"].store_station(station)
        fake_stores["obs_store"].store_observations(
            [
                make_observation(
                    station_id=stations[0].id,
                    timestamp=_NOW,
                    qc_status=QcStatus.MISSING,
                )
            ]
        )
        response = client.get(_PATH, params={"parameter": "discharge"})
        assert response.status_code == 200
        items = {item["station_id"]: item for item in response.json()["items"]}
        assert items[str(stations[0].id)]["observation"]["value"] is None
        assert items[str(stations[0].id)]["observation"]["qc_status"] == "missing"
        assert items[str(stations[1].id)]["observation"] is None
        doc = json.loads(
            (
                Path(__file__).resolve().parents[3]
                / "docs/spec/api-v1-map.openapi.json"
            ).read_text()
        )
        schema = doc["paths"][_PATH]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        jsonschema.validate(
            response.json(),
            {**schema, "components": doc["components"]},
            cls=jsonschema.Draft202012Validator,
        )

    def test_requested_non_default_source_is_not_replaced_by_measured(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        from sapphire_flow.types.enums import ObservationSource

        station = make_station_config()
        fake_stores["station_store"].store_station(station)
        measured = make_observation(
            station_id=station.id, timestamp=_NOW, rng=random.Random(51)
        )
        manual = replace(
            make_observation(
                station_id=station.id,
                timestamp=ensure_utc(_NOW - timedelta(hours=1)),
                rng=random.Random(52),
            ),
            source=ObservationSource.MANUAL_IMPORT,
        )
        fake_stores["obs_store"].store_observations([measured, manual])
        response = client.get(
            _PATH, params={"parameter": "discharge", "source": "manual_import"}
        )
        assert response.status_code == 200
        assert response.json()["items"][0]["observation"]["id"] == str(manual.id)

    @pytest.mark.parametrize(
        "role", [AccessTokenRole.CONSUMER, AccessTokenRole.REVIEWER]
    )
    def test_no_grants_does_not_expose_populated_inventory(
        self, client: TestClient, fake_stores: dict[str, Any], role: AccessTokenRole
    ) -> None:
        station = make_station_config()
        fake_stores["station_store"].store_station(station)
        fake_stores["obs_store"].store_observations(
            [make_observation(station_id=station.id, timestamp=_NOW)]
        )
        app.dependency_overrides[require_principal] = lambda: Principal(
            token_id=AccessTokenId(UUID(int=1)),
            role=role,
            tenant_id=None,
            station_ids=frozenset(),
        )
        response = client.get(_PATH, params={"parameter": "discharge"})
        assert response.status_code == 200
        assert response.json()["total"] == 0
        assert response.json()["items"] == []

    def test_empty_scope_is_empty_and_not_cached(self, client: TestClient) -> None:
        response = client.get(_PATH, params={"parameter": "discharge"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["items"] == []
        assert response.json()["total"] == 0

    @pytest.mark.parametrize(
        "role", [AccessTokenRole.CONSUMER, AccessTokenRole.REVIEWER]
    )
    def test_scope_filters_before_count_and_page(
        self, client: TestClient, fake_stores: dict[str, Any], role: AccessTokenRole
    ) -> None:
        stations = [
            make_station_config(code=f"S-{i}", rng=random.Random(i)) for i in range(3)
        ]
        for station in reversed(stations):
            fake_stores["station_store"].store_station(station)
        allowed = sorted(stations[:2], key=lambda s: str(s.id))
        app.dependency_overrides[require_principal] = lambda: Principal(
            token_id=AccessTokenId(UUID(int=1)),
            role=role,
            tenant_id=None,
            station_ids=frozenset(s.id for s in allowed),
        )
        response = client.get(
            _PATH, params={"parameter": "discharge", "limit": 1, "offset": 1}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert [item["station_id"] for item in body["items"]] == [str(allowed[1].id)]
        assert body["items"][0]["observation"] is None

    @pytest.mark.parametrize("qc", list(QcStatus))
    def test_latest_qc_is_not_replaced_by_older_passed(
        self, client: TestClient, fake_stores: dict[str, Any], qc: QcStatus
    ) -> None:
        station = make_station_config()
        fake_stores["station_store"].store_station(station)
        older = make_observation(
            station_id=station.id,
            timestamp=ensure_utc(_NOW - timedelta(hours=1)),
            rng=random.Random(10),
        )
        latest = make_observation(
            station_id=station.id, timestamp=_NOW, qc_status=qc, rng=random.Random(11)
        )
        future = make_observation(
            station_id=station.id,
            timestamp=ensure_utc(_NOW + timedelta(seconds=1)),
            rng=random.Random(12),
        )
        fake_stores["obs_store"].store_observations([older, latest, future])
        body = client.get(_PATH, params={"parameter": "discharge"}).json()
        assert body["items"][0]["observation"]["id"] == str(latest.id)
        assert body["items"][0]["observation"]["qc_status"] == qc.value
        assert body["generated_at"] == "2026-10-30T00:00:00Z"
        assert body["window_start"] == "2026-10-23T00:00:00Z"

    @pytest.mark.parametrize("role", list(AccessTokenRole))
    def test_delivery_exclusion_precedes_latest_and_keeps_null_delivery(
        self, client: TestClient, fake_stores: dict[str, Any], role: AccessTokenRole
    ) -> None:
        station = make_station_config()
        fake_stores["station_store"].store_station(station)
        older = make_observation(
            station_id=station.id,
            timestamp=ensure_utc(_NOW - timedelta(hours=1)),
            rng=random.Random(20),
        )
        hidden = replace(
            make_observation(
                station_id=station.id, timestamp=_NOW, rng=random.Random(21)
            ),
            delivery_id=DELIVERY_ID,
        )
        fake_stores["obs_store"].store_observations([older, hidden])
        app.dependency_overrides[require_principal] = lambda: Principal(
            token_id=AccessTokenId(UUID(int=1)),
            role=role,
            tenant_id=None,
            station_ids=frozenset({station.id}),
        )
        response = client.get(_PATH, params={"parameter": "discharge"})
        expected = hidden if role is AccessTokenRole.ADMIN else older
        assert response.json()["items"][0]["observation"]["id"] == str(expected.id)

    def test_outside_window_and_held_values_are_not_exposed(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        station = make_station_config()
        fake_stores["station_store"].store_station(station)
        old = make_observation(
            station_id=station.id,
            timestamp=ensure_utc(_NOW - timedelta(days=8)),
            rng=random.Random(30),
        )
        held = make_observation(
            station_id=station.id, timestamp=_NOW, rng=random.Random(31)
        )
        fake_stores["obs_store"].store_observations([old, held])
        fake_stores["obs_store"].hold_unit_observation(held.id, "test-hold")
        response = client.get(_PATH, params={"parameter": "discharge"})
        assert response.json()["items"][0]["observation"] is None

    @pytest.mark.parametrize(
        "params",
        [
            {},
            {"parameter": ""},
            {"parameter": "water_level"},
            {"parameter": "discharge", "source": "invalid"},
            {"parameter": "discharge", "limit": 201},
            {"parameter": "discharge", "observation_hours": 721},
        ],
    )
    def test_validation_errors_are_not_cached(
        self, client: TestClient, params: dict[str, Any]
    ) -> None:
        response = client.get(_PATH, params=params)
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], list)
        assert response.headers["cache-control"] == "no-store"

    def test_authentication_error_is_not_cached(self, client: TestClient) -> None:
        def deny() -> Principal:
            raise HTTPException(401, "Missing access token")

        app.dependency_overrides[require_principal] = deny
        response = client.get(_PATH, params={"parameter": "discharge"})
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"

    def test_unexpected_error_is_not_cached(self, client: TestClient) -> None:
        def broken_clock() -> datetime:
            raise RuntimeError("synthetic failure")

        app.dependency_overrides[get_observation_clock] = broken_clock
        with TestClient(app, raise_server_exceptions=False) as error_client:
            response = error_client.get(_PATH, params={"parameter": "discharge"})
        assert response.status_code == 500
        assert response.json() == {"error": "internal_server_error", "detail": None}
        assert response.headers["cache-control"] == "no-store"
