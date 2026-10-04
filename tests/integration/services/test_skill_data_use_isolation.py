from __future__ import annotations

import math
from dataclasses import asdict
from datetime import timedelta
from fractions import Fraction
from itertools import product
from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy import event as sa_event

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
    from collections.abc import Callable, Sequence

    from sapphire_flow.types.datetime import UtcDatetime
    from tests.integration.services.skill_isolation_fixture import (
        CleanCase,
        DiagramKind,
        GenerationMode,
        Perturbation,
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
    row: SkillScore | SkillDiagram,
    strategy: Strategy,
    generation: UUID | None,
    *,
    clock_offset_seconds: int = 0,
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
        "created_at": C + timedelta(seconds=clock_offset_seconds),
        "time_step_seconds": 3600,
        "phase_offset_seconds": 0,
        "generation_id": generation,
    }
    ignored = {"id", "season", "flow_regime"}
    if isinstance(row, SkillScore):
        common.update(
            forcing_type=ForcingType.REANALYSIS,
            computed_at=C + timedelta(seconds=clock_offset_seconds),
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
    scores: list[SkillScore],
    strategy: Strategy,
    generation: UUID | None,
    *,
    clock_offset_seconds: int = 0,
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
        assert_metadata(
            row, strategy, generation, clock_offset_seconds=clock_offset_seconds
        )
        population = len(indices(key[1]))
        assert row.sample_size == (population // 2 if strategy == "BMA" else population)
        assert_scalar(row.score, reference, strategy, row.metric)
    assert sum(math.isnan(row.score) for row in scores) == 10


def assert_diagrams(
    diagrams: list[SkillDiagram],
    strategy: Strategy,
    generation: UUID | None,
    *,
    clock_offset_seconds: int = 0,
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
        assert_metadata(
            row, strategy, generation, clock_offset_seconds=clock_offset_seconds
        )
        assert_value(
            asdict(row)["data"], expected_payload(strategy, key[1], key[2], "domain")
        )


def assert_clean_task_roundtrip(
    clean_skill_case: CleanCase,
    strategy: Strategy,
    mode: GenerationMode,
    record_property: Callable[[str, object], None],
    perturbation: Perturbation | None = None,
) -> dict[str, object]:
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
        perturbed_payload,
        regime_key,
        wire_from_domain,
    )

    case = clean_skill_case
    conn = case.connection
    record_property("owner_role", case.owner_role)
    before = conn.scalar(sa.text("SELECT current_user"))
    assert before == "sapphire_worker"
    record_property("worker_before", before)
    database_transaction_time = conn.scalar(sa.select(sa.func.transaction_timestamp()))
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
    if perturbation is None:
        assert_scores(scores, strategy, generation)
        assert_diagrams(diagrams, strategy, generation)
    else:
        assert_perturbed_scores(scores, strategy, generation, perturbation)
        assert_perturbed_diagrams(diagrams, strategy, generation, perturbation)
    persisted_scores = case.skill_store.fetch_latest_scores(
        SID, MODEL_IDS[strategy], parameter="discharge"
    )
    persisted_diagrams = case.skill_store.fetch_latest_diagrams(
        SID, MODEL_IDS[strategy], parameter="discharge"
    )
    if perturbation is None:
        assert_scores(persisted_scores, strategy, generation)
        assert_diagrams(persisted_diagrams, strategy, generation)
    else:
        assert_perturbed_scores(persisted_scores, strategy, generation, perturbation)
        assert_perturbed_diagrams(
            persisted_diagrams, strategy, generation, perturbation
        )
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
        expected = (
            expected_payload(
                strategy, regime_key(diagram.flow_regime), diagram.diagram_type, "json"
            )
            if perturbation is None
            else perturbed_payload(
                strategy,
                regime_key(diagram.flow_regime),
                diagram.diagram_type,
                perturbation,
                "json",
            )
        )
        assert_value(raw.data, expected)
        assert_same_source(
            raw.data, wire_from_domain(asdict(diagram)["data"], expected)
        )
        json.dumps(raw.data, allow_nan=False)
        undefined_counts[diagram.diagram_type] += null_count(raw.data)
    assert undefined_counts == {
        "rank_histogram": 0,
        "reliability": (68 if strategy == "SINGLE" else 66)
        if perturbation is None
        else {"SINGLE": 66, "POOLED": 64, "BMA": 60}[strategy],
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
    return {
        "scores": {
            (row.season, row.flow_regime, row.metric): {
                key: value for key, value in asdict(row).items() if key != "id"
            }
            for row in scores
        },
        "diagrams": {
            (row.season, row.flow_regime, row.diagram_type, row.threshold_level): {
                key: value for key, value in asdict(row).items() if key != "id"
            }
            for row in diagrams
        },
        "ledger": [dict(row) for row in ledger],
    }


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
        assert_clean_task_roundtrip(clean_skill_case, strategy, mode, record_property)


class TestProtectedSkillIsolation:
    @pytest.mark.parametrize("strategy", ["SINGLE", "POOLED", "BMA"])
    @pytest.mark.parametrize("mode", ["OFF", "ON"])
    def test_clean_matches_protected(
        self,
        protected_pair_connection: sa.Connection,
        strategy: Strategy,
        mode: GenerationMode,
        record_property: Callable[[str, object], None],
    ) -> None:
        from tests.integration.services.skill_isolation_fixture import (
            PROTECTED_CONTENT_HASHES,
            assert_same_source,
            protected_scenario,
        )

        outcomes: list[dict[str, object]] = []
        ordinary_snapshots: list[dict[UUID, dict[str, object]]] = []
        for presence in ("absent", "present"):
            with protected_scenario(protected_pair_connection, presence) as (
                case,
                ordinary,
            ):

                def record_scenario(
                    name: str, value: object, label: str = presence
                ) -> None:
                    record_property(f"{label}.{name}", value)

                outcomes.append(
                    assert_clean_task_roundtrip(case, strategy, mode, record_scenario)
                )
                ordinary_snapshots.append({row.id: asdict(row) for row in ordinary})
        assert_same_source(ordinary_snapshots[0], ordinary_snapshots[1])
        assert_same_source(outcomes[0], outcomes[1])
        record_property("task_calls", 2)
        record_property("ordinary_rows", 64)
        record_property("ordinary_fields_per_row", 13)
        record_property("paired_full_ordinary_equality", True)
        record_property("paired_full_output_equality_except_primary_uuid", True)
        record_property(
            "protected_lineage_fingerprints", list(PROTECTED_CONTENT_HASHES)
        )
        record_property("protected_rows", 48)
        record_property("chronological_halves", 2)
        record_property("buckets_with_all_three_intrusions", 16)
        record_property("worker_readable_measured_levels", 48)
        record_property("worker_protected_select_denied", True)


def assert_perturbed_scores(
    scores: list[SkillScore],
    strategy: Strategy,
    generation: UUID | None,
    perturbation: Perturbation,
    *,
    clock_offset_seconds: int = 0,
) -> None:
    from tests.integration.services.skill_isolation_fixture import (
        METRICS,
        PERTURBED_SCALARS,
        REGIMES,
        SCALAR_VALUES,
        perturbed_indices,
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
    keyed = {
        (row.season, regime_key(row.flow_regime), row.metric): row for row in scores
    }
    assert len(scores) == len(keyed) == (104 if strategy == "BMA" else 98)
    assert keyed.keys() == expected.keys()
    assert len({row.id for row in scores}) == len(scores)
    for key, clean_value in expected.items():
        row = keyed[key]
        assert_metadata(
            row, strategy, generation, clock_offset_seconds=clock_offset_seconds
        )
        selected = perturbed_indices(key[1], perturbation)
        fold_sizes = [sum(i // 8 == half for i in selected) for half in (0, 1)]
        assert row.sample_size == (
            sum(fold_sizes) // 2 if strategy == "BMA" else len(selected)
        )
        selected_scalars = PERTURBED_SCALARS[perturbation][strategy][key[1]]
        if row.metric in selected_scalars:
            assert_scalar(row.score, selected_scalars[row.metric], strategy, row.metric)
        elif clean_value is None:
            assert math.isnan(row.score)
        else:
            # Unselected values get exact pair/readback checks, not numeric oracles.
            assert math.isfinite(row.score)
    assert sum(math.isnan(row.score) for row in scores) == 10


def assert_perturbed_diagrams(
    diagrams: list[SkillDiagram],
    strategy: Strategy,
    generation: UUID | None,
    perturbation: Perturbation,
    *,
    clock_offset_seconds: int = 0,
) -> None:
    from tests.integration.services.skill_isolation_fixture import (
        KINDS,
        REGIMES,
        assert_value,
        perturbed_payload,
        regime_key,
    )

    expected_keys: set[tuple[str | None, RegimeKey, DiagramKind, str | None]] = {
        (season, regime, kind, None if kind == "rank_histogram" else "2")
        for season, regime, kind in product((None, "winter"), REGIMES, KINDS)
    }
    keyed = {
        (
            row.season,
            regime_key(row.flow_regime),
            row.diagram_type,
            row.threshold_level,
        ): row
        for row in diagrams
    }
    assert len(diagrams) == len(keyed) == 24
    assert keyed.keys() == expected_keys
    assert len({row.id for row in diagrams}) == 24
    for key in expected_keys:
        row = keyed[key]
        assert_metadata(
            row, strategy, generation, clock_offset_seconds=clock_offset_seconds
        )
        assert_value(
            asdict(row)["data"],
            perturbed_payload(strategy, key[1], key[2], perturbation, "domain"),
        )


class TestProtectedSkillSensitivity:
    @pytest.mark.parametrize("strategy", ["SINGLE", "POOLED", "BMA"])
    @pytest.mark.parametrize("mode", ["OFF", "ON"])
    @pytest.mark.parametrize("perturbation", ["P_FIRST", "P_SECOND"])
    def test_both_fold_controls(
        self,
        protected_pair_connection: sa.Connection,
        strategy: Strategy,
        mode: GenerationMode,
        perturbation: Perturbation,
        record_property: Callable[[str, object], None],
    ) -> None:
        from tests.integration.services.skill_isolation_fixture import (
            PROTECTED_CONTENT_HASHES,
            assert_same_source,
            protected_scenario,
        )

        def record_baseline(name: str, value: object) -> None:
            record_property(f"baseline.{name}", value)

        with protected_scenario(protected_pair_connection, "absent") as (
            case,
            ordinary,
        ):
            baseline = assert_clean_task_roundtrip(
                case, strategy, mode, record_baseline
            )
            baseline_inputs = {row.id: asdict(row) for row in ordinary}
        outcomes: list[dict[str, object]] = []
        snapshots: list[dict[UUID, dict[str, object]]] = []
        for presence in ("absent", "present"):
            with protected_scenario(
                protected_pair_connection, presence, perturbation
            ) as (case, ordinary):

                def record_scenario(
                    name: str, value: object, label: str = presence
                ) -> None:
                    record_property(f"{label}.{name}", value)

                outcomes.append(
                    assert_clean_task_roundtrip(
                        case, strategy, mode, record_scenario, perturbation
                    )
                )
                snapshots.append({row.id: asdict(row) for row in ordinary})
        assert_same_source(snapshots[0], snapshots[1])
        assert_same_source(outcomes[0], outcomes[1])
        differences = {
            key: {
                field
                for field, value in row.items()
                if snapshots[0][key][field] != value
            }
            for key, row in baseline_inputs.items()
        }
        assert sum(bool(fields) for fields in differences.values()) == 1
        assert {tuple(sorted(fields)) for fields in differences.values() if fields} == {
            ("value",)
        }
        metric = "pbias" if strategy == "POOLED" else "crps"
        baseline_scores = cast(
            "dict[tuple[object, ...], dict[str, object]]", baseline["scores"]
        )
        changed_scores = cast(
            "dict[tuple[object, ...], dict[str, object]]", outcomes[0]["scores"]
        )
        assert (
            baseline_scores[(None, None, metric)]["score"]
            != changed_scores[(None, None, metric)]["score"]
        )
        baseline_ledger = cast("list[dict[str, object]]", baseline["ledger"])
        changed_ledger = cast("list[dict[str, object]]", outcomes[0]["ledger"])
        if mode == "ON":
            assert baseline_ledger[0]["id"] != changed_ledger[0]["id"]
        else:
            assert baseline_ledger == changed_ledger == []
        record_property("task_calls", 3)
        record_property("perturbation", perturbation)
        record_property("discriminating_metric", metric)
        record_property("ordinary_changed_rows", 1)
        record_property("ordinary_changed_fields", "value only; +24 raw quarter")
        record_property("ordinary_rows", 64)
        record_property("ordinary_fields_per_row", 13)
        record_property("paired_full_ordinary_equality", True)
        record_property("paired_full_output_equality_except_primary_uuid", True)
        record_property(
            "protected_lineage_fingerprints", list(PROTECTED_CONTENT_HASHES)
        )
        record_property("protected_rows", 48)
        record_property("buckets_with_all_three_intrusions", 16)
        record_property("chronological_halves", 2)
        record_property("worker_readable_measured_levels", 48)
        record_property("worker_protected_select_denied", True)
        record_property("independent_perturbed_scalar_metrics", "crps,mae,pbias")
        record_property("same_invocation_and_clock", True)


def invoke_replay_task(
    case: CleanCase,
    strategy: Strategy,
    mode: GenerationMode,
    step: int,
    invocation: UUID,
) -> tuple[list[SkillScore], list[SkillDiagram]]:
    from sapphire_flow.types.datetime import ensure_utc
    from tests.integration.services.skill_isolation_fixture import (
        AID,
        MID,
        MID_B,
        RUN,
        RUN_B,
        SID,
        C,
        deployment,
    )

    config = deployment(mode)
    now = ensure_utc(C + timedelta(seconds=step))
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
            clock=lambda: now,
            generation_id=invocation,
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
            clock=lambda: now,
            generation_id=invocation,
        )
    return scores, diagrams


def replay_semantic_content(
    scores: list[SkillScore], diagrams: list[SkillDiagram]
) -> dict[str, object]:
    transient = {"id", "generation_id", "computed_at", "created_at"}
    return {
        "scores": {
            (row.season, row.flow_regime, row.metric): {
                key: value for key, value in asdict(row).items() if key not in transient
            }
            for row in scores
        },
        "diagrams": {
            (row.season, row.flow_regime, row.diagram_type, row.threshold_level): {
                key: value for key, value in asdict(row).items() if key not in transient
            }
            for row in diagrams
        },
    }


def replay_sql_fields(row: SkillScore | SkillDiagram) -> dict[str, object]:
    from sapphire_flow.types.enums import FlowRegime

    regimes: dict[FlowRegime | None, str | None] = {
        None: None,
        FlowRegime.LOW: "low",
        FlowRegime.HIGH: "high",
        FlowRegime.FLOOD: "flood",
    }
    assert row.skill_source is SkillSource.HINDCAST_REANALYSIS
    fields: dict[str, object] = asdict(row)
    fields["skill_source"] = "hindcast_reanalysis"
    fields["flow_regime"] = regimes[row.flow_regime]
    if isinstance(row, SkillScore):
        assert row.forcing_type is ForcingType.REANALYSIS
        assert row.freshness is SkillFreshness.CURRENT
        fields["forcing_type"] = "reanalysis"
        fields["freshness"] = "current"
    return fields


class TestSkillReplayPublication:
    @pytest.mark.parametrize("strategy", ["SINGLE", "POOLED", "BMA"])
    @pytest.mark.parametrize("mode", ["OFF", "ON"])
    def test_replay_selects_published_rows(
        self,
        protected_pair_connection: sa.Connection,
        strategy: Strategy,
        mode: GenerationMode,
        record_property: Callable[[str, object], None],
    ) -> None:
        import json

        from tests.integration.services.skill_isolation_fixture import (
            AID,
            INVOCATIONS,
            MODEL_IDS,
            SID,
            C,
            ReplayObserver,
            assert_ordinary_manifest,
            assert_same_source,
            assert_value,
            correct_replay_observation,
            expected_payload,
            fixture_json,
            null_count,
            perturbed_payload,
            regime_key,
            replay_scenario,
            replay_snapshot,
            wire_from_domain,
        )

        count = 104 if strategy == "BMA" else 98
        invocation = INVOCATIONS[strategy]
        other = UUID(int={"SINGLE": 85001, "POOLED": 85002, "BMA": 85003}[strategy])
        with replay_scenario(protected_pair_connection) as case:
            conn = case.connection
            transaction_time = conn.scalar(sa.select(sa.func.transaction_timestamp()))
            observer = ReplayObserver(case, strategy, record_property)
            raw: dict[str, dict[UUID, dict[str, object]]] = {
                name: {}
                for name in ("skill_scores", "skill_diagrams", "skill_generations")
            }
            outputs: dict[int, tuple[list[SkillScore], list[SkillDiagram]]] = {}
            generations: dict[int, UUID | None] = {}
            all_returned_ids: set[UUID] = set()
            with observer.observe():
                for step in range(5):
                    previous = replay_snapshot(case, strategy)
                    first_event = len(observer.events)
                    if step == 4:
                        correct_replay_observation(case)
                        assert len(observer.events) == first_event
                        assert_same_source(
                            replay_snapshot(case, strategy).rows, previous.rows
                        )
                    ordinary = assert_ordinary_manifest(
                        case.obs_store, "P_FIRST" if step == 4 else None
                    )
                    record_property(
                        f"step{step}.ordinary",
                        json.dumps(
                            fixture_json([asdict(row) for row in ordinary]),
                            sort_keys=True,
                        ),
                    )
                    record_property(f"step{step}.task_call_started", True)
                    scores, diagrams = invoke_replay_task(
                        case, strategy, mode, step, other if step == 2 else invocation
                    )
                    assert (
                        conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
                    )
                    record_property(f"step{step}.task_call_returned", True)
                    assert observer.pending is None
                    outputs[step] = (scores, diagrams)
                    candidates = {row.generation_id for row in [*scores, *diagrams]}
                    assert len(candidates) == 1
                    generation = candidates.pop()
                    generations[step] = generation
                    if mode == "ON":
                        assert isinstance(generation, UUID) and generation.version == 5
                        if step in (1, 3):
                            assert generation == generations[0]
                        elif step:
                            assert (
                                generation not in {generations[0], generations.get(2)}
                                if step == 4
                                else generation != generations[0]
                            )
                    else:
                        assert generation is None
                    fresh_ids = {row.id for row in [*scores, *diagrams]}
                    assert len(fresh_ids) == count + 24
                    assert not fresh_ids & all_returned_ids
                    all_returned_ids |= fresh_ids
                    if step < 4:
                        assert_scores(
                            scores, strategy, generation, clock_offset_seconds=step
                        )
                        assert_diagrams(
                            diagrams, strategy, generation, clock_offset_seconds=step
                        )
                        assert_same_source(
                            replay_semantic_content(scores, diagrams),
                            replay_semantic_content(*outputs[0]),
                        )
                    else:
                        assert_perturbed_scores(
                            scores,
                            strategy,
                            generation,
                            "P_FIRST",
                            clock_offset_seconds=step,
                        )
                        assert_perturbed_diagrams(
                            diagrams,
                            strategy,
                            generation,
                            "P_FIRST",
                            clock_offset_seconds=step,
                        )
                        metric = "pbias" if strategy == "POOLED" else "crps"
                        old_score = next(
                            row.score
                            for row in outputs[0][0]
                            if row.season is None
                            and row.flow_regime is None
                            and row.metric == metric
                        )
                        new_score = next(
                            row.score
                            for row in scores
                            if row.season is None
                            and row.flow_regime is None
                            and row.metric == metric
                        )
                        assert old_score != new_score
                    submitted_scores = {
                        row.id: replay_sql_fields(row) for row in scores
                    }
                    submitted_diagrams: dict[UUID, dict[str, object]] = {}
                    nulls = {"reliability": 0, "roc": 0, "rank_histogram": 0}
                    for diagram in diagrams:
                        expected = (
                            expected_payload(
                                strategy,
                                regime_key(diagram.flow_regime),
                                diagram.diagram_type,
                                "json",
                            )
                            if step < 4
                            else perturbed_payload(
                                strategy,
                                regime_key(diagram.flow_regime),
                                diagram.diagram_type,
                                "P_FIRST",
                                "json",
                            )
                        )
                        wire = wire_from_domain(asdict(diagram)["data"], expected)
                        assert_value(wire, expected)
                        json.dumps(wire, allow_nan=False)
                        submitted_diagrams[diagram.id] = dict(
                            replay_sql_fields(diagram), data=wire
                        )
                        nulls[diagram.diagram_type] += null_count(wire)
                    assert nulls == {
                        "rank_histogram": 0,
                        "roc": 404,
                        "reliability": (68 if strategy == "SINGLE" else 66)
                        if step < 4
                        else {"SINGLE": 66, "POOLED": 64, "BMA": 60}[strategy],
                    }
                    new_rows = step == 0 or mode == "ON" and step in (2, 4)
                    publication: dict[str, object] = {
                        "id": generation,
                        "station_id": SID,
                        "model_id": MODEL_IDS[strategy],
                        "model_artifact_id": AID if strategy == "SINGLE" else None,
                        "parameter": "discharge",
                        "skill_source": SkillSource.HINDCAST_REANALYSIS.value,
                        "forcing_type": ForcingType.REANALYSIS.value,
                        "computation_version": 2,
                        "published_at": C + timedelta(seconds=step),
                        "score_count": count,
                        "diagram_count": 24,
                    }
                    events = observer.events[first_event:]
                    expected_tables = ["skill_scores", "skill_diagrams"] + (
                        ["skill_generations"] if mode == "ON" else []
                    )
                    assert [event.table for event in events] == expected_tables
                    submissions = {
                        "skill_scores": submitted_scores,
                        "skill_diagrams": submitted_diagrams,
                    }
                    if mode == "ON":
                        assert isinstance(generation, UUID)
                        submissions["skill_generations"] = {generation: publication}
                    origin = (
                        (0 if step < 2 else 2 if step < 4 else 4) if mode == "ON" else 0
                    )
                    public_scores = {row.id: asdict(row) for row in outputs[origin][0]}
                    public_diagrams = {
                        row.id: asdict(row) for row in outputs[origin][1]
                    }
                    for event in events:
                        assert_same_source(event.before.rows, raw)
                        assert_same_source(event.submitted, submissions[event.table])
                        inserted: set[UUID] = (
                            set(submissions[event.table]) if new_rows else set()
                        )
                        assert event.inserted_ids() == inserted
                        if new_rows:
                            additions = submissions[event.table]
                            if event.table == "skill_generations":
                                additions = {
                                    key: dict(row, created_at=transaction_time)
                                    for key, row in additions.items()
                                }
                            raw[event.table].update(additions)
                        assert_same_source(event.after.rows, raw)
                        if mode == "ON":
                            assert_same_source(event.before.scores, previous.scores)
                            assert_same_source(event.before.diagrams, previous.diagrams)
                            # Already-published replays never disappear or re-promote.
                            if event.table == "skill_generations":
                                assert_same_source(event.after.scores, public_scores)
                                assert_same_source(
                                    event.after.diagrams, public_diagrams
                                )
                                candidate_scores = {
                                    key
                                    for key, row in raw["skill_scores"].items()
                                    if row["generation_id"] == generation
                                }
                                candidate_diagrams = {
                                    key
                                    for key, row in raw["skill_diagrams"].items()
                                    if row["generation_id"] == generation
                                }
                                assert (
                                    len(candidate_scores) == count
                                    and len(candidate_diagrams) == 24
                                )
                                assert candidate_scores == {
                                    row.id
                                    for row in outputs[step if new_rows else 0][0]
                                }
                                assert candidate_diagrams == {
                                    row.id
                                    for row in outputs[step if new_rows else 0][1]
                                }
                            else:
                                assert_same_source(event.before.scores, previous.scores)
                                assert_same_source(
                                    event.before.diagrams, previous.diagrams
                                )
                                assert_same_source(event.after.scores, previous.scores)
                                assert_same_source(
                                    event.after.diagrams, previous.diagrams
                                )
                                if new_rows:
                                    assert (
                                        generation
                                        not in event.after.rows["skill_generations"]
                                    )
                        record_property(
                            f"step{step}.{event.table}.submitted", len(event.submitted)
                        )
                        record_property(
                            f"step{step}.{event.table}.inserted",
                            len(event.inserted_ids()),
                        )
                        record_property(
                            f"step{step}.{event.table}.submitted_ids",
                            sorted(map(str, event.submitted)),
                        )
                        record_property(
                            f"step{step}.{event.table}.inserted_ids",
                            sorted(map(str, event.inserted_ids())),
                        )
                    final = replay_snapshot(case, strategy)
                    assert_same_source(final.rows, raw)
                    assert_same_source(final.scores, public_scores)
                    assert_same_source(final.diagrams, public_diagrams)
                    multiplier = [1, 1, 2, 2, 3][step] if mode == "ON" else 1
                    assert {name: len(rows) for name, rows in raw.items()} == {
                        "skill_scores": count * multiplier,
                        "skill_diagrams": 24 * multiplier,
                        "skill_generations": multiplier if mode == "ON" else 0,
                    }
                    candidate_totals = {
                        name: sum(
                            row["generation_id"] == generation
                            for row in raw[name].values()
                        )
                        for name in ("skill_scores", "skill_diagrams")
                    }
                    assert candidate_totals == {
                        "skill_scores": count,
                        "skill_diagrams": 24,
                    }
                    record_property(f"step{step}.candidate_totals", candidate_totals)
                    record_property(
                        f"step{step}.scope_totals",
                        {name: len(rows) for name, rows in raw.items()},
                    )
                    record_property(f"step{step}.returned_generation", str(generation))
                    record_property(f"step{step}.selected_origin", origin)
                    record_property(
                        f"step{step}.returned_clock",
                        (C + timedelta(seconds=step)).isoformat(),
                    )
                    record_property(
                        f"step{step}.ledger",
                        json.dumps(
                            fixture_json(list(raw["skill_generations"].values())),
                            sort_keys=True,
                        ),
                    )
                    record_property(
                        f"step{step}.reconciliation_source_contract_not_call_measurement",
                        "ON total-generation reconciliation"
                        if mode == "ON"
                        else "OFF not applicable",
                    )
            assert not sa_event.contains(conn, "before_cursor_execute", observer.before)
            assert not sa_event.contains(conn, "after_cursor_execute", observer.after)
            record_property("task_calls", 5)
            record_property("observed_skill_insert_statements", len(observer.events))
            record_property("ordinary_changed_rows", 1)
            record_property(
                "ordinary_changed_id", "6b9bb2f6-535a-4e07-b6df-fce8112d9d11"
            )


class TestProtectedOnlySkillInput:
    @pytest.mark.parametrize("strategy", ["SINGLE", "POOLED", "BMA"])
    @pytest.mark.parametrize("mode", ["OFF", "ON"])
    def test_protected_only_has_no_skill_output(
        self,
        protected_pair_connection: sa.Connection,
        strategy: Strategy,
        mode: GenerationMode,
        record_property: Callable[[str, object], None],
    ) -> None:
        from tests.integration.services.skill_isolation_fixture import (
            INVOCATIONS,
            ReplayObserver,
            assert_stored_skill_hindcasts,
            fixture_json,
            protected_only_scenario,
            protected_scenario,
            replay_snapshot,
        )

        def positive_record(name: str, value: object) -> None:
            record_property(f"positive.{name}", value)

        def negative_record(name: str, value: object) -> None:
            record_property(f"negative.{name}", value)

        conn = protected_pair_connection
        assert conn.scalar(sa.text("SELECT session_user")) == "test"
        assert conn.scalar(sa.text("SELECT current_user")) == "test"
        with protected_scenario(conn, "present") as (case, _ordinary):
            assert_stored_skill_hindcasts(case)
            observer = ReplayObserver(case, strategy, positive_record)
            with observer.observe():
                assert sa_event.contains(conn, "before_cursor_execute", observer.before)
                assert sa_event.contains(conn, "after_cursor_execute", observer.after)
                positive_record("listeners_attached", True)
                positive_record("call_started", True)
                assert_clean_task_roundtrip(case, strategy, mode, positive_record)
                positive_record("call_returned", True)
            expected = {
                "skill_scores": 104 if strategy == "BMA" else 98,
                "skill_diagrams": 24,
            }
            if mode == "ON":
                expected["skill_generations"] = 1
            assert [event.table for event in observer.events] == list(expected)
            assert {
                event.table: len(event.submitted) for event in observer.events
            } == expected
            assert {
                event.table: len(event.inserted_ids()) for event in observer.events
            } == expected
            positive_record("submitted", fixture_json(expected))
            positive_record("inserted", fixture_json(expected))
        assert conn.scalar(sa.text("SELECT current_user")) == "test"
        positive_record("scenario_rolled_back", True)
        with protected_only_scenario(conn, negative_record) as case:
            before = replay_snapshot(case, strategy)
            assert before.rows == {
                "skill_scores": {},
                "skill_diagrams": {},
                "skill_generations": {},
            }
            assert before.scores == before.diagrams == {}
            observer = ReplayObserver(case, strategy, negative_record)
            with observer.observe():
                assert sa_event.contains(conn, "before_cursor_execute", observer.before)
                assert sa_event.contains(conn, "after_cursor_execute", observer.after)
                negative_record("listeners_attached", True)
                negative_record("call_started", True)
                scores, diagrams = invoke_replay_task(
                    case, strategy, mode, 0, INVOCATIONS[strategy]
                )
                negative_record("call_returned", True)
            assert scores == [] and diagrams == []
            assert observer.events == []
            after = replay_snapshot(case, strategy)
            assert after.rows == before.rows
            assert after.scores == after.diagrams == {}
            zeros = {"skill_scores": 0, "skill_diagrams": 0, "skill_generations": 0}
            negative_record("returned", fixture_json({"scores": 0, "diagrams": 0}))
            negative_record("insert_events", 0)
            negative_record("submitted", fixture_json(zeros))
            negative_record("inserted", fixture_json(zeros))
            negative_record(
                "scope_rows",
                fixture_json({name: len(rows) for name, rows in after.rows.items()}),
            )
            negative_record(
                "public_rows",
                fixture_json(
                    {"scores": len(after.scores), "diagrams": len(after.diagrams)}
                ),
            )
        record_property("task_calls", 2)


def invoke_first_bucket_task(
    case: CleanCase,
    strategy: Strategy,
    mode: GenerationMode,
    now: UtcDatetime,
) -> tuple[list[SkillScore], list[SkillDiagram]]:
    from tests.integration.services.skill_isolation_fixture import (
        AID,
        INVOCATIONS,
        MID,
        MID_B,
        RUN,
        RUN_B,
        SID,
        deployment,
    )

    if strategy == "SINGLE":
        return compute_skills_task.fn(
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
            deployment_config=deployment(mode),
            clock=lambda: now,
            generation_id=INVOCATIONS[strategy],
        )
    return compute_combined_skills_task.fn(
        station_id=SID,
        parameter="discharge",
        strategy=ModelCombinationStrategy[strategy],
        hindcast_run_ids={MID: RUN, MID_B: RUN_B},
        hindcast_store=case.hindcast_store,
        obs_store=case.obs_store,
        skill_store=case.skill_store,
        station_store=case.station_store,
        flow_regime_store=case.flow_regime_store,
        deployment_config=deployment(mode),
        clock=lambda: now,
        generation_id=INVOCATIONS[strategy],
    )


def first_bucket_evidence(
    rows: Sequence[SkillScore | SkillDiagram],
) -> dict[str, object]:
    allowed: set[tuple[int | str, ...]] = set()
    for index, row in enumerate(rows):
        if isinstance(row, SkillScore):
            if row.metric in {
                "nse",
                "kge",
                "bss_danger_2",
                "pod_danger_2",
                "far_danger_2",
                "csi_danger_2",
            }:
                allowed.add((index, "score"))
        elif row.diagram_type == "reliability":
            allowed.update(
                (index, "data", "observed_freq", slot) for slot in range(1, 10)
            )
        elif row.diagram_type == "roc":
            allowed.update((index, "data", "hit_rate", slot) for slot in range(101))
    visited: set[tuple[int | str, ...]] = set()

    def project(value: object, path: tuple[int | str, ...]) -> object:
        if path in allowed:
            assert isinstance(value, float) and math.isnan(value)
            visited.add(path)
            return {"$nonfinite": "float_nan"}
        if isinstance(value, float):
            assert math.isfinite(value), "Unexpected nonfinite evidence value"
        if isinstance(value, dict):
            mapping = cast("dict[str, object]", value)
            assert all(isinstance(key, str) for key in mapping)
            return {key: project(item, (*path, key)) for key, item in mapping.items()}
        if isinstance(value, list):
            return [
                project(item, (*path, index))
                for index, item in enumerate(cast("list[object]", value))
            ]
        return value

    records = project([asdict(row) for row in rows], ())
    assert visited == allowed
    return {
        "encoding": "first-bucket-nan-tags-v1",
        "records": records,
        "nonfinite": [
            {"path": list(path), "type": "float_nan"}
            for path in sorted(visited, key=repr)
        ],
    }


def assert_first_bucket_roundtrip(
    case: CleanCase,
    scores: list[SkillScore],
    diagrams: list[SkillDiagram],
    mode: GenerationMode,
    record: Callable[[str, object], None],
) -> UUID | None:
    from sapphire_flow.types.enums import FlowRegime
    from tests.integration.services.skill_isolation_fixture import (
        AID,
        FIRST_BUCKET_END,
        FIRST_BUCKET_METRICS,
        INVOCATIONS,
        KINDS,
        MID,
        SID,
        assert_same_source,
        assert_value,
        first_bucket_payload,
        fixture_json,
        null_count,
        wire_from_domain,
    )

    assert len(scores) == 48 and len(diagrams) == 12
    generations = {row.generation_id for row in [*scores, *diagrams]}
    assert len(generations) == 1
    generation = next(iter(generations))
    if mode == "ON":
        assert isinstance(generation, UUID) and generation.version == 5
        assert generation != INVOCATIONS["SINGLE"]
    else:
        assert generation is None
    expected_keys = {
        (season, regime, metric)
        for season, regime, metric in product(
            (None, "winter"),
            (None, FlowRegime.LOW),
            FIRST_BUCKET_METRICS,
        )
    }
    keyed_scores = {(row.season, row.flow_regime, row.metric): row for row in scores}
    assert keyed_scores.keys() == expected_keys
    assert len({row.id for row in scores}) == 48
    for row in scores:
        assert_metadata(row, "SINGLE", generation, clock_offset_seconds=-136800)
        assert row.sample_size == 1
        assert_scalar(row.score, FIRST_BUCKET_METRICS[row.metric], "SINGLE", row.metric)
    assert sum(math.isnan(row.score) for row in scores) == 24
    expected_diagrams = {
        (season, regime, kind, None if kind == "rank_histogram" else "2")
        for season, regime, kind in product(
            (None, "winter"), (None, FlowRegime.LOW), KINDS
        )
    }
    keyed_diagrams = {
        (row.season, row.flow_regime, row.diagram_type, row.threshold_level): row
        for row in diagrams
    }
    assert keyed_diagrams.keys() == expected_diagrams
    assert len({row.id for row in diagrams}) == 12
    for row in diagrams:
        assert_metadata(row, "SINGLE", generation, clock_offset_seconds=-136800)
        assert_value(
            asdict(row)["data"], first_bucket_payload(row.diagram_type, "domain")
        )
    persisted_scores = case.skill_store.fetch_latest_scores(
        SID, MID, parameter="discharge"
    )
    persisted_diagrams = case.skill_store.fetch_latest_diagrams(
        SID, MID, parameter="discharge"
    )
    assert_same_source(
        {row.id: asdict(row) for row in persisted_scores},
        {row.id: asdict(row) for row in scores},
    )
    assert_same_source(
        {row.id: asdict(row) for row in persisted_diagrams},
        {row.id: asdict(row) for row in diagrams},
    )
    raw_scores = case.connection.execute(
        sa.select(db.skill_scores.c.id, db.skill_scores.c.score)
    ).all()
    scores_by_id = {row.id: row for row in scores}
    assert (
        len(raw_scores) == 48 and {row.id for row in raw_scores} == scores_by_id.keys()
    )
    for row in raw_scores:
        assert_same_source(row.score, scores_by_id[row.id].score)
    assert sum(math.isnan(row.score) for row in raw_scores) == 24
    raw_diagrams = case.connection.execute(
        sa.select(db.skill_diagrams.c.id, db.skill_diagrams.c.data)
    ).all()
    diagrams_by_id = {row.id: row for row in diagrams}
    assert (
        len(raw_diagrams) == 12
        and {row.id for row in raw_diagrams} == diagrams_by_id.keys()
    )
    undefined = {"rank_histogram": 0, "reliability": 0, "roc": 0}
    for raw in raw_diagrams:
        row = diagrams_by_id[raw.id]
        expected = first_bucket_payload(row.diagram_type, "json")
        assert_value(raw.data, expected)
        assert_same_source(raw.data, wire_from_domain(asdict(row)["data"], expected))
        undefined[row.diagram_type] += null_count(raw.data)
    assert undefined == {"rank_histogram": 0, "reliability": 36, "roc": 404}
    ledger = [
        dict(row)
        for row in case.connection.execute(sa.select(db.skill_generations)).mappings()
    ]
    if mode == "ON":
        assert ledger == [
            {
                "id": generation,
                "station_id": SID,
                "model_id": MID,
                "model_artifact_id": AID,
                "parameter": "discharge",
                "skill_source": SkillSource.HINDCAST_REANALYSIS.value,
                "forcing_type": ForcingType.REANALYSIS.value,
                "computation_version": 2,
                "published_at": FIRST_BUCKET_END,
                "created_at": case.connection.scalar(
                    sa.select(sa.func.transaction_timestamp())
                ),
                "score_count": 48,
                "diagram_count": 12,
            }
        ]
    else:
        assert ledger == []
    record("scores", 48)
    record("diagrams", 12)
    record("scalar_nan_count", 24)
    record("diagram_nulls", fixture_json(undefined))
    record(
        "score_sample_sizes", fixture_json(sorted({row.sample_size for row in scores}))
    )
    record("generation", str(generation) if generation is not None else "NULL")
    record("ledger", fixture_json(ledger))
    record("scores_full", fixture_json(first_bucket_evidence(scores)))
    record("diagrams_full", fixture_json(first_bucket_evidence(diagrams)))
    return generation


class TestFirstCompletedSkillBucket:
    @pytest.mark.parametrize("strategy", ["SINGLE", "POOLED", "BMA"])
    @pytest.mark.parametrize("mode", ["OFF", "ON"])
    def test_first_completed_bucket_boundary(
        self,
        protected_pair_connection: sa.Connection,
        strategy: Strategy,
        mode: GenerationMode,
        record_property: Callable[[str, object], None],
    ) -> None:
        from tests.integration.services.skill_isolation_fixture import (
            FIRST_BUCKET_BEFORE,
            FIRST_BUCKET_END,
            MODEL_IDS,
            SID,
            C,
            ReplayObserver,
            assert_first_bucket_inputs,
            fixture_json,
            protected_scenario,
            replay_snapshot,
        )

        scenarios = [("full", C), ("before", FIRST_BUCKET_BEFORE)]
        if strategy == "SINGLE":
            scenarios.append(("at", FIRST_BUCKET_END))
        full_generation: UUID | None = None
        conn = protected_pair_connection
        for label, now in scenarios:

            def record(name: str, value: object, prefix: str = label) -> None:
                record_property(f"{prefix}.{name}", value)

            assert conn.scalar(sa.text("SELECT session_user")) == "test"
            assert conn.scalar(sa.text("SELECT current_user")) == "test"
            record("owner_session_user", "test")
            record("owner_current_user", "test")
            with protected_scenario(conn, "present") as (case, ordinary):
                assert_first_bucket_inputs(case)
                record(
                    "ordinary_manifest", fixture_json([asdict(row) for row in ordinary])
                )
                record("worker_before", "sapphire_worker")
                record("ordinary_rows", 64)
                record("water_level_rows", 48)
                record("hindcast_headers", 32)
                record("hindcast_members", 64)
                record("clock", now.isoformat())
                before = replay_snapshot(case, strategy)
                assert before.rows == {
                    "skill_scores": {},
                    "skill_diagrams": {},
                    "skill_generations": {},
                }
                assert before.scores == before.diagrams == {}
                task_result: tuple[list[SkillScore], list[SkillDiagram]] | None = None
                observer = ReplayObserver(case, strategy, record)
                with observer.observe():
                    assert sa_event.contains(
                        conn, "before_cursor_execute", observer.before
                    )
                    assert sa_event.contains(
                        conn, "after_cursor_execute", observer.after
                    )
                    record("listeners_attached", True)
                    record("call_started", True)
                    if label == "full":
                        assert_clean_task_roundtrip(case, strategy, mode, record)
                        full_generation = case.skill_store.fetch_latest_scores(
                            SID,
                            MODEL_IDS[strategy],
                            parameter="discharge",
                        )[0].generation_id
                    else:
                        task_result = invoke_first_bucket_task(
                            case, strategy, mode, now
                        )
                    record("call_returned", True)
                if label == "before":
                    assert task_result == ([], [])
                    expected: dict[str, int] = {}
                elif label == "at":
                    assert task_result is not None
                    generation = assert_first_bucket_roundtrip(
                        case, task_result[0], task_result[1], mode, record
                    )
                    if mode == "ON":
                        assert generation != full_generation
                    expected = {"skill_scores": 48, "skill_diagrams": 12}
                else:
                    expected = {
                        "skill_scores": 104 if strategy == "BMA" else 98,
                        "skill_diagrams": 24,
                    }
                if label != "before" and mode == "ON":
                    expected["skill_generations"] = 1
                assert [event.table for event in observer.events] == list(expected)
                assert {
                    event.table: len(event.submitted) for event in observer.events
                } == expected
                assert {
                    event.table: len(event.inserted_ids()) for event in observer.events
                } == expected
                after = replay_snapshot(case, strategy)
                counts = {table: len(rows) for table, rows in after.rows.items()}
                assert counts == {
                    table: expected.get(table, 0) for table in before.rows
                }
                if label == "before":
                    assert after.scores == after.diagrams == {}
                    record("returned", fixture_json({"scores": 0, "diagrams": 0}))
                record(
                    "submitted",
                    fixture_json(
                        {table: expected.get(table, 0) for table in before.rows}
                    ),
                )
                record(
                    "inserted",
                    fixture_json(
                        {table: expected.get(table, 0) for table in before.rows}
                    ),
                )
                record("scope_rows", fixture_json(counts))
                record(
                    "public_rows",
                    fixture_json(
                        {"scores": len(after.scores), "diagrams": len(after.diagrams)}
                    ),
                )
                assert_first_bucket_inputs(case)
                record("worker_after", "sapphire_worker")
            assert conn.scalar(sa.text("SELECT current_user")) == "test"
            record("scenario_rolled_back", True)
        record_property("task_calls", len(scenarios))
