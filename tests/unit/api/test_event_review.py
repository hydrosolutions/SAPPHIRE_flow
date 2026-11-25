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
    ReviewSeriesQuery,
    ReviewSeriesSnapshot,
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
        self.series = ReviewSeriesSnapshot(sources=sources, records=())
        self.series_queries: list[ReviewSeriesQuery] = []

    def fetch_options(self, query: ReviewOptionsQuery) -> tuple[ReviewSource, ...]:
        self.queries.append(query)
        if self.failure:
            raise self.failure
        return self.sources

    def fetch_series(self, query: ReviewSeriesQuery) -> ReviewSeriesSnapshot:
        self.series_queries.append(query)
        if self.failure:
            raise self.failure
        return self.series


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


class TestReviewSeries:
    def request_params(self) -> dict[str, str]:
        return {
            "model_id": "climatology_fallback",
            "lead_hours": "48",
            "start": "2019-06-01T00:00:00Z",
            "end": "2019-07-01T00:00:00Z",
            "source_mode": "operational",
        }

    @pytest.mark.parametrize("byte_limit, status", [(2 * 1024 * 1024, 200), (20, 422)])
    def test_ordinary_bulk_read(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        catalog: Catalog,
        monkeypatch: pytest.MonkeyPatch,
        byte_limit: int,
        status: int,
    ) -> None:
        from tests.unit.services.test_event_review import record, series_query

        monkeypatch.setattr(
            "sapphire_flow.api.routes.api_event_review.MAX_REVIEW_BYTES", byte_limit
        )
        from sapphire_flow.types.domain import StationThreshold
        from sapphire_flow.types.enums import ThresholdSource

        station = seed(fake_stores)
        value = record(series_query())
        fake_stores["station_store"].store_thresholds(
            [
                StationThreshold(
                    station_id=station.id,
                    parameter="discharge",
                    danger_level="High",
                    value=3,
                    source=ThresholdSource.AUTHORITY,
                    created_at=value.valid_time,
                    updated_at=value.valid_time,
                )
            ]
        )
        value = replace(
            value,
            source=replace(
                value.source,
                kind=ReviewKind.OPERATIONAL,
                hindcast_run_id=None,
                forcing_type=None,
            ),
        )
        catalog.series = ReviewSeriesSnapshot(sources=(value.source,), records=(value,))
        response = client.get(
            f"/api/v1/stations/{station.id}/review-series", params=self.request_params()
        )
        assert response.status_code == status
        body = response.json()
        if status == 422:
            assert body["detail"]["code"] == "review_series_limit_exceeded"
            return
        point = body["segments"][0]["points"][0]
        assert point["median"] == 3.0
        threshold = body["thresholds"][0]
        assert threshold["source"] == "authority"
        assert point["threshold_probabilities"][threshold["key"]]["probability"] == 0.4
        assert point["issued_at"] == "2019-05-30T00:00:00Z"
        assert point["valid_interval_start"] is None
        assert body["temporal_support"] == "unknown"
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.parametrize(
        "changes",
        [
            {"start": "2019-06-01"},
            {"end": "2019-06-01T00:00:00Z"},
            {"end": "2021-01-01T00:00:00Z"},
            {"end": "2099-01-01T00:00:00Z"},
            {"lead_hours": "-1"},
            {"lead_hours": "2.5"},
            {"data_use": "provisional_rating"},
            {"source_mode": "hindcast"},
            {"hindcast_run_id": str(uuid4())},
        ],
    )
    def test_invalid_queries_are_typed_and_do_not_read(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        catalog: Catalog,
        changes: dict[str, str],
    ) -> None:
        station = seed(fake_stores)
        response = client.get(
            f"/api/v1/stations/{station.id}/review-series",
            params=self.request_params() | changes,
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_review_query"
        assert catalog.series_queries == []
        assert response.headers["cache-control"] == "no-store"

    def test_scope_before_query_parsing(
        self, client: TestClient, fake_stores: dict[str, Any], catalog: Catalog
    ) -> None:
        seed(fake_stores)
        response = client.get(f"/api/v1/stations/{uuid4()}/review-series")
        assert response.status_code == 404
        assert catalog.series_queries == []

    def test_repeated_parameters_rejected(
        self, client: TestClient, fake_stores: dict[str, Any], catalog: Catalog
    ) -> None:
        station = seed(fake_stores)
        params = [*self.request_params().items(), ("lead_hours", "48")]
        response = client.get(
            f"/api/v1/stations/{station.id}/review-series", params=params
        )
        assert response.status_code == 422
        assert catalog.series_queries == []

    @pytest.mark.parametrize("mode", ["hindcast", "operational_with_hindcast"])
    def test_gated_hindcast_rejected_before_lookup(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        catalog: Catalog,
        mode: str,
    ) -> None:
        station = seed(fake_stores)
        app.dependency_overrides[get_publication_gate] = lambda: PublicationGate(
            active_tenant_ids=frozenset({station.tenant_id})
        )
        params = self.request_params() | {
            "source_mode": mode,
            "hindcast_run_id": str(uuid4()),
            "hindcast_artifact_id": str(uuid4()),
            "forcing_type": "reanalysis",
            "time_step_seconds": "86400",
        }
        response = client.get(
            f"/api/v1/stations/{station.id}/review-series", params=params
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "review_source_unsupported"
        assert catalog.series_queries == []

    def test_unknown_source_404(
        self, client: TestClient, fake_stores: dict[str, Any], catalog: Catalog
    ) -> None:
        station = seed(fake_stores)
        response = client.get(
            f"/api/v1/stations/{station.id}/review-series", params=self.request_params()
        )
        assert response.status_code == 404

    def test_timeout_503(
        self, client: TestClient, fake_stores: dict[str, Any], catalog: Catalog
    ) -> None:
        station = seed(fake_stores)
        catalog.failure = ReviewQueryTimeoutError("private sql")
        response = client.get(
            f"/api/v1/stations/{station.id}/review-series", params=self.request_params()
        )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "review_query_timeout"
        assert "private" not in response.text


class TestThresholdReviewBoundary:
    @pytest.mark.parametrize("access", ["consumer", "out_of_scope", "foreign_tenant"])
    def test_denied_request_never_fetches_thresholds(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        catalog: Catalog,
        monkeypatch: pytest.MonkeyPatch,
        access: str,
    ) -> None:
        station = seed(
            fake_stores,
            AccessTokenRole.CONSUMER
            if access == "consumer"
            else AccessTokenRole.REVIEWER,
        )
        target = uuid4() if access == "out_of_scope" else station.id
        if access == "foreign_tenant":
            fake_stores["station_store"].store_station(
                replace(station, tenant_id=TenantId(uuid4()))
            )

        def forbidden_read(*args: object) -> None:
            raise AssertionError("threshold read before authorization")

        monkeypatch.setattr(
            fake_stores["station_store"], "fetch_thresholds", forbidden_read
        )
        response = client.get(
            f"/api/v1/stations/{target}/review-series",
            params=TestReviewSeries().request_params(),
        )
        assert response.status_code == (403 if access == "consumer" else 404)

    @pytest.mark.parametrize(
        "count,step_hours,status", [(366, 24, 200), (8784, 1, 422)]
    )
    def test_four_threshold_year_response_bound(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        catalog: Catalog,
        count: int,
        step_hours: int,
        status: int,
    ) -> None:
        from datetime import timedelta

        from sapphire_flow.types.domain import StationThreshold
        from sapphire_flow.types.enums import ThresholdSource
        from tests.unit.services.test_event_review import record, series_query

        station = seed(fake_stores)
        value = record(series_query())
        selected = replace(
            value.source,
            kind=ReviewKind.OPERATIONAL,
            hindcast_run_id=None,
            forcing_type=None,
            time_step_seconds=step_hours * 3600,
        )
        value = replace(value, source=selected)
        fake_stores["station_store"].store_thresholds(
            [
                StationThreshold(
                    station_id=station.id,
                    parameter="discharge",
                    danger_level=level,
                    value=float(i),
                    source=ThresholdSource.AUTHORITY,
                    created_at=value.valid_time,
                    updated_at=value.valid_time,
                )
                for i, level in enumerate(
                    ["Moderate", "Considerable", "High", "Very High"], 1
                )
            ]
        )
        catalog.series = ReviewSeriesSnapshot(
            sources=(selected,),
            records=tuple(
                replace(
                    value,
                    forecast_id=uuid4(),
                    valid_time=value.valid_time + timedelta(hours=i * step_hours),
                    issued_at=value.issued_at + timedelta(hours=i * step_hours),
                )
                for i in range(count)
            ),
        )
        params = TestReviewSeries().request_params() | {"end": "2020-06-01T00:00:00Z"}
        response = client.get(
            f"/api/v1/stations/{station.id}/review-series", params=params
        )
        assert response.status_code == status
        if status == 200:
            assert len(response.content) < 2 * 1024 * 1024
            assert len(response.json()["segments"][0]["points"]) == count
        else:
            assert response.json()["detail"]["code"] == "review_series_limit_exceeded"

    def test_publication_filtered_empty_series_has_no_probabilities(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        catalog: Catalog,
    ) -> None:
        station = seed(fake_stores)
        app.dependency_overrides[get_publication_gate] = lambda: PublicationGate(
            active_tenant_ids=frozenset({station.tenant_id})
        )
        selected = replace(
            source(),
            kind=ReviewKind.OPERATIONAL,
            hindcast_run_id=None,
            forcing_type=None,
        )
        catalog.series = ReviewSeriesSnapshot(sources=(selected,), records=())
        response = client.get(
            f"/api/v1/stations/{station.id}/review-series",
            params=TestReviewSeries().request_params(),
        )
        assert response.status_code == 200
        assert catalog.series_queries[0].scope.policy is ReviewPolicy.PUBLISHED_ONLY
        assert response.json()["state"] == "withheld"
        assert response.json()["segments"][0]["points"] == []
