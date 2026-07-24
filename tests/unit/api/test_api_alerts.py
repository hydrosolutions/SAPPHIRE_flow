from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AlertSource, AlertStatus
from tests.conftest import make_alert

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

_EPOCH = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))


class TestListAlerts:
    def test_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/alerts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["limit"] == 50
        assert body["offset"] == 0

    def test_returns_alert(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        alert = make_alert(rng=random.Random(1))
        fake_stores["alert_store"].upsert_alert(alert)

        resp = client.get("/api/v1/alerts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["id"] == str(alert.id)
        assert item["station_id"] == str(alert.station_id)
        assert item["source"] == alert.source.value
        assert item["alert_level"] == alert.alert_level
        assert item["status"] == alert.status.value
        assert item["trigger_probability"] == alert.trigger_probability
        assert item["trigger_value"] == alert.trigger_value
        assert item["triggered_at"] is not None
        assert isinstance(item["model_ids"], list)

    def test_filter_by_status_raised(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        raised = make_alert(status=AlertStatus.RAISED, rng=random.Random(1))
        resolved = make_alert(
            status=AlertStatus.RESOLVED,
            alert_level="High",
            rng=random.Random(2),
        )
        fake_stores["alert_store"].upsert_alert(raised)
        fake_stores["alert_store"].upsert_alert(resolved)

        resp = client.get("/api/v1/alerts", params={"status": "raised"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["status"] == "raised"

    def test_filter_by_source(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        forecast_alert = make_alert(source=AlertSource.FORECAST, rng=random.Random(1))
        obs_alert = make_alert(
            source=AlertSource.OBSERVATION,
            alert_level="High",
            rng=random.Random(2),
        )
        fake_stores["alert_store"].upsert_alert(forecast_alert)
        fake_stores["alert_store"].upsert_alert(obs_alert)

        resp = client.get("/api/v1/alerts", params={"source": "observation"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["source"] == "observation"

    def test_pagination(self, client: TestClient, fake_stores: dict[str, Any]) -> None:
        for i in range(3):
            a = make_alert(alert_level=f"Level-{i}", rng=random.Random(10 + i))
            triggered = ensure_utc(datetime(2025, 1, 1, i, tzinfo=UTC))
            a = replace(a, triggered_at=triggered)
            fake_stores["alert_store"].upsert_alert(a)

        resp = client.get("/api/v1/alerts", params={"limit": 2})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] == 3
        assert body["limit"] == 2

    def test_pagination_offset(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        for i in range(3):
            a = make_alert(alert_level=f"Level-{i}", rng=random.Random(10 + i))
            fake_stores["alert_store"].upsert_alert(a)

        resp = client.get("/api/v1/alerts", params={"limit": 10, "offset": 2})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 1
        assert body["offset"] == 2

    def test_filter_by_level(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        moderate = make_alert(alert_level="Moderate", rng=random.Random(1))
        high = make_alert(alert_level="High", rng=random.Random(2))
        fake_stores["alert_store"].upsert_alert(moderate)
        fake_stores["alert_store"].upsert_alert(high)

        resp = client.get("/api/v1/alerts", params={"level": "High"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["alert_level"] == "High"


class TestAcknowledgeAlertRemovedFromV10Surface:
    """G4 LOCKED: the sole HTTP mutation is removed from the v1.0
    access-token surface (returns 501), regardless of alert state — no
    bearer key of any role may POST."""

    def test_acknowledge_returns_501_for_existing_raised_alert(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        alert = make_alert(status=AlertStatus.RAISED, rng=random.Random(1))
        fake_stores["alert_store"].upsert_alert(alert)
        user_id = str(uuid4())

        resp = client.post(
            f"/api/v1/alerts/{alert.id}/acknowledge",
            json={"acknowledged_by": user_id},
        )
        assert resp.status_code == 501

    def test_acknowledge_returns_501_for_unknown_alert(
        self, client: TestClient
    ) -> None:
        user_id = str(uuid4())
        resp = client.post(
            f"/api/v1/alerts/{uuid4()}/acknowledge",
            json={"acknowledged_by": user_id},
        )
        assert resp.status_code == 501
