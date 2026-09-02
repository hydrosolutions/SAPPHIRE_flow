"""Single ForecastInterface conformance boundary for SAP3."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar, cast

import polars as pl
import structlog
from forecast_interface import (
    AggregationMethod as FIAggregationMethod,
)
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

from sapphire_flow.exceptions import (
    ConfigurationError,
    ModelOutputError,
    UnsupportedModelRequirementError,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    AggregationMethod,
    ArtifactScope,
    EnsembleMode,
    ForcingRoute,
    SpatialRepresentation,
)
from sapphire_flow.types.forcing_track import FeatureName, FutureSteps
from sapphire_flow.types.model import ModelDataRequirements

if TYPE_CHECKING:
    import random
    from collections.abc import Callable, Iterator, Mapping
    from datetime import datetime, timedelta

    from sapphire_flow.types.forcing_track import FeatureFetchHorizons
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

SUPPORTED_FI_VERSION: str = "0.1.19"
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


def fi_aggregation_to_canonical(method: FIAggregationMethod) -> AggregationMethod:
    """Map a ForecastInterface ``AggregationMethod`` to SAP3's own enum of
    the same name (Plan 228 review fixer round, blocker) — the two are
    separate classes so a declared aggregation must be translated, never
    assumed interchangeable just because their VALUES happen to match."""
    try:
        return AggregationMethod(method.value)
    except ValueError as exc:
        raise ConfigurationError(
            f"No SAP3 AggregationMethod mapping for ForecastInterface "
            f"AggregationMethod.{method.name}"
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

    @property
    def config_hash(self) -> str | None:
        """Plan 157 T3 fixer round: an FI model's own `config_hash` (a
        SAP3-side convention, not part of the FI protocol — D1 ships an
        aquacast-style shim's config as package data alongside the native
        artifact, and this is how import-time drift between the two is
        detected) is NOT forwarded by default — this class has no
        `__getattr__` passthrough. Without this, every real FI model reaches
        `import_external_artifact` wrapped, and `getattr(model,
        "config_hash", None)` silently returns `None` regardless of what the
        wrapped model declares, disabling the drift check entirely."""
        return getattr(self._model, "config_hash", None)

    def _future_forced_time_steps(self, req: InputRequirement) -> tuple[timedelta, ...]:
        # Iterates req.dynamic directly (NOT _iter_dynamic_specs, which
        # discards the time_step key) so each branch's future_known-ness can
        # be attributed to ITS time_step, not flattened away.
        return tuple(
            sorted(
                time_step
                for time_step, spatial_spec in req.dynamic.items()
                if any(spec.future_known for spec in spatial_spec.data.values())
            )
        )

    def _assert_single_future_known_product(self, spec: DynamicInputSpec) -> None:
        # Plan 151 T2 (D4): Phase 3's per-track contract supports exactly ONE
        # non-empty future_known product per branch, with ONE ensemble_mode.
        # past_known products are NOT counted (D4 review fix) —
        # SeasonalPrecipRunoffRegression's second past_known product must
        # keep constructing. Same exception class as the sibling guards
        # above/below: discover_models() SKIPS this entry point rather than
        # darkening the whole registry (D21).
        future_products = [
            product for product, variables in spec.future_known.items() if variables
        ]
        if len(future_products) > 1:
            names = ", ".join(sorted(future_products))
            raise UnsupportedModelRequirementError(
                "ForecastInterface InputRequirement declares "
                f"{len(future_products)} non-empty future_known products in one "
                f"branch ({names}); Phase 3 supports exactly ONE forcing track "
                "per assignment (multi-product/mixed-mode requirements are a "
                "follow-on, D22)."
            )
        if not future_products:
            return
        modes = {
            variable.ensemble_mode
            for variable in spec.future_known[future_products[0]].values()
        }
        if len(modes) > 1:
            mode_names = ", ".join(sorted(mode.value for mode in modes))
            raise UnsupportedModelRequirementError(
                "ForecastInterface InputRequirement future_known product "
                f"{future_products[0]!r} mixes ensemble_mode values "
                f"({mode_names}) within one branch; Phase 3 supports exactly "
                "ONE ensemble_mode per forcing track (D4)."
            )

    def future_feature_horizons(self, time_step: timedelta) -> FeatureFetchHorizons:
        """Per-time-step, per-feature future horizons for the branch at
        ``time_step`` (Plan 151 D3) — WITHOUT the cross-branch/cross-variable
        max collapse `_project_requirements` performs. Looks up the branch
        selected by `time_step` directly (not necessarily the model's single
        deliverable branch, D32) so a caller can query ANY declared branch.
        Returns an EMPTY mapping — never raises — for a branch with no
        `future_known` (including a past-only branch or an undeclared
        `time_step`)."""
        return {
            FeatureName(name): FutureSteps(value=variable.future_steps)
            for _, variables in self._future_known_for_time_step(time_step)
            for name, variable in variables.items()
        }

    def future_feature_modes(
        self, time_step: timedelta
    ) -> Mapping[FeatureName, EnsembleMode]:
        """Per-time-step, per-feature ensemble mode for the branch at
        ``time_step`` (Plan 151 D3) — companion to `future_feature_horizons`.
        Empty mapping for a past-only/undeclared branch; never raises."""
        return {
            FeatureName(name): (
                EnsembleMode.ENSEMBLE
                if variable.ensemble_mode is FIEnsembleMode.ENSEMBLE
                else EnsembleMode.SINGLE
            )
            for _, variables in self._future_known_for_time_step(time_step)
            for name, variable in variables.items()
        }

    def _future_known_for_time_step(
        self, time_step: timedelta
    ) -> list[tuple[str, dict[str, FutureKnownVariable]]]:
        branch = self._model.input_requirement.dynamic.get(time_step)
        if branch is None:
            return []
        return [
            (product, variables)
            for _, spec in branch.data.items()
            for product, variables in spec.future_known.items()
        ]

    def _project_requirements(self, req: InputRequirement) -> ModelDataRequirements:
        future_forced_time_steps = self._future_forced_time_steps(req)
        if len(future_forced_time_steps) > 1:
            resolutions = ", ".join(str(step) for step in future_forced_time_steps)
            raise UnsupportedModelRequirementError(
                "ForecastInterface InputRequirement declares non-empty "
                "future_known in more than one time_step branch: "
                f"{resolutions}. SAP3 domain types are single-resolution; "
                "a model that genuinely needs multiple FUTURE-FORCED "
                "resolutions simultaneously is not yet supported (Plan 153)."
            )

        spatial_reps: set[FISpatialRepresentation] = set()
        future_dynamic_features: set[str] = set()
        past_variables: list[tuple[str, PastKnownVariable]] = []
        future_variables: list[tuple[str, FutureKnownVariable]] = []
        forecast_horizon_steps: int | None = None
        any_ensemble_future = False

        for fi_rep, spec in self._iter_dynamic_specs(req):
            spatial_reps.add(fi_rep)
            self._assert_single_future_known_product(spec)

            for variables in spec.past_known.values():
                past_variables.extend(variables.items())

            for variables in spec.future_known.values():
                for name, variable in variables.items():
                    future_dynamic_features.add(name)
                    future_variables.append((name, variable))
                    if variable.ensemble_mode is FIEnsembleMode.ENSEMBLE:
                        any_ensemble_future = True
                    if forecast_horizon_steps is None:
                        forecast_horizon_steps = variable.future_steps
                    else:
                        forecast_horizon_steps = max(
                            forecast_horizon_steps, variable.future_steps
                        )

        # These three are all "a VALID FI requirement whose shape SAP3 cannot
        # represent" — the same class as the multi-future-forced-branch guard
        # above, and therefore the same exception type. Using ConfigurationError
        # here would defeat the plan's registry invariant: discover_models()
        # re-raises ConfigurationError, so ONE model declaring an unsupported
        # (but legal) shape would darken discovery for EVERY model. Genuine
        # application-configuration faults — a missing ModelTier, say — keep
        # raising ConfigurationError and keep hard-failing. Review major,
        # 2026-08-13.
        if not spatial_reps:
            raise UnsupportedModelRequirementError(
                "cannot derive spatial input type: InputRequirement declares no "
                "dynamic input"
            )
        if len(spatial_reps) > 1:
            rep_names = ", ".join(sorted(rep.value for rep in spatial_reps))
            raise UnsupportedModelRequirementError(
                f"multi-spatial input not supported in v1: {rep_names}"
            )
        if forecast_horizon_steps is None:
            raise UnsupportedModelRequirementError(
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

        # Plan 228 review fixer round (blocker): a model's DECLARED
        # per-variable aggregation (`PastKnownVariable.aggregation` /
        # `FutureKnownVariable.aggregation`) must survive projection into
        # `ModelDataRequirements` — dropping it here is exactly what let
        # input assembly fall back to matching purely on parameter NAME,
        # silently downgrading a legally-declared MAX to MEAN. `None`
        # (undeclared) is skipped, not recorded as a conflict-checkable
        # value; the SAME name declared with two DIFFERENT non-None methods
        # (across branches, or between a past- and future-known variable) is
        # a genuine model-configuration fault and raises, exactly like the
        # existing unit-conflict check below.
        declared_aggregation: dict[str, AggregationMethod] = {}
        for name, past_variable in past_variables:
            if past_variable.aggregation is None:
                continue
            self._record_conflict_checked(
                values=declared_aggregation,
                name=name,
                value=fi_aggregation_to_canonical(past_variable.aggregation),
                label="aggregation",
            )
        for name, future_variable in future_variables:
            if future_variable.aggregation is None:
                continue
            self._record_conflict_checked(
                values=declared_aggregation,
                name=name,
                value=fi_aggregation_to_canonical(future_variable.aggregation),
                label="aggregation",
            )

        [spatial_rep] = spatial_reps
        return ModelDataRequirements(
            target_parameters=frozenset(req.targets),
            past_dynamic_features=frozenset(past_dynamic_features),
            future_dynamic_features=frozenset(future_dynamic_features),
            static_features=frozenset(req.static),
            # Plan 156: the FUTURE-FORCED branch(es) only — never a past-only
            # branch — so a downstream `next(iter(supported_time_steps))`
            # (services/model_onboarding.py, services/onboarding.py) can no
            # longer land on a resolution the model cannot forecast at. At
            # most one entry survives the guard above.
            supported_time_steps=frozenset(future_forced_time_steps),
            lookback_steps=lookback_steps,
            # V1 proxy: FI declares horizon only at output time; future_steps is
            # the input-forcing length used as the horizon proxy, endorsed in SF2.
            forecast_horizon_steps=forecast_horizon_steps,
            spatial_input_type=SpatialRepresentation(spatial_rep.value),
            ensemble_mode=(
                EnsembleMode.ENSEMBLE if any_ensemble_future else EnsembleMode.SINGLE
            ),
            declared_aggregations=frozenset(declared_aggregation.items()),
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

    def _past_known_nan_tolerances(self) -> dict[str, int]:
        tolerances: dict[str, int] = {}

        for name, variable in self._iter_past_known_variables(
            self._model.input_requirement
        ):
            self._record_conflict_checked(
                values=tolerances,
                name=name,
                value=variable.max_nan,
                label="max_nan",
            )

        return tolerances

    def _future_known_nan_tolerances(self) -> dict[str, int]:
        tolerances: dict[str, int] = {}

        for name, variable in self._iter_future_known_variables(
            self._model.input_requirement
        ):
            self._record_conflict_checked(
                values=tolerances,
                name=name,
                value=variable.max_nan,
                label="max_nan",
            )

        return tolerances

    def _past_known_nan_tolerances_for_spec(
        self, spec: DynamicInputSpec
    ) -> dict[str, int]:
        """Plan 151 T6 (D9): the SELECTED branch's own tolerances — no
        flattening across every declared branch. Companion to
        `_past_known_nan_tolerances`, which stays the GROUP path's
        unmigrated (D8-group) flattened form."""
        tolerances: dict[str, int] = {}
        for variables in spec.past_known.values():
            for name, variable in variables.items():
                self._record_conflict_checked(
                    values=tolerances,
                    name=name,
                    value=variable.max_nan,
                    label="max_nan",
                )
        return tolerances

    def _future_known_nan_tolerances_for_spec(
        self, spec: DynamicInputSpec
    ) -> tuple[dict[str, int], dict[str, int]]:
        """Companion to `_past_known_nan_tolerances_for_spec` (D9). Also
        returns each variable's OWN declared ``future_steps`` — the NaN gate
        slices to it (D9: "a short-horizon feature is not counted NaN for
        lacking long-horizon steps") exactly as `_future_known_inputs` slices
        for `InputSeries` construction."""
        tolerances: dict[str, int] = {}
        future_steps: dict[str, int] = {}
        for variables in spec.future_known.values():
            for name, variable in variables.items():
                self._record_conflict_checked(
                    values=tolerances,
                    name=name,
                    value=variable.max_nan,
                    label="max_nan",
                )
                self._record_conflict_checked(
                    values=future_steps,
                    name=name,
                    value=variable.future_steps,
                    label="future_steps",
                )
        return tolerances, future_steps

    def _variables_over_nan_tolerance(
        self,
        *,
        past_targets: pl.DataFrame,
        past_dynamic: pl.DataFrame,
        future_dynamic: pl.DataFrame,
        time_step: timedelta | None = None,
        forcing_route: ForcingRoute = ForcingRoute.LEGACY_SUPERSET,
    ) -> dict[str, int]:
        # Gated independently per temporality (past_known vs future_known) and
        # checked against ONLY that temporality's frame(s). A model may declare
        # a past_known and a future_known variable with the SAME bare name
        # (e.g. SeasonalPrecipRunoffRegression's past_known
        # reanalysis/precipitation vs the base's future_known nwp/precipitation,
        # Plan 129) — resolving both to a single name-keyed tolerance dict and
        # picking "whichever frame has the column first" would silently drop
        # the NaN gate for whichever temporality is checked second. Reporting
        # keys are prefixed with the temporality so a name collision across
        # past/future stays distinguishable in the raised/logged detail.
        #
        # Plan 151 T6 (D9) + D10: ``forcing_route`` — NOT the presence of
        # ``time_step`` — is the discriminant here ("explicit discriminant,
        # never inference"). Only PER_TRACK inputs take the per-spec path,
        # where both tolerance maps derive from THAT ONE `DynamicInputSpec`
        # and each future_known variable's own declared ``future_steps``
        # bounds the count.
        #
        # LEGACY_SUPERSET keeps the pre-T6 FLATTENED tolerance maps and never
        # resolves ``time_step`` *in this gate* — that route is byte-for-byte
        # unchanged, error paths included. (Later input conversion still
        # calls `_dynamic_spec_for_time_step` for BOTH routes, exactly as
        # pre-T6 did: `_station_inputs_from_frames`.) Resolving it here would
        # let `_dynamic_spec_for_time_step`'s ConfigurationError pre-empt the
        # pre-T6 missing-column / max_nan error whenever a legacy caller
        # carries a ``time_step`` the requirement does not declare. The GROUP
        # path (predict_batch, D8-group — out of Phase 3 scope) reaches here
        # with the LEGACY default and ``time_step is None``, so it lands on
        # the same flattened branch it always did.
        future_steps_by_name: dict[str, int] = {}
        if forcing_route is ForcingRoute.PER_TRACK:
            # The route is the ONLY discriminant: a missing ``time_step`` must
            # never demote a PER_TRACK input onto the flattened legacy branch,
            # which would silently apply tolerances this input never selected.
            # ``StationModelInputs.time_step`` is statically non-optional
            # (`types/model.py`) but unvalidated at runtime, and this helper's
            # own signature admits ``None`` — so fail loudly here, at the
            # boundary, BEFORE `predict()` runs. This is a SAP3-side
            # configuration fault, not an anticipated model failure, so it
            # raises (matching `_dynamic_spec_for_time_step`) rather than
            # returning ModelFailure.
            if time_step is None:
                declared = ", ".join(
                    str(step) for step in self._model.input_requirement.dynamic
                )
                raise ConfigurationError(
                    "ForecastInterface NaN gate reached on the "
                    f"{ForcingRoute.PER_TRACK.value!r} forcing route without a "
                    "time_step; per-track inputs must carry the resolved "
                    "time_step that selects the DynamicInputSpec "
                    f"(declared time steps: {declared})"
                )
            _, spec = self._dynamic_spec_for_time_step(time_step)
            past_tolerances = self._past_known_nan_tolerances_for_spec(spec)
            future_tolerances, future_steps_by_name = (
                self._future_known_nan_tolerances_for_spec(spec)
            )
        else:
            past_tolerances = self._past_known_nan_tolerances()
            future_tolerances = self._future_known_nan_tolerances()

        over_tolerance: dict[str, int] = {}

        for name, tolerance in past_tolerances.items():
            frame = self._frame_with_column(
                name=name,
                frames=(
                    ("past_dynamic", past_dynamic),
                    ("past_targets", past_targets),
                ),
                temporality="past_known",
            )
            missing_count = self._missing_value_count(frame=frame, name=name)
            if missing_count > tolerance:
                over_tolerance[f"past_known.{name}"] = missing_count

        for name, tolerance in future_tolerances.items():
            frame = self._frame_with_column(
                name=name,
                frames=(("future_dynamic", future_dynamic),),
                temporality="future_known",
            )
            # Plan 151 T6 (D9): slice to THIS variable's own declared
            # `future_steps` before counting — a short-horizon feature must
            # not be evaluated over the whole (possibly longer, D5/D26)
            # frame a sibling feature/assignment needed. `future_steps_by_name`
            # is empty on every non-PER_TRACK route (D10), so the LEGACY
            # superset route and the GROUP path are byte-for-byte unchanged.
            if name in future_steps_by_name:
                frame = self._slice_to_future_steps(
                    frame=frame, name=name, future_steps=future_steps_by_name[name]
                )
            missing_count = self._missing_value_count(frame=frame, name=name)
            if missing_count > tolerance:
                over_tolerance[f"future_known.{name}"] = missing_count

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
        self._assert_single_deliverable_dynamic_branch()
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

        # BEFORE the NaN gate (review blocker, 2026-08-13): the gate flattens
        # across ALL branches, so a multi-branch requirement previously
        # surfaced as a misleading ModelOutputError ("max_nan exceeded") or
        # ConfigurationError ("missing <past-only-branch variable>") rather
        # than the real cause. The guard must be the FIRST thing every
        # delivery entry point does.
        self._assert_single_deliverable_dynamic_branch()

        over_tolerance = self._variables_over_nan_tolerance(
            past_targets=inputs.data.past_targets,
            past_dynamic=inputs.data.past_dynamic,
            future_dynamic=inputs.data.future_dynamic,
            time_step=inputs.time_step,
            forcing_route=inputs.forcing_route,
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

        # BEFORE the per-station NaN gate — see predict() above.
        self._assert_single_deliverable_dynamic_branch()

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

    def _iter_past_known_variables(
        self, req: InputRequirement
    ) -> Iterator[tuple[str, PastKnownVariable]]:
        for _, spec in self._iter_dynamic_specs(req):
            for variables in spec.past_known.values():
                yield from variables.items()

    def _iter_future_known_variables(
        self, req: InputRequirement
    ) -> Iterator[tuple[str, FutureKnownVariable]]:
        for _, spec in self._iter_dynamic_specs(req):
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
        # Plan 151 T6 (D9) + D10: the per-variable future_steps slice applies
        # on the single-station, single-cycle `predict()` route AND ONLY when
        # `forcing_route` says these inputs came from the per-track assembler.
        # Both `train()` (a multi-sample window, `.head(future_steps)` would
        # truncate the whole training set to its first N rows) and
        # `predict_batch` (GROUP, explicitly out of Phase 3 scope, D8-group)
        # keep the UNSLICED frame — this is the one place a `future_dynamic`
        # frame genuinely represents "one cycle's worth of future rows".
        #
        # The LEGACY superset route (`services/operational_inputs.py`) also
        # keeps the UNSLICED frame: `build_superset_requirements` sizes ONE
        # frame to the MAX horizon across the station's co-assigned models
        # (`services/operational_inputs.py:495`), so a shorter-horizon model
        # is routinely over-delivered BY DESIGN and forecasts the full frame
        # (`models/nwp_regression.py`: "Over-delivery ... is tolerated and
        # forecast in full"). Slicing there would silently shorten that
        # model's horizon.
        station_inputs = self._station_inputs_from_station_data(
            data=data.data,
            time_step=data.time_step,
            forcing_route=data.forcing_route,
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
        forcing_route: ForcingRoute = ForcingRoute.LEGACY_SUPERSET,
    ) -> StationInputs:
        return self._station_inputs_from_frames(
            past_targets=data.past_targets,
            past_dynamic=data.past_dynamic,
            future_dynamic=data.future_dynamic,
            static=data.static,
            time_step=time_step,
            forcing_route=forcing_route,
        )

    def _assert_single_deliverable_dynamic_branch(self) -> None:
        # Plan 156 (blocker follow-up): a requirement with one FUTURE-FORCED
        # branch plus a past-only branch is ACCEPTED at construction (Plan
        # 151 T2 needs that shape constructible), but delivery below builds
        # only ONE `dynamic={time_step: ...}` entry — the ACTIVE branch
        # matching the caller's `time_step`. A second, past-only branch's
        # variables (e.g. a daily `soil_moisture`) would be fetched and
        # NaN-checked (both flatten across ALL branches) yet silently
        # OMITTED from what the model actually receives — an incomplete
        # input that produces a plausible but wrong result, exactly what
        # this plan exists to prevent. Until real multi-resolution delivery
        # lands (Plan 153), fail loudly here instead of construction time,
        # so Plan 151's per-branch accessors stay usable without the
        # adapter claiming full operational support.
        branches = self._model.input_requirement.dynamic
        if len(branches) > 1:
            resolutions = ", ".join(str(step) for step in sorted(branches))
            raise UnsupportedModelRequirementError(
                "ForecastInterface InputRequirement declares more than one "
                f"time_step branch ({resolutions}); SAP3 can only deliver "
                "ONE branch's dynamic inputs per predict/train call, so the "
                "non-active branch(es) would be silently omitted from "
                "ModelInputs. Multi-resolution input delivery is not yet "
                "supported (Plan 153)."
            )

    def _station_inputs_from_frames(
        self,
        *,
        past_targets: pl.DataFrame,
        past_dynamic: pl.DataFrame,
        future_dynamic: pl.DataFrame,
        static: pl.DataFrame | None,
        time_step: timedelta,
        forcing_route: ForcingRoute = ForcingRoute.LEGACY_SUPERSET,
    ) -> StationInputs:
        self._assert_single_deliverable_dynamic_branch()
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
                forcing_route=forcing_route,
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
        forcing_route: ForcingRoute = ForcingRoute.LEGACY_SUPERSET,
    ) -> dict[str, dict[str, InputSeries]]:
        def _series_frame(name: str, variable: FutureKnownVariable) -> pl.DataFrame:
            frame = self._frame_with_column(
                name=name,
                frames=(("future_dynamic", future_dynamic),),
                temporality="future_known",
            )
            if forcing_route is ForcingRoute.PER_TRACK:
                frame = self._slice_to_future_steps(
                    frame=frame, name=name, future_steps=variable.future_steps
                )
            return frame

        return {
            product: product_inputs
            for product, variables in spec.future_known.items()
            if (
                product_inputs := {
                    name: self._input_series_from_frame(
                        frame=_series_frame(name, variable),
                        name=name,
                        unit=variable.unit,
                    )
                    for name, variable in variables.items()
                }
            )
        }

    def _slice_to_future_steps(
        self, *, frame: pl.DataFrame, name: str, future_steps: int
    ) -> pl.DataFrame:
        """Plan 151 T6 (D9): the earliest ``future_steps`` rows by
        ``timestamp`` — this variable's OWN declared horizon, never the
        frame's full length. A per-assignment frame may be assembled longer
        than one variable needs (its own `InputFrameHorizon` is the MAX
        across the assignment's own features, D9), and the underlying track
        may be fetched longer still to satisfy a sibling assignment sharing
        the track (D5/D26) — this is the slice that keeps a short-horizon
        feature from receiving (and being NaN-checked against) more rows
        than it ever declared.

        ``frame`` is ``future_dynamic`` — a UNION frame potentially pivoted
        across every future_known variable, each of which may carry its own
        feature-local timestamp set (D9 permits this). A row absent from
        THIS variable's own set is a STRUCTURAL null the pivot introduced,
        never a value this variable declared and never one of its "earliest"
        rows (review fold-in — blocker): filtering to ``name``'s non-null
        rows BEFORE sorting/capping is what makes ``head(future_steps)``
        select this variable's own earliest real values rather than the
        union frame's earliest positions, which can interleave a sibling
        variable's longer-horizon-only timestamps ahead of this variable's
        genuine (but later-sorted-among-nulls) ones. A genuine in-window
        NaN (a float NaN, not a polars null) still survives this filter and
        is still counted by the NaN gate — only STRUCTURAL absence is
        removed.

        Reached ONLY from ``ForcingRoute.PER_TRACK`` inputs (D10); the legacy
        superset route never calls it."""
        if "timestamp" not in frame.columns:
            return frame
        return (
            frame.filter(pl.col(name).is_not_null())
            .sort("timestamp")
            .head(future_steps)
        )

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
