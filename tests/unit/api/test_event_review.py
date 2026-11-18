from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from sapphire_flow.api import app
from sapphire_flow.api.publication_gate import PublicationGate, get_publication_gate
from sapphire_flow.api.routes.api_event_review import get_event_review_store
from sapphire_flow.api.security import Principal, require_principal
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AccessTokenRole, ForcingType
from sapphire_flow.types.event_review import (
    ReviewCatalogLimitError,
    ReviewCompleteness,
    ReviewKind,
    ReviewOptionsQuery,
    ReviewPolicy,
    ReviewQueryTimeoutError,
    ReviewSource,
)
from sapphire_flow.types.ids import AccessTokenId, ArtifactId, ModelId, TenantId
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from typing import Any


class Catalog:
    def __init__(self, sources: tuple[ReviewSource, ...] = ()) -> None:
        self.sources = sources
        self.queries: list[ReviewOptionsQuery] = []
        self.failure: Exception | None = None

    def fetch_options(self, query: ReviewOptionsQuery) -> tuple[ReviewSource, ...]:
        self.queries.append(query)
        if self.failure:
            raise self.failure
        return self.sources


def source() -> ReviewSource:
    return ReviewSource(
        kind=ReviewKind.HINDCAST,
        model_id=ModelId("climatology_fallback"),
        model_name="Climatology",
        artifact_id=ArtifactId(uuid4()),
        artifact_sha256="a" * 64,
        training_period_start=ensure_utc(datetime(2010, 1, 1, tzinfo=UTC)),
        training_period_end=ensure_utc(datetime(2015, 1, 1, tzinfo=UTC)),
        hindcast_run_id=uuid4(),
        forcing_type=ForcingType.REANALYSIS,
        time_step_seconds=86400,
        units="m³/s",
        lead_hours=(0, 48),
        first_valid_time=ensure_utc(datetime(2019, 6, 1, tzinfo=UTC)),
        last_valid_time=ensure_utc(datetime(2019, 6, 30, tzinfo=UTC)),
        completeness=ReviewCompleteness.UNKNOWN,
        reasons=("expected_coverage_unknown",),
    )


@pytest.fixture
def catalog() -> Catalog:
    value = Catalog()
    app.dependency_overrides[get_event_review_store] = lambda: value
    return value


def seed(
    stores: dict[str, Any], role: AccessTokenRole = AccessTokenRole.REVIEWER
) -> Any:
    station = make_station_config()
    stores["station_store"].store_station(station)
    app.dependency_overrides[require_principal] = lambda: Principal(
        token_id=AccessTokenId(uuid4()),
        role=role,
        tenant_id=station.tenant_id,
        station_ids=frozenset({station.id}),
    )
    return station


class TestReviewOptions:
    def test_historical_source_choices_and_baseline_label(
        self, client: TestClient, fake_stores: dict[str, Any], catalog: Catalog
    ) -> None:
        station = seed(fake_stores)
        catalog.sources = (source(),)
        response = client.get(f"/api/v1/stations/{station.id}/review-options")
        assert response.status_code == 200
        body = response.json()
        item = body["sources"][0]
        assert item["lead_hours"] == [0, 48]
        assert item["model_role"] == "baseline"
        assert item["artifact_id"] == str(catalog.sources[0].artifact_id)
        assert item["completeness"] == "unknown"
        assert item["model_version"] is None
        assert body["hindcast_availability"] == "available"
        assert response.headers["cache-control"] == "no-store"

    def test_empty_catalog_is_absent_not_incomplete(
        self, client: TestClient, fake_stores: dict[str, Any], catalog: Catalog
    ) -> None:
        station = seed(fake_stores)
        body = client.get(f"/api/v1/stations/{station.id}/review-options").json()
        assert body["sources"] == []
        assert body["hindcast_availability"] == "absent"
        assert body["operational_availability"] == "absent"
        assert body["message"]

    @pytest.mark.parametrize("foreign", [False, True])
    def test_scope_before_catalog(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        catalog: Catalog,
        foreign: bool,
    ) -> None:
        station = seed(fake_stores)
        target = uuid4()
        if foreign:
            fake_stores["station_store"].store_station(
                replace(station, tenant_id=TenantId(uuid4()))
            )
            target = station.id
        response = client.get(f"/api/v1/stations/{target}/review-options")
        assert response.status_code == 404
        assert catalog.queries == []
        assert response.headers["cache-control"] == "no-store"

    def test_consumer_denied_before_catalog(
        self, client: TestClient, fake_stores: dict[str, Any], catalog: Catalog
    ) -> None:
        station = seed(fake_stores, AccessTokenRole.CONSUMER)
        response = client.get(f"/api/v1/stations/{station.id}/review-options")
        assert response.status_code == 403
        assert catalog.queries == []

    def test_publication_policy_reaches_store_before_read(
        self, client: TestClient, fake_stores: dict[str, Any], catalog: Catalog
    ) -> None:
        station = seed(fake_stores)
        app.dependency_overrides[get_publication_gate] = lambda: PublicationGate(
            active_tenant_ids=frozenset({station.tenant_id})
        )
        response = client.get(f"/api/v1/stations/{station.id}/review-options")
        assert response.status_code == 200
        assert catalog.queries[0].policy is ReviewPolicy.PUBLISHED_ONLY
        assert response.json()["hindcast_availability"] == "unsupported"
        assert response.json()["sources"] == []

    @pytest.mark.parametrize(
        "failure,status,code",
        [
            (ReviewCatalogLimitError("limit"), 422, "review_catalog_limit_exceeded"),
            (ReviewQueryTimeoutError("timeout"), 503, "review_query_timeout"),
        ],
    )
    def test_errors_have_contract_envelope(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        catalog: Catalog,
        failure: Exception,
        status: int,
        code: str,
    ) -> None:
        station = seed(fake_stores)
        catalog.failure = failure
        response = client.get(f"/api/v1/stations/{station.id}/review-options")
        assert response.status_code == status
        assert response.json()["detail"]["code"] == code
        assert "error" not in response.json()
        assert response.headers["cache-control"] == "no-store"

    def test_response_size_overflow(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        catalog: Catalog,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        station = seed(fake_stores)
        monkeypatch.setattr(
            "sapphire_flow.api.routes.api_event_review.MAX_REVIEW_BYTES", 20
        )
        response = client.get(f"/api/v1/stations/{station.id}/review-options")
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "review_catalog_limit_exceeded"

    def test_unknown_query_does_not_silently_filter(
        self, client: TestClient, fake_stores: dict[str, Any], catalog: Catalog
    ) -> None:
        station = seed(fake_stores)
        response = client.get(
            f"/api/v1/stations/{station.id}/review-options?data_use=provisional_rating"
        )
        assert response.status_code == 400
        assert catalog.queries == []

    def test_region_and_timezone_do_not_change_choices(
        self, client: TestClient, fake_stores: dict[str, Any], catalog: Catalog
    ) -> None:
        station = seed(fake_stores)
        catalog.sources = (source(),)
        url = f"/api/v1/stations/{station.id}/review-options"
        before = client.get(url).json()
        fake_stores["station_store"].store_station(
            replace(station, network="dhm", timezone="Asia/Kathmandu")
        )
        assert client.get(url).json() == before

    def test_unhandled_error_is_no_store(
        self, client: TestClient, fake_stores: dict[str, Any], catalog: Catalog
    ) -> None:
        station = seed(fake_stores)
        catalog.failure = RuntimeError("private database details")
        with TestClient(app, raise_server_exceptions=False) as safe_client:
            response = safe_client.get(f"/api/v1/stations/{station.id}/review-options")
        assert response.status_code == 500
        assert "private" not in response.text
        assert response.headers["cache-control"] == "no-store"
