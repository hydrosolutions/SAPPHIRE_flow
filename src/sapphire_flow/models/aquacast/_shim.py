"""Plan 159 T1 — the aquacast shim: zero-argument construction + the SAP3 boundary.
Plan 181 completes the boundary: proxying the five FI methods (T1), rewriting the
declaration to canonical names/units (T2), and translating the data both directions
(T3).

Two things force this class to exist, both verified against the real package:

1. **Construction.** `discover_models` builds every entry point with NO arguments
   (`services/model_registry.py`), while `AquacastModel.__init__` requires a
   `template` (`aquacast/operational/model.py`). SAP3 therefore cannot instantiate
   it, and something must bind the config at import time. That is what a
   zero-argument subclass per trained config is for.
2. **Discovery.** `discover_models()` sees only INSTALLED entry points, and aquacast
   declares none at all.

Two further boundaries are handled here by CHOICE, not necessity (Plan 159 D17 holds
the reasoning): SAP3 does have `area`, so the unit conversion could live in the FI
adapter. Keeping it here keeps `adapters/forecast_interface.py` — the single
SAP3<->FI boundary — free of per-model special cases.

**Not fixed here, deliberately:** `cmal_pool_PT` declares `future_steps=15` even
though the modeller relaxed the trained horizon to a *maximum*. The FI contract has no
"at most" form, so a strict provider still refuses a short horizon. The shim must NOT
paper over that by declaring a smaller number — that would silently claim a capability
the artifact does not advertise. See
`docs/fi-issues/002-future-steps-at-most-semantics.md`.

**Plan 181 design note — why the shim converts numerically, not just relabels.**
Aquacast's OWN `InMemoryDataSource` (`aquacast/operational/datasource.py`) already
converts a declared FI unit into whatever it internally expects, area included. So in
production, relabeling alone (handing aquacast the SAP3-canonical unit + untouched
values) would ALSO end up numerically correct once the real inner model runs. But this
shim's own tests exercise it against a FAKE inner (Plan 159 established that pattern to
keep the FI-surface tests fast and real-package-grounded without needing a trained
artifact), so nothing downstream of `self._inner.predict(...)` ever runs to do that
conversion. The shim therefore owns the numeric translation itself, using `_units.py`,
and hands the inner model values already in ITS OWN declared units — which also means a
real `AquacastModel` receives an identity conversion at its own boundary (no double
scaling).
"""

from __future__ import annotations

import importlib
from datetime import timedelta
from importlib import resources
from typing import TYPE_CHECKING, Any, ClassVar, Final, TypeVar

import polars as pl
from forecast_interface import (
    DeterministicData,
    DynamicInputs,
    DynamicInputSpec,
    EpistemicUncertaintyData,
    FailureCause,
    InputRequirement,
    InputSeries,
    ModelFailure,
    ModelInputs,
    ModelOutput,
    ModelSuccess,
    QuantileData,
    SpatialInputs,
    SpatialInputSpec,
    StationInputs,
    TrajectoryData,
    Unit,
)

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.models.aquacast._units import (
    AreaConversionError,
    m3_per_s_to_mm_per_day,
    mm_per_day_to_m3_per_s,
)
from sapphire_flow.types.enums import AlertEligibility, ModelTier, StaticNaming

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from pathlib import Path
    from random import Random

    from forecast_interface import (
        FutureKnownVariable,
        ModelResult,
        PastKnownVariable,
        TrainedArtifact,
        VariableOutput,
    )

_CONFIG_PACKAGE: Final[str] = "sapphire_flow.models.aquacast.configs"

# aquacast declares `mean_temperature`; SAP3's canonical dynamic vocabulary is
# {"precipitation", "temperature"} (`config/deployment.py`). One entry, not a general
# mechanism: a second divergence should be a second explicit line, not a pattern.
AQUACAST_TO_CANONICAL_NAME: Final[dict[str, str]] = {"mean_temperature": "temperature"}
CANONICAL_TO_AQUACAST_NAME: Final[dict[str, str]] = {
    canonical: aquacast for aquacast, canonical in AQUACAST_TO_CANONICAL_NAME.items()
}

# The two names Plan 181 gives a special-cased unit translation. Same "one entry, not
# a general mechanism" style as the name map above.
_DISCHARGE: Final[str] = "discharge"
_PRECIPITATION: Final[str] = "precipitation"
_DAILY_STEP: Final[timedelta] = timedelta(days=1)


def _config_path(filename: str) -> Path:
    """The vendored trained config, shipped as package data (Plan 152 D1).

    Read at IMPORT time because the adapter computes `data_requirements` during
    construction and `discover_models` constructs with no arguments — there is no
    later hook to supply it.
    """
    return resources.files(_CONFIG_PACKAGE).joinpath(filename)  # type: ignore[return-value]


# ---------------------------------------------------------------------------------
# T2 — declaration rewrite: aquacast's own InputRequirement -> SAP3-canonical.
# ---------------------------------------------------------------------------------


def _translate_target_unit(name: str, unit: Unit) -> Unit:
    if name == _DISCHARGE and unit is Unit.MM_PER_DAY:
        return Unit.M3_PER_S
    return unit


def _translate_declared_unit(name: str, unit: Unit, *, time_step: timedelta) -> Unit:
    """aquacast's declared (name, unit) -> the SAP3-canonical unit for it.

    Plan 181 D1: the discharge conversion is a genuine physical conversion (a rate,
    valid at any step); the precipitation relabel is numerically identical to the
    aquacast unit ONLY at a daily step, so a non-daily branch must raise rather than
    silently be wrong by up to 8x.
    """
    if name == _DISCHARGE and unit is Unit.MM_PER_DAY:
        return Unit.M3_PER_S
    if name == _PRECIPITATION and unit is Unit.MM_PER_DAY:
        if time_step != _DAILY_STEP:
            raise ConfigurationError(
                f"cannot relabel {_PRECIPITATION!r} {unit.value!r} as "
                f"{Unit.MM.value!r} at time_step {time_step}: mm/day over a "
                "non-daily step is not mm (Plan 181 D1)"
            )
        return Unit.MM
    return unit


_DeclaredVariable = TypeVar(
    "_DeclaredVariable", "PastKnownVariable", "FutureKnownVariable"
)


def _translate_declared_group(
    group: dict[str, dict[str, _DeclaredVariable]],
    *,
    time_step: timedelta,
) -> dict[str, dict[str, _DeclaredVariable]]:
    return {
        product: {
            AQUACAST_TO_CANONICAL_NAME.get(name, name): variable.model_copy(
                update={
                    "unit": _translate_declared_unit(
                        name, variable.unit, time_step=time_step
                    )
                }
            )
            for name, variable in variables.items()
        }
        for product, variables in group.items()
    }


def _canonical_requirement(req: InputRequirement) -> InputRequirement:
    """aquacast's own `input_requirement` -> the SAP3-canonical declaration this shim
    exposes. Preserves the nesting (`dynamic[step].data[spatial].{past,future}_known
    [SOURCE][name]`, source key "aquacast") and every non-name/unit field."""
    return InputRequirement(
        targets={
            AQUACAST_TO_CANONICAL_NAME.get(name, name): spec.model_copy(
                update={"unit": _translate_target_unit(name, spec.unit)}
            )
            for name, spec in req.targets.items()
        },
        dynamic={
            time_step: SpatialInputSpec(
                data={
                    rep: DynamicInputSpec(
                        past_known=_translate_declared_group(
                            spec.past_known, time_step=time_step
                        ),
                        future_known=_translate_declared_group(
                            spec.future_known, time_step=time_step
                        ),
                    )
                    for rep, spec in spatial_spec.data.items()
                }
            )
            for time_step, spatial_spec in req.dynamic.items()
        },
        static=set(req.static),
    )


# ---------------------------------------------------------------------------------
# T3 — data translation, inbound (canonical -> aquacast-native).
# ---------------------------------------------------------------------------------


def _scale_columns(
    frame: pl.DataFrame, *, columns: Sequence[str], factor: float
) -> pl.DataFrame:
    if not columns:
        return frame
    return frame.with_columns((pl.col(c) * factor).alias(c) for c in columns)


def _discharge_scale(*, area_km2: object, station: str, to_aquacast: bool) -> float:
    """Multiplicative factor for ONE discharge value, in the requested direction.

    Delegates entirely to `_units.py`'s validated, tested conversion — computed
    against 1.0 so the returned number IS the scale factor. `area_km2` is `object`
    because it comes straight off `StationInputs.static` (`int | float | str`); the
    narrowing to a real `float` (or the D3 "raise, naming the station" failure) is
    `_units.py`'s job, not this call site's.
    """
    if to_aquacast:
        return m3_per_s_to_mm_per_day(1.0, area_km2=area_km2, station=station)  # type: ignore[arg-type]
    return mm_per_day_to_m3_per_s(1.0, area_km2=area_km2, station=station)  # type: ignore[arg-type]


def _aquacast_value_and_unit(
    name: str,
    unit: Unit,
    data: pl.DataFrame,
    *,
    value_columns: Sequence[str],
    area_km2: object,
    station: str,
    time_step: timedelta,
) -> tuple[Unit, pl.DataFrame]:
    """canonical -> aquacast, for ONE declared (name, unit, data) triple.

    Mirrors `_translate_declared_unit`'s D1 daily-step guard: the precipitation
    `mm` -> `mm/day` relabel is numerically identical to the aquacast unit ONLY at a
    daily step, so this data path must refuse a non-daily step exactly like the
    declaration path does, rather than silently mislabeling by up to 8x.
    """
    if name == _DISCHARGE and unit is Unit.M3_PER_S:
        factor = _discharge_scale(area_km2=area_km2, station=station, to_aquacast=True)
        return Unit.MM_PER_DAY, _scale_columns(
            data, columns=value_columns, factor=factor
        )
    if name == _PRECIPITATION and unit is Unit.MM:
        if time_step != _DAILY_STEP:
            raise ConfigurationError(
                f"cannot relabel {_PRECIPITATION!r} {unit.value!r} as "
                f"{Unit.MM_PER_DAY.value!r} at time_step {time_step}: mm over a "
                "non-daily step is not mm/day (Plan 181 D1)"
            )
        return Unit.MM_PER_DAY, data
    return unit, data


def _series_inbound(
    name: str,
    series: InputSeries,
    *,
    area_km2: object,
    station: str,
    time_step: timedelta,
) -> tuple[str, InputSeries]:
    aquacast_name = CANONICAL_TO_AQUACAST_NAME.get(name, name)
    value_columns = [c for c in series.data.columns if c != "datetime"]
    unit, data = _aquacast_value_and_unit(
        name,
        series.unit,
        series.data,
        value_columns=value_columns,
        area_km2=area_km2,
        station=station,
        time_step=time_step,
    )
    if len(value_columns) == 1 and value_columns[0] != aquacast_name:
        data = data.rename({value_columns[0]: aquacast_name})
    return aquacast_name, InputSeries(unit=unit, data=data)


def _translate_data_group(
    group: dict[str, dict[str, InputSeries]],
    *,
    area_km2: object,
    station: str,
    time_step: timedelta,
) -> dict[str, dict[str, InputSeries]]:
    return {
        product: dict(
            _series_inbound(
                name, series, area_km2=area_km2, station=station, time_step=time_step
            )
            for name, series in variables.items()
        )
        for product, variables in group.items()
    }


def _to_aquacast_station_inputs(
    station_key: str, station: StationInputs
) -> StationInputs:
    area_km2 = station.static.get("area")
    return StationInputs(
        static=station.static,
        dynamic={
            time_step: SpatialInputs(
                data={
                    rep: DynamicInputs(
                        past_known=_translate_data_group(
                            dyn.past_known,
                            area_km2=area_km2,
                            station=station_key,
                            time_step=time_step,
                        ),
                        future_known=_translate_data_group(
                            dyn.future_known,
                            area_km2=area_km2,
                            station=station_key,
                            time_step=time_step,
                        ),
                    )
                    for rep, dyn in spatial.data.items()
                }
            )
            for time_step, spatial in station.dynamic.items()
        },
    )


def _to_aquacast_inputs(inputs: ModelInputs) -> ModelInputs:
    return ModelInputs(
        stations={
            station_key: _to_aquacast_station_inputs(station_key, station)
            for station_key, station in inputs.stations.items()
        }
    )


# ---------------------------------------------------------------------------------
# T3 — data translation, outbound (aquacast-native -> canonical).
# ---------------------------------------------------------------------------------

_TEMPORAL_COLUMNS: Final[frozenset[str]] = frozenset({"issue_datetime", "datetime"})

_DataContainer = TypeVar(
    "_DataContainer",
    DeterministicData,
    QuantileData,
    TrajectoryData,
    EpistemicUncertaintyData,
)


def _scaled_data_container(
    container: _DataContainer | None, *, factor: float
) -> _DataContainer | None:
    if container is None:
        return None
    value_columns = [c for c in container.data.columns if c not in _TEMPORAL_COLUMNS]
    return container.model_copy(
        update={
            "data": _scale_columns(container.data, columns=value_columns, factor=factor)
        }
    )


def _area_for_output_station(inputs: ModelInputs, station_key: str) -> object:
    try:
        station = inputs.stations[station_key]
    except KeyError as exc:
        raise ConfigurationError(
            f"aquacast returned station {station_key!r}, which was not present in "
            "the inputs it was given"
        ) from exc
    return station.static.get("area")


def _variable_output_outbound(
    name: str, var: VariableOutput, *, area_km2: object, station: str
) -> VariableOutput:
    if name != _DISCHARGE or var.metadata.unit is not Unit.MM_PER_DAY:
        return var
    factor = _discharge_scale(area_km2=area_km2, station=station, to_aquacast=False)
    return var.model_copy(
        update={
            "metadata": var.metadata.model_copy(update={"unit": Unit.M3_PER_S}),
            "deterministic": _scaled_data_container(var.deterministic, factor=factor),
            "quantiles": _scaled_data_container(var.quantiles, factor=factor),
            "trajectories": _scaled_data_container(var.trajectories, factor=factor),
            "epistemic_uncertainty": _scaled_data_container(
                var.epistemic_uncertainty, factor=factor
            ),
        }
    )


def _to_canonical_output(output: ModelOutput, *, inputs: ModelInputs) -> ModelOutput:
    return ModelOutput(
        model_name=output.model_name,
        issue_datetime=output.issue_datetime,
        variables={
            station_key: {
                name: _variable_output_outbound(
                    name,
                    var,
                    area_km2=_area_for_output_station(inputs, station_key),
                    station=station_key,
                )
                for name, var in station_vars.items()
            }
            for station_key, station_vars in output.variables.items()
        },
    )


def _to_canonical_result(result: ModelResult, *, inputs: ModelInputs) -> ModelResult:
    if isinstance(result, ModelFailure):
        return result
    return ModelSuccess(output=_to_canonical_output(result.output, inputs=inputs))


def _area_failure(
    exc: AreaConversionError, *, model_name: str, issue_datetime: datetime
) -> ModelFailure:
    """A missing/invalid station `area` is an ANTICIPATED input-data failure — the
    mandatory FI rule is `ModelFailure`, never a raise, for exactly this case
    (`CLAUDE.md` § ForecastInterface Adherence). `AreaConversionError` is the ONLY
    `ConfigurationError` subtype this boundary intercepts; every other
    `ConfigurationError` (e.g. D1's non-daily precipitation guard) keeps raising —
    those are configuration/programming defects, not anticipated bad station data."""
    return ModelFailure(
        model_name=model_name,
        issue_datetime=issue_datetime,
        cause=FailureCause.INPUT_DATA,
        message=str(exc),
    )


class AquacastShim:
    """Base for one trained aquacast config.

    Subclasses set `CONFIG_FILENAME` and the classification attributes
    `discover_models` requires. Instantiating this base directly is a programming
    error — it has no config to bind.
    """

    CONFIG_FILENAME: ClassVar[str]

    # `discover_models` requires both, via MODEL_TIERS/ALERT_ELIGIBILITIES or these
    # attributes. Declared per subclass so a new artifact must state its own.
    model_tier: ClassVar[ModelTier]
    alert_eligibility: ClassVar[AlertEligibility]

    # Plan 155 D16: aquacast's statics are Caravan-named, so this model opts into the
    # strict `caravan:`-namespaced, no-bare-fallback resolution. Plan 155 records the
    # shim as the natural owner of this flag, since it already binds the config.
    static_naming: ClassVar[StaticNaming] = StaticNaming.CARAVAN

    def __init__(self) -> None:
        cls = type(self)
        filename = getattr(cls, "CONFIG_FILENAME", None)
        if not filename:
            raise TypeError(
                f"{cls.__name__} must set CONFIG_FILENAME — AquacastShim binds a "
                "trained config at import time and cannot be constructed without one"
            )
        # A stable name for `ModelFailure.model_name`, captured up front: an
        # anticipated `predict`/`hindcast` input failure (Plan 181 fixer, FI
        # adherence) must be able to name itself even though the failure can happen
        # BEFORE `self._inner` is ever reached.
        self._model_name: str = filename.removesuffix(".yaml")
        # Imported lazily so `sapphire_flow` stays importable WITHOUT the `aquacast`
        # extra. discover_models tolerates an entry point that fails to construct, but
        # an import-time failure here would break unrelated model discovery.
        # `aquacast` is an OPTIONAL extra, absent from the base install that CI
        # type-checks. Imported through `importlib` rather than a `from` statement so
        # the optionality is explicit and no unresolved-import diagnostics have to be
        # silenced: the module genuinely is not present in every environment.
        config_mod: Any = importlib.import_module("aquacast.operational.config")
        model_mod: Any = importlib.import_module("aquacast.operational.model")

        template: Any = config_mod.ModelTemplate.from_yaml(str(_config_path(filename)))
        self._inner: Any = model_mod.AquacastModel(template)

    @property
    def artifact_scope(self) -> object:
        return self._inner.artifact_scope

    @property
    def input_requirement(self) -> InputRequirement:
        return _canonical_requirement(self._inner.input_requirement)

    def train(
        self, inputs: ModelInputs, *, config: Mapping[str, Any], rng: Random
    ) -> TrainedArtifact:
        return self._inner.train(_to_aquacast_inputs(inputs), config=config, rng=rng)

    def predict(
        self,
        artifact: TrainedArtifact,
        *,
        inputs: ModelInputs,
        issue_datetime: datetime,
        rng: Random,
    ) -> ModelResult:
        try:
            aquacast_inputs = _to_aquacast_inputs(inputs)
        except AreaConversionError as exc:
            return _area_failure(
                exc, model_name=self._model_name, issue_datetime=issue_datetime
            )
        result = self._inner.predict(
            artifact, inputs=aquacast_inputs, issue_datetime=issue_datetime, rng=rng
        )
        return _to_canonical_result(result, inputs=aquacast_inputs)

    def serialize_artifact(self, artifact: TrainedArtifact) -> bytes:
        return self._inner.serialize_artifact(artifact)

    def deserialize_artifact(self, raw: bytes) -> TrainedArtifact:
        return self._inner.deserialize_artifact(raw)

    def hindcast(
        self,
        artifact: TrainedArtifact,
        *,
        inputs: ModelInputs,
        issue_datetimes: Sequence[datetime],
        rng: Random,
    ) -> ModelResult:
        inner_hindcast = getattr(self._inner, "hindcast", None)
        if inner_hindcast is None:
            raise ConfigurationError(
                f"{type(self._inner).__name__} does not implement hindcast; "
                f"{type(self).__name__} cannot proxy it"
            )
        try:
            aquacast_inputs = _to_aquacast_inputs(inputs)
        except AreaConversionError as exc:
            # Mirrors aquacast's OWN hindcast (`operational/model.py`), which reports
            # a whole-batch failure against `issue_list[0]` — the earliest requested
            # issue — since `ModelFailure.issue_datetime` is singular.
            return _area_failure(
                exc, model_name=self._model_name, issue_datetime=issue_datetimes[0]
            )
        result = inner_hindcast(
            artifact, inputs=aquacast_inputs, issue_datetimes=issue_datetimes, rng=rng
        )
        return _to_canonical_result(result, inputs=aquacast_inputs)


class CmalPoolPT(AquacastShim):
    """`cmal_pool_PT` — the pooled CMAL artifact (12,952 basins).

    DAILY only, precipitation + temperature, 50 Caravan-named statics, quantile and
    deterministic heads, ArtifactScope.GROUP.
    """

    CONFIG_FILENAME = "cmal_pool_pt.yaml"
    model_tier = ModelTier.SKILL
    alert_eligibility = AlertEligibility.SKILL_FORECAST
