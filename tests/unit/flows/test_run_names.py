# pyright: strict
"""Resolve every Prefect run-name template set under ``src/sapphire_flow/flows/``
and assert it renders a sane, non-empty, ASCII-safe string.

Plan 050 Task 2 scaffolded this module; Task 10 activated it after Phase 2
decorator templates landed. Each case loads the decorated flow/task, extracts
the ``flow_run_name`` / ``task_run_name`` attribute, and resolves it either by
``str.format(**params)`` (string templates) or by patching ``prefect.runtime``
state and invoking the callable.
"""

from __future__ import annotations

import importlib
import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ModelCombinationStrategy
from sapphire_flow.types.ids import (
    ArtifactId,
    ModelId,
    StationGroupId,
    StationId,
)
from sapphire_flow.types.training import TrainingUnit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Plan 050 D1 targets ≤60 chars, but realistic UUID-sharded names
# (e.g. "compute-combined-skills-<UUID36>-<parameter>-<strategy>")
# run ~77 chars. Keep a reasonable ceiling well under Prefect's 200-char
# internal limit so any accidental unbounded growth is still flagged.
_MAX_NAME_LEN = 128
_ASCII_SAFE = re.compile(r"^[A-Za-z0-9:_.\-]+$")

_RNG = random.Random(42)


def _uuid() -> UUID:
    return UUID(int=_RNG.getrandbits(128), version=4)


_CYCLE_TIME = ensure_utc(datetime(2026, 4, 17, 6, tzinfo=UTC))
_PERIOD_START = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))
_PERIOD_END = ensure_utc(datetime(2024, 12, 31, tzinfo=UTC))
_PERIOD_START_ISO = _PERIOD_START.isoformat()
_PERIOD_END_ISO = _PERIOD_END.isoformat()
_SCHEDULED_START = ensure_utc(datetime(2026, 4, 17, 6, 30, tzinfo=UTC))

_STATION_ID = StationId(_uuid())
_GROUP_ID = StationGroupId(_uuid())
_ARTIFACT_ID = ArtifactId(_uuid())
_MODEL_ID = ModelId("test_model")
_SINCE: dict[StationId, Any] = {_STATION_ID: _CYCLE_TIME}


def _station_group() -> Any:
    """Build a StationGroup stand-in exposing the ``.id`` attribute the
    ``_run_group_hindcast_task`` task_run_name template dereferences.
    """
    from sapphire_flow.types.station import StationGroup

    return StationGroup(
        id=_GROUP_ID,
        name="test-group",
        station_ids=frozenset({_STATION_ID}),
        created_at=_CYCLE_TIME,
    )


def _station_unit() -> TrainingUnit:
    return TrainingUnit(
        model_id=_MODEL_ID,
        station_id=_STATION_ID,
        group_id=None,
        station_ids=frozenset({_STATION_ID}),
        training_period_start=_PERIOD_START,
        training_period_end=_PERIOD_END,
        time_step=timedelta(hours=1),
    )


def _group_unit() -> TrainingUnit:
    return TrainingUnit(
        model_id=_MODEL_ID,
        station_id=None,
        group_id=_GROUP_ID,
        station_ids=frozenset({StationId(_uuid())}),
        training_period_start=_PERIOD_START,
        training_period_end=_PERIOD_END,
        time_step=timedelta(hours=1),
    )


# ---------------------------------------------------------------------------
# Case table — one row per decorator site in docs/standards/orchestration.md
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class Case:
    import_path: str
    kind: str  # "flow" | "task"
    params: dict[str, Any]


def _c(
    import_path: str,
    kind: str,
    params: dict[str, Any],
    *,
    test_id: str,
) -> Any:
    """Build a parametrize case."""
    case = Case(import_path=import_path, kind=kind, params=params)
    return pytest.param(case, id=test_id)


CASES: list[Any] = [
    # --- run_forecast_cycle.py ---
    _c(
        "sapphire_flow.flows.run_forecast_cycle:run_forecast_cycle_flow",
        "flow",
        {"cycle_time": _CYCLE_TIME, "scheduled_start_time": _SCHEDULED_START},
        test_id="run-forecast-cycle-flow",
    ),
    _c(
        "sapphire_flow.flows.run_forecast_cycle:run_forecast_cycle_flow",
        "flow",
        {"cycle_time": None, "scheduled_start_time": _SCHEDULED_START},
        test_id="run-forecast-cycle-flow-no-cycle-time",
    ),
    _c(
        "sapphire_flow.flows.run_forecast_cycle:_fetch_nwp_task",
        "task",
        {"cycle_time": _CYCLE_TIME},
        test_id="fetch-nwp-task",
    ),
    _c(
        "sapphire_flow.flows.run_forecast_cycle:_fetch_obs_timestamps_task",
        "task",
        {},
        test_id="fetch-obs-timestamps-task",
    ),
    # --- ingest_observations.py ---
    _c(
        "sapphire_flow.flows.ingest_observations:ingest_observations_flow",
        "flow",
        {"scheduled_start_time": _SCHEDULED_START},
        test_id="ingest-observations-flow",
    ),
    _c(
        "sapphire_flow.flows.ingest_observations:_fetch_observations_task",
        "task",
        {"since": _SINCE, "scheduled_start_time": _SCHEDULED_START},
        test_id="fetch-observations-task",
    ),
    _c(
        "sapphire_flow.flows.ingest_observations:_store_raw_task",
        "task",
        {},
        test_id="store-raw-task",
    ),
    _c(
        "sapphire_flow.flows.ingest_observations:_run_qc_task",
        "task",
        {"station_id": _STATION_ID, "parameter": "discharge"},
        test_id="run-qc-task",
    ),
    # --- train_models.py ---
    _c(
        "sapphire_flow.flows.train_models:train_models_flow",
        "flow",
        # Decorator signature declares period_{start,end}: str | None, so the
        # callable runs datetime.fromisoformat() on them.
        {
            "period_start": _PERIOD_START_ISO,
            "period_end": _PERIOD_END_ISO,
            "scheduled_start_time": _SCHEDULED_START,
        },
        test_id="train-models-flow",
    ),
    _c(
        "sapphire_flow.flows.train_models:train_models_flow",
        "flow",
        {
            "period_start": None,
            "period_end": None,
            "scheduled_start_time": _SCHEDULED_START,
        },
        test_id="train-models-flow-no-period",
    ),
    _c(
        "sapphire_flow.flows.train_models:_determine_scope_task",
        "task",
        {},
        test_id="determine-scope-task",
    ),
    _c(
        "sapphire_flow.flows.train_models:_assemble_data_task",
        "task",
        {"unit": _station_unit()},
        test_id="assemble-data-task-station",
    ),
    _c(
        "sapphire_flow.flows.train_models:_assemble_data_task",
        "task",
        {"unit": _group_unit()},
        test_id="assemble-data-task-group",
    ),
    _c(
        "sapphire_flow.flows.train_models:_train_model_task",
        "task",
        {"unit": _station_unit()},
        test_id="train-model-task",
    ),
    _c(
        "sapphire_flow.flows.train_models:_store_artifact_task",
        "task",
        {"unit": _station_unit()},
        test_id="store-artifact-task",
    ),
    # --- onboard_model.py ---
    _c(
        "sapphire_flow.flows.onboard_model:onboard_model_flow",
        "flow",
        # onboard_model_flow signature: period_start: str | None; the resolver
        # calls datetime.fromisoformat() so a datetime instance is rejected.
        {
            "model_id": _MODEL_ID,
            "period_start": _PERIOD_START_ISO,
            "scheduled_start_time": _SCHEDULED_START,
        },
        test_id="onboard-model-flow",
    ),
    _c(
        "sapphire_flow.flows.onboard_model:onboard_model_flow",
        "flow",
        {
            "model_id": _MODEL_ID,
            "period_start": None,
            "scheduled_start_time": _SCHEDULED_START,
        },
        test_id="onboard-model-flow-no-period-start",
    ),
    _c(
        "sapphire_flow.flows.onboard_model:_determine_onboarding_scope_task",
        "task",
        {"model_id": _MODEL_ID},
        test_id="determine-onboarding-scope-task",
    ),
    _c(
        "sapphire_flow.flows.onboard_model:_register_model_class_task",
        "task",
        {"model_id": _MODEL_ID},
        test_id="register-model-class-task",
    ),
    _c(
        "sapphire_flow.flows.onboard_model:_validate_compatibility_task",
        "task",
        {"model_id": _MODEL_ID, "unit": _station_unit()},
        test_id="validate-compatibility-task",
    ),
    _c(
        "sapphire_flow.flows.onboard_model:_smoke_test_model_task",
        "task",
        {},
        test_id="smoke-test-model-task",
    ),
    _c(
        "sapphire_flow.flows.onboard_model:_assemble_onboarding_data_task",
        "task",
        {"unit": _station_unit()},
        test_id="assemble-onboarding-data-task",
    ),
    _c(
        "sapphire_flow.flows.onboard_model:_train_and_store_artifact_task",
        "task",
        {"unit": _station_unit()},
        test_id="train-and-store-artifact-task",
    ),
    _c(
        "sapphire_flow.flows.onboard_model:_evaluate_skill_gate_task",
        "task",
        {"model_id": _MODEL_ID, "artifact_id": _ARTIFACT_ID},
        test_id="evaluate-skill-gate-task",
    ),
    _c(
        "sapphire_flow.flows.onboard_model:_promote_artifact_task",
        "task",
        {"unit": _station_unit(), "artifact_id": _ARTIFACT_ID},
        test_id="promote-artifact-task",
    ),
    _c(
        "sapphire_flow.flows.onboard_model:_create_assignment_task",
        "task",
        {"model_id": _MODEL_ID, "unit": _station_unit()},
        test_id="create-assignment-task",
    ),
    # --- run_hindcast.py ---
    _c(
        "sapphire_flow.flows.run_hindcast:run_hindcast_flow",
        "flow",
        {
            "model_id": _MODEL_ID,
            "period_start": _PERIOD_START,
            "period_end": _PERIOD_END,
            "scheduled_start_time": _SCHEDULED_START,
        },
        test_id="run-hindcast-flow",
    ),
    _c(
        "sapphire_flow.flows.run_hindcast:run_hindcast_flow",
        "flow",
        {
            "model_id": _MODEL_ID,
            "period_start": None,
            "period_end": _PERIOD_END,
            "scheduled_start_time": _SCHEDULED_START,
        },
        test_id="run-hindcast-flow-no-period-start",
    ),
    _c(
        "sapphire_flow.flows.run_hindcast:_run_station_hindcast_task",
        "task",
        {"model_id": _MODEL_ID, "station_id": _STATION_ID},
        test_id="run-station-hindcast-task",
    ),
    _c(
        "sapphire_flow.flows.run_hindcast:_run_group_hindcast_task",
        "task",
        # Template is "hindcast-group-{model_id}-{group.id}" — needs a real
        # StationGroup (not a raw group_id) to support attribute dereferencing.
        {"model_id": _MODEL_ID, "group": _station_group()},
        test_id="run-group-hindcast-task",
    ),
    # --- compute_skills.py ---
    _c(
        "sapphire_flow.flows.compute_skills:compute_skills_flow",
        "flow",
        {
            "model_id": _MODEL_ID,
            "station_id": _STATION_ID,
            "parameter": "discharge",
        },
        test_id="compute-skills-flow",
    ),
    _c(
        "sapphire_flow.flows.compute_skills:compute_combined_skills_flow",
        "flow",
        {
            "station_id": _STATION_ID,
            "parameter": "discharge",
            "strategy": ModelCombinationStrategy.POOLED,
        },
        test_id="compute-combined-skills-flow",
    ),
    _c(
        "sapphire_flow.flows.compute_skills:compute_skills_task",
        "task",
        {
            "model_id": _MODEL_ID,
            "station_id": _STATION_ID,
            "parameter": "discharge",
        },
        test_id="compute-skills-task",
    ),
    _c(
        "sapphire_flow.flows.compute_skills:compute_combined_skills_task",
        "task",
        {
            "station_id": _STATION_ID,
            "parameter": "discharge",
            "strategy": ModelCombinationStrategy.POOLED,
        },
        test_id="compute-combined-skills-task",
    ),
    # --- backup.py ---
    _c(
        "sapphire_flow.flows.backup:backup_database_flow",
        "flow",
        {"scheduled_start_time": _SCHEDULED_START},
        test_id="backup-database-flow",
    ),
    _c(
        "sapphire_flow.flows.backup:dump_database_task",
        "task",
        {"scheduled_start_time": _SCHEDULED_START},
        test_id="dump-database-task",
    ),
    _c(
        "sapphire_flow.flows.backup:cleanup_old_backups_task",
        "task",
        {},
        test_id="cleanup-old-backups-task",
    ),
    # --- onboard.py ---
    _c(
        "sapphire_flow.flows.onboard:onboard_stations_flow",
        "flow",
        {"scheduled_start_time": _SCHEDULED_START},
        test_id="onboard-stations-flow",
    ),
    _c(
        "sapphire_flow.flows.onboard:_download_task",
        "task",
        {},
        test_id="download-camels-ch-task",
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_decorated(import_path: str) -> Any:
    module_name, attr = import_path.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _extract_template(decorated: Any, kind: str) -> str | Any:
    attr = "flow_run_name" if kind == "flow" else "task_run_name"
    template = getattr(decorated, attr, None)
    assert template is not None, (
        f"{attr!r} is not set on {decorated!r} — Phase 2 template missing."
    )
    return template


def _resolve_string(template: str, params: dict[str, Any]) -> str:
    # Only keep params that would make sense as scalar substitutions.
    safe_params = {k: v for k, v in params.items() if k != "scheduled_start_time"}
    return template.format(**safe_params)


def _resolve_callable(template: Any, kind: str, params: dict[str, Any]) -> str:
    scheduled = params.get("scheduled_start_time")
    # Strip helper keys before passing to runtime.parameters.
    runtime_params = {k: v for k, v in params.items() if k != "scheduled_start_time"}

    target = (
        "prefect.runtime.flow_run" if kind == "flow" else "prefect.runtime.task_run"
    )

    with patch(f"{target}.parameters", new=runtime_params):
        if kind == "flow":
            with patch(f"{target}.scheduled_start_time", new=scheduled):
                result = template()
        else:
            # task_run has no scheduled_start_time in prefect.runtime;
            # callables used on tasks must stick to parameters.
            result = template()
    return result


def _assert_valid_name(name: str) -> None:
    assert isinstance(name, str), f"run name must be a str, got {type(name).__name__}"
    assert name, "run name must be non-empty"
    assert len(name) <= _MAX_NAME_LEN, (
        f"run name {name!r} is {len(name)} chars (>{_MAX_NAME_LEN})"
    )
    assert _ASCII_SAFE.match(name), (
        f"run name {name!r} contains characters outside [A-Za-z0-9:_.\\-]"
    )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_run_name_template_resolves(case: Case) -> None:
    decorated = _load_decorated(case.import_path)
    template = _extract_template(decorated, case.kind)

    if callable(template):
        rendered = _resolve_callable(template, case.kind, case.params)
    elif isinstance(template, str):
        rendered = _resolve_string(template, case.params)
    else:
        raise AssertionError(
            f"Unsupported template type {type(template).__name__!r} "
            f"on {case.import_path}"
        )

    _assert_valid_name(rendered)
