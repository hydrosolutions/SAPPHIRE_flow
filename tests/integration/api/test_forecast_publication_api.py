from __future__ import annotations

import os
from dataclasses import replace
from datetime import timedelta

import sqlalchemy as sa
from fastapi.testclient import TestClient

from sapphire_flow.api import app
from sapphire_flow.api.deps import get_stores
from sapphire_flow.api.human_auth import require_human_principal
from sapphire_flow.api.publication_gate import PublicationGate, get_publication_gate
from sapphire_flow.api.routes.forecast_publication import publication_clock
from sapphire_flow.api.security import Principal, require_principal
from sapphire_flow.config.deployment import load_config
from sapphire_flow.db.metadata import (
    forecasts,
    protected_backup_health,
    rejected_forecasts,
)
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.enums import AccessTokenRole
from sapphire_flow.types.forecast_publication import PublishRequest
from sapphire_flow.types.human_auth import HumanPermission, StationGrant
from sapphire_flow.types.ids import AccessTokenId, PublicationDecisionId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.integration.store.test_forecast_publication_store import (
    _NOW,
    _add_candidate,
    _seed,
)


class TestPublicationApiPostgres:
    def test_api_store_uses_configured_backup_and_proof_windows(
        self, db_connection: sa.Connection
    ) -> None:
        from uuid import uuid4

        writer, human, forecast_a, station_id = _seed(db_connection)
        forecast_b = _add_candidate(db_connection, station_id)
        config = load_config("config.toml").model_copy(
            update={
                "protected_backup_max_age_hours": 2,
                "publication_proof_window_hours": 1,
            }
        )
        api_store = get_stores(conn=db_connection, config=config)["publication_store"]
        db_connection.execute(
            sa.update(protected_backup_health).values(
                checked_at=_NOW - timedelta(hours=3),
                restored_at=_NOW - timedelta(hours=3),
            )
        )
        stale = api_store.assess_candidate(forecast_b, _NOW)
        assert "stale" in stale.remaining_reasons[0]

        db_connection.execute(
            sa.update(protected_backup_health).values(
                checked_at=_NOW - timedelta(minutes=5),
                restored_at=_NOW - timedelta(hours=1),
            )
        )
        writer.publish(
            PublishRequest(
                forecast_id=forecast_a,
                expected_forecast_version=1,
                expected_selection_version=None,
                idempotency_key="window-first",
            ),
            human,
            decision_id=PublicationDecisionId(uuid4()),
            now=_NOW,
        )
        later = _NOW + timedelta(hours=2)
        db_connection.execute(
            sa.update(protected_backup_health).values(
                checked_at=later - timedelta(minutes=5),
                restored_at=later - timedelta(hours=1),
            )
        )
        overdue = api_store.assess_candidate(forecast_b, later)
        assert "overdue" in overdue.remaining_reasons[0]

    def test_human_replacement_preserves_old_values_then_withdraws_only_old_id(
        self, db_connection: sa.Connection
    ) -> None:
        from uuid import uuid4

        os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
        pub, human, forecast_a, station_id = _seed(db_connection)
        human = replace(
            human,
            grants=frozenset(
                {
                    StationGrant(
                        station_id=station_id, permission=HumanPermission.REVIEW
                    ),
                    StationGrant(
                        station_id=station_id, permission=HumanPermission.PUBLISH
                    ),
                }
            ),
        )
        forecast_b = _add_candidate(db_connection, station_id)
        rejected_id = uuid4()
        rejected_model_id = db_connection.execute(
            sa.select(forecasts.c.model_id).where(forecasts.c.id == forecast_a)
        ).scalar_one()
        db_connection.execute(
            sa.insert(rejected_forecasts).values(
                id=rejected_id,
                attempt_id=uuid4(),
                station_id=station_id,
                model_id=rejected_model_id,
                issued_at=_NOW,
                parameter="discharge",
                units="m3/s",
                representation="members",
                time_step_seconds=3600,
                values={},
                qc_status="qc_failed",
                qc_flags=[],
            )
        )
        stores = {
            "forecast_store": PgForecastStore(db_connection),
            "station_store": PgStationStore(db_connection),
            "publication_store": pub,
        }
        app.dependency_overrides[get_stores] = lambda: stores
        app.dependency_overrides[require_human_principal] = lambda: human
        app.dependency_overrides[require_principal] = lambda: Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.CONSUMER,
            tenant_id=DEFAULT_TENANT_ID,
            station_ids=frozenset({station_id}),
        )
        app.dependency_overrides[get_publication_gate] = lambda: PublicationGate(
            active_tenant_ids=frozenset({DEFAULT_TENANT_ID})
        )
        app.dependency_overrides[publication_clock] = lambda: lambda: _NOW
        try:
            with TestClient(app) as client:
                rejected_paths = [
                    client.get(f"/api/v1/forecasts/{rejected_id}"),
                    client.get(f"/api/v1/review/forecasts/{rejected_id}"),
                    client.post(
                        f"/api/v1/review/forecasts/{rejected_id}/publish",
                        json={
                            "expected_forecast_version": 1,
                            "idempotency_key": "rejected-publish",
                        },
                    ),
                    client.post(
                        f"/api/v1/review/forecasts/{rejected_id}/withdraw",
                        json={
                            "expected_selection_version": 1,
                            "reason_code": "data_error",
                            "reason_text": "QC rejected",
                            "idempotency_key": "rejected-withdraw",
                        },
                    ),
                ]
                assert all(response.status_code == 404 for response in rejected_paths)
                first = client.post(
                    f"/api/v1/review/forecasts/{forecast_a}/publish",
                    json={
                        "expected_forecast_version": 1,
                        "expected_selection_version": None,
                        "idempotency_key": "first",
                    },
                )
                assert first.status_code == 200, first.text
                db_connection.execute(
                    sa.update(forecasts)
                    .where(forecasts.c.id == forecast_a)
                    .values(status="superseded", version=2)
                )
                old_selected = client.get(f"/api/v1/forecasts/{forecast_a}")
                assert old_selected.status_code == 200
                assert old_selected.json()["publication_state"] == "selected"
                assert old_selected.json()["status"] == "superseded"
                assert client.get(f"/api/v1/forecasts/{forecast_b}").status_code == 404

                replacement = client.post(
                    f"/api/v1/review/forecasts/{forecast_b}/publish",
                    json={
                        "expected_forecast_version": 1,
                        "expected_selection_version": 1,
                        "idempotency_key": "replacement",
                    },
                )
                assert replacement.status_code == 200, replacement.text
                assert replacement.json()["replaced_forecast_id"] == str(forecast_a)
                old = client.get(f"/api/v1/forecasts/{forecast_a}")
                assert old.json()["publication_state"] == "replaced"
                assert old.json()["ensemble"]["series"]

                withdrawal = client.post(
                    f"/api/v1/review/forecasts/{forecast_a}/withdraw",
                    json={
                        "expected_selection_version": 2,
                        "reason_code": "data_error",
                        "reason_text": "source correction",
                        "idempotency_key": "withdraw-old",
                    },
                )
                assert withdrawal.status_code == 200, withdrawal.text
                assert client.get(f"/api/v1/forecasts/{forecast_a}").status_code == 410
                latest = client.get(
                    f"/api/v1/stations/{station_id}/forecasts/latest-published",
                    params={"parameter": "discharge"},
                )
                assert latest.json()["id"] == str(forecast_b)
                history = client.get(
                    f"/api/v1/stations/{station_id}/forecast-publications"
                )
                assert history.status_code == 200
                assert [item["event_type"] for item in history.json()["items"]] == [
                    "published",
                    "replaced",
                    "withdrawn",
                ]
                assert history.json()["items"][0]["forecast"] is None
                assert history.json()["items"][1]["forecast"]["id"] == str(forecast_b)
        finally:
            app.dependency_overrides.clear()
