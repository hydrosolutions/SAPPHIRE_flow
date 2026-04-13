from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from sapphire_flow.services.skill.bma_weights import compute_bma_weights
from sapphire_flow.types.enums import (
    ForcingType,
    SkillFreshness,
    SkillSource,
)
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.skill import SkillScore

_STATION_ID = StationId(UUID("00000000-0000-0000-0000-000000000001"))
_MODEL_A = ModelId("model_a")
_MODEL_B = ModelId("model_b")
_T0 = datetime(2024, 1, 1, tzinfo=UTC)
_T1 = datetime(2024, 1, 2, tzinfo=UTC)


def _make_score(
    model_id: ModelId,
    score: float,
    freshness: SkillFreshness = SkillFreshness.CURRENT,
    lead_time_hours: int = 24,
    computed_at: datetime = _T0,
    metric: str = "crps",
) -> SkillScore:
    return SkillScore(
        id=uuid4(),
        station_id=_STATION_ID,
        model_id=model_id,
        parameter="discharge",
        model_artifact_id=None,
        skill_source=SkillSource.HINDCAST_NWP_ARCHIVE,
        forcing_type=ForcingType.NWP_ARCHIVE,
        computation_version=1,
        computed_at=computed_at,
        lead_time_hours=lead_time_hours,
        season=None,
        flow_regime=None,
        flow_regime_config_id=None,
        metric=metric,
        score=score,
        sample_size=100,
        freshness=freshness,
        eval_period_start=_T0,
        eval_period_end=_T1,
        created_at=_T0,
    )


class TestComputeBmaWeights:
    def test_two_models_different_crps(self) -> None:
        # Model A CRPS=2.0 → raw=0.5, Model B CRPS=4.0 → raw=0.25
        # weights: A=0.5/0.75≈2/3, B=0.25/0.75≈1/3
        scores = {
            _MODEL_A: [_make_score(_MODEL_A, 2.0)],
            _MODEL_B: [_make_score(_MODEL_B, 4.0)],
        }
        weights = compute_bma_weights(_STATION_ID, "discharge", scores)
        assert set(weights) == {_MODEL_A, _MODEL_B}
        assert weights[_MODEL_A] == pytest.approx(2 / 3, rel=1e-6)
        assert weights[_MODEL_B] == pytest.approx(1 / 3, rel=1e-6)
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_single_model_gets_weight_one(self) -> None:
        scores = {_MODEL_A: [_make_score(_MODEL_A, 3.0)]}
        weights = compute_bma_weights(_STATION_ID, "discharge", scores)
        assert weights == {_MODEL_A: 1.0}

    def test_model_with_no_scores_gets_zero(self) -> None:
        # Model B has no matching skill scores
        scores = {
            _MODEL_A: [_make_score(_MODEL_A, 2.0)],
            _MODEL_B: [],
        }
        weights = compute_bma_weights(_STATION_ID, "discharge", scores)
        assert weights[_MODEL_A] == pytest.approx(1.0)
        assert weights[_MODEL_B] == pytest.approx(0.0)

    def test_no_scores_returns_empty_dict(self) -> None:
        weights = compute_bma_weights(_STATION_ID, "discharge", {})
        assert weights == {}

    def test_all_models_no_matching_scores_returns_empty(self) -> None:
        scores = {_MODEL_A: [], _MODEL_B: []}
        weights = compute_bma_weights(_STATION_ID, "discharge", scores)
        assert weights == {}

    def test_lead_time_filtering(self) -> None:
        # Model A has score at lead_time=24, Model B at lead_time=48
        # When filtering for lead_time=24, only Model A should be used
        scores = {
            _MODEL_A: [_make_score(_MODEL_A, 2.0, lead_time_hours=24)],
            _MODEL_B: [_make_score(_MODEL_B, 4.0, lead_time_hours=48)],
        }
        weights = compute_bma_weights(
            _STATION_ID, "discharge", scores, lead_time_hours=24
        )
        assert weights[_MODEL_A] == pytest.approx(1.0)
        assert weights[_MODEL_B] == pytest.approx(0.0)

    def test_freshness_filtering_excludes_stale(self) -> None:
        # Model B only has a STALE score → should get weight 0.0
        scores = {
            _MODEL_A: [_make_score(_MODEL_A, 2.0, freshness=SkillFreshness.CURRENT)],
            _MODEL_B: [_make_score(_MODEL_B, 1.0, freshness=SkillFreshness.STALE)],
        }
        weights = compute_bma_weights(_STATION_ID, "discharge", scores)
        assert weights[_MODEL_A] == pytest.approx(1.0)
        assert weights[_MODEL_B] == pytest.approx(0.0)

    def test_most_recent_score_used_when_multiple_match(self) -> None:
        # Model A has two current scores; the one with later computed_at should be used
        old_score = _make_score(_MODEL_A, 10.0, computed_at=_T0)
        new_score = _make_score(_MODEL_A, 2.0, computed_at=_T1)
        scores = {
            _MODEL_A: [old_score, new_score],
            _MODEL_B: [_make_score(_MODEL_B, 4.0, computed_at=_T0)],
        }
        weights = compute_bma_weights(_STATION_ID, "discharge", scores)
        # If old (CRPS=10) were used: A raw=0.1, B raw=0.25 → A≈0.286
        # If new (CRPS=2) were used: A raw=0.5, B raw=0.25 → A≈0.667
        assert weights[_MODEL_A] == pytest.approx(2 / 3, rel=1e-6)

    def test_metric_matching_is_case_insensitive(self) -> None:
        scores = {
            _MODEL_A: [_make_score(_MODEL_A, 2.0, metric="CRPS")],
            _MODEL_B: [_make_score(_MODEL_B, 4.0, metric="crps")],
        }
        weights = compute_bma_weights(_STATION_ID, "discharge", scores, metric="crps")
        assert set(weights) == {_MODEL_A, _MODEL_B}
        assert sum(weights.values()) == pytest.approx(1.0)
