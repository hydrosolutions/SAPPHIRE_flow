"""Plan 405 T4 — the SERVICE half of onboarding's empty-config assertion (§ 8).

`services/model_onboarding.py` reaches training at two sites and passes the config
**POSITIONALLY**: `train_*_model(model, training_data, {}, rng)`. ⛔ The flow half
(`tests/unit/flows/test_onboarding_passes_no_config.py`) uses `params={}`, so a
recorder written for the keyword form alone would cover two sites while appearing to
cover four — the trap T4's Pre-change names explicitly.

⚠️ Both `train_*_model` and `assemble_*_training_data` are imported INSIDE
`onboard_model`, so they are patched on their SOURCE modules, not on
`model_onboarding`. Assembly is stubbed to hand back ready data: the site under test
is the train call, and building real observations would only add ways for the unit to
be skipped before reaching it.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import polars as pl

from sapphire_flow.services import training as training_mod
from sapphire_flow.services import training_data as training_data_mod
from sapphire_flow.services.model_onboarding import onboard_model
from sapphire_flow.types.enums import ArtifactScope
from sapphire_flow.types.ids import ModelId, StationGroupId
from sapphire_flow.types.model import GroupTrainingData, StationTrainingData
from sapphire_flow.types.station import StationGroup
from tests.conftest import (
    make_deployment_config,
    make_station_config,
    make_training_unit,
)
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_models import FakeGroupForecastModel, FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeFlowRegimeConfigStore,
    FakeHindcastStore,
    FakeModelArtifactStore,
    FakeModelStore,
    FakeObservationStore,
    FakeSkillStore,
    FakeStationGroupStore,
    FakeStationStore,
)

if TYPE_CHECKING:
    import pytest

_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)
_MODEL_ID = ModelId("t4_config_probe")


class _ConfigRecorder:
    """Captures the config, positionally OR by keyword."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def __call__(self, *args: Any, **kwargs: Any) -> bytes:
        self.calls.append(kwargs["params"] if "params" in kwargs else args[2])
        return b"artifact"


def _station_data() -> StationTrainingData:
    frame = pl.DataFrame({"timestamp": [_EPOCH], "discharge": [1.0]})
    return StationTrainingData(
        past_targets=frame,
        past_dynamic=pl.DataFrame({"timestamp": [_EPOCH], "precipitation": [1.0]}),
        future_dynamic=pl.DataFrame({"timestamp": [], "precipitation": []}),
        static=None,
        time_step=timedelta(days=1),
        val_start=None,
    )


_GROUP_ID = StationGroupId(UUID(int=7, version=4))


def _group_data() -> GroupTrainingData:
    sid = str(_STATION.id)
    return GroupTrainingData(
        group_id=_GROUP_ID,
        station_ids=(_STATION.id,),
        past_targets=pl.DataFrame(
            {"timestamp": [_EPOCH], "station_id": [sid], "discharge": [1.0]}
        ),
        past_dynamic=pl.DataFrame(
            {"timestamp": [_EPOCH], "station_id": [sid], "precipitation": [1.0]}
        ),
        future_dynamic=pl.DataFrame(
            {"timestamp": [], "station_id": [], "precipitation": []}
        ),
        static=None,
        time_step=timedelta(days=1),
        val_start=None,
    )


def _group_store_with_group() -> FakeStationGroupStore:
    store = FakeStationGroupStore()
    store.store_group(
        StationGroup(
            id=_GROUP_ID,
            name="T4 group",
            station_ids=frozenset({_STATION.id}),
            created_at=_EPOCH,
        )
    )
    return store


def _run_onboarding(*, model: object, unit: object, group_store: object) -> object:
    return onboard_model(
        model_id=_MODEL_ID,
        model=model,  # type: ignore[arg-type]
        units=(unit,),  # type: ignore[arg-type]
        model_store=FakeModelStore(),
        station_store=_station_store(),
        group_store=group_store,  # type: ignore[arg-type]
        artifact_store=FakeModelArtifactStore(group_store),
        obs_store=FakeObservationStore(),
        basin_store=FakeBasinStore(),
        hindcast_store=FakeHindcastStore(),
        skill_store=FakeSkillStore(),
        flow_regime_store=FakeFlowRegimeConfigStore(),
        forcing_source=FakeWeatherReanalysisSource(),
        config=make_deployment_config(),
        clock=lambda: _EPOCH,
        rng=random.Random(42),
        skip_smoke_test=True,
    )


_STATION = make_station_config(code="T4-001")


def _station_store() -> FakeStationStore:
    store = FakeStationStore()
    store.store_station(_STATION)
    return store


class TestOnboardingServiceSites:
    def test_the_station_site_passes_an_empty_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _ConfigRecorder()
        monkeypatch.setattr(training_mod, "train_station_model", recorder)
        monkeypatch.setattr(
            training_data_mod,
            "assemble_station_training_data",
            lambda **_: _station_data(),
        )

        _run_onboarding(
            model=FakeStationForecastModel(),
            unit=make_training_unit(model_id=_MODEL_ID, station_id=_STATION.id),
            group_store=FakeStationGroupStore(),
        )

        assert len(recorder.calls) == 1, (
            "the station train site was never reached — the unit was skipped "
            "earlier, so this assertion would have been vacuously true"
        )
        assert recorder.calls[0] == {}

    def test_the_group_site_passes_an_empty_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _ConfigRecorder()
        monkeypatch.setattr(training_mod, "train_group_model", recorder)
        monkeypatch.setattr(
            training_data_mod,
            "assemble_group_training_data",
            lambda **_: _group_data(),
        )
        model = FakeGroupForecastModel()
        assert model.artifact_scope is ArtifactScope.GROUP, (
            "this test must drive the GROUP branch; a station-scoped model would "
            "silently exercise the station site again"
        )

        _run_onboarding(
            model=model,
            unit=make_training_unit(model_id=_MODEL_ID, group_id=_GROUP_ID),
            group_store=_group_store_with_group(),
        )

        assert len(recorder.calls) == 1, (
            "the group train site was never reached — the unit was skipped earlier"
        )
        assert recorder.calls[0] == {}
