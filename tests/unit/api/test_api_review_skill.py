"""Plan 402 T2 — `GET /api/v1/stations/{id}/skill`."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    AccessTokenRole,
    ModelArtifactStatus,
    ModelAssignmentStatus,
    SkillSource,
)
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.skill import SkillScore
from sapphire_flow.types.station import ModelAssignment
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

_T0 = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
_T1 = ensure_utc(datetime(2020, 12, 31, tzinfo=UTC))
_TRAINED = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))


def _make_score(**kwargs: object) -> SkillScore:
    from sapphire_flow.types.enums import SkillFreshness

    defaults = dict(
        id=uuid4(),
        parameter="discharge",
        skill_source=SkillSource.HINDCAST_REANALYSIS,
        forcing_type=None,
        computation_version=1,
        computed_at=_TRAINED,
        lead_time_hours=24,
        season=None,
        flow_regime=None,
        flow_regime_config_id=None,
        metric="nse",
        score=0.8,
        sample_size=100,
        freshness=SkillFreshness.CURRENT,
        eval_period_start=_T0,
        eval_period_end=_T1,
        created_at=_TRAINED,
    )
    defaults.update(kwargs)
    return SkillScore(**defaults)


def _set_principal(role: AccessTokenRole, station_ids: frozenset[StationId]) -> None:
    from sapphire_flow.api import app
    from sapphire_flow.api.security import Principal, require_principal
    from sapphire_flow.types.ids import AccessTokenId

    app.dependency_overrides[require_principal] = lambda: Principal(
        token_id=AccessTokenId(uuid4()),
        role=role,
        tenant_id=None,
        station_ids=station_ids,
    )


def _seed_station_with_active_artifact(fake_stores: dict, *, model_id: ModelId):
    station = make_station_config(code="SKILL-1")
    fake_stores["station_store"].store_station(station)
    fake_stores["station_store"].store_model_assignment(
        ModelAssignment(
            station_id=station.id,
            model_id=model_id,
            time_step=timedelta(days=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=0,
            created_at=_TRAINED,
        )
    )
    artifact_store = fake_stores["artifact_store"]
    aid, _ = artifact_store.store_artifact(
        model_id, b"bytes", _T0, _T1, _TRAINED, station_id=station.id
    )
    artifact_store.transition_artifact_status(aid, ModelArtifactStatus.ACTIVE)
    return station, aid


class TestStationSkill:
    def test_malformed_station_id_is_400(self, client: TestClient) -> None:
        resp = client.get("/api/v1/stations/not-a-uuid/skill")
        assert resp.status_code == 400

    def test_unknown_station_is_404(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/stations/{uuid4()}/skill")
        assert resp.status_code == 404

    def test_no_active_artifact_gives_empty_rows(
        self, client: TestClient, fake_stores: dict
    ) -> None:
        station = make_station_config(code="NOSKILL-1")
        fake_stores["station_store"].store_station(station)
        resp = client.get(f"/api/v1/stations/{station.id}/skill")
        assert resp.status_code == 200
        body = resp.json()
        assert body["station_id"] == str(station.id)
        assert body["rows"] == []
        assert body["selection"] == "latest_generation_on_forecast_artifact"

    def test_active_artifact_rows_are_served_and_training_period_attached(
        self, client: TestClient, fake_stores: dict
    ) -> None:
        model_id = ModelId("skill_test_model")
        station, aid = _seed_station_with_active_artifact(
            fake_stores, model_id=model_id
        )
        fake_stores["skill_store"].store_skill_scores(
            [
                _make_score(
                    station_id=station.id,
                    model_id=model_id,
                    model_artifact_id=aid,
                    generation_id=None,
                )
            ]
        )

        resp = client.get(f"/api/v1/stations/{station.id}/skill")
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) == 1
        row = rows[0]
        assert row["model_id"] == str(model_id)
        assert row["model_artifact_id"] == str(aid)
        assert row["evaluated_on"] == "training_period"
        assert row["training_period_start"] is not None
        assert row["metric"] == "nse"
        assert row["score"] == 0.8

    def test_score_from_a_different_artifact_is_excluded(
        self, client: TestClient, fake_stores: dict
    ) -> None:
        model_id = ModelId("skill_test_model_2")
        station, aid = _seed_station_with_active_artifact(
            fake_stores, model_id=model_id
        )
        other_aid = fake_stores["artifact_store"].store_artifact(
            model_id, b"other", _T0, _T1, _TRAINED, station_id=station.id
        )[0]
        fake_stores["skill_store"].store_skill_scores(
            [
                _make_score(
                    station_id=station.id,
                    model_id=model_id,
                    model_artifact_id=other_aid,
                    generation_id=None,
                )
            ]
        )

        resp = client.get(f"/api/v1/stations/{station.id}/skill")
        assert resp.json()["rows"] == []

    def test_eval_period_end_after_request_time_is_dropped(
        self, client: TestClient, fake_stores: dict
    ) -> None:
        model_id = ModelId("skill_test_model_future")
        station, aid = _seed_station_with_active_artifact(
            fake_stores, model_id=model_id
        )
        future = ensure_utc(datetime(2999, 1, 1, tzinfo=UTC))
        fake_stores["skill_store"].store_skill_scores(
            [
                _make_score(
                    station_id=station.id,
                    model_id=model_id,
                    model_artifact_id=aid,
                    generation_id=None,
                    eval_period_start=future,
                    eval_period_end=future,
                )
            ]
        )

        resp = client.get(f"/api/v1/stations/{station.id}/skill")
        assert resp.json()["rows"] == []

    def test_nan_score_is_served_as_null(
        self, client: TestClient, fake_stores: dict
    ) -> None:
        model_id = ModelId("skill_test_model_nan")
        station, aid = _seed_station_with_active_artifact(
            fake_stores, model_id=model_id
        )
        fake_stores["skill_store"].store_skill_scores(
            [
                _make_score(
                    station_id=station.id,
                    model_id=model_id,
                    model_artifact_id=aid,
                    generation_id=None,
                    score=math.nan,
                )
            ]
        )

        resp = client.get(f"/api/v1/stations/{station.id}/skill")
        assert resp.status_code == 200
        assert resp.json()["rows"][0]["score"] is None

    def test_model_id_filters(self, client: TestClient, fake_stores: dict) -> None:
        model_id = ModelId("skill_test_model_filter")
        other_model_id = ModelId("skill_test_model_filter_other")
        station, aid = _seed_station_with_active_artifact(
            fake_stores, model_id=model_id
        )
        fake_stores["skill_store"].store_skill_scores(
            [
                _make_score(
                    station_id=station.id,
                    model_id=model_id,
                    model_artifact_id=aid,
                    generation_id=None,
                )
            ]
        )

        resp = client.get(
            f"/api/v1/stations/{station.id}/skill",
            params={"model_id": str(other_model_id)},
        )
        assert resp.json()["rows"] == []

    def test_out_of_scope_station_is_404_for_reviewer(
        self, client: TestClient, fake_stores: dict
    ) -> None:
        station = make_station_config(code="SKILL-OOS")
        fake_stores["station_store"].store_station(station)
        _set_principal(AccessTokenRole.REVIEWER, frozenset({StationId(uuid4())}))
        try:
            resp = client.get(f"/api/v1/stations/{station.id}/skill")
        finally:
            from sapphire_flow.api import app
            from sapphire_flow.api.security import require_principal

            app.dependency_overrides.pop(require_principal, None)
        assert resp.status_code == 404

    def test_consumer_gets_403(self, client: TestClient) -> None:
        _set_principal(AccessTokenRole.CONSUMER, frozenset())
        try:
            resp = client.get(f"/api/v1/stations/{uuid4()}/skill")
        finally:
            from sapphire_flow.api import app
            from sapphire_flow.api.security import require_principal

            app.dependency_overrides.pop(require_principal, None)
        assert resp.status_code == 403
