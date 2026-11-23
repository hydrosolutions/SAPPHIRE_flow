from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sapphire_flow.exceptions import ArtifactIntegrityError
from sapphire_flow.flows import fine_tuning_evaluation as module
from sapphire_flow.services.fine_tuning_evidence import RecordingReanalysisSource
from sapphire_flow.types.training import HindcastStepResult, TrainingUnit
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_models import FakeGroupForecastModel
from tests.fakes.fake_stores import FakeSkillStore
from tests.unit.services.test_fine_tuning_review import candidate


@pytest.mark.parametrize("missing_donor_skill", [False, True])
@pytest.mark.parametrize("corrupt_artifact", ["none", "candidate", "donor"])
def test_evaluation_separates_donor_and_candidate_on_requested_period(
    monkeypatch: pytest.MonkeyPatch,
    missing_donor_skill: bool,
    corrupt_artifact: str,
) -> None:
    b = candidate()
    request = b.receipt.request
    calls = []
    skill_calls = []
    sid = b.receipt.requested_stations[0]

    def hindcast(**kwargs: object) -> dict:
        calls.append(kwargs)
        return {
            sid: [HindcastStepResult(issue_time=request.evaluation_start, success=True)]
        }

    def skill(**kwargs: object) -> tuple:
        skill_calls.append(kwargs)
        if missing_donor_skill and kwargs["artifact_id"] == request.base_artifact_id:
            return [], []
        score = SimpleNamespace(
            id=uuid4(),
            generation_id=uuid4(),
            lead_time_hours=24,
            time_step_seconds=86400,
            phase_offset_seconds=None,
            metric="nse",
            score=float("nan"),
            sample_size=20,
            eval_period_start=request.evaluation_start,
            eval_period_end=request.evaluation_end,
            season=None,
            flow_regime=None,
        )
        return [score], []

    monkeypatch.setattr(module, "run_hindcast_flow", hindcast)
    monkeypatch.setattr(module, "compute_skills_task", skill)
    candidate_run, donor_run = uuid4(), uuid4()
    source = RecordingReanalysisSource(FakeWeatherReanalysisSource([]))
    kwargs = dict(
        unit=TrainingUnit(
            model_id=request.model_id,
            station_id=None,
            group_id=request.group_id,
            station_ids=b.group.station_ids,
            training_period_start=request.fitting_start,
            training_period_end=request.fitting_end,
            time_step=timedelta(days=1),
        ),
        candidate_run_id=candidate_run,
        donor_run_id=donor_run,
        model=FakeGroupForecastModel(),
        artifacts=b.artifacts,
        source=source,
        obs_store=None,
        hindcast_store=None,
        station_store=b.stations,
        group_store=b.groups,
        basin_store=None,
        skill_store=FakeSkillStore(),
        flow_regime_store=None,
        deployment_config=None,
        clock=lambda: request.evaluation_end,
    )
    if corrupt_artifact != "none":
        corrupt_id = (
            b.receipt.artifact_id
            if corrupt_artifact == "candidate"
            else request.base_artifact_id
        )
        fetch = b.artifacts.fetch_artifact
        monkeypatch.setattr(
            b.artifacts,
            "fetch_artifact",
            lambda aid: (aid, b"changed") if aid == corrupt_id else fetch(aid),
        )
        with pytest.raises(ArtifactIntegrityError, match="differs from its receipt"):
            module.evaluate_candidate(b.receipt, **kwargs)
        return
    updated, result = module.evaluate_candidate(b.receipt, **kwargs)
    assert [call["hindcast_run_id"] for call in calls] == [candidate_run, donor_run]
    assert all(call["period_start"] == request.evaluation_start for call in calls)
    assert all(call["period_end"] == request.evaluation_end for call in calls)
    assert all(call["forcing_source"] is source for call in calls)
    assert [call["artifact_id"] for call in skill_calls] == [
        b.receipt.artifact_id,
        request.base_artifact_id,
    ]
    report = json.loads(updated.evaluation_json)
    assert report["candidate"]["metrics"][0]["score"] is None
    assert result.skill_computed is (not missing_donor_skill)
    assert report["state"] == ("incomplete" if missing_donor_skill else "complete")
    assert report["comparison"].startswith("no automatic ranking")
