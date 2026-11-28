from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.compute_skills import compute_skills_task
from sapphire_flow.store.rated_threshold_codec import (
    parse_rated_evaluation,
    rated_evaluation_content,
)
from sapphire_flow.store.threshold_skill_codec import (
    decode_threshold_context,
    encode_threshold_context,
)
from sapphire_flow.types.ids import ArtifactId, ModelId
from sapphire_flow.types.rated_threshold_skill import RatedThresholdCompatibility
from sapphire_flow.types.rating_curve import (
    RatingCurve,  # noqa: TC001 -- pytest fixture annotation
)
from tests.conftest import make_deployment_config, make_station_config
from tests.unit.flows.test_compute_skills import _EPOCH, _populate_stores
from tests.unit.services.skill.test_rated_thresholds import (
    STATION,
    manifest,
)
from tests.unit.services.skill.test_rated_thresholds import (
    curve as curve,
)


@pytest.fixture
def inputs(curve: RatingCurve) -> dict[str, Any]:
    mid, aid = ModelId("test"), ArtifactId(UUID(int=20))
    hc, obs, skills, stations, regimes, run = _populate_stores(STATION, mid, aid)
    m = manifest([curve], level=1.1)
    stations.store_station(
        make_station_config(
            station_id=STATION,
            code=m.station_code,
            network=m.network,
            tenant_id=m.tenant_id,
        )
    )
    receipt = RatedThresholdCompatibility(
        operator="test",
        verified_at=_EPOCH,
        consumers=tuple(
            (name, "test-image-schema-2")
            for name in ("api", "forecast", "scoring", "review", "rollback")
        ),
    )
    return dict(
        station_id=STATION,
        model_id=mid,
        artifact_id=aid,
        parameter="discharge",
        hindcast_run_id=run,
        hindcast_store=hc,
        obs_store=obs,
        skill_store=skills,
        station_store=stations,
        flow_regime_store=regimes,
        deployment_config=make_deployment_config(enable_skill_generations=True),
        clock=lambda: _EPOCH,
        rated_thresholds=m,
        rated_compatibility=receipt,
    )


def test_complete_generation_keeps_ordinary_scores_and_roundtrips_evidence(
    inputs: dict[str, Any],
) -> None:
    scores, diagrams = compute_skills_task.fn(**inputs)
    assert "mae" in {score.metric for score in scores}
    assert "brier_stage_equivalent_warning" in {score.metric for score in scores}
    current = inputs["skill_store"].fetch_latest_scores(
        STATION, inputs["model_id"], parameter="discharge"
    )
    assert len(current) == len(scores)
    context = next(
        score.threshold_context for score in scores if score.threshold_context
    )
    assert decode_threshold_context(encode_threshold_context(context)) == context
    assert all(
        item.threshold_context == context for item in diagrams if item.threshold_context
    )
    evaluation = inputs["skill_store"].fetch_threshold_evaluation(
        current[0].generation_id
    )
    assert parse_rated_evaluation(rated_evaluation_content(evaluation)) == evaluation
    assert evaluation.hindcast_count == 3


def test_dry_run_has_no_score_or_generation_writes(inputs: dict[str, Any]) -> None:
    inputs.update(rated_dry_run=True, rated_compatibility=None)
    scores, _ = compute_skills_task.fn(**inputs)
    assert scores
    assert (
        inputs["skill_store"].fetch_latest_scores(
            STATION, inputs["model_id"], parameter="discharge"
        )
        == []
    )


def test_invalid_storage_envelope_is_rejected_before_any_write(
    inputs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject(value: object) -> None:
        raise ValueError("rated threshold evaluation exceeds 2 MiB")

    def write(scores: object) -> None:
        pytest.fail("skill writes must follow the storage-envelope preflight")

    monkeypatch.setattr(
        "sapphire_flow.flows.compute_skills.rated_evaluation_content", reject
    )
    monkeypatch.setattr(inputs["skill_store"], "store_skill_scores", write)
    with pytest.raises(ValueError, match="2 MiB"):
        compute_skills_task.fn(**inputs)


@pytest.mark.parametrize("change", ["receipt", "tenant", "generations", "artifact"])
def test_admission_refuses_unqualified_writes(
    inputs: dict[str, Any], change: str
) -> None:
    if change == "receipt":
        inputs["rated_compatibility"] = None
    elif change == "tenant":
        inputs["rated_thresholds"] = replace(
            inputs["rated_thresholds"], network="different"
        )
    elif change == "artifact":
        inputs["artifact_id"] = ArtifactId(UUID(int=9876))
    else:
        inputs["deployment_config"] = make_deployment_config(
            enable_skill_generations=False
        )
    with pytest.raises(ConfigurationError, match="rated"):
        compute_skills_task.fn(**inputs)
    assert (
        inputs["skill_store"].fetch_latest_scores(
            STATION, inputs["model_id"], parameter="discharge"
        )
        == []
    )


def test_append_during_scoring_preserves_previous_generation(
    inputs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    compute_skills_task.fn(**inputs)
    store = inputs["skill_store"]
    previous = store.fetch_latest_scores(
        STATION, inputs["model_id"], parameter="discharge"
    )
    original = inputs["obs_store"].fetch_observations
    calls = 0

    def fetch(**kwargs: Any) -> list[Any]:
        nonlocal calls
        calls += 1
        values = original(**kwargs)
        return (
            [replace(item, value=item.value + 1) for item in values]
            if calls > 1
            else values
        )

    monkeypatch.setattr(inputs["obs_store"], "fetch_observations", fetch)
    with pytest.raises(ConfigurationError, match="inputs changed"):
        compute_skills_task.fn(**inputs)
    assert (
        store.fetch_latest_scores(STATION, inputs["model_id"], parameter="discharge")
        == previous
    )


def test_no_resolved_threshold_preserves_reason_and_ordinary_metrics(
    inputs: dict[str, Any],
) -> None:
    inputs["rated_thresholds"] = replace(
        inputs["rated_thresholds"],
        thresholds=(replace(inputs["rated_thresholds"].thresholds[0], value=50.0),),
    )
    scores, _ = compute_skills_task.fn(**inputs)
    assert scores and all(score.threshold_context is None for score in scores)
    evaluation = inputs["skill_store"].fetch_threshold_evaluation(
        scores[0].generation_id
    )
    assert evaluation.diagnostics[0].context.exclusions == (
        ("threshold_outside_rating_range", 3),
    )


def test_context_rejects_false_event_support(inputs: dict[str, Any]) -> None:
    scores, _ = compute_skills_task.fn(**inputs)
    encoded = encode_threshold_context(
        next(score.threshold_context for score in scores if score.threshold_context)
    )
    encoded["event_support"] = "at_least_ten_each"
    with pytest.raises(ValueError, match="event-support"):
        decode_threshold_context(encoded)


def test_exact_storage_payload_obeys_reader_size_limit(inputs: dict[str, Any]) -> None:
    scores, _ = compute_skills_task.fn(**inputs)
    original = inputs["skill_store"].fetch_threshold_evaluation(scores[0].generation_id)
    diagnostic = original.diagnostics[0]
    low, high = 1, 10000
    while high - low > 1:
        size = (low + high) // 2
        try:
            replace(original, diagnostics=(diagnostic,) * size)
        except ValueError as error:
            assert "2 MiB" in str(error)
            high = size
        else:
            low = size
    near_limit = replace(original, diagnostics=(diagnostic,) * low)
    with pytest.raises(ValueError, match="2 MiB"):
        rated_evaluation_content(near_limit)
    admitted = replace(original, diagnostics=(diagnostic,) * (low - 100))
    assert parse_rated_evaluation(rated_evaluation_content(admitted)) == admitted


def test_declared_period_and_displaced_reference_survive_scoring(
    inputs: dict[str, Any],
) -> None:
    from datetime import timedelta

    from sapphire_flow.types.datetime import ensure_utc
    from tests.conftest import make_observation
    from tests.fakes.fake_stores import FakeHindcastStore, FakeObservationStore
    from tests.unit.services.test_hindcast_verification import _hindcast

    original = _hindcast()
    hindcast = replace(
        original,
        station_id=STATION,
        ensemble=replace(original.ensemble, station_id=STATION),
        source_periods=tuple(
            replace(source, station_id=STATION) for source in original.source_periods
        ),
    )
    hc, obs = FakeHindcastStore(), FakeObservationStore()
    hc.store_hindcast(hindcast)
    obs.store_observations(
        [
            make_observation(
                station_id=STATION,
                timestamp=ensure_utc(
                    hindcast.ensemble.values["valid_time"][0] + timedelta(minutes=15)
                ),
                value=12.0,
            )
        ]
    )
    inputs.update(
        hindcast_store=hc,
        obs_store=obs,
        model_id=hindcast.model_id,
        artifact_id=hindcast.model_artifact_id,
        hindcast_run_id=hindcast.hindcast_run_id,
        clock=lambda: hindcast.created_at,
        reference_displacement=timedelta(minutes=15),
    )
    scores, _ = compute_skills_task.fn(**inputs)
    rated = next(score for score in scores if score.threshold_context)
    assert rated.threshold_context.temporal_basis == "declared_period"
    assert rated.evaluation_context.reference_displacement == timedelta(minutes=15)
    assert (
        rated.evaluation_context.forecast_period == hindcast.ensemble.period_definition
    )


def test_unavailable_threshold_diagnostic_identifies_its_period_cohort(
    inputs: dict[str, Any], curve: RatingCurve
) -> None:
    import random
    from datetime import timedelta
    from uuid import uuid4

    import polars as pl

    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.ids import HindcastForecastId
    from sapphire_flow.types.rating_reference import curve_snapshot
    from tests.conftest import make_observation
    from tests.fakes.fake_stores import FakeHindcastStore, FakeObservationStore
    from tests.unit.services.test_hindcast_verification import _hindcast

    first = _hindcast()
    first = replace(
        first,
        station_id=STATION,
        ensemble=replace(first.ensemble, station_id=STATION),
        source_periods=tuple(
            replace(source, station_id=STATION) for source in first.source_periods
        ),
    )
    day = timedelta(days=1)
    second = replace(
        first,
        id=HindcastForecastId(uuid4()),
        hindcast_step=ensure_utc(first.hindcast_step + day),
        ensemble=replace(
            first.ensemble,
            issued_at=ensure_utc(first.hindcast_step + day),
            values=first.ensemble.values.with_columns(pl.col("valid_time") + day),
        ),
        source_periods=tuple(
            replace(source, evidence="second documented delivery")
            for source in first.source_periods
        ),
    )
    hc, obs = FakeHindcastStore(), FakeObservationStore()
    for index, item in enumerate((first, second)):
        hc.store_hindcast(item)
        obs.store_observations(
            [
                make_observation(
                    station_id=STATION,
                    timestamp=ensure_utc(
                        item.ensemble.values["valid_time"][0] + timedelta(minutes=15)
                    ),
                    value=12.0,
                    rng=random.Random(index),
                )
            ]
        )
    end = ensure_utc(
        first.ensemble.values["valid_time"][0] + day + timedelta(minutes=15)
    )
    inputs.update(
        hindcast_store=hc,
        obs_store=obs,
        model_id=first.model_id,
        artifact_id=first.model_artifact_id,
        hindcast_run_id=first.hindcast_run_id,
        clock=lambda: first.created_at,
        reference_displacement=timedelta(minutes=15),
        rated_thresholds=replace(
            inputs["rated_thresholds"],
            curves=(curve_snapshot(replace(curve, valid_to=end)),),
        ),
    )
    scores, _ = compute_skills_task.fn(**inputs)
    evaluation = inputs["skill_store"].fetch_threshold_evaluation(
        scores[0].generation_id
    )
    assert len(evaluation.evaluation_contexts) == 2
    assert len({item.evaluation_context_sha256 for item in evaluation.diagnostics}) == 2
    assert sorted(item.context.scored_count for item in evaluation.diagnostics) == [
        0,
        1,
    ]
    assert parse_rated_evaluation(rated_evaluation_content(evaluation)) == evaluation
