from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import numpy as np
import pytest

from sapphire_flow.services.skill.diagrams import (
    compute_reliability_from_probabilities,
    compute_roc_from_probabilities,
)
from sapphire_flow.services.skill.metrics import (
    compute_brier_from_probabilities,
    compute_bss_from_probabilities,
    compute_contingency_from_probabilities,
)
from sapphire_flow.services.skill.quantiles import QuantileSample
from sapphire_flow.services.skill.thresholds import paired_threshold_probabilities
from sapphire_flow.store.threshold_skill_codec import (
    decode_threshold_context,
    encode_threshold_context,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import StationThreshold
from sapphire_flow.types.enums import ThresholdSource
from sapphire_flow.types.ids import StationId


def threshold() -> StationThreshold:
    now = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
    return StationThreshold(
        station_id=StationId(uuid4()),
        danger_level="High",
        parameter="discharge",
        value=5,
        source=ThresholdSource.AUTHORITY,
        created_at=now,
        updated_at=now,
    )


def quantiles(low: float, high: float) -> QuantileSample:
    return QuantileSample(
        probabilities=(0.05, 0.5, 0.95), values=(low, (low + high) / 2, high)
    )


class TestProbabilityMetrics:
    def test_hand_worked_probabilities(self) -> None:
        probabilities = np.array([0.25, 0.75])
        events = np.array([0, 1])
        assert compute_brier_from_probabilities(probabilities, events) == 0.0625
        assert compute_bss_from_probabilities(probabilities, events) == 0.75
        assert compute_contingency_from_probabilities(probabilities, events, 0.5) == (
            1,
            0,
            1,
        )
        reliability = compute_reliability_from_probabilities(probabilities, events)
        assert sum(reliability["sample_counts"]) == 2
        roc = compute_roc_from_probabilities(probabilities, events)
        assert roc["n_events"] == roc["n_non_events"] == 1

    @pytest.mark.parametrize(
        "events", [np.array([]), np.array([0, 0]), np.array([1, 1])]
    )
    def test_zero_reference_variance_is_undefined(self, events: np.ndarray) -> None:
        assert math.isnan(
            compute_bss_from_probabilities(np.full(len(events), 0.5), events)
        )

    def test_invalid_pairs_rejected(self) -> None:
        with pytest.raises(ValueError, match="paired"):
            compute_brier_from_probabilities(np.array([0.1]), np.array([]))
        with pytest.raises(ValueError, match="binary"):
            compute_brier_from_probabilities(np.array([0.1]), np.array([2]))


class TestThresholdPairs:
    def test_tail_dates_excluded_without_observation_selection(self) -> None:
        probs, events, context = paired_threshold_probabilities(
            [quantiles(0, 10), quantiles(10, 20), quantiles(0, 2)],
            [6, 0, 10],
            threshold(),
            "m3/s",
        )
        assert probs.tolist() == [0.5]
        assert events.tolist() == [True]
        assert (context.paired_count, context.scored_count, context.excluded_count) == (
            3,
            1,
            2,
        )
        assert (
            context.paired_event_count,
            context.scored_event_count,
            context.scored_non_event_count,
        ) == (2, 1, 0)
        assert context.coverage == "conditional_resolved_subset"
        assert decode_threshold_context(encode_threshold_context(context)) == context

    def test_variable_member_counts_are_not_truncated(self) -> None:
        probs, _, context = paired_threshold_probabilities(
            [np.array([0.0, 10.0]), np.array([0.0, 0.0, 0.0, 10.0])],
            [0, 10],
            threshold(),
            "m3/s",
        )
        assert probs.tolist() == [0.5, 0.25]
        assert context.coverage == "all_paired"

    def test_no_resolved_dates_are_explicit(self) -> None:
        probabilities, events, context = paired_threshold_probabilities(
            [quantiles(0, 1)], [10], threshold(), "m3/s"
        )
        assert len(probabilities) == len(events) == 0
        assert context.coverage == "no_resolved_probabilities"
        assert context.paired_event_count == 1

    def test_context_rejects_inconsistent_counts(self) -> None:
        _, _, context = paired_threshold_probabilities(
            [np.array([0.0, 10.0])], [10], threshold(), "m3/s"
        )
        with pytest.raises(ValueError, match="inconsistent"):
            replace(context, excluded_count=2)

    def test_old_context_is_absent_not_fabricated(self) -> None:
        assert decode_threshold_context(None) is None


class TestThresholdFingerprint:
    def test_context_only_change_changes_score_and_diagram_identity(self) -> None:
        from datetime import timedelta

        from sapphire_flow.services.skill.service import compute_generation_fingerprint
        from sapphire_flow.types.ids import ArtifactId, ModelId
        from tests.integration.store.test_skill_store import _make_diagram, _make_score

        t = threshold()
        _, _, context = paired_threshold_probabilities(
            [np.array([0.0, 10.0])], [10.0], t, "m3/s"
        )
        mid = ModelId("fingerprint")
        aid = ArtifactId(uuid4())
        score = replace(_make_score(t.station_id, mid, aid), threshold_context=context)
        diagram = replace(
            _make_diagram(t.station_id, mid, aid), threshold_context=context
        )
        changed = replace(
            context,
            threshold_updated_at=ensure_utc(
                context.threshold_updated_at + timedelta(seconds=1)
            ),
        )
        assert compute_generation_fingerprint(
            [score], []
        ) != compute_generation_fingerprint(
            [replace(score, threshold_context=changed)], []
        )
        assert compute_generation_fingerprint(
            [], [diagram]
        ) != compute_generation_fingerprint(
            [], [replace(diagram, threshold_context=changed)]
        )


class TestThresholdMetricActivationBoundary:
    def test_threshold_specific_metric_is_not_an_automatic_gate_option(self) -> None:
        from pydantic import ValidationError

        from sapphire_flow.types.model_onboarding import SkillGateMetric
        from tests.conftest import make_deployment_config

        with pytest.raises(ValidationError, match="Unknown skill gate metric"):
            make_deployment_config(
                skill_gate_thresholds={
                    "bss_danger_High": SkillGateMetric(
                        threshold=0.5, higher_is_better=True
                    )
                }
            )
