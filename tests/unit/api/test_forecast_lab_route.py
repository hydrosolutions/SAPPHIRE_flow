"""Plan 198 T5 — `GET /api/v1/forecast-lab/snapshot`.

Locks: AC2 (both MVP stations in one request), AC3/D8 (scoping: an
explicit out-of-scope/unknown code 404s; a stationless non-admin with no
code gets `200` + empty `stations`), AC25 (a mixed valid/invalid
`station_code` list 404s the WHOLE request, not a partial 200), AC26/D8
(an admin token requesting an unknown code still 404s — the existence
check runs independently of `is_admin`), AC19/D17a (a `lake` station is
excluded, indistinguishable from a typo).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from sapphire_flow.api import app
from sapphire_flow.api.routes.forecast_lab import get_bafu_forecast_archive_path
from sapphire_flow.api.security import Principal, require_principal
from sapphire_flow.types.enums import AccessTokenRole, StationKind
from sapphire_flow.types.ids import AccessTokenId, StationId
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


CONSUMER_PRINCIPAL_TOKEN_ID = AccessTokenId(
    UUID("00000000-0000-0000-0000-0000000000c1")
)


def _consumer_principal(station_ids: frozenset[StationId]) -> Principal:
    return Principal(
        token_id=CONSUMER_PRINCIPAL_TOKEN_ID,
        role=AccessTokenRole.CONSUMER,
        tenant_id=None,
        station_ids=station_ids,
    )


@pytest.fixture(autouse=True)
def _no_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    app.dependency_overrides[get_bafu_forecast_archive_path] = lambda: None
    yield
    app.dependency_overrides.pop(get_bafu_forecast_archive_path, None)


def _set_principal(principal: Principal) -> None:
    app.dependency_overrides[require_principal] = lambda: principal


class TestBothStationsInOneRequest:
    def test_two_eligible_stations_returned_together(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        s1 = make_station_config(code="2009", station_id=StationId(UUID(int=1)))
        s2 = make_station_config(code="2091", station_id=StationId(UUID(int=2)))
        fake_stores["station_store"].store_station(s1)
        fake_stores["station_store"].store_station(s2)

        resp = client.get("/api/v1/forecast-lab/snapshot")

        assert resp.status_code == 200
        body = resp.json()
        assert [s["station"]["code"] for s in body["stations"]] == ["2009", "2091"]


class TestScoping:
    def test_out_of_scope_explicit_code_is_404(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        station = make_station_config(code="2009", station_id=StationId(UUID(int=1)))
        fake_stores["station_store"].store_station(station)
        _set_principal(_consumer_principal(frozenset()))

        resp = client.get("/api/v1/forecast-lab/snapshot?station_code=2009")

        assert resp.status_code == 404

    def test_unknown_code_is_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/forecast-lab/snapshot?station_code=does-not-exist")
        assert resp.status_code == 404

    def test_stationless_non_admin_gets_200_with_empty_stations(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        station = make_station_config(code="2009", station_id=StationId(UUID(int=1)))
        fake_stores["station_store"].store_station(station)
        _set_principal(_consumer_principal(frozenset()))

        resp = client.get("/api/v1/forecast-lab/snapshot")

        assert resp.status_code == 200
        body = resp.json()
        assert body["stations"] == []
        assert body["status"]["overall"] == "unavailable"

    def test_lake_station_is_excluded_like_a_typo(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        lake = make_station_config(
            code="3001",
            station_kind=StationKind.LAKE,
            station_id=StationId(UUID(int=3)),
        )
        fake_stores["station_store"].store_station(lake)

        resp = client.get("/api/v1/forecast-lab/snapshot?station_code=3001")

        assert resp.status_code == 404


class TestMixedValidInvalidCodes404sWholeRequest:
    def test_one_bad_code_among_several_fails_the_whole_request(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        good = make_station_config(code="2009", station_id=StationId(UUID(int=1)))
        fake_stores["station_store"].store_station(good)

        resp = client.get(
            "/api/v1/forecast-lab/snapshot?station_code=2009&station_code=does-not-exist"
        )

        assert resp.status_code == 404


class TestAdminUnknownCodeStill404s:
    """AC26/D8 — the existence check runs regardless of `is_admin`; an
    admin token requesting a typo'd code must not silently pass scope and
    reach assembly with no station."""

    def test_admin_requesting_unknown_code_gets_404_not_a_crash(
        self, client: TestClient
    ) -> None:
        resp = client.get("/api/v1/forecast-lab/snapshot?station_code=does-not-exist")
        assert resp.status_code == 404
