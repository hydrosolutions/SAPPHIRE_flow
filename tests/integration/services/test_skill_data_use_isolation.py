from __future__ import annotations

import math
from dataclasses import asdict
from datetime import timedelta
from fractions import Fraction
from itertools import product
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
import sqlalchemy as sa

from sapphire_flow.db import metadata as db
from sapphire_flow.flows.compute_skills import (
    compute_combined_skills_task,
    compute_skills_task,
)
from sapphire_flow.types.enums import (
    ForcingType,
    ModelCombinationStrategy,
    SkillFreshness,
    SkillSource,
)
from sapphire_flow.types.skill import SkillDiagram, SkillScore

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.integration.services.skill_isolation_fixture import (
        CleanCase,
        DiagramKind,
        GenerationMode,
        RegimeKey,
        Strategy,
    )

pytest_plugins = (
    "tests.integration.services.skill_persistence_fixture",
    "tests.integration.services.skill_isolation_fixture",
)


def assert_scalar(
    actual: float, reference: str | None, strategy: Strategy, metric: str
) -> None:
    if reference is None:
        assert math.isnan(actual)
        return
    expected = float(Fraction(reference))
    assert math.isfinite(actual)
    exact = (
        metric
        in {
            "mae",
            "sharpness_p10_p90",
            "sharpness_p25_p75",
            "ensemble_range",
            "peak_timing_error",
        }
        or metric == "crps"
        and strategy != "BMA"
        or metric in {"pod_danger_2", "far_danger_2", "csi_danger_2"}
        and reference in {"0", "1"}
    )
    if exact:
        assert actual == expected
    else:
        assert abs(actual - expected) <= max(1e-12, 1e-12 * abs(expected))


def assert_metadata(
    row: SkillScore | SkillDiagram, strategy: Strategy, generation: UUID | None
) -> None:
    from tests.integration.services.skill_isolation_fixture import (
        AID,
        MODEL_IDS,
        REGIME,
        SID,
        C,
        T,
    )

    assert isinstance(row.id, UUID) and row.id.version == 4
    common: dict[str, object] = {
        "station_id": SID,
        "model_id": MODEL_IDS[strategy],
        "parameter": "discharge",
        "model_artifact_id": AID if strategy == "SINGLE" else None,
        "skill_source": SkillSource.HINDCAST_REANALYSIS,
        "computation_version": 2,
        "lead_time_hours": 1,
        "flow_regime_config_id": REGIME,
        "eval_period_start": T,
        "eval_period_end": T + timedelta(hours=30),
        "created_at": C,
        "time_step_seconds": 3600,
        "phase_offset_seconds": 0,
        "generation_id": generation,
    }
    ignored = {"id", "season", "flow_regime"}
    if isinstance(row, SkillScore):
        common.update(
            forcing_type=ForcingType.REANALYSIS,
            computed_at=C,
            freshness=SkillFreshness.CURRENT,
        )
        if strategy == "BMA":
            # Current first-evaluation-fold bounds, not endorsed union-score metadata.
            common["eval_period_start"] = T + timedelta(hours=16)
        ignored |= {"metric", "score", "sample_size"}
    else:
        ignored |= {"diagram_type", "threshold_level", "data"}
    assert {k: v for k, v in asdict(row).items() if k not in ignored} == common


def assert_scores(
    scores: list[SkillScore], strategy: Strategy, generation: UUID | None
) -> None:
    from tests.integration.services.skill_isolation_fixture import (
        METRICS,
        REGIMES,
        SCALAR_VALUES,
        indices,
        regime_key,
    )

    expected: dict[tuple[str | None, RegimeKey, str], str | None] = {
        (season, regime, metric): value
        for season, regime in product((None, "winter"), REGIMES)
        for metric, value in zip(
            METRICS[: len(SCALAR_VALUES[strategy][regime])],
            SCALAR_VALUES[strategy][regime],
            strict=True,
        )
    }
    keyed = {(s.season, regime_key(s.flow_regime), s.metric): s for s in scores}
    assert len(scores) == (104 if strategy == "BMA" else 98)
    assert len(keyed) == len(scores)
    assert keyed.keys() == expected.keys()
    assert len({s.id for s in scores}) == len(scores)
    for key, reference in expected.items():
        row = keyed[key]
        assert_metadata(row, strategy, generation)
        population = len(indices(key[1]))
        assert row.sample_size == (population // 2 if strategy == "BMA" else population)
        assert_scalar(row.score, reference, strategy, row.metric)
    assert sum(math.isnan(row.score) for row in scores) == 10


def assert_diagrams(
    diagrams: list[SkillDiagram], strategy: Strategy, generation: UUID | None
) -> None:
    from tests.integration.services.skill_isolation_fixture import (
        KINDS,
        REGIMES,
        assert_value,
        expected_payload,
        regime_key,
    )

    expected_keys: set[tuple[str | None, RegimeKey, DiagramKind, str | None]] = {
        (season, regime, kind, None if kind == "rank_histogram" else "2")
        for season, regime, kind in product((None, "winter"), REGIMES, KINDS)
    }
    actual = {
        (
            d.season,
            regime_key(d.flow_regime),
            d.diagram_type,
            d.threshold_level,
        ): d
        for d in diagrams
    }
    assert len(diagrams) == len(actual) == 24
    assert len({d.id for d in diagrams}) == 24
    assert actual.keys() == expected_keys
    for key in expected_keys:
        row = actual[key]
        assert_metadata(row, strategy, generation)
        assert_value(
            asdict(row)["data"], expected_payload(strategy, key[1], key[2], "domain")
        )


class TestCleanSkillBaseline:
    @pytest.mark.parametrize("strategy", ["SINGLE", "POOLED", "BMA"])
    @pytest.mark.parametrize("mode", ["OFF", "ON"])
    def test_roundtrips_clean_baseline(
        self,
        clean_skill_case: CleanCase,
        strategy: Strategy,
        mode: GenerationMode,
        record_property: Callable[[str, object], None],
    ) -> None:
        from tests.integration.services.skill_isolation_fixture import (
            AID,
            INVOCATIONS,
            MID,
            MID_B,
            MODEL_IDS,
            RUN,
            RUN_B,
            SID,
            C,
            assert_same_source,
            assert_value,
            deployment,
            expected_payload,
            null_count,
            regime_key,
            wire_from_domain,
        )

        case = clean_skill_case
        conn = case.connection
        record_property("owner_role", case.owner_role)
        before = conn.scalar(sa.text("SELECT current_user"))
        assert before == "sapphire_worker"
        record_property("worker_before", before)
        database_transaction_time = conn.scalar(
            sa.select(sa.func.transaction_timestamp())
        )
        config = deployment(mode)
        if strategy == "SINGLE":
            scores, diagrams = compute_skills_task.fn(
                station_id=SID,
                model_id=MID,
                artifact_id=AID,
                parameter="discharge",
                hindcast_run_id=RUN,
                hindcast_store=case.hindcast_store,
                obs_store=case.obs_store,
                skill_store=case.skill_store,
                station_store=case.station_store,
                flow_regime_store=case.flow_regime_store,
                deployment_config=config,
                clock=lambda: C,
                generation_id=INVOCATIONS[strategy],
            )
        else:
            scores, diagrams = compute_combined_skills_task.fn(
                station_id=SID,
                parameter="discharge",
                strategy=ModelCombinationStrategy[strategy],
                hindcast_run_ids={MID: RUN, MID_B: RUN_B},
                hindcast_store=case.hindcast_store,
                obs_store=case.obs_store,
                skill_store=case.skill_store,
                station_store=case.station_store,
                flow_regime_store=case.flow_regime_store,
                deployment_config=config,
                clock=lambda: C,
                generation_id=INVOCATIONS[strategy],
            )
        generations = {row.generation_id for row in [*scores, *diagrams]}
        assert len(generations) == 1
        generation = next(iter(generations))
        if mode == "ON":
            assert isinstance(generation, UUID) and generation.version == 5
            assert generation != INVOCATIONS[strategy]
        else:
            assert generation is None
        assert_scores(scores, strategy, generation)
        assert_diagrams(diagrams, strategy, generation)
        persisted_scores = case.skill_store.fetch_latest_scores(
            SID, MODEL_IDS[strategy], parameter="discharge"
        )
        persisted_diagrams = case.skill_store.fetch_latest_diagrams(
            SID, MODEL_IDS[strategy], parameter="discharge"
        )
        assert_scores(persisted_scores, strategy, generation)
        assert_diagrams(persisted_diagrams, strategy, generation)
        # Preserve full records: UUIDs, metadata and nonfinite positions.
        assert_same_source(
            {s.id: asdict(s) for s in persisted_scores},
            {s.id: asdict(s) for s in scores},
        )
        assert_same_source(
            {d.id: asdict(d) for d in persisted_diagrams},
            {d.id: asdict(d) for d in diagrams},
        )
        raw_scores = conn.execute(
            sa.select(db.skill_scores.c.id, db.skill_scores.c.score)
        ).all()
        assert len(raw_scores) == len(scores)
        assert {row.id for row in raw_scores} == {row.id for row in scores}
        returned_scores = {row.id: row.score for row in scores}
        for raw in raw_scores:
            assert_same_source(raw.score, returned_scores[raw.id])
        assert sum(math.isnan(raw.score) for raw in raw_scores) == 10
        raw_diagrams = conn.execute(
            sa.select(db.skill_diagrams.c.id, db.skill_diagrams.c.data)
        ).all()
        assert len(raw_diagrams) == 24
        returned_diagrams = {d.id: d for d in diagrams}
        assert {r.id for r in raw_diagrams} == returned_diagrams.keys()
        undefined_counts = {"reliability": 0, "roc": 0, "rank_histogram": 0}
        for raw in raw_diagrams:
            import json

            diagram = returned_diagrams[raw.id]
            expected = expected_payload(
                strategy,
                regime_key(diagram.flow_regime),
                diagram.diagram_type,
                "json",
            )
            assert_value(raw.data, expected)
            assert_same_source(
                raw.data, wire_from_domain(asdict(diagram)["data"], expected)
            )
            json.dumps(raw.data, allow_nan=False)
            undefined_counts[diagram.diagram_type] += null_count(raw.data)
        assert undefined_counts == {
            "rank_histogram": 0,
            "reliability": 68 if strategy == "SINGLE" else 66,
            "roc": 404,
        }
        ledger = conn.execute(sa.select(db.skill_generations)).mappings().all()
        if mode == "ON":
            assert len(ledger) == 1
            assert dict(ledger[0]) == {
                "id": generation,
                "station_id": SID,
                "model_id": MODEL_IDS[strategy],
                "model_artifact_id": AID if strategy == "SINGLE" else None,
                "parameter": "discharge",
                "skill_source": SkillSource.HINDCAST_REANALYSIS.value,
                "forcing_type": ForcingType.REANALYSIS.value,
                "computation_version": 2,
                "published_at": C,
                "created_at": database_transaction_time,
                "score_count": len(scores),
                "diagram_count": 24,
            }
        else:
            assert not ledger
        after = conn.scalar(sa.text("SELECT current_user"))
        assert after == "sapphire_worker"
        record_property("worker_after", after)
        record_property("scores", len(scores))
        record_property("diagrams", len(diagrams))
        record_property("scalar_nan_count", 10)
        record_property("generation_mode", mode)
