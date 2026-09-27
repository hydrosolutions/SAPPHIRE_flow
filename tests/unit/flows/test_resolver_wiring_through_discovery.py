"""Plan 405 T5 — the station-code resolver wiring, through REAL discovery (§ 9).

399's headline blocker: `discover_models()` wraps an FI model with NO resolver, and
the adapter raises `ConfigurationError` for every GROUP conversion without one — which
is why group training had never produced an artifact on any deployment. The flow
attaches the resolver after registration. ⛔ **Deleting that block failed no test.**

Every other retrain/train test injects `models={...}` directly, so the flow never
runs discovery and the adapter is never the thing being trained. This test closes
that: `importlib.metadata.entry_points` is patched so the REAL `discover_models()`
loads a raw FI model and really calls `adapt_if_fi`, exactly as production does.
"""

from __future__ import annotations

import importlib.metadata
import random
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import forecast_interface as fi

from sapphire_flow.flows.train_models import train_models_flow
from sapphire_flow.services import model_registry
from sapphire_flow.types.enums import (
    AlertEligibility,
    ModelAssignmentStatus,
    ModelTier,
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import ModelId, StationGroupId, StationId
from sapphire_flow.types.station import (
    GroupModelAssignment,
    StationGroup,
    StationWeatherSource,
)
from tests.conftest import make_observations, make_station_config
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
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
from tests.unit.flows.test_train_models import (
    _EPOCH,
    _N_OBS_DAYS,
    _RNG_SEED,
    _TRAINING_END,
    _TRAINING_START,
    _make_forcing_records,
)

if TYPE_CHECKING:
    import pytest

_MODEL_ID = ModelId("t5_fi_group_model")
_DAILY = timedelta(days=1)


def _requirement() -> fi.InputRequirement:
    """Shaped to exactly what the flow supplies: daily POINT `smn` precipitation and
    temperature, discharge target, no statics."""
    return fi.InputRequirement(
        targets={
            "discharge": fi.TargetSpec(
                unit=fi.Unit.M3_PER_S,
                representations=frozenset({fi.OutputRepresentation.DETERMINISTIC}),
            )
        },
        dynamic={
            _DAILY: fi.SpatialInputSpec(
                data={
                    fi.SpatialRepresentation.POINT: fi.DynamicInputSpec(
                        past_known={
                            "smn": {
                                "precipitation": fi.PastKnownVariable(
                                    lookback=3, max_nan=0, unit=fi.Unit.MM
                                ),
                                "temperature": fi.PastKnownVariable(
                                    lookback=3, max_nan=0, unit=fi.Unit.DEG_C
                                ),
                            }
                        },
                        future_known={
                            "smn": {
                                "precipitation": fi.FutureKnownVariable(
                                    future_steps=1, max_nan=0, unit=fi.Unit.MM
                                )
                            }
                        },
                    )
                }
            )
        },
    )


class _RawFIGroupModel:
    """A RAW FI model — not an adapter. `adapt_if_fi` wraps it at discovery."""

    artifact_scope = fi.ArtifactScope.GROUP
    model_tier = ModelTier.SKILL
    alert_eligibility = AlertEligibility.SKILL_FORECAST

    @property
    def input_requirement(self) -> fi.InputRequirement:
        return _requirement()

    def train(self, inputs: Any, *, config: Any, rng: Any) -> object:
        return b"trained"

    def predict(self, artifact: Any, *, inputs: Any, issue_datetime: Any, rng: Any):
        raise NotImplementedError

    def serialize_artifact(self, artifact: object) -> bytes:
        return b"serialized"

    def deserialize_artifact(self, raw: bytes) -> object:
        return raw


class _FakeEntryPoint:
    name = str(_MODEL_ID)

    def load(self) -> type[_RawFIGroupModel]:
        return _RawFIGroupModel


def _run_group_training_through_discovery(monkeypatch: pytest.MonkeyPatch) -> list:
    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda **_: [_FakeEntryPoint()]
    )
    assert model_registry.discover_models(), "discovery produced no model"

    rng = random.Random(_RNG_SEED + 2)
    sid_a = StationId(UUID(int=rng.getrandbits(128), version=4))
    sid_b = StationId(UUID(int=rng.getrandbits(128), version=4))
    group_id = StationGroupId(UUID(int=rng.getrandbits(128), version=4))

    model_store = FakeModelStore()
    station_store = FakeStationStore()
    group_store = FakeStationGroupStore()
    obs_store = FakeObservationStore()
    artifact_store = FakeModelArtifactStore(group_store=group_store)

    group_store.store_group(
        StationGroup(
            id=group_id,
            name="t5-group",
            station_ids=frozenset({sid_a, sid_b}),
            created_at=_EPOCH,
        )
    )
    assignment = GroupModelAssignment(
        group_id=group_id,
        model_id=_MODEL_ID,
        time_step=_DAILY,
        status=ModelAssignmentStatus.ACTIVE,
        priority=1,
        created_at=_EPOCH,
    )
    group_store.store_group_model_assignment(assignment)
    group_store.seed_group_model_assignment(group_id, _MODEL_ID, assignment)

    records = []
    for sid in (sid_a, sid_b):
        station_store.store_station(make_station_config(station_id=sid))
        station_store.store_weather_source(
            StationWeatherSource(
                station_id=sid,
                nwp_source="smn",
                extraction_type=SpatialRepresentation.POINT,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.REANALYSIS,
            )
        )
        obs_store.store_observations(
            make_observations(
                n=_N_OBS_DAYS,
                station_id=sid,
                parameter="discharge",
                start=_TRAINING_START,
                interval=_DAILY,
                rng=random.Random(_RNG_SEED),
            )
        )
        records.extend(_make_forcing_records(sid, _TRAINING_START, _N_OBS_DAYS))

    return train_models_flow(
        period_start=str(_TRAINING_START.isoformat()),
        period_end=str(_TRAINING_END.isoformat()),
        model_store=model_store,
        station_store=station_store,
        group_store=group_store,
        obs_store=obs_store,
        basin_store=FakeBasinStore(),
        artifact_store=artifact_store,
        hindcast_store=FakeHindcastStore(),
        skill_store=FakeSkillStore(),
        flow_regime_store=FakeFlowRegimeConfigStore(),
        forcing_source=FakeWeatherReanalysisSource(records=records),
        # 🔑 models=None — the flow runs discovery itself, so the model it trains is
        # a REAL ForecastInterfaceAdapter with no resolver of its own.
        models=None,
        clock=lambda: _EPOCH,
        rng=random.Random(0),
    )


def test_group_training_through_discovery_gets_the_station_code_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🔴 Remove the wiring block and this fails with the resolver error."""
    results = _run_group_training_through_discovery(monkeypatch)

    assert len(results) == 1
    result = results[0]
    assert result.error is None, (
        f"group training through discovery failed: {result.error} — if this names "
        "the station-code resolver, the wiring block is gone"
    )
    assert result.artifact_id is not None
