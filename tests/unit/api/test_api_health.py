from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import PipelineCheckType, PipelineHealthStatus
from sapphire_flow.types.pipeline import PipelineHealthRecord

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

_EPOCH = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))


class TestHealthDetail:
    def test_returns_pipeline_health_records(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        fake_stores["pipeline_health_store"].append_health_record(
            PipelineHealthRecord(
                check_type=PipelineCheckType.FORECAST_FRESHNESS,
                checked_at=_EPOCH,
                status=PipelineHealthStatus.CRITICAL,
                subject="station:123",
                detail={"reason": "dark"},
                cycle_time=None,
                created_at=_EPOCH,
            )
        )

        resp = client.get("/api/v1/health/detail")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["check_type"] == "forecast_freshness"
        assert item["status"] == "critical"
        assert item["subject"] == "station:123"
        assert item["detail"] == {"reason": "dark"}

    def test_rejects_unknown_check_type(self, client: TestClient) -> None:
        resp = client.get("/api/v1/health/detail", params={"check_type": "unknown"})
        assert resp.status_code == 400
