from typing import Literal
from uuid import uuid4

import pytest

from sapphire_flow.services.daily_group_provenance import capture_daily_group_provenance
from sapphire_flow.services.run_group_forecast import GroupForecastOutcome
from sapphire_flow.types.enums import NwpCycleSource
from sapphire_flow.types.ids import StationId
from tests.fakes.fake_stores import FakeModelArtifactStore
from tests.unit.services.test_run_group_forecast import (
    _CYCLE,
    _BatchGroupModel,
    _call_run_group_forecast_outcome,
    _make_ensemble,
    _make_group,
    _make_group_inputs,
    _make_metadata,
    _QcChecker,
    _seed_group_artifact,
)


class TestCaptureDailyGroupProvenance:
    @pytest.mark.parametrize("case", ["accepted", "rejected", "missing"])
    def test_retains_captured_age_and_does_not_invent_quality(
        self, case: Literal["accepted", "rejected", "missing"]
    ) -> None:
        sid = StationId(uuid4())
        group = _make_group(sid)
        inputs = _make_group_inputs(group)
        metadata = {sid: _make_metadata(7.5)}
        artifacts = FakeModelArtifactStore()
        _seed_group_artifact(artifacts, group)
        result = GroupForecastOutcome(results={})
        if case != "missing":
            result = _call_run_group_forecast_outcome(
                group=group,
                group_inputs=inputs,
                metadata_by_station=metadata,
                model=_BatchGroupModel(
                    {sid: ({"discharge": _make_ensemble(sid, 10.0)}, None)}
                ),
                artifact_store=artifacts,
                qc_checker=_QcChecker(sid if case == "rejected" else None),
            )
        captured = capture_daily_group_provenance(
            result=result,
            metadata_by_station=metadata,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            nwp_cycle_reference_time=_CYCLE,
        )[sid]
        assert captured.nwp_age_hours == 7.5
        assert captured.nwp_reference_time == _CYCLE
        assert (
            captured.observation_staleness_hours
            == metadata[sid].observation_staleness_hours
        )
        if case == "accepted":
            forecast = result.results[sid].forecasts[0]
            assert captured.input_quality == forecast.input_quality
            assert captured.input_quality_flags == forecast.input_quality_flags
        else:
            assert captured.input_quality is None
            assert captured.input_quality_flags == ()

    def test_runoff_only_has_no_weather_age(self) -> None:
        sid = StationId(uuid4())
        captured = capture_daily_group_provenance(
            result=GroupForecastOutcome(results={}),
            metadata_by_station={sid: _make_metadata(7.5)},
            nwp_cycle_source=NwpCycleSource.RUNOFF_ONLY,
            nwp_cycle_reference_time=None,
        )[sid]
        assert captured.nwp_age_hours is None
        assert captured.nwp_reference_time is None
