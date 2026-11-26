from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.api import app
from sapphire_flow.api.deps import get_observation_clock
from sapphire_flow.db.metadata import (
    access_tokens,
    group_model_assignments,
    model_artifacts,
    model_assignments,
    station_group_members,
    station_groups,
    stations,
)
from sapphire_flow.db.metadata import skill_scores as scores
from tests.integration.api.test_network_overview import overview_case as overview_case
from tests.integration.api.test_skill_readers_generation_aware import (
    _seed_two_generations,
)
from tests.integration.db.test_role_bootstrap import role_harness as role_harness


def _seed_scores(case: tuple) -> tuple[str, str]:
    conn, _, record, _, _, _ = case
    conn.execute(sa.text("RESET ROLE"))
    candidate = uuid4()
    artifact = dict(
        conn.execute(
            sa.select(model_artifacts).where(
                model_artifacts.c.id == record.model_artifact_id
            )
        )
        .mappings()
        .one()
    )
    conn.execute(
        sa.insert(model_artifacts).values(
            {**artifact, "id": candidate, "status": "pending_approval"}
        )
    )
    for artifact in (record.model_artifact_id, candidate):
        _seed_two_generations(
            conn,
            station_id=record.station_id,
            model_id=record.model_id,
            artifact_id=artifact,
        )
    conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
    return str(record.model_artifact_id), str(candidate)


@pytest.mark.parametrize("assignment", ["station", "group"])
def test_catalogue_matches_explicit_skill_and_active_identity(
    overview_case: tuple,
    assignment: str,
) -> None:
    active, candidate = _seed_scores(overview_case)
    conn, client, record, _, _, _ = overview_case
    if assignment == "group":
        conn.execute(sa.text("RESET ROLE"))
        group_id = uuid4()
        tenant_id = conn.execute(
            sa.select(stations.c.tenant_id).where(stations.c.id == record.station_id)
        ).scalar_one()
        conn.execute(
            sa.insert(station_groups).values(
                id=group_id,
                name="skill-options-group",
                tenant_id=tenant_id,
            )
        )
        conn.execute(
            sa.insert(station_group_members).values(
                group_id=group_id,
                station_id=record.station_id,
                tenant_id=tenant_id,
            )
        )
        conn.execute(
            sa.insert(group_model_assignments).values(
                group_id=group_id,
                model_id=record.model_id,
                time_step=timedelta(days=1),
            )
        )
        conn.execute(
            sa.delete(model_assignments).where(
                model_assignments.c.station_id == record.station_id,
            )
        )
        conn.execute(
            sa.update(model_artifacts)
            .where(
                model_artifacts.c.id == record.model_artifact_id,
            )
            .values(station_id=None, group_id=group_id)
        )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
    path = f"/api/v1/stations/{record.station_id}"
    response = client.get(path + "/skill-options")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["availability"] == "available"
    narrowed = client.get(path + "/skill-options", params={"model_id": record.model_id})
    assert narrowed.status_code == 200
    assert narrowed.json() == payload
    by_id = {item["model_artifact_id"]: item for item in payload["artifacts"]}
    assert set(by_id) == {active, candidate}
    assert by_id[active]["forecasting_status"] == "active_at_station"
    assert by_id[candidate]["forecasting_status"] == "not_active_at_station"
    for aid, option in by_id.items():
        scalar = client.get(path + "/skill", params={"artifact_id": aid})
        assert scalar.status_code == 200, scalar.text
        rows = scalar.json()["rows"]
        assert len(option["evaluations"]) == 1
        assert len(rows) == 1
        evaluation = option["evaluations"][0]
        assert evaluation["generation_id"] == rows[0]["generation_id"]
        assert evaluation["evaluated_on"] == rows[0]["evaluated_on"]
        assert evaluation["eval_period_end"] == rows[0]["eval_period_end"]
        assert rows[0]["score"] == 0.4
    unknown = client.get(path + "/skill-options", params={"model_id": "unknown"})
    assert unknown.json()["artifacts"] == []
    assert unknown.json()["availability"] == "no_stored_skill"
    app.dependency_overrides[get_observation_clock] = lambda: datetime(
        2024, 1, 1, tzinfo=UTC
    )
    assert client.get(path + "/skill-options").json()["artifacts"] == []
    assert (
        client.get(path + "/skill", params={"artifact_id": active}).json()["rows"] == []
    )


@pytest.mark.parametrize("exclusion", ["unassigned", "future", "other_parameter"])
def test_catalogue_excludes_unreadable_scores(
    overview_case: tuple, exclusion: str
) -> None:
    _seed_scores(overview_case)
    conn, client, record, _, _, _ = overview_case
    conn.execute(sa.text("RESET ROLE"))
    if exclusion == "unassigned":
        conn.execute(
            sa.delete(model_assignments).where(
                model_assignments.c.station_id == record.station_id
            )
        )
    else:
        values = (
            {"eval_period_end": datetime(2099, 1, 1, tzinfo=UTC)}
            if exclusion == "future"
            else {"parameter": "water_level"}
        )
        conn.execute(
            sa.update(scores)
            .where(scores.c.station_id == record.station_id)
            .values(**values)
        )
    conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
    response = client.get(f"/api/v1/stations/{record.station_id}/skill-options")
    assert response.status_code == 200, response.text
    assert response.json()["artifacts"] == []


@pytest.mark.parametrize("role, status", [("consumer", 403), ("reviewer", 200)])
def test_catalogue_authorization_and_non_disclosing_absence(
    overview_case: tuple, role: str, status: int
) -> None:
    conn, client, record, _, _, token = overview_case
    conn.execute(sa.text("RESET ROLE"))
    conn.execute(
        sa.update(access_tokens).where(access_tokens.c.id == token).values(role=role)
    )
    conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
    response = client.get(f"/api/v1/stations/{record.station_id}/skill-options")
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    if role == "reviewer":
        denied = client.get(f"/api/v1/stations/{uuid4()}/skill-options")
        assert denied.status_code == 404
        malformed = client.get("/api/v1/stations/invalid/skill-options")
        assert malformed.status_code == 400


def test_catalogue_overflow_never_truncates(
    overview_case: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_scores(overview_case)
    monkeypatch.setattr("sapphire_flow.api.skill_options.MAX_SKILL_EVALUATIONS", 1)
    _, client, record, _, _, _ = overview_case
    response = client.get(f"/api/v1/stations/{record.station_id}/skill-options")
    assert response.status_code == 503
    assert "exceeds" in response.json()["error"]
    assert response.headers["cache-control"] == "no-store"


def test_displayed_forecast_keeps_its_artifact_after_active_artifact_changes(
    overview_case: tuple,
) -> None:
    active, candidate = _seed_scores(overview_case)
    conn, client, record, _, network, _ = overview_case
    conn.execute(sa.text("RESET ROLE"))
    conn.execute(
        sa.update(model_artifacts)
        .where(model_artifacts.c.id == UUID(active))
        .values(status="superseded")
    )
    conn.execute(
        sa.update(model_artifacts)
        .where(model_artifacts.c.id == UUID(candidate))
        .values(status="active")
    )
    conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
    overview = client.get(
        f"/api/v1/networks/{network}/overview?data_use=provisional_rating"
    )
    assert overview.status_code == 200, overview.text
    assert overview.json()["items"][0]["forecasts"][0]["model_artifact_id"] == active
    options = client.get(f"/api/v1/stations/{record.station_id}/skill-options")
    assert options.status_code == 200, options.text
    by_id = {item["model_artifact_id"]: item for item in options.json()["artifacts"]}
    assert by_id[candidate]["forecasting_status"] == "active_at_station"
    assert by_id[active]["forecasting_status"] == "not_active_at_station"
