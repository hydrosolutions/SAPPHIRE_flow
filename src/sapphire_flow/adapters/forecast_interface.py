"""Single ForecastInterface conformance boundary for SAP3."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar, cast

import polars as pl
import structlog
from forecast_interface import (
    ArtifactScope as FIArtifactScope,
)
from forecast_interface import (
    DeterministicData,
    DynamicInputs,
    DynamicInputSpec,
    FailureCause,
    ForecastFlag,
    ForecastModel,
    FutureKnownVariable,
    InputRequirement,
    InputSeries,
    ModelFailure,
    ModelInputs,
    ModelOutput,
    ModelResult,
    ModelSuccess,
    OutputRepresentation,
    PastKnownVariable,
    QuantileData,
    SpatialInputs,
    SpatialInputSpec,
    StationInputs,
    TargetSpec,
    TrajectoryData,
    Unit,
    VariableMetadata,
    VariableOutput,
    VariableStatus,
)
from forecast_interface import (
    EnsembleMode as FIEnsembleMode,
)
from forecast_interface import (
    SpatialRepresentation as FISpatialRepresentation,
)

from sapphire_flow.exceptions import ConfigurationError, ModelOutputError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    ArtifactScope,
    EnsembleMode,
    SpatialRepresentation,
)
from sapphire_flow.types.model import ModelDataRequirements

if TYPE_CHECKING:
    import random
    from collections.abc import Callable, Iterator
    from datetime import datetime, timedelta

    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.model import (
        GroupModelInputs,
        GroupTrainingData,
        ModelArtifact,
        ModelParams,
        StationInputData,
        StationModelInputs,
        StationTrainingData,
    )

log = structlog.get_logger(__name__)

T = TypeVar("T")

SUPPORTED_FI_VERSION: str = "0.1.17"
# STATION artifacts are store-bound; FI key is fixed (1.10 gauge-code is GROUP-scoped).
_STATION_SCOPE_KEY: str = "station"

__all__ = [
    "DynamicInputs",
    "DynamicInputSpec",
    "DeterministicData",
    "FailureCause",
    "FIArtifactScope",
    "FISpatialRepresentation",
    "ForecastInterfaceAdapter",
    "ForecastModel",
    "ForecastFlag",
    "FutureKnownVariable",
    "InputSeries",
    "InputRequirement",
    "ModelFailure",
    # Re-exported for FI boundary tests; avoid confusing with legacy SAP3 ModelInputs.
    "ModelInputs",
    "ModelOutput",
    "ModelResult",
    "ModelSuccess",
    "OutputRepresentation",
    "PastKnownVariable",
    "SpatialInputs",
    "SpatialInputSpec",
    "StationInputs",
    "SUPPORTED_FI_VERSION",
    "TargetSpec",
    "TrajectoryData",
    "QuantileData",
    "Unit",
    "VariableMetadata",
    "VariableOutput",
    "VariableStatus",
    "adapt_if_fi",
    "check_fi_version",
    "fi_unit_to_canonical",
    "is_fi_model",
]

_FI_UNIT_TO_CANONICAL: dict[Unit, str] = {
    # MM_PER_DAY, MM_PER_S, UNITLESS, DEGREE intentionally absent: no SAP3 canonical string; v0 NWP allows tp+t_2m, so onboarding rejects unsupported units loudly.  # noqa: E501
    Unit.M3_PER_S: "m³/s",
    Unit.MM: "mm",
    Unit.CM: "cm",
    Unit.M: "m",
    Unit.DEG_C: "°C",
    Unit.PERCENT: "%",
    Unit.M_PER_S: "m/s",
    Unit.W_PER_M2: "W/m²",
    Unit.MM_PER_HOUR: "mm/h",
}


def check_fi_version() -> None:
    import forecast_interface

    actual_version: str = forecast_interface.__version__
    if actual_version != SUPPORTED_FI_VERSION:
        raise ConfigurationError(
            "ForecastInterface version mismatch: "
            f"supported forecastinterface=={SUPPORTED_FI_VERSION}, "
            f"actual forecast_interface.__version__=={actual_version}. "
            f"Install forecastinterface=={SUPPORTED_FI_VERSION} "
            "and sync the environment."
        )

    log.debug(
        "forecast_interface.version_compatible",
        supported_version=SUPPORTED_FI_VERSION,
        actual_version=actual_version,
    )


def fi_unit_to_canonical(unit: Unit) -> str:
    """Return SAP3's exact canonical string for a ForecastInterface unit."""
    try:
        return _FI_UNIT_TO_CANONICAL[unit]
    except KeyError as exc:
        raise ConfigurationError(
            f"No SAP3 canonical unit mapping for ForecastInterface Unit.{unit.name}"
        ) from exc


def is_fi_model(obj: object) -> bool:
    return isinstance(obj, ForecastModel)


def adapt_if_fi(
    obj: T,
    *,
    station_code_resolver: Callable[[StationId], str] | None = None,
) -> ForecastInterfaceAdapter | T:
    # discover_models() wraps FI models at discovery time with no resolver; a
    # later adapt_if_fi(..., station_code_resolver=...) (e.g. GROUP onboarding)
    # must ATTACH the resolver to the already-wrapped adapter, not drop it.
    if isinstance(obj, ForecastInterfaceAdapter):
        if station_code_resolver is not None:
            obj.with_station_code_resolver(station_code_resolver)
        return obj
    if is_fi_model(obj):
        return ForecastInterfaceAdapter(
            cast("ForecastModel", obj),
            station_code_resolver=station_code_resolver,
        )
    return obj


def _ensemble_from_variable_output(
    *,
    station_id: StationId,
    parameter: str,
    issue_datetime: datetime,
    var_output: VariableOutput,
) -> ForecastEnsemble:
    issued_at = ensure_utc(issue_datetime)
    units = fi_unit_to_canonical(var_output.metadata.unit)
    time_step = var_output.metadata.timedelta

    # Trajectories/MEMBERS is richest and the only combinable representation (FI 1.14).
    if var_output.trajectories is not None:
        values = _members_from_trajectories(
            var_output.trajectories,
            metadata=var_output.metadata,
            parameter=parameter,
        )
        try:
            return ForecastEnsemble.from_members(
                station_id=station_id,
                issued_at=issued_at,
                parameter=parameter,
                units=units,
                time_step=time_step,
                values=values,
                model_id=None,
            )
        except ValueError as exc:
            raise ModelOutputError(
                f"ForecastInterface output for parameter {parameter!r} failed "
                f"ensemble validation: {exc}"
            ) from exc

    if var_output.quantiles is not None:
        values = _quantiles_from_quantile_data(
            var_output.quantiles,
            metadata=var_output.metadata,
            parameter=parameter,
        )
        try:
            return ForecastEnsemble.from_quantiles(
                station_id=station_id,
                issued_at=issued_at,
                parameter=parameter,
                units=units,
                time_step=time_step,
                values=values,
                model_id=None,
            )
        except ValueError as exc:
            raise ModelOutputError(
                f"ForecastInterface output for parameter {parameter!r} failed "
                f"ensemble validation: {exc}"
            ) from exc

    if var_output.deterministic is not None:
        values = _members_from_deterministic(
            var_output.deterministic,
            metadata=var_output.metadata,
            parameter=parameter,
        )
        try:
            return ForecastEnsemble.from_members(
                station_id=station_id,
                issued_at=issued_at,
                parameter=parameter,
                units=units,
                time_step=time_step,
                values=values,
                model_id=None,
            )
        except ValueError as exc:
            raise ModelOutputError(
                f"ForecastInterface output for parameter {parameter!r} failed "
                f"ensemble validation: {exc}"
            ) from exc

    raise ModelOutputError(
        f"ForecastInterface output for parameter {parameter!r} has no convertible "
        "deterministic, quantile, or trajectory data"
    )


def _members_from_trajectories(
    data: TrajectoryData,
    *,
    metadata: VariableMetadata,
    parameter: str,
) -> pl.DataFrame:
    member_columns = [str(member_id) for member_id in range(1, data.num_samples + 1)]
    values = (
        _with_utc_valid_time(data.data, parameter=parameter)
        .unpivot(
            index="valid_time",
            on=member_columns,
            variable_name="member_id",
            value_name="value",
        )
        .select(
            "valid_time",
            pl.col("member_id").cast(pl.Int32),
            pl.col("value").cast(pl.Float64),
        )
        .sort("valid_time", "member_id")
    )
    _assert_forecast_horizon(values, metadata=metadata, parameter=parameter)
    return values


def _quantiles_from_quantile_data(
    data: QuantileData,
    *,
    metadata: VariableMetadata,
    parameter: str,
) -> pl.DataFrame:
    quantile_columns = [str(level) for level in data.quantile_levels]
    values = (
        _with_utc_valid_time(data.data, parameter=parameter)
        .unpivot(
            index="valid_time",
            on=quantile_columns,
            variable_name="quantile",
            value_name="value",
        )
        .select(
            "valid_time",
            pl.col("quantile").cast(pl.Float64),
            pl.col("value").cast(pl.Float64),
        )
        .sort("valid_time", "quantile")
    )
    _assert_forecast_horizon(values, metadata=metadata, parameter=parameter)
    return values


def _members_from_deterministic(
    data: DeterministicData,
    *,
    metadata: VariableMetadata,
    parameter: str,
) -> pl.DataFrame:
    values = (
        _with_utc_valid_time(data.data, parameter=parameter)
        .select(
            "valid_time",
            pl.lit(1).cast(pl.Int32).alias("member_id"),
            pl.col("value").cast(pl.Float64),
        )
        .sort("valid_time", "member_id")
    )
    _assert_forecast_horizon(values, metadata=metadata, parameter=parameter)
    return values


def _with_utc_valid_time(frame: pl.DataFrame, *, parameter: str) -> pl.DataFrame:
    if "datetime" not in frame.columns:
        raise ModelOutputError(
            f"ForecastInterface output for parameter {parameter!r} is missing "
            "'datetime' column"
        )
    # FI datetimes are UTC by contract; tz-naive values are localized, not shifted.
    return frame.rename({"datetime": "valid_time"}).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC"))
    )


def _assert_forecast_horizon(
    values: pl.DataFrame,
    *,
    metadata: VariableMetadata,
    parameter: str,
) -> None:
    actual = values["valid_time"].n_unique()
    expected = metadata.forecast_horizon
    if actual != expected:
        raise ModelOutputError(
            f"ForecastInterface output for parameter {parameter!r} has {actual} "
            f"unique valid_time values; expected forecast_horizon={expected}"
        )


def _output_from_result(result: ModelResult) -> ModelOutput:
    if isinstance(result, ModelFailure):
        raise ModelOutputError(
            f"ForecastInterface model failure: {result.cause.name}: {result.message}"
        )
    output = result.output
    if not output.variables:
        raise ModelOutputError("ForecastInterface model produced empty variables")
    return output


def _station_variables_for_single_station(
    output: ModelOutput,
) -> tuple[str, dict[str, VariableOutput]]:
    if _STATION_SCOPE_KEY in output.variables:
        return _STATION_SCOPE_KEY, output.variables[_STATION_SCOPE_KEY]
    if len(output.variables) == 1:
        return next(iter(output.variables.items()))

    station_keys = ", ".join(sorted(output.variables))
    raise ModelOutputError(
        "ForecastInterface station output could not be resolved for STATION "
        f"prediction; expected {_STATION_SCOPE_KEY!r} or one station, got: "
        f"{station_keys}"
    )


def _ensembles_from_station_variables(
    *,
    station_id: StationId,
    station_key: str,
    station_vars: dict[str, VariableOutput],
    issue_datetime: datetime,
) -> dict[str, ForecastEnsemble]:
    ensembles: dict[str, ForecastEnsemble] = {}
    for parameter, var_output in station_vars.items():
        if var_output.status is VariableStatus.FAILURE:
            continue
        _log_variable_output_warning(
            station_key=station_key,
            parameter=parameter,
            var_output=var_output,
        )
        ensembles[parameter] = _ensemble_from_variable_output(
            station_id=station_id,
            parameter=parameter,
            issue_datetime=issue_datetime,
            var_output=var_output,
        )
    return ensembles


def _log_variable_output_warning(
    *,
    station_key: str,
    parameter: str,
    var_output: VariableOutput,
) -> None:
    if var_output.status is not VariableStatus.PARTIAL and not var_output.flags:
        return

    log.warning(
        "forecast_interface.variable_output_warning",
        station_key=station_key,
        parameter=parameter,
        status=var_output.status.name,
        flags=sorted(flag.name for flag in var_output.flags),
    )


class ForecastInterfaceAdapter:
    def __init__(
        self,
        fi_model: ForecastModel,
        station_code_resolver: Callable[[StationId], str] | None = None,
    ) -> None:
        check_fi_version()
        self._model = fi_model
        self._station_code_resolver = station_code_resolver
        self.artifact_scope = ArtifactScope(fi_model.artifact_scope.value)
        self.data_requirements = self._project_requirements(fi_model.input_requirement)

    def with_station_code_resolver(
        self, resolver: Callable[[StationId], str]
    ) -> ForecastInterfaceAdapter:
        """Attach (or replace) the GROUP station-code resolver; returns self."""
        self._station_code_resolver = resolver
        return self

    def _project_requirements(self, req: InputRequirement) -> ModelDataRequirements:
        spatial_reps: set[FISpatialRepresentation] = set()
        future_dynamic_features: set[str] = set()
        past_variables: list[tuple[str, PastKnownVariable]] = []
        forecast_horizon_steps: int | None = None
        any_ensemble_future = False

        for fi_rep, spec in self._iter_dynamic_specs(req):
            spatial_reps.add(fi_rep)

            for variables in spec.past_known.values():
                past_variables.extend(variables.items())

            for variables in spec.future_known.values():
                for name, variable in variables.items():
                    future_dynamic_features.add(name)
                    if variable.ensemble_mode is FIEnsembleMode.ENSEMBLE:
                        any_ensemble_future = True
                    if forecast_horizon_steps is None:
                        forecast_horizon_steps = variable.future_steps
                    else:
                        forecast_horizon_steps = max(
                            forecast_horizon_steps, variable.future_steps
                        )

        if not spatial_reps:
            raise ConfigurationError(
                "cannot derive spatial input type: InputRequirement declares no "
                "dynamic input"
            )
        if len(spatial_reps) > 1:
            rep_names = ", ".join(sorted(rep.value for rep in spatial_reps))
            raise ConfigurationError(
                f"multi-spatial input not supported in v1: {rep_names}"
            )
        if forecast_horizon_steps is None:
            raise ConfigurationError(
                "cannot derive forecast horizon: InputRequirement declares no "
                "future_known forcing"
            )

        # A target's own past_known history is autoregressive conditioning
        # delivered from the TARGET channel (past_targets), never a forcing
        # feature — it must not leak into past_dynamic_features (the forcing
        # channel keyed for reanalysis fetch), regardless of its lookback vs the
        # forecast horizon. Its lookback STILL counts toward lookback_steps: the
        # model needs those past target steps, delivered from past_targets.
        past_dynamic_features: set[str] = set()
        lookback_steps = 1
        for name, variable in past_variables:
            lookback_steps = max(lookback_steps, variable.lookback)
            if name in req.targets:
                continue
            past_dynamic_features.add(name)

        [spatial_rep] = spatial_reps
        return ModelDataRequirements(
            target_parameters=frozenset(req.targets),
            past_dynamic_features=frozenset(past_dynamic_features),
            future_dynamic_features=frozenset(future_dynamic_features),
            static_features=frozenset(req.static),
            supported_time_steps=frozenset(req.dynamic),
            lookback_steps=lookback_steps,
            # V1 proxy: FI declares horizon only at output time; future_steps is
            # the input-forcing length used as the horizon proxy, endorsed in SF2.
            forecast_horizon_steps=forecast_horizon_steps,
            spatial_input_type=SpatialRepresentation(spatial_rep.value),
            ensemble_mode=(
                EnsembleMode.ENSEMBLE if any_ensemble_future else EnsembleMode.SINGLE
            ),
        )

    def _declared_fi_units(self) -> dict[str, Unit]:
        units: dict[str, Unit] = {}

        for name, target in self._model.input_requirement.targets.items():
            self._record_conflict_checked(
                values=units,
                name=name,
                value=target.unit,
                label="unit",
            )

        for name, variable in self._iter_dynamic_variables(
            self._model.input_requirement
        ):
            self._record_conflict_checked(
                values=units,
                name=name,
                value=variable.unit,
                label="unit",
            )

        return units

    # declared_units() and unsupported_units() partition declared variables by
    # whether SAP3 has a canonical unit mapping for the ForecastInterface unit.
    def declared_units(self) -> dict[str, str]:
        units: dict[str, str] = {}

        for name, unit in self._declared_fi_units().items():
            try:
                units[name] = fi_unit_to_canonical(unit)
            except ConfigurationError:
                continue

        return units

    def unsupported_units(self) -> frozenset[str]:
        unsupported: set[str] = set()

        for name, unit in self._declared_fi_units().items():
            try:
                fi_unit_to_canonical(unit)
            except ConfigurationError:
                unsupported.add(name)

        return frozenset(unsupported)

    def max_nan_tolerances(self) -> dict[str, int]:
        tolerances: dict[str, int] = {}

        for name, variable in self._iter_dynamic_variables(
            self._model.input_requirement
        ):
            self._record_conflict_checked(
                values=tolerances,
                name=name,
                value=variable.max_nan,
                label="max_nan",
            )

        return tolerances

    def _variables_over_nan_tolerance(
        self,
        *,
        past_targets: pl.DataFrame,
        past_dynamic: pl.DataFrame,
        future_dynamic: pl.DataFrame,
    ) -> dict[str, int]:
        over_tolerance: dict[str, int] = {}

        for name, tolerance in self.max_nan_tolerances().items():
            frame = self._frame_with_column(
                name=name,
                frames=(
                    ("past_dynamic", past_dynamic),
                    ("future_dynamic", future_dynamic),
                    ("past_targets", past_targets),
                ),
                temporality="dynamic",
            )
            missing_count = self._missing_value_count(frame=frame, name=name)
            if missing_count > tolerance:
                over_tolerance[name] = missing_count

        return over_tolerance

    def _missing_value_count(self, *, frame: pl.DataFrame, name: str) -> int:
        series = frame.get_column(name)
        missing_count = series.null_count()

        # SAP3 treats both Polars nulls and IEEE float NaNs as missing for the
        # FI max_nan gate; values within tolerance are delivered unchanged.
        if series.dtype.is_float():
            missing_count += int(series.is_nan().fill_null(False).sum())

        return missing_count

    def _format_nan_tolerance_counts(self, counts: dict[str, int]) -> str:
        return ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))

    def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
        return self._model.serialize_artifact(artifact)

    def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
        return self._model.deserialize_artifact(raw)

    def train(
        self,
        data: StationTrainingData | GroupTrainingData,
        params: ModelParams,
        rng: random.Random,
    ) -> ModelArtifact:
        model_inputs = self._model_inputs_from_data(data)
        return self._model.train(model_inputs, config=params, rng=rng)

    def predict(
        self,
        artifact: ModelArtifact,
        inputs: StationModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[dict[str, ForecastEnsemble], bytes | None]:
        if self.artifact_scope is ArtifactScope.GROUP:
            raise ConfigurationError("dispatch must key on artifact_scope")

        over_tolerance = self._variables_over_nan_tolerance(
            past_targets=inputs.data.past_targets,
            past_dynamic=inputs.data.past_dynamic,
            future_dynamic=inputs.data.future_dynamic,
        )
        if over_tolerance:
            raise ModelOutputError(
                "ForecastInterface input max_nan tolerance exceeded for "
                f"station {inputs.station_id}: "
                f"{self._format_nan_tolerance_counts(over_tolerance)}"
            )

        model_inputs = self._model_inputs_from_data(inputs)
        result = self._model.predict(
            artifact,
            inputs=model_inputs,
            issue_datetime=ensure_utc(inputs.issue_time),
            rng=rng,
        )
        output = _output_from_result(result)
        station_key, station_vars = _station_variables_for_single_station(output)
        ensembles = _ensembles_from_station_variables(
            station_id=inputs.station_id,
            station_key=station_key,
            station_vars=station_vars,
            issue_datetime=output.issue_datetime,
        )
        if not ensembles:
            raise ModelOutputError("model produced no usable output")

        # ForecastInterface is state-free; prior_state is intentionally ignored.
        return ensembles, None

    def predict_batch(
        self,
        artifact: ModelArtifact,
        inputs: GroupModelInputs,
        rng: random.Random,
    ) -> dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]:
        if self.artifact_scope is ArtifactScope.STATION:
            raise ConfigurationError("dispatch must key on artifact_scope")

        station_codes_by_id = {
            station_id: self._station_code(station_id)
            for station_id in inputs.station_ids
        }
        serviceable_station_ids: list[StationId] = []
        for station_id in inputs.station_ids:
            station_data = inputs.for_station(station_id)
            over_tolerance = self._variables_over_nan_tolerance(
                past_targets=station_data.past_targets,
                past_dynamic=station_data.past_dynamic,
                future_dynamic=station_data.future_dynamic,
            )
            if over_tolerance:
                log.warning(
                    "forecast_interface.station_input_nan_tolerance_exceeded",
                    station_id=str(station_id),
                    station_key=station_codes_by_id[station_id],
                    variables=over_tolerance,
                )
                continue
            serviceable_station_ids.append(station_id)

        if not serviceable_station_ids:
            raise ModelOutputError(
                "ForecastInterface input max_nan tolerance exceeded for all stations"
            )

        station_ids_by_code = {
            station_codes_by_id[station_id]: station_id
            for station_id in serviceable_station_ids
        }
        model_inputs = self._model_inputs_from_group_data(
            inputs,
            station_ids=tuple(serviceable_station_ids),
        )
        result = self._model.predict(
            artifact,
            inputs=model_inputs,
            issue_datetime=ensure_utc(inputs.issue_time),
            rng=rng,
        )
        output = _output_from_result(result)
        missing = set(station_ids_by_code) - set(output.variables)
        if missing:
            raise ModelOutputError(
                f"ForecastInterface model omitted requested stations: {sorted(missing)}"
            )

        forecasts: dict[
            StationId, tuple[dict[str, ForecastEnsemble], bytes | None]
        ] = {}
        for station_key, station_vars in output.variables.items():
            try:
                station_id = station_ids_by_code[station_key]
            except KeyError as exc:
                raise ModelOutputError(
                    "ForecastInterface model returned unknown station key "
                    f"{station_key!r}"
                ) from exc

            ensembles = _ensembles_from_station_variables(
                station_id=station_id,
                station_key=station_key,
                station_vars=station_vars,
                issue_datetime=output.issue_datetime,
            )
            if not ensembles:
                log.warning(
                    "forecast_interface.station_output_skipped",
                    station_key=station_key,
                    reason="no usable variable output",
                )
                continue

            forecasts[station_id] = (ensembles, None)

        if not forecasts:
            raise ModelOutputError("model produced no usable output")
        return forecasts

    def _iter_dynamic_specs(
        self, req: InputRequirement
    ) -> Iterator[tuple[FISpatialRepresentation, DynamicInputSpec]]:
        for spatial_spec in req.dynamic.values():
            yield from spatial_spec.data.items()

    def _iter_dynamic_variables(
        self, req: InputRequirement
    ) -> Iterator[tuple[str, PastKnownVariable | FutureKnownVariable]]:
        for _, spec in self._iter_dynamic_specs(req):
            for variables in spec.past_known.values():
                yield from variables.items()
            for variables in spec.future_known.values():
                yield from variables.items()

    def _model_inputs_from_data(
        self,
        data: (
            StationModelInputs
            | GroupModelInputs
            | StationTrainingData
            | GroupTrainingData
        ),
    ) -> ModelInputs:
        if hasattr(data, "station_ids"):
            return self._model_inputs_from_group_data(
                cast("GroupModelInputs | GroupTrainingData", data)
            )
        if hasattr(data, "station_id"):
            return self._model_inputs_from_station_model_inputs(
                cast("StationModelInputs", data)
            )
        return self._model_inputs_from_station_training_data(
            cast("StationTrainingData", data)
        )

    def _model_inputs_from_station_model_inputs(
        self,
        data: StationModelInputs,
    ) -> ModelInputs:
        station_inputs = self._station_inputs_from_station_data(
            data=data.data,
            time_step=data.time_step,
        )
        return ModelInputs(stations={_STATION_SCOPE_KEY: station_inputs})

    def _model_inputs_from_station_training_data(
        self,
        data: StationTrainingData,
    ) -> ModelInputs:
        station_inputs = self._station_inputs_from_frames(
            past_targets=data.past_targets,
            past_dynamic=data.past_dynamic,
            future_dynamic=data.future_dynamic,
            static=data.static,
            time_step=data.time_step,
        )
        return ModelInputs(stations={_STATION_SCOPE_KEY: station_inputs})

    def _model_inputs_from_group_data(
        self,
        data: GroupModelInputs | GroupTrainingData,
        *,
        station_ids: tuple[StationId, ...] | None = None,
    ) -> ModelInputs:
        selected_station_ids = data.station_ids if station_ids is None else station_ids
        return ModelInputs(
            stations={
                self._station_code(station_id): self._station_inputs_from_station_data(
                    data=data.for_station(station_id),
                    time_step=data.time_step,
                )
                for station_id in selected_station_ids
            }
        )

    def _station_inputs_from_station_data(
        self,
        *,
        data: StationInputData | StationTrainingData,
        time_step: timedelta,
    ) -> StationInputs:
        return self._station_inputs_from_frames(
            past_targets=data.past_targets,
            past_dynamic=data.past_dynamic,
            future_dynamic=data.future_dynamic,
            static=data.static,
            time_step=time_step,
        )

    def _station_inputs_from_frames(
        self,
        *,
        past_targets: pl.DataFrame,
        past_dynamic: pl.DataFrame,
        future_dynamic: pl.DataFrame,
        static: pl.DataFrame | None,
        time_step: timedelta,
    ) -> StationInputs:
        rep, spec = self._dynamic_spec_for_time_step(time_step)
        dynamic_inputs = DynamicInputs(
            past_known=self._past_known_inputs(
                spec=spec,
                past_targets=past_targets,
                past_dynamic=past_dynamic,
            ),
            future_known=self._future_known_inputs(
                spec=spec,
                future_dynamic=future_dynamic,
            ),
        )
        return StationInputs(
            dynamic={
                time_step: SpatialInputs(data={rep: dynamic_inputs}),
            },
            static=self._static_inputs(static),
        )

    def _past_known_inputs(
        self,
        *,
        spec: DynamicInputSpec,
        past_targets: pl.DataFrame,
        past_dynamic: pl.DataFrame,
    ) -> dict[str, dict[str, InputSeries]]:
        return {
            product: product_inputs
            for product, variables in spec.past_known.items()
            if (
                product_inputs := {
                    name: self._past_input_series(
                        name=name,
                        variable=variable,
                        past_targets=past_targets,
                        past_dynamic=past_dynamic,
                    )
                    for name, variable in variables.items()
                }
            )
        }

    def _future_known_inputs(
        self,
        *,
        spec: DynamicInputSpec,
        future_dynamic: pl.DataFrame,
    ) -> dict[str, dict[str, InputSeries]]:
        return {
            product: product_inputs
            for product, variables in spec.future_known.items()
            if (
                product_inputs := {
                    name: self._input_series_from_frame(
                        frame=self._frame_with_column(
                            name=name,
                            frames=(("future_dynamic", future_dynamic),),
                            temporality="future_known",
                        ),
                        name=name,
                        unit=variable.unit,
                    )
                    for name, variable in variables.items()
                }
            )
        }

    def _past_input_series(
        self,
        *,
        name: str,
        variable: PastKnownVariable,
        past_targets: pl.DataFrame,
        past_dynamic: pl.DataFrame,
    ) -> InputSeries:
        return self._input_series_from_frame(
            frame=self._frame_with_column(
                name=name,
                frames=(
                    ("past_dynamic", past_dynamic),
                    ("past_targets", past_targets),
                ),
                temporality="past_known",
            ),
            name=name,
            unit=variable.unit,
        )

    def _input_series_from_frame(
        self,
        *,
        frame: pl.DataFrame,
        name: str,
        unit: Unit,
    ) -> InputSeries:
        if "timestamp" not in frame.columns:
            raise ConfigurationError(
                f"missing timestamp column for ForecastInterface input {name!r}"
            )
        data = frame.select("timestamp", name).rename({"timestamp": "datetime"})
        return InputSeries(unit=unit, data=data.sort("datetime"))

    def _frame_with_column(
        self,
        *,
        name: str,
        frames: tuple[tuple[str, pl.DataFrame], ...],
        temporality: str,
    ) -> pl.DataFrame:
        for _frame_name, frame in frames:
            if name in frame.columns:
                return frame

        source_names = ", ".join(frame_name for frame_name, _ in frames)
        raise ConfigurationError(
            f"missing ForecastInterface {temporality} input {name!r}; "
            f"not found in {source_names}"
        )

    def _static_inputs(
        self, static: pl.DataFrame | None
    ) -> dict[str, int | float | str]:
        static_names = self._model.input_requirement.static
        if static is None or not static_names:
            return {}

        missing = static_names - set(static.columns)
        if missing:
            missing_names = ", ".join(sorted(missing))
            raise ConfigurationError(
                f"missing ForecastInterface static input(s): {missing_names}"
            )
        if static.height != 1:
            raise ConfigurationError(
                "ForecastInterface static input frame must contain exactly one row"
            )

        row = static.select(sorted(static_names)).row(0, named=True)
        return {name: self._static_value(name, row[name]) for name in static_names}

    def _static_value(self, name: str, value: object) -> int | float | str:
        if isinstance(value, bool):
            raise ConfigurationError(
                f"ForecastInterface static input {name!r} must be int, float, or str"
            )
        if isinstance(value, int | float | str):
            return value
        raise ConfigurationError(
            f"ForecastInterface static input {name!r} must be int, float, or str"
        )

    def _dynamic_spec_for_time_step(
        self,
        time_step: timedelta,
    ) -> tuple[FISpatialRepresentation, DynamicInputSpec]:
        try:
            spatial_spec = self._model.input_requirement.dynamic[time_step]
        except KeyError as exc:
            supported = ", ".join(
                str(step) for step in self._model.input_requirement.dynamic
            )
            raise ConfigurationError(
                f"ForecastInterface input requirement does not declare time_step "
                f"{time_step}; supported time steps: {supported}"
            ) from exc

        if len(spatial_spec.data) != 1:
            rep_names = ", ".join(sorted(rep.value for rep in spatial_spec.data))
            raise ConfigurationError(
                f"multi-spatial input not supported in v1: {rep_names}"
            )
        return next(iter(spatial_spec.data.items()))

    def _station_code(self, station_id: StationId) -> str:
        try:
            return self._require_resolver()(station_id)
        except KeyError as exc:
            raise ConfigurationError(
                f"station_code_resolver could not resolve station_id {station_id!r}"
            ) from exc

    def _require_resolver(self) -> Callable[[StationId], str]:
        if self._station_code_resolver is None:
            raise ConfigurationError(
                "station_code_resolver required for GROUP input conversion / train "
                "/ predict"
            )
        return self._station_code_resolver

    def _record_conflict_checked(
        self,
        *,
        values: dict[str, T],
        name: str,
        value: T,
        label: str,
    ) -> None:
        existing = values.get(name)
        if existing is not None and existing != value:
            raise ConfigurationError(
                f"conflicting ForecastInterface {label} for variable {name!r}: "
                f"{existing!r} != {value!r}"
            )
        values[name] = value
