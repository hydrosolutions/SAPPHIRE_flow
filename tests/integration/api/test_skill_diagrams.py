from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.api import app
from sapphire_flow.api.deps import get_observation_clock
from sapphire_flow.db.metadata import access_tokens, model_artifacts, model_assignments
from sapphire_flow.db.metadata import skill_diagrams as diagrams
from sapphire_flow.db.metadata import skill_generations as generations
from sapphire_flow.db.metadata import skill_scores as scores
from tests.integration.api.test_network_overview import overview_case as overview_case
from tests.integration.api.test_skill_options import _seed_scores
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.integration.store.test_skill_store import _period_context

if TYPE_CHECKING:
    from sapphire_flow.types.forecast import OperationalForecast

RELIABILITY = {
    "bins": [0.25, 0.75],
    "forecast_freq": [0.0, 1.0],
    "sample_counts": [0, 1],
    "observed_freq": [None, 1.0],
}
ROC = {
    "thresholds": [0.0, 1.0],
    "hit_rate": [1.0, 0.0],
    "false_alarm_rate": [None, None],
    "n_events": 1,
    "n_non_events": 0,
}


def test_readers_preserve_all_evaluation_contexts_in_one_generation(
    overview_case: tuple,
) -> None:
    import json
    from dataclasses import replace

    from sapphire_flow.store.skill_period_codec import evaluation_context_content
    from sapphire_flow.store.skill_store import PgSkillStore
    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.enums import ForcingType, SkillSource

    candidate = _seed_diagrams(overview_case)
    conn, client, record, _, _, _ = overview_case
    conn.execute(sa.text("RESET ROLE"))
    generation = uuid4()
    approximate = _period_context(900)
    revised_source = replace(
        approximate,
        source_periods=(
            replace(
                approximate.source_periods[0], evidence="requalified native archive"
            ),
        ),
    )
    contexts = (_period_context(), approximate, revised_source)
    for table in (scores, diagrams):
        original = (
            conn.execute(
                sa.select(table)
                .where(table.c.model_artifact_id == UUID(candidate))
                .order_by(table.c.computation_version.desc())
                .limit(1)
            )
            .mappings()
            .one()
        )
        for context in contexts:
            conn.execute(
                sa.insert(table).values(
                    {
                        **dict(original),
                        "id": uuid4(),
                        "computation_version": 5,
                        "generation_id": generation,
                        "lead_time_hours": -6,
                        **evaluation_context_content(context),
                    }
                )
            )
    PgSkillStore(conn).publish_generation(
        generation_id=generation,
        station_id=record.station_id,
        model_id=record.model_id,
        model_artifact_id=UUID(candidate),
        parameter="discharge",
        skill_source=SkillSource.HINDCAST_REANALYSIS,
        forcing_type=ForcingType.REANALYSIS,
        computation_version=5,
        published_at=ensure_utc(datetime(2025, 7, 1, tzinfo=UTC)),
        score_count=3,
        diagram_count=3,
        evaluation_contexts=contexts,
    )
    conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
    path = f"/api/v1/stations/{record.station_id}"
    options = client.get(path + "/skill-options").json()["artifacts"]
    selected = next(item for item in options if item["model_artifact_id"] == candidate)
    scalar = client.get(path + "/skill", params={"artifact_id": candidate})
    diagram = client.get(
        path + "/skill-diagrams",
        params={
            **_params(record, candidate),
            "lead_hours": [-6, 24],
        },
    )
    assert scalar.status_code == diagram.status_code == 200
    for rows in (
        selected["evaluations"],
        scalar.json()["rows"],
        diagram.json()["diagrams"],
    ):
        assert {
            row["evaluation_context"]["reference_displacement_seconds"]
            if row["evaluation_context"] is not None
            else None
            for row in rows
        } == {None, 0, 900}
        assert {
            json.dumps(row["evaluation_context"], sort_keys=True)
            for row in rows
            if row["evaluation_context"] is not None
        } == {
            json.dumps(evaluation_context_content(context), sort_keys=True)
            for context in contexts
        }


def _seed_diagrams(case: tuple) -> str:
    _, candidate = _seed_scores(case)
    conn, _, record, _, _, _ = case
    conn.execute(sa.text("RESET ROLE"))
    for table in (scores, diagrams, generations):
        values = {"skill_source": "hindcast_reanalysis"}
        if "forcing_type" in table.c:
            values["forcing_type"] = "reanalysis"
        conn.execute(
            sa.update(table)
            .where(table.c.station_id == record.station_id)
            .values(**values)
        )
    conn.execute(
        sa.update(diagrams)
        .where(diagrams.c.station_id == record.station_id)
        .values(threshold_level="High")
    )
    rows = (
        conn.execute(
            sa.select(diagrams).where(
                diagrams.c.station_id == record.station_id,
                diagrams.c.diagram_type == "reliability",
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        conn.execute(
            sa.update(diagrams)
            .where(diagrams.c.id == row["id"])
            .values(data=RELIABILITY)
        )
        conn.execute(
            sa.insert(diagrams).values(
                {**dict(row), "id": uuid4(), "diagram_type": "roc", "data": ROC}
            )
        )
    conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
    return candidate


def _params(record: OperationalForecast, artifact: str) -> dict[str, Any]:
    return {
        "model_id": record.model_id,
        "artifact_id": artifact,
        "lead_hours": [24],
        "threshold_level": ["High"],
    }


def test_bulk_diagrams_match_scalar_generation_and_preserve_undefined(
    overview_case: tuple,
) -> None:
    candidate = _seed_diagrams(overview_case)
    _, client, record, _, _, _ = overview_case
    path = f"/api/v1/stations/{record.station_id}"
    response = client.get(path + "/skill-diagrams", params=_params(record, candidate))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["availability"] == "available"
    assert payload["missing"] == []
    assert len(payload["diagrams"]) == 2
    scalar = client.get(path + "/skill", params={"artifact_id": candidate}).json()[
        "rows"
    ][0]
    for diagram in payload["diagrams"]:
        assert diagram["generation_id"] == scalar["generation_id"]
        assert diagram["eval_period_end"] == scalar["eval_period_end"]
        assert diagram["forcing_type"] == "reanalysis"
        assert diagram["threshold_context"] is None
        assert diagram["data"] == (
            ROC if diagram["diagram_type"] == "roc" else RELIABILITY
        )
    assert payload["training_period_end"] == scalar["training_period_end"]


def test_missing_requested_combinations_are_explicit(overview_case: tuple) -> None:
    candidate = _seed_diagrams(overview_case)
    _, client, record, _, _, _ = overview_case
    response = client.get(
        f"/api/v1/stations/{record.station_id}/skill-diagrams",
        params={
            **_params(record, candidate),
            "lead_hours": [24, 48],
            "threshold_level": ["High", "Warning"],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["availability"] == "partial"
    assert len(payload["diagrams"]) == 2
    assert len(payload["missing"]) == 6


def test_maximum_bulk_selection_preserves_every_combination(
    overview_case: tuple,
) -> None:
    candidate = _seed_diagrams(overview_case)
    _, client, record, _, _, _ = overview_case
    response = client.get(
        f"/api/v1/stations/{record.station_id}/skill-diagrams",
        params={
            **_params(record, candidate),
            "lead_hours": [24 * day for day in range(1, 17)],
            "threshold_level": ["High", *[f"level-{i}" for i in range(7)]],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["diagrams"]) == 2
    assert len(payload["missing"]) == 254
    assert len(response.content) <= 4 * 1024 * 1024


@pytest.mark.parametrize(
    "reason",
    [
        "unassigned",
        "future",
        "wrong_artifact",
        "other_station_artifact",
        "new_scalar_generation",
        "different_scalar_period",
    ],
)
def test_unavailable_never_falls_back_to_stale_or_other_artifact(
    overview_case: tuple, reason: str
) -> None:
    candidate = _seed_diagrams(overview_case)
    conn, client, record, _, _, _ = overview_case
    conn.execute(sa.text("RESET ROLE"))
    if reason == "unassigned":
        conn.execute(
            sa.delete(model_assignments).where(
                model_assignments.c.station_id == record.station_id
            )
        )
    elif reason == "future":
        app.dependency_overrides[get_observation_clock] = lambda: datetime(
            2024, 1, 1, tzinfo=UTC
        )
    elif reason == "wrong_artifact":
        candidate = str(uuid4())
    elif reason == "other_station_artifact":
        from tests.integration.api.test_skill_readers_generation_aware import (
            _seed_station,
        )

        other_station = _seed_station(conn)
        original = (
            conn.execute(
                sa.select(model_artifacts).where(
                    model_artifacts.c.id == UUID(candidate),
                )
            )
            .mappings()
            .one()
        )
        new_id = uuid4()
        conn.execute(
            sa.insert(model_artifacts).values(
                {
                    **dict(original),
                    "id": new_id,
                    "station_id": other_station,
                }
            )
        )
        candidate = str(new_id)
    elif reason == "different_scalar_period":
        conn.execute(
            sa.update(scores)
            .where(scores.c.station_id == record.station_id)
            .values(eval_period_start=datetime(2025, 2, 1, tzinfo=UTC))
        )
    else:
        old = (
            conn.execute(
                sa.select(generations)
                .where(generations.c.model_artifact_id == UUID(candidate))
                .order_by(generations.c.published_at.desc())
                .limit(1)
            )
            .mappings()
            .one()
        )
        new_id = uuid4()
        conn.execute(
            sa.insert(generations).values(
                {
                    **dict(old),
                    "id": new_id,
                    "published_at": datetime(2026, 1, 1, tzinfo=UTC),
                    "diagram_count": 0,
                }
            )
        )
        conn.execute(
            sa.update(scores)
            .where(scores.c.generation_id == old["id"])
            .values(generation_id=new_id)
        )
    conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
    response = client.get(
        f"/api/v1/stations/{record.station_id}/skill-diagrams",
        params=_params(record, candidate),
    )
    assert response.status_code == 200, response.text
    assert response.json()["availability"] == "no_stored_diagrams"
    assert response.json()["diagrams"] == []
    assert response.json()["training_period_start"] is None
    assert response.json()["training_period_end"] is None


@pytest.mark.parametrize(
    "change, status",
    [
        ("consumer", 403),
        ("outside_scope", 404),
        ("invalid_leads", 422),
        ("invalid_thresholds", 422),
    ],
)
def test_diagram_access_and_selector_bounds(
    overview_case: tuple, change: str, status: int
) -> None:
    conn, client, record, _, _, token = overview_case
    sid = record.station_id
    params = _params(record, str(record.model_artifact_id))
    if change == "consumer":
        conn.execute(sa.text("RESET ROLE"))
        conn.execute(
            sa.update(access_tokens)
            .where(access_tokens.c.id == token)
            .values(role="consumer")
        )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
    elif change == "outside_scope":
        sid = uuid4()
    elif change == "invalid_leads":
        params["lead_hours"] = list(range(17))
    else:
        params["threshold_level"] = [str(i) for i in range(9)]
    response = client.get(f"/api/v1/stations/{sid}/skill-diagrams", params=params)
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("limit", [10, 400])
def test_oversized_diagram_or_envelope_is_rejected(
    overview_case: tuple, monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    candidate = _seed_diagrams(overview_case)
    monkeypatch.setattr("sapphire_flow.api.skill_diagrams.MAX_DIAGRAM_BYTES", limit)
    _, client, record, _, _, _ = overview_case
    response = client.get(
        f"/api/v1/stations/{record.station_id}/skill-diagrams",
        params=_params(record, candidate),
    )
    assert response.status_code == 422, response.text
    assert "exceed" in response.json()["error"]
    assert "diagrams" not in response.json()


def test_threshold_counts_and_provenance_are_stored_values(
    overview_case: tuple,
) -> None:
    from sapphire_flow.services.skill.thresholds import paired_threshold_probabilities
    from sapphire_flow.store.threshold_skill_codec import encode_threshold_context
    from tests.unit.services.skill.test_thresholds import quantiles, threshold

    candidate = _seed_diagrams(overview_case)
    conn, client, record, _, _, _ = overview_case
    _, _, context = paired_threshold_probabilities(
        [quantiles(0, 10), quantiles(6, 7)], [10.0, 0.0], threshold(), "m3/s"
    )
    encoded = encode_threshold_context(context)
    conn.execute(sa.text("RESET ROLE"))
    conn.execute(
        sa.update(diagrams)
        .where(
            diagrams.c.model_artifact_id == UUID(candidate),
        )
        .values(threshold_context=encoded)
    )
    conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
    response = client.get(
        f"/api/v1/stations/{record.station_id}/skill-diagrams",
        params=_params(record, candidate),
    )
    assert response.status_code == 200, response.text
    for row in response.json()["diagrams"]:
        actual = row["threshold_context"]
        assert actual["excluded_count"] == 1
        assert actual["scored_count"] == 1
        assert actual["threshold_source"] == "authority"
        assert actual["threshold_value"] == context.threshold_value
        assert actual["comparison"] == "strict_gt"


def test_legacy_forcing_and_counts_stay_unknown(overview_case: tuple) -> None:
    candidate = _seed_diagrams(overview_case)
    conn, client, record, _, _, _ = overview_case
    conn.execute(sa.text("RESET ROLE"))
    current = conn.execute(
        sa.select(generations.c.id)
        .where(
            generations.c.model_artifact_id == UUID(candidate),
        )
        .order_by(generations.c.published_at.desc())
        .limit(1)
    ).scalar_one()
    for table in (scores, diagrams):
        conn.execute(
            sa.delete(table).where(
                table.c.model_artifact_id == UUID(candidate),
                table.c.generation_id != current,
            )
        )
        conn.execute(
            sa.update(table)
            .where(table.c.generation_id == current)
            .values(generation_id=None)
        )
    conn.execute(
        sa.delete(generations).where(generations.c.model_artifact_id == UUID(candidate))
    )
    conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
    response = client.get(
        f"/api/v1/stations/{record.station_id}/skill-diagrams",
        params=_params(record, candidate),
    )
    assert response.status_code == 200, response.text
    assert response.json()["availability"] == "available"
    assert all(
        row["generation_id"] is None
        and row["forcing_type"] is None
        and row["threshold_context"] is None
        for row in response.json()["diagrams"]
    )
