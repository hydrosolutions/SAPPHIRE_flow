from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sapphire_flow.api import app
from sapphire_flow.api.human_auth import require_human_principal
from sapphire_flow.api.publication_gate import PublicationGate, get_publication_gate
from sapphire_flow.api.routes.forecast_lab import (
    get_bafu_forecast_archive_path,
    get_forecast_combination_strategy,
)
from sapphire_flow.api.security import Principal, require_principal
from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.store.forecast_publication_store import CandidateAssessment
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import (
    AccessTokenRole,
    EnsembleRepresentation,
    ForecastStatus,
    NwpCycleSource,
)
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.forecast_publication import (
    PreservationAtPublish,
    PublicationAction,
    PublicationDecision,
    PublicationEvent,
    PublicationEventType,
    PublicationKey,
    PublicationSelection,
)
from sapphire_flow.types.human_auth import HumanPermission, HumanPrincipal, StationGrant
from sapphire_flow.types.ids import (
    AccessTokenId,
    ForecastId,
    ModelId,
    PublicationDecisionId,
    StationId,
    TenantId,
    UserId,
)
from tests.conftest import make_forecast_ensemble, make_station_config

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from tests.fakes.fake_stores import FakeForecastStore

ISSUED = ensure_utc(datetime(2026, 9, 28, 6, tzinfo=UTC))


def _forecast(station_id: StationId, model_id: str) -> OperationalForecast:
    ensemble = make_forecast_ensemble(
        station_id=station_id, rng=random.Random(42), n_members=3, n_steps=5
    )
    return OperationalForecast(
        id=ForecastId(uuid4()),
        station_id=station_id,
        model_id=ModelId(model_id),
        model_artifact_id=None,
        issued_at=ISSUED,
        nwp_cycle_reference_time=ISSUED,
        nwp_cycle_source=NwpCycleSource.PRIMARY,
        representation=EnsembleRepresentation.MEMBERS,
        status=ForecastStatus.RAW,
        version=1,
        warm_up_source=None,
        warm_up_state_age_hours=None,
        observation_staleness_hours=None,
        ensemble=ensemble,
        created_at=ISSUED,
        updated_at=ISSUED,
    )


class FakePublicationStore:
    def __init__(self, forecasts: FakeForecastStore) -> None:
        self.forecasts = forecasts
        self.decisions: dict[ForecastId, list[PublicationDecision]] = {}
        self.selections: dict[PublicationKey, PublicationSelection] = {}
        self.events: list[PublicationEvent] = []

    def lock_read_snapshot(self) -> None:
        pass

    def add_publish(
        self,
        forecast: OperationalForecast,
        tenant_id: TenantId,
        *,
        replace_selected: bool = False,
    ) -> PublicationDecision:
        key = PublicationKey(
            tenant_id=tenant_id,
            station_id=forecast.station_id,
            parameter=forecast.ensemble.parameter,
            issued_at=forecast.issued_at,
        )
        prior = self.selections.get(key)
        version = prior.version + 1 if prior else 1
        decision = PublicationDecision(
            id=PublicationDecisionId(uuid4()),
            key=key,
            forecast_id=forecast.id,
            actor_user_id=UserId(uuid4()),
            action=PublicationAction.PUBLISH,
            selection_version=version,
            forecast_version=forecast.version,
            preservation_at_publish=PreservationAtPublish.BACKUP_PENDING,
            replaced_forecast_id=(
                prior.selected_forecast_id if replace_selected and prior else None
            ),
            replaced_decision_id=None,
            reason_code=None,
            reason_text=None,
            created_at=ISSUED,
        )
        self.decisions.setdefault(forecast.id, []).append(decision)
        self.selections[key] = PublicationSelection(
            key=key,
            selected_forecast_id=forecast.id,
            version=version,
            linked_warning_publication_id=None,
        )
        self.events.append(
            PublicationEvent(
                sequence=len(self.events) + 1,
                event_type=(
                    PublicationEventType.REPLACED
                    if replace_selected
                    else PublicationEventType.PUBLISHED
                ),
                decision=decision,
                withdrawn=False,
            )
        )
        return decision

    def add_withdraw(self, forecast: OperationalForecast) -> None:
        prior = self.decisions[forecast.id][-1]
        decision = replace(
            prior,
            id=PublicationDecisionId(uuid4()),
            action=PublicationAction.WITHDRAW,
            preservation_at_publish=None,
            reason_text="bad data",
            created_at=ensure_utc(ISSUED + timedelta(hours=1)),
        )
        self.decisions[forecast.id].append(decision)
        selection = self.selections[prior.key]
        if selection.selected_forecast_id == forecast.id:
            self.selections[prior.key] = replace(
                selection,
                selected_forecast_id=None,
                version=selection.version + 1,
            )
        self.events.append(
            PublicationEvent(
                sequence=len(self.events) + 1,
                event_type=PublicationEventType.WITHDRAWN,
                decision=decision,
                withdrawn=True,
            )
        )

    def fetch_decisions(self, forecast_id: ForecastId) -> list[PublicationDecision]:
        return self.decisions.get(forecast_id, [])

    def fetch_selection(self, key: PublicationKey) -> PublicationSelection | None:
        return self.selections.get(key)

    def fetch_selected_ids(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        *,
        model_id: str | None,
        parameter: str | None,
        degraded_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[ForecastId], int]:
        rows = [
            self.forecasts.fetch_forecast(s.selected_forecast_id)
            for s in self.selections.values()
            if s.key.station_id == station_id
            and start <= s.key.issued_at < end
            and s.selected_forecast_id is not None
            and (parameter is None or parameter == s.key.parameter)
        ]
        forecasts = [
            f
            for f in rows
            if f is not None
            and (model_id is None or str(f.model_id) == model_id)
            and (not degraded_only or f.input_quality is not None)
        ]
        return [f.id for f in forecasts[offset : offset + limit]], len(forecasts)

    def fetch_latest_selected_id(
        self, station_id: StationId, parameter: str
    ) -> ForecastId | None:
        selected = [
            s
            for s in self.selections.values()
            if s.key.station_id == station_id
            and s.key.parameter == parameter
            and s.selected_forecast_id is not None
        ]
        return (
            max(selected, key=lambda s: s.key.issued_at).selected_forecast_id
            if selected
            else None
        )

    def fetch_events(
        self,
        *,
        after_sequence: int,
        limit: int,
        tenant_ids: frozenset[TenantId],
        station_ids: frozenset[StationId] | None = None,
    ) -> list[PublicationEvent]:
        return [
            replace(
                e,
                withdrawn=self.decisions[e.decision.forecast_id][-1].action
                is PublicationAction.WITHDRAW,
            )
            for e in self.events
            if e.sequence > after_sequence
            and e.decision.key.tenant_id in tenant_ids
            and (station_ids is None or e.decision.key.station_id in station_ids)
        ][:limit]

    def assess_candidate(
        self, forecast_id: ForecastId, now: UtcDatetime
    ) -> CandidateAssessment:
        return CandidateAssessment(
            capture_status="complete",
            preservation_status="backup_pending",
            attestation_id=None,
            remaining_reasons=(),
            preservation_at_publish=PreservationAtPublish.BACKUP_PENDING,
        )


@pytest.fixture
def publication_data(fake_stores: dict[str, Any]) -> tuple[Any, FakePublicationStore]:
    station = make_station_config(rng=random.Random(7))
    fake_stores["station_store"].store_station(station)
    pub = FakePublicationStore(fake_stores["forecast_store"])
    fake_stores["publication_store"] = pub
    app.dependency_overrides[get_publication_gate] = lambda: PublicationGate(
        active_tenant_ids=frozenset({station.tenant_id})
    )
    return station, pub


def _service_principal(role: AccessTokenRole, sid: StationId) -> Principal:
    return Principal(
        token_id=AccessTokenId(uuid4()),
        role=role,
        tenant_id=None,
        station_ids=frozenset({sid}),
    )


class TestPublishedReads:
    def test_admin_uses_forecast_station_tenant_for_gate(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        publication_data: tuple[Any, FakePublicationStore],
    ) -> None:
        active_station, _ = publication_data
        other_station = make_station_config(rng=random.Random(8))
        other_station = replace(other_station, tenant_id=TenantId(uuid4()))
        fake_stores["station_store"].store_station(other_station)
        unpublished_active = _forecast(active_station.id, "model_a")
        raw_other = _forecast(other_station.id, "model_b")
        fake_stores["forecast_store"].store_forecast(unpublished_active)
        fake_stores["forecast_store"].store_forecast(raw_other)
        app.dependency_overrides[require_principal] = lambda: _service_principal(
            AccessTokenRole.ADMIN, active_station.id
        )

        assert (
            client.get(f"/api/v1/forecasts/{unpublished_active.id}").status_code == 404
        )
        assert client.get(f"/api/v1/forecasts/{raw_other.id}").status_code == 200

    def test_filtered_total_and_newer_issue_latest_are_publication_backed(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        publication_data: tuple[Any, FakePublicationStore],
    ) -> None:
        station, pub = publication_data
        first = _forecast(station.id, "model_a")
        newer = replace(
            _forecast(station.id, "model_b"),
            issued_at=ensure_utc(ISSUED + timedelta(hours=6)),
        )
        raw = replace(
            _forecast(station.id, "model_c"),
            issued_at=ensure_utc(ISSUED + timedelta(hours=12)),
        )
        for forecast in (first, newer, raw):
            fake_stores["forecast_store"].store_forecast(forecast)
        pub.add_publish(first, station.tenant_id)
        pub.add_publish(newer, station.tenant_id)

        base = f"/api/v1/stations/{station.id}/forecasts"
        window = {
            "start": "2026-09-28T00:00:00+00:00",
            "end": "2026-09-29T00:00:00+00:00",
        }
        filtered = client.get(base, params={**window, "model_id": "model_a"})
        page = client.get(base, params={**window, "limit": 1, "offset": 1})
        latest = client.get(
            f"/api/v1/stations/{station.id}/forecasts/latest-published",
            params={"parameter": first.ensemble.parameter},
        )

        assert filtered.json()["total"] == 1
        assert [item["id"] for item in filtered.json()["items"]] == [str(first.id)]
        assert page.json()["total"] == 2
        assert len(page.json()["items"]) == 1
        assert latest.json()["id"] == str(newer.id)
        assert client.get(f"/api/v1/forecasts/{raw.id}").status_code == 404

    def test_reselection_keeps_history_and_historical_withdrawal_keeps_current(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        publication_data: tuple[Any, FakePublicationStore],
    ) -> None:
        station, pub = publication_data
        first = _forecast(station.id, "model_a")
        second = _forecast(station.id, "model_b")
        for forecast in (first, second):
            fake_stores["forecast_store"].store_forecast(forecast)
        pub.add_publish(first, station.tenant_id)
        pub.add_publish(second, station.tenant_id, replace_selected=True)
        pub.add_publish(first, station.tenant_id, replace_selected=True)
        pub.add_withdraw(second)

        history = client.get(f"/api/v1/stations/{station.id}/forecast-publications")
        current = client.get(
            f"/api/v1/stations/{station.id}/forecasts/latest-published",
            params={"parameter": first.ensemble.parameter},
        )
        assert history.status_code == 200
        assert [item["event_type"] for item in history.json()["items"]] == [
            "published",
            "replaced",
            "replaced",
            "withdrawn",
        ]
        assert history.json()["items"][1]["forecast"] is None
        assert current.json()["id"] == str(first.id)
        assert client.get(f"/api/v1/forecasts/{second.id}").status_code == 410

    def test_selected_superseded_id_is_served_while_unpublished_id_is_hidden(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        publication_data: tuple[Any, FakePublicationStore],
    ) -> None:
        station, pub = publication_data
        selected = replace(
            _forecast(station.id, "model_a"), status=ForecastStatus.SUPERSEDED
        )
        raw = _forecast(station.id, "model_b")
        fake_stores["forecast_store"].store_forecast(selected)
        fake_stores["forecast_store"].store_forecast(raw)
        pub.add_publish(selected, station.tenant_id)

        visible = client.get(f"/api/v1/forecasts/{selected.id}")
        hidden = client.get(f"/api/v1/forecasts/{raw.id}")
        listing = client.get(
            f"/api/v1/stations/{station.id}/forecasts",
            params={
                "start": "2026-09-28T00:00:00+00:00",
                "end": "2026-09-29T00:00:00+00:00",
            },
        )

        assert visible.status_code == 200
        assert visible.headers["cache-control"] == "no-store"
        assert visible.json()["status"] == "superseded"
        assert visible.json()["publication_state"] == "selected"
        assert hidden.status_code == 404
        assert hidden.headers["cache-control"] == "no-store"
        assert listing.json()["total"] == 1
        assert [item["id"] for item in listing.json()["items"]] == [str(selected.id)]

    def test_replaced_values_remain_and_withdrawal_returns_metadata_only(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        publication_data: tuple[Any, FakePublicationStore],
    ) -> None:
        station, pub = publication_data
        old = _forecast(station.id, "model_a")
        new = _forecast(station.id, "model_b")
        for f in (old, new):
            fake_stores["forecast_store"].store_forecast(f)
        pub.add_publish(old, station.tenant_id)
        pub.add_publish(new, station.tenant_id, replace_selected=True)
        assert (
            client.get(f"/api/v1/forecasts/{old.id}").json()["publication_state"]
            == "replaced"
        )
        assert client.get(f"/api/v1/forecasts/{old.id}").json()["ensemble"]["series"]

        pub.add_withdraw(old)
        tombstone = client.get(f"/api/v1/forecasts/{old.id}")
        current = client.get(
            f"/api/v1/stations/{station.id}/forecasts/latest-published",
            params={"parameter": old.ensemble.parameter},
        )
        assert tombstone.status_code == 410
        assert tombstone.headers["cache-control"] == "no-store"
        assert tombstone.json()["publication_state"] == "withdrawn"
        assert "ensemble" not in tombstone.json()
        assert current.json()["id"] == str(new.id)

    def test_history_tombstones_old_decisions_and_feed_cursors_advance(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        publication_data: tuple[Any, FakePublicationStore],
    ) -> None:
        station, pub = publication_data
        old = _forecast(station.id, "model_a")
        fake_stores["forecast_store"].store_forecast(old)
        pub.add_publish(old, station.tenant_id)
        pub.add_withdraw(old)
        history = client.get(f"/api/v1/stations/{station.id}/forecast-publications")
        first = client.get("/api/v1/forecast-publications", params={"limit": 1})
        second = client.get(
            "/api/v1/forecast-publications",
            params={"limit": 1, "cursor": first.json()["next_cursor"]},
        )
        assert history.status_code == 200
        assert all(item["forecast"] is None for item in history.json()["items"])
        assert [
            item["event_type"]
            for item in (first.json()["items"][0], second.json()["items"][0])
        ] == ["published", "withdrawn"]

    @pytest.mark.parametrize(
        "role",
        [
            AccessTokenRole.CONSUMER,
            AccessTokenRole.REVIEWER,
            AccessTokenRole.ADMIN,
        ],
    )
    def test_same_scope_consumer_and_reviewer_get_same_published_values(
        self,
        role: AccessTokenRole,
        client: TestClient,
        fake_stores: dict[str, Any],
        publication_data: tuple[Any, FakePublicationStore],
    ) -> None:
        station, pub = publication_data
        fc = _forecast(station.id, "model_a")
        fake_stores["forecast_store"].store_forecast(fc)
        pub.add_publish(fc, station.tenant_id)
        app.dependency_overrides[require_principal] = lambda: _service_principal(
            role, station.id
        )
        response = client.get(f"/api/v1/forecasts/{fc.id}")
        assert response.status_code == 200
        assert response.json()["publication_state"] == "selected"

    @pytest.mark.parametrize(
        "role",
        [
            AccessTokenRole.CONSUMER,
            AccessTokenRole.REVIEWER,
            AccessTokenRole.ADMIN,
        ],
    )
    def test_forecast_lab_denies_activated_station_before_raw_assembly(
        self,
        role: AccessTokenRole,
        client: TestClient,
        publication_data: tuple[Any, FakePublicationStore],
    ) -> None:
        from sapphire_flow.types.enums import ModelCombinationStrategy

        station, _ = publication_data
        app.dependency_overrides[require_principal] = lambda: _service_principal(
            role, station.id
        )
        app.dependency_overrides[get_bafu_forecast_archive_path] = lambda: None
        app.dependency_overrides[get_forecast_combination_strategy] = lambda: (
            ModelCombinationStrategy.PRIMARY
        )
        response = client.get(
            "/api/v1/forecast-lab/snapshot", params={"station_code": station.code}
        )
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"


class TestHumanReview:
    def test_service_token_cannot_reach_candidate_route(
        self, client: TestClient, publication_data: tuple[Any, FakePublicationStore]
    ) -> None:
        station, _ = publication_data
        response = client.get(f"/api/v1/review/forecasts/{uuid4()}")
        assert response.status_code in (401, 503)

    def test_station_grant_is_required_before_candidate_detail(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        publication_data: tuple[Any, FakePublicationStore],
    ) -> None:
        station, _ = publication_data
        fc = _forecast(station.id, "model_a")
        fake_stores["forecast_store"].store_forecast(fc)
        app.dependency_overrides[require_human_principal] = lambda: HumanPrincipal(
            user_id=UserId(uuid4()), tenant_id=station.tenant_id, grants=frozenset()
        )
        assert client.get(f"/api/v1/review/forecasts/{fc.id}").status_code == 404

    def test_review_grant_sees_candidate_but_writes_remain_disabled_without_activation(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        publication_data: tuple[Any, FakePublicationStore],
    ) -> None:
        station, _ = publication_data
        fc = _forecast(station.id, "model_a")
        fake_stores["forecast_store"].store_forecast(fc)
        app.dependency_overrides[require_human_principal] = lambda: HumanPrincipal(
            user_id=UserId(uuid4()),
            tenant_id=station.tenant_id,
            grants=frozenset(
                {StationGrant(station_id=station.id, permission=HumanPermission.REVIEW)}
            ),
        )
        candidate = client.get(f"/api/v1/review/forecasts/{fc.id}")
        publish = client.post(
            f"/api/v1/review/forecasts/{fc.id}/publish",
            json={"expected_forecast_version": 1, "idempotency_key": "one"},
        )
        assert candidate.status_code == 200
        assert candidate.json()["publication_state"] == "unpublished"
        assert publish.status_code == 404

    def test_publish_grant_still_cannot_write_when_operational_gate_is_off(
        self,
        client: TestClient,
        fake_stores: dict[str, Any],
        publication_data: tuple[Any, FakePublicationStore],
    ) -> None:
        station, _ = publication_data
        fc = _forecast(station.id, "model_a")
        fake_stores["forecast_store"].store_forecast(fc)
        app.dependency_overrides[require_human_principal] = lambda: HumanPrincipal(
            user_id=UserId(uuid4()),
            tenant_id=station.tenant_id,
            grants=frozenset(
                {
                    StationGrant(
                        station_id=station.id, permission=HumanPermission.REVIEW
                    ),
                    StationGrant(
                        station_id=station.id, permission=HumanPermission.PUBLISH
                    ),
                }
            ),
        )
        app.dependency_overrides.pop(get_publication_gate)
        publish = client.post(
            f"/api/v1/review/forecasts/{fc.id}/publish",
            json={"expected_forecast_version": 1, "idempotency_key": "one"},
        )
        assert publish.status_code == 503


class TestActivationConfig:
    def test_nonempty_activation_is_rejected_before_plan_342(self) -> None:
        with pytest.raises(ValidationError, match="requires Plan 342"):
            DeploymentConfig.model_validate(
                {
                    "max_retention_days": 3000,
                    "publication_active_tenant_ids": [str(uuid4())],
                }
            )
