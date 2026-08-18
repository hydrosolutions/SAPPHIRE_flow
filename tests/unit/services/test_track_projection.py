"""Plan 151 T3 red-first: track projection + resolve_tracks() (D2, D5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.services.track_projection import (
    project_forcing_requirement,
    resolve_tracks,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    EnsembleMode,
    ModelAssignmentStatus,
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.forcing_track import (
    FeatureName,
    ForcingRequired,
    ForcingTrackKey,
    FutureSteps,
    NoForcingRequired,
)
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.model import ModelDataRequirements
from sapphire_flow.types.station import ModelAssignment, StationWeatherSource

_STATION_A = StationId(UUID(int=1))
_STATION_B = StationId(UUID(int=2))
_MODEL = ModelId("test_model")
_CREATED = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


def _assignment(
    *, station_id: StationId = _STATION_A, time_step: timedelta = timedelta(hours=1)
) -> ModelAssignment:
    return ModelAssignment(
        station_id=station_id,
        model_id=_MODEL,
        time_step=time_step,
        status=ModelAssignmentStatus.ACTIVE,
        priority=1,
        created_at=_CREATED,
    )


def _sws(*, station_id: StationId = _STATION_A) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station_id,
        nwp_source="ifs_ecmwf",
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.FORECAST,
    )


class _NativeModel:
    """Minimal fake satisfying StationForecastModel structurally."""

    def __init__(self, data_requirements: ModelDataRequirements) -> None:
        self.artifact_scope = (
            data_requirements.spatial_input_type
        )  # unused by projector
        self.data_requirements = data_requirements


def _native_requirements(
    *,
    future_dynamic_features: frozenset[str],
    forecast_horizon_steps: int = 7,
    ensemble_mode: EnsembleMode = EnsembleMode.SINGLE,
) -> ModelDataRequirements:
    return ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=future_dynamic_features,
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=1)}),
        lookback_steps=1,
        forecast_horizon_steps=forecast_horizon_steps,
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
        ensemble_mode=ensemble_mode,
    )


# --- FI fixtures (reuse the FI adapter's own construction helpers) ---


class FakeFIForecastModel:
    def __init__(self, input_requirement: fi_boundary.InputRequirement) -> None:
        self._input_requirement = input_requirement
        self.artifact_scope = fi_boundary.FIArtifactScope.STATION

    @property
    def input_requirement(self) -> fi_boundary.InputRequirement:
        return self._input_requirement

    def train(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def predict(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def serialize_artifact(self, artifact: object) -> bytes:
        raise NotImplementedError

    def deserialize_artifact(self, raw: bytes) -> object:
        raise NotImplementedError


def _future(*, future_steps: int, unit: fi_boundary.Unit = fi_boundary.Unit.MM):
    return fi_boundary.FutureKnownVariable(
        future_steps=future_steps, max_nan=0, unit=unit
    )


def _fi_requirement_heterogeneous_horizons() -> fi_boundary.InputRequirement:
    spec = fi_boundary.DynamicInputSpec(
        future_known={
            "nwp": {
                "tp": _future(future_steps=2, unit=fi_boundary.Unit.MM),
                "t_2m": _future(future_steps=10, unit=fi_boundary.Unit.DEG_C),
            },
        },
    )
    return fi_boundary.InputRequirement(
        targets={
            "discharge": fi_boundary.TargetSpec(
                unit=fi_boundary.Unit.M3_PER_S,
                representations=frozenset(
                    {fi_boundary.OutputRepresentation.DETERMINISTIC}
                ),
            )
        },
        dynamic={
            timedelta(hours=1): fi_boundary.SpatialInputSpec(
                data={fi_boundary.FISpatialRepresentation.POINT: spec}
            ),
        },
    )


def _fi_model_with_past_only_selected_branch() -> fi_boundary.InputRequirement:
    # 1h is future-forced (tp); 24h is past-only. An assignment whose OWN
    # time_step is 24h selects the past-only branch, even though the 1h
    # sibling branch has future forcing.
    future_spec = fi_boundary.DynamicInputSpec(
        future_known={"nwp": {"tp": _future(future_steps=5)}},
    )
    past_only_spec = fi_boundary.DynamicInputSpec(
        past_known={
            "era5": {
                "soil_moisture": fi_boundary.PastKnownVariable(
                    lookback=10, max_nan=0, unit=fi_boundary.Unit.PERCENT
                )
            }
        },
    )
    return fi_boundary.InputRequirement(
        targets={
            "discharge": fi_boundary.TargetSpec(
                unit=fi_boundary.Unit.M3_PER_S,
                representations=frozenset(
                    {fi_boundary.OutputRepresentation.DETERMINISTIC}
                ),
            )
        },
        dynamic={
            timedelta(hours=1): fi_boundary.SpatialInputSpec(
                data={fi_boundary.FISpatialRepresentation.POINT: future_spec}
            ),
            timedelta(hours=24): fi_boundary.SpatialInputSpec(
                data={fi_boundary.FISpatialRepresentation.POINT: past_only_spec}
            ),
        },
    )


class TestProjectForcingRequirementFI:
    def test_projects_per_feature_horizons_with_locked_key_shape(self) -> None:
        model = fi_boundary.ForecastInterfaceAdapter(
            FakeFIForecastModel(_fi_requirement_heterogeneous_horizons())
        )
        projection = project_forcing_requirement(
            _assignment(time_step=timedelta(hours=1)), model, _sws()
        )

        assert isinstance(projection, ForcingRequired)
        assert projection.assignment_horizons == {
            FeatureName("tp"): FutureSteps(value=2),
            FeatureName("t_2m"): FutureSteps(value=10),
        }
        assert projection.key == ForcingTrackKey(
            nwp_source="ifs_ecmwf",
            ensemble_mode=EnsembleMode.SINGLE,
            time_step=timedelta(hours=1),
            spatial_representation=SpatialRepresentation.BASIN_AVERAGE,
            features=frozenset({FeatureName("tp"), FeatureName("t_2m")}),
        )

    def test_selected_branch_with_no_future_known_projects_to_no_forcing_required(
        self,
    ) -> None:
        # Even though the model's OTHER (1h) branch has future forcing, an
        # assignment whose OWN time_step selects the past-only 24h branch
        # must project to NoForcingRequired.
        model = fi_boundary.ForecastInterfaceAdapter(
            FakeFIForecastModel(_fi_model_with_past_only_selected_branch())
        )
        projection = project_forcing_requirement(
            _assignment(time_step=timedelta(hours=24)), model, _sws()
        )

        assert isinstance(projection, NoForcingRequired)


class TestProjectForcingRequirementNative:
    def test_fallback_model_with_no_future_features_projects_to_no_forcing_required(
        self,
    ) -> None:
        model = _NativeModel(_native_requirements(future_dynamic_features=frozenset()))
        projection = project_forcing_requirement(_assignment(), model, _sws())

        assert isinstance(projection, NoForcingRequired)

    def test_multi_feature_model_broadcasts_scalar_horizon_to_every_feature(
        self,
    ) -> None:
        model = _NativeModel(
            _native_requirements(
                future_dynamic_features=frozenset({"tp", "t_2m"}),
                forecast_horizon_steps=7,
            )
        )
        projection = project_forcing_requirement(_assignment(), model, _sws())

        assert isinstance(projection, ForcingRequired)
        assert projection.assignment_horizons == {
            FeatureName("tp"): FutureSteps(value=7),
            FeatureName("t_2m"): FutureSteps(value=7),
        }


class TestResolveTracks:
    def test_two_assignments_same_key_different_horizons_dedup_to_one_track_at_max(
        self,
    ) -> None:
        key = ForcingTrackKey(
            nwp_source="ifs_ecmwf",
            ensemble_mode=EnsembleMode.SINGLE,
            time_step=timedelta(hours=1),
            spatial_representation=SpatialRepresentation.BASIN_AVERAGE,
            features=frozenset({FeatureName("tp")}),
        )
        short = ForcingRequired(
            key=key,
            assignment_horizons={FeatureName("tp"): FutureSteps(value=5)},
            assignment=(_STATION_A, ModelId("short")),  # type: ignore[arg-type]
        )
        long_ = ForcingRequired(
            key=key,
            assignment_horizons={FeatureName("tp"): FutureSteps(value=10)},
            assignment=(_STATION_B, ModelId("long")),  # type: ignore[arg-type]
        )

        resolved = resolve_tracks([short, long_])

        assert len(resolved) == 1
        assert resolved[0].fetch_horizons == {FeatureName("tp"): FutureSteps(value=10)}
        # ForcingRequired is not hashable (its assignment_horizons is a plain
        # dict, D1) -- compare as an ordered tuple, not a set.
        assert resolved[0].assignments == (short, long_)
        # Each assignment's OWN horizons are retained, not overwritten.
        assert short in resolved[0].assignments
        assert short.assignment_horizons[FeatureName("tp")].value == 5

    def test_reducer_output_order_is_deterministic(self) -> None:
        key_b = ForcingTrackKey(
            nwp_source="ifs_ecmwf",
            ensemble_mode=EnsembleMode.SINGLE,
            time_step=timedelta(hours=1),
            spatial_representation=SpatialRepresentation.BASIN_AVERAGE,
            features=frozenset({FeatureName("t_2m")}),
        )
        key_a = ForcingTrackKey(
            nwp_source="ifs_ecmwf",
            ensemble_mode=EnsembleMode.SINGLE,
            time_step=timedelta(hours=1),
            spatial_representation=SpatialRepresentation.BASIN_AVERAGE,
            features=frozenset({FeatureName("tp")}),
        )
        req_b = ForcingRequired(
            key=key_b,
            assignment_horizons={FeatureName("t_2m"): FutureSteps(value=5)},
            assignment=(_STATION_A, ModelId("b")),  # type: ignore[arg-type]
        )
        req_a = ForcingRequired(
            key=key_a,
            assignment_horizons={FeatureName("tp"): FutureSteps(value=5)},
            assignment=(_STATION_A, ModelId("a")),  # type: ignore[arg-type]
        )

        first = resolve_tracks([req_b, req_a])
        second = resolve_tracks([req_a, req_b])

        assert [r.key for r in first] == [r.key for r in second]
        # "t_2m" sorts before "tp" lexicographically ('_' < 'p'); the sort key
        # itself just needs to be a stable TOTAL order, not this particular one.
        assert [r.key.features for r in first] == [
            frozenset({FeatureName("t_2m")}),
            frozenset({FeatureName("tp")}),
        ]

    def test_empty_input_returns_empty_list(self) -> None:
        assert resolve_tracks([]) == []
