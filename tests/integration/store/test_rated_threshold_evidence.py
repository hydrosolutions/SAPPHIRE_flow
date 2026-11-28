from __future__ import annotations

import json
from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from sapphire_flow.api.routes.api_review import get_rated_threshold_context
from sapphire_flow.api.security import Principal
from sapphire_flow.db.metadata import skill_generations
from sapphire_flow.store.skill_store import PgSkillStore
from sapphire_flow.types.enums import AccessTokenRole, ForcingType, SkillSource
from sapphire_flow.types.ids import AccessTokenId, HindcastForecastId, StationId
from sapphire_flow.types.native_capture import Sha256Digest
from sapphire_flow.types.rated_threshold_skill import (
    RatedThresholdCompatibility,
    RatedThresholdEvaluation,
)
from sapphire_flow.types.rating_curve import (
    RatingCurve,  # noqa: TC001 -- pytest fixture
)
from sapphire_flow.types.rating_reference import curve_snapshot
from tests.integration.store.test_skill_store import _seed_model, _seed_station
from tests.unit.services.skill.test_rated_thresholds import (
    STAMP,
    manifest,
)
from tests.unit.services.skill.test_rated_thresholds import (
    curve as curve,
)


def test_generation_evidence_roundtrip_replay_and_scoped_api(
    db_connection: sa.Connection, curve: RatingCurve
) -> None:
    sid, mid, generation = (
        _seed_station(db_connection),
        _seed_model(db_connection),
        uuid4(),
    )
    m = replace(
        manifest([curve]),
        station_id=sid,
        curves=(curve_snapshot(replace(curve, station_id=sid)),),
    )
    receipt = RatedThresholdCompatibility(
        operator="test",
        verified_at=STAMP,
        consumers=tuple(
            (name, "test-schema-2-image")
            for name in ("api", "forecast", "scoring", "review", "rollback")
        ),
    )
    evidence = RatedThresholdEvaluation(
        manifest=m,
        compatibility=receipt,
        input_sha256=Sha256Digest(value="b" * 64),
        hindcast_count=1,
        hindcast_ids=(HindcastForecastId(uuid4()),),
        hindcast_run_ids=(uuid4(),),
        issue_start=STAMP,
        issue_end=STAMP,
        observation_count=1,
        diagnostics=(),
    )
    store = PgSkillStore(db_connection)
    args = dict(
        generation_id=generation,
        station_id=sid,
        model_id=mid,
        model_artifact_id=None,
        parameter="discharge",
        skill_source=SkillSource.HINDCAST_REANALYSIS,
        forcing_type=ForcingType.REANALYSIS,
        computation_version=4,
        published_at=STAMP,
        score_count=0,
        diagram_count=0,
        threshold_evaluation=evidence,
    )
    store.publish_generation(**args)
    store.publish_generation(**args)
    assert store.fetch_threshold_evaluation(generation) == evidence
    assert (
        db_connection.execute(
            sa.select(skill_generations.c.evaluation_contexts).where(
                skill_generations.c.id == generation
            )
        ).scalar_one()
        is None
    )
    with pytest.raises(ValueError, match="conflicts"):
        store.publish_generation(
            **(args | {"threshold_evaluation": replace(evidence, observation_count=2)})
        )
    principal = Principal(
        token_id=AccessTokenId(uuid4()),
        role=AccessTokenRole.REVIEWER,
        tenant_id=None,
        station_ids=frozenset({sid}),
    )
    response = get_rated_threshold_context(
        str(sid), generation, principal, db_connection
    )
    body = json.loads(response.body)
    assert body["manifest_sha256"] == m.digest.value
    assert body["reference_basis"] == "owner_assumed_same_zero_unverified"
    assert "points" not in body["curves"][0]
    assert body["curves"][0]["deliveries"] == [[str(curve.id), 1]]
    with pytest.raises(HTTPException) as denied:
        get_rated_threshold_context(str(uuid4()), generation, principal, db_connection)
    assert denied.value.status_code == 404
    other = StationId(uuid4())
    broad = replace(principal, station_ids=frozenset({sid, other}))
    with pytest.raises(HTTPException) as absent:
        get_rated_threshold_context(str(other), generation, broad, db_connection)
    assert absent.value.status_code == 404
