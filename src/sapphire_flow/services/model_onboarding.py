from __future__ import annotations

import hashlib
import time
import traceback
from datetime import UTC
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

import polars as pl
import structlog

from sapphire_flow.exceptions import (
    ConfigurationError,
    ModelSmokeTestError,
    StoreError,
)
from sapphire_flow.types.enums import (
    ArtifactScope,
    EnsembleRepresentation,
    ModelAssignmentStatus,
    OnboardingOutcome,
    SpatialRepresentation,
    StationStatus,
)
from sapphire_flow.types.ids import StationGroupId, StationId
from sapphire_flow.types.model_onboarding import (
    CompatibilityReport,
    ModelOnboardingResult,
    OnboardingUnitResult,
    SkillGateResult,
)
from sapphire_flow.types.station import GroupModelAssignment, ModelAssignment
from sapphire_flow.types.training import TrainingUnit

if TYPE_CHECKING:
    import random
    from collections.abc import Callable, Mapping
    from datetime import timedelta

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.protocols.forecast_model import (
        ForecastModel,
        GroupForecastModel,
        StationForecastModel,
    )
    from sapphire_flow.protocols.stores import (
        BasinStore,
        FlowRegimeConfigStore,
        HindcastStore,
        ModelArtifactStore,
        ModelStore,
        ObservationStore,
        SkillStore,
        StationGroupStore,
        StationStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.ids import ArtifactId, ModelId
    from sapphire_flow.types.model import (
        GroupTrainingData,
        ModelDataRequirements,
        StationTrainingData,
    )
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger(__name__)

_DELIVERABLE_FI_SPATIAL_TYPES = frozenset(
    {
        SpatialRepresentation.POINT,
        SpatialRepresentation.BASIN_AVERAGE,
        SpatialRepresentation.ELEVATION_BAND,
    }
)


def validate_compatibility(
    model: ForecastModel,
    station_config: StationConfig,
    available_features: frozenset[str],
    available_static: frozenset[str],
    requested_time_step: timedelta,
) -> CompatibilityReport:
    from sapphire_flow.protocols.forecast_model import (
        GroupForecastModel,
        StationForecastModel,
    )

    # Runtime conformance gate for entry-point-discovered (plugin) models: the
    # static ForecastModel annotation makes this look redundant, but the flow
    # wrapper passes a plugin object that may not actually conform.
    protocol_conforms = isinstance(
        model,
        StationForecastModel | GroupForecastModel,
    )  # pyright: ignore[reportUnnecessaryIsInstance]  # 2026-06-01: re-review 2026-12-01
    req: ModelDataRequirements = model.data_requirements

    if station_config.forecast_targets is None:
        missing_targets = frozenset(req.target_parameters)
    else:
        missing_targets = req.target_parameters - station_config.forecast_targets

    missing_past = req.past_dynamic_features - available_features
    missing_future = req.future_dynamic_features - available_features
    missing_static = req.static_features - available_static
    time_step_ok = requested_time_step in req.supported_time_steps

    return CompatibilityReport(
        model_id=model_id_from_model(model),
        station_id=station_config.id,
        group_id=None,
        protocol_conforms=protocol_conforms,
        missing_target_parameters=missing_targets,
        missing_past_dynamic=missing_past,
        missing_future_dynamic=missing_future,
        missing_static_features=missing_static,
        time_step_compatible=time_step_ok,
    )


def model_id_from_model(model: ForecastModel) -> ModelId:
    # Models carry their identity via the registry — passed explicitly at call sites
    # that need model_id. This helper is not used; model_id is always passed directly.
    raise NotImplementedError("model_id must be passed explicitly")


def _fi_compatibility_checks(
    *,
    model: ForecastModel,
    stations_by_id: Mapping[StationId, StationConfig | None],
    canonical_units: Mapping[str, str] | None,
) -> tuple[frozenset[str], frozenset[str], bool, bool]:
    from sapphire_flow.adapters.forecast_interface import ForecastInterfaceAdapter

    if not isinstance(model, ForecastInterfaceAdapter):
        return frozenset(), frozenset(), True, True

    declared_units = model.declared_units()
    catalog = canonical_units or {}
    mismatches: set[str] = set()
    unsupported = set(model.unsupported_units())

    for name, declared_unit in declared_units.items():
        expected_unit = catalog.get(name)
        if expected_unit is None:
            unsupported.add(name)
        elif declared_unit != expected_unit:
            mismatches.add(name)

    spatial_type_supported = (
        model.data_requirements.spatial_input_type in _DELIVERABLE_FI_SPATIAL_TYPES
    )
    station_codes_resolvable = all(
        station is not None and bool(station.code.strip())
        for station in stations_by_id.values()
    )

    return (
        frozenset(mismatches),
        frozenset(unsupported),
        spatial_type_supported,
        station_codes_resolvable,
    )


def validate_compatibility_for_unit(
    model_id: ModelId,
    model: ForecastModel,
    unit: TrainingUnit,
    station_store: StationStore,
    group_store: StationGroupStore,
    available_features: frozenset[str],
    available_static_by_station: dict[StationId, frozenset[str]],
    requested_time_step: timedelta,
    canonical_units: Mapping[str, str] | None = None,
) -> CompatibilityReport:
    from sapphire_flow.protocols.forecast_model import (
        GroupForecastModel,
        StationForecastModel,
    )

    # Runtime conformance gate for entry-point-discovered (plugin) models: the
    # static ForecastModel annotation makes this look redundant, but the flow
    # wrapper passes a plugin object that may not actually conform.
    protocol_conforms = isinstance(
        model,
        StationForecastModel | GroupForecastModel,
    )  # pyright: ignore[reportUnnecessaryIsInstance]  # 2026-06-01: re-review 2026-12-01
    req: ModelDataRequirements = model.data_requirements
    missing_past = req.past_dynamic_features - available_features
    missing_future = req.future_dynamic_features - available_features
    time_step_ok = requested_time_step in req.supported_time_steps

    # Union of all missing targets/static across member stations
    all_missing_targets: frozenset[str] = frozenset()
    all_missing_static: frozenset[str] = frozenset()
    stations_by_id: dict[StationId, StationConfig | None] = {}

    for sid in unit.station_ids:
        station = station_store.fetch_station(sid)
        stations_by_id[sid] = station
        if station is None:
            all_missing_targets = all_missing_targets | frozenset(req.target_parameters)
            all_missing_static = all_missing_static | req.static_features
            continue
        if station.forecast_targets is None:
            all_missing_targets = all_missing_targets | frozenset(req.target_parameters)
        else:
            all_missing_targets = all_missing_targets | (
                req.target_parameters - station.forecast_targets
            )
        avail_static = available_static_by_station.get(sid, frozenset())
        all_missing_static = all_missing_static | (req.static_features - avail_static)

    (
        fi_unit_mismatches,
        fi_unsupported_units,
        spatial_type_supported,
        station_codes_resolvable,
    ) = _fi_compatibility_checks(
        model=model,
        stations_by_id=stations_by_id,
        canonical_units=canonical_units,
    )

    return CompatibilityReport(
        model_id=model_id,
        station_id=unit.station_id,
        group_id=unit.group_id,
        protocol_conforms=protocol_conforms,
        missing_target_parameters=all_missing_targets,
        missing_past_dynamic=missing_past,
        missing_future_dynamic=missing_future,
        missing_static_features=all_missing_static,
        time_step_compatible=time_step_ok,
        fi_unit_mismatches=fi_unit_mismatches,
        fi_unsupported_units=fi_unsupported_units,
        spatial_type_supported=spatial_type_supported,
        station_codes_resolvable=station_codes_resolvable,
    )


def _make_synthetic_station_training_data(
    req: ModelDataRequirements,
    rng: random.Random,
    n_past_rows: int = 50,
    n_future_rows: int = 10,
) -> StationTrainingData:
    from datetime import datetime

    from sapphire_flow.types.model import StationTrainingData

    time_step = next(iter(req.supported_time_steps))
    base = datetime(2000, 1, 1, tzinfo=UTC)
    past_ts = [base + i * time_step for i in range(n_past_rows)]
    future_ts = [base + (n_past_rows + i) * time_step for i in range(n_future_rows)]

    def _rand_col(n: int) -> list[float]:
        return [max(0.0, rng.gauss(1.0, 0.5)) for _ in range(n)]

    past_targets = pl.DataFrame(
        {"timestamp": past_ts}
        | {col: _rand_col(n_past_rows) for col in req.target_parameters}
    )
    past_dynamic = pl.DataFrame(
        {"timestamp": past_ts}
        | {col: _rand_col(n_past_rows) for col in req.past_dynamic_features}
    )
    future_dynamic = pl.DataFrame(
        {"timestamp": future_ts}
        | {col: _rand_col(n_future_rows) for col in req.future_dynamic_features}
    )
    static: pl.DataFrame | None = None
    if req.static_features:
        static = pl.DataFrame({col: [rng.random()] for col in req.static_features})

    return StationTrainingData(
        past_targets=past_targets,
        past_dynamic=past_dynamic,
        future_dynamic=future_dynamic,
        static=static,
        time_step=time_step,
        val_start=None,
    )


def _make_synthetic_group_training_data(
    req: ModelDataRequirements,
    rng: random.Random,
    n_stations: int = 3,
    n_past_rows: int = 50,
    n_future_rows: int = 10,
) -> GroupTrainingData:
    from datetime import datetime

    from sapphire_flow.types.model import GroupTrainingData

    time_step = next(iter(req.supported_time_steps))
    base = datetime(2000, 1, 1, tzinfo=UTC)
    past_ts = [base + i * time_step for i in range(n_past_rows)]
    future_ts = [base + (n_past_rows + i) * time_step for i in range(n_future_rows)]
    # StationId is a UUID NewType; the stacked-frame "station_id" column must
    # equal str(sid) so GroupTrainingData.for_station can slice by station.
    station_ids = tuple(_station_id_from_rng(rng) for _ in range(n_stations))

    def _stacked_df(
        cols: frozenset[str],
        timestamps: list,
    ) -> pl.DataFrame:
        rows: list[dict] = []
        for sid in station_ids:
            for ts in timestamps:
                row: dict = {"station_id": str(sid), "timestamp": ts}
                for col in cols:
                    row[col] = max(0.0, rng.gauss(1.0, 0.5))
                rows.append(row)
        return pl.DataFrame(rows)

    static: pl.DataFrame | None = None
    if req.static_features:
        rows: list[dict[str, str | float]] = []
        for sid in station_ids:
            row: dict[str, str | float] = {"station_id": str(sid)}
            for col in req.static_features:
                row[col] = rng.random()
            rows.append(row)
        static = pl.DataFrame(rows)

    return GroupTrainingData(
        group_id=_station_group_id_from_rng(rng),
        station_ids=station_ids,
        past_targets=_stacked_df(req.target_parameters, past_ts),
        past_dynamic=_stacked_df(req.past_dynamic_features, past_ts),
        future_dynamic=_stacked_df(req.future_dynamic_features, future_ts),
        static=static,
        time_step=time_step,
        val_start=None,
    )


def smoke_test_model(model: ForecastModel, rng: random.Random) -> None:
    req = model.data_requirements
    seed = _synthetic_run_seed(req)
    rng.seed(seed)

    try:
        _, ensembles = _run_synthetic_train_predict(model, rng)
        _validate_synthetic_ensembles(ensembles, req.target_parameters)
    except Exception as exc:
        raise ModelSmokeTestError(
            f"Smoke test failed: {exc}\n{traceback.format_exc()}"
        ) from exc


def assert_model_conforms(
    model: StationForecastModel | GroupForecastModel,
    rng: random.Random,
) -> None:
    """Validate a forecast model through SAP3's protocol surface."""
    _assert_protocol_shape(model)
    determinism_seed = rng.getrandbits(64)
    smoke_test_model(model, rng)
    _assert_fixed_seed_determinism(model, determinism_seed)


def assert_operational_floors(
    model: StationForecastModel | GroupForecastModel,
    config: DeploymentConfig,
    rng: random.Random,
) -> None:
    """Validate that synthetic FI outputs meet operational ensemble floors."""
    req = model.data_requirements
    rng.seed(_synthetic_run_seed(req))

    try:
        _, ensembles = _run_synthetic_train_predict(model, rng)
        _validate_synthetic_ensembles(ensembles, req.target_parameters)
        _assert_synthetic_ensembles_meet_operational_floors(ensembles, config)
    except ModelSmokeTestError:
        raise
    except Exception as exc:
        raise ModelSmokeTestError(
            f"Operational floor check failed: {exc}\n{traceback.format_exc()}"
        ) from exc


def _run_synthetic_train_predict(
    model: ForecastModel,
    rng: random.Random,
) -> tuple[
    bytes,
    dict[str, ForecastEnsemble] | dict[StationId, dict[str, ForecastEnsemble]],
]:
    from sapphire_flow.protocols.forecast_model import (
        GroupForecastModel,
        StationForecastModel,
    )

    req = model.data_requirements
    # Past needs lookback + horizon rows; future IS the horizon.
    smoke_horizon = max(req.forecast_horizon_steps, 10)
    n_past = req.lookback_steps + smoke_horizon + 10
    n_future = smoke_horizon
    time_step = next(iter(req.supported_time_steps))
    issue_time = _synthetic_issue_time(time_step, n_past)

    if model.artifact_scope is ArtifactScope.GROUP:
        if not isinstance(model, GroupForecastModel):
            raise ModelSmokeTestError(
                "GROUP-scoped model does not implement GroupForecastModel"
            )
        data = _make_synthetic_group_training_data(
            req, rng, n_past_rows=n_past, n_future_rows=n_future
        )
        artifact = model.train(data, {}, rng)
        raw_bytes = model.serialize_artifact(artifact)
        reloaded = model.deserialize_artifact(raw_bytes)

        from sapphire_flow.types.model import GroupModelInputs

        inputs = GroupModelInputs(
            group_id=data.group_id,
            station_ids=data.station_ids,
            past_targets=data.past_targets,
            past_dynamic=data.past_dynamic,
            future_dynamic=data.future_dynamic,
            static=data.static,
            issue_time=issue_time,
            forecast_horizon_steps=min(
                req.forecast_horizon_steps, len(data.future_dynamic)
            ),
            time_step=time_step,
        )
        results = model.predict_batch(reloaded, inputs, rng)
        return raw_bytes, {
            station_id: ensembles for station_id, (ensembles, _) in results.items()
        }

    if model.artifact_scope is ArtifactScope.STATION:
        if not isinstance(model, StationForecastModel):
            raise ModelSmokeTestError(
                "STATION-scoped model does not implement StationForecastModel"
            )
        data = _make_synthetic_station_training_data(
            req, rng, n_past_rows=n_past, n_future_rows=n_future
        )
        artifact = model.train(data, {}, rng)
        raw_bytes = model.serialize_artifact(artifact)
        reloaded = model.deserialize_artifact(raw_bytes)

        from sapphire_flow.types.model import StationInputData, StationModelInputs

        inputs = StationModelInputs(
            station_id=_station_id_from_rng(rng),
            data=StationInputData(
                past_targets=data.past_targets,
                past_dynamic=data.past_dynamic,
                future_dynamic=data.future_dynamic,
                static=data.static,
            ),
            issue_time=issue_time,
            forecast_horizon_steps=min(
                req.forecast_horizon_steps, len(data.future_dynamic)
            ),
            time_step=time_step,
        )
        ensembles, _ = model.predict(reloaded, inputs, rng)
        return raw_bytes, ensembles

    raise ModelSmokeTestError(f"Unsupported artifact_scope: {model.artifact_scope!r}")


def _synthetic_run_seed(req: ModelDataRequirements) -> int:
    return int(hashlib.sha256(str(req).encode()).hexdigest(), 16) & 0xFFFFFFFF


def _uuid_from_rng(rng: random.Random) -> UUID:
    return UUID(int=rng.getrandbits(128), version=4)


def _station_id_from_rng(rng: random.Random) -> StationId:
    return StationId(_uuid_from_rng(rng))


def _station_group_id_from_rng(rng: random.Random) -> StationGroupId:
    return StationGroupId(_uuid_from_rng(rng))


def _synthetic_issue_time(
    time_step: timedelta,
    n_past_rows: int,
) -> UtcDatetime:
    from datetime import datetime

    from sapphire_flow.types.datetime import UtcDatetime

    base = datetime(2000, 1, 1, tzinfo=UTC)
    return UtcDatetime(base + max(n_past_rows - 1, 0) * time_step)


def _assert_protocol_shape(model: object) -> None:
    from sapphire_flow.protocols.forecast_model import (
        GroupForecastModel,
        StationForecastModel,
    )

    try:
        artifact_scope = model.artifact_scope  # type: ignore[attr-defined]
    except AttributeError as exc:
        raise ModelSmokeTestError("Model is missing artifact_scope") from exc

    if artifact_scope is ArtifactScope.STATION:
        if not isinstance(model, StationForecastModel):
            raise ModelSmokeTestError(
                "STATION-scoped model does not implement StationForecastModel"
            )
        return

    if artifact_scope is ArtifactScope.GROUP:
        if not isinstance(model, GroupForecastModel):
            raise ModelSmokeTestError(
                "GROUP-scoped model does not implement GroupForecastModel"
            )
        return

    raise ModelSmokeTestError(f"Unsupported artifact_scope: {artifact_scope!r}")


def _assert_fixed_seed_determinism(
    model: ForecastModel,
    seed: int,
) -> None:
    import random

    try:
        first_bytes, first_ensembles = _run_synthetic_train_predict(
            model, random.Random(seed)
        )
        second_bytes, second_ensembles = _run_synthetic_train_predict(
            model, random.Random(seed)
        )
    except Exception as exc:
        raise ModelSmokeTestError(
            f"Determinism check failed during synthetic run: {exc}"
        ) from exc

    if first_bytes != second_bytes:
        raise ModelSmokeTestError(
            "Determinism check failed: serialized artifacts differ for identical seed"
        )

    _assert_synthetic_ensembles_equal(first_ensembles, second_ensembles)


class _HasParameter(Protocol):
    @property
    def parameter(self) -> str: ...


def _validate_ensemble_dict(
    ensembles: Mapping[str, _HasParameter],
    expected_keys: frozenset[str],
) -> None:
    missing = expected_keys - set(ensembles.keys())
    if missing:
        raise ModelSmokeTestError(
            f"predict() result missing keys: {missing}. Got: {set(ensembles.keys())}"
        )
    for key, ensemble in ensembles.items():
        if ensemble.parameter != key:
            raise ModelSmokeTestError(
                f"ForecastEnsemble key/value mismatch: key={key!r}, "
                f"ensemble.parameter={ensemble.parameter!r}"
            )


def _validate_synthetic_ensembles(
    ensembles: (
        dict[str, ForecastEnsemble] | dict[StationId, dict[str, ForecastEnsemble]]
    ),
    expected_keys: frozenset[str],
) -> None:
    if all(isinstance(key, str) for key in ensembles):
        _validate_ensemble_dict(
            cast("dict[str, ForecastEnsemble]", ensembles),
            expected_keys,
        )
        return

    for station_ensembles in cast(
        "dict[StationId, dict[str, ForecastEnsemble]]", ensembles
    ).values():
        _validate_ensemble_dict(station_ensembles, expected_keys)


def _assert_synthetic_ensembles_meet_operational_floors(
    ensembles: (
        dict[str, ForecastEnsemble] | dict[StationId, dict[str, ForecastEnsemble]]
    ),
    config: DeploymentConfig,
) -> None:
    for station_id, parameter, ensemble in _iter_synthetic_ensembles(ensembles):
        representation = ensemble.representation
        if representation is EnsembleRepresentation.MEMBERS:
            # FI 1.5: one member means deterministic-only, which is non-operational.
            required = config.min_operational_ensemble_size
        elif representation is EnsembleRepresentation.QUANTILES:
            required = config.min_operational_quantile_levels
        else:
            raise ModelSmokeTestError(
                "Operational floor check failed for "
                f"station {station_id} parameter {parameter!r}: unsupported "
                f"representation {representation!r}"
            )

        observed = ensemble.member_count
        if observed < required:
            raise ModelSmokeTestError(
                "Operational floor check failed for "
                f"station {station_id} parameter {parameter!r}: "
                f"observed_count={observed}, representation={representation.value}, "
                f"required_floor={required}"
            )


def _iter_synthetic_ensembles(
    ensembles: (
        dict[str, ForecastEnsemble] | dict[StationId, dict[str, ForecastEnsemble]]
    ),
) -> tuple[tuple[str, str, ForecastEnsemble], ...]:
    if all(isinstance(key, str) for key in ensembles):
        return tuple(
            (str(ensemble.station_id), parameter, ensemble)
            for parameter, ensemble in cast(
                "dict[str, ForecastEnsemble]", ensembles
            ).items()
        )

    return tuple(
        (str(station_id), parameter, ensemble)
        for station_id, station_ensembles in cast(
            "dict[StationId, dict[str, ForecastEnsemble]]", ensembles
        ).items()
        for parameter, ensemble in station_ensembles.items()
    )


def _assert_synthetic_ensembles_equal(
    first: dict[str, ForecastEnsemble] | dict[StationId, dict[str, ForecastEnsemble]],
    second: dict[str, ForecastEnsemble] | dict[StationId, dict[str, ForecastEnsemble]],
) -> None:
    first_is_station = all(isinstance(key, str) for key in first)
    second_is_station = all(isinstance(key, str) for key in second)
    if first_is_station != second_is_station:
        raise ModelSmokeTestError(
            "Determinism check failed: output scope changed for identical seed"
        )

    if first_is_station:
        _assert_ensemble_dicts_equal(
            "station",
            cast("dict[str, ForecastEnsemble]", first),
            cast("dict[str, ForecastEnsemble]", second),
        )
        return

    first_group = cast("dict[StationId, dict[str, ForecastEnsemble]]", first)
    second_group = cast("dict[StationId, dict[str, ForecastEnsemble]]", second)
    if set(first_group) != set(second_group):
        raise ModelSmokeTestError(
            "Determinism check failed: station result keys differ for identical seed"
        )

    for station_id in sorted(first_group, key=str):
        _assert_ensemble_dicts_equal(
            f"station {station_id}",
            first_group[station_id],
            second_group[station_id],
        )


def _assert_ensemble_dicts_equal(
    label: str,
    first: dict[str, ForecastEnsemble],
    second: dict[str, ForecastEnsemble],
) -> None:
    if set(first) != set(second):
        raise ModelSmokeTestError(
            f"Determinism check failed: ensemble keys differ for {label}"
        )

    for parameter in sorted(first):
        if not first[parameter].values.equals(second[parameter].values):
            raise ModelSmokeTestError(
                "Determinism check failed: ForecastEnsemble.values differ for "
                f"{label} parameter {parameter!r}"
            )


def evaluate_skill_gate(
    model_id: ModelId,
    model_artifact_id: ArtifactId,
    skill_store: SkillStore,
    config: DeploymentConfig,
) -> SkillGateResult:
    scores = skill_store.fetch_skill_scores(model_id, model_artifact_id)
    valid_scores = [s for s in scores if s.sample_size >= config.min_skill_samples]

    metric_worst: dict[str, float] = {}
    for score in valid_scores:
        gate_metric = score.metric
        if gate_metric not in config.skill_gate_thresholds:
            continue
        gate_cfg = config.skill_gate_thresholds[gate_metric]
        prev = metric_worst.get(gate_metric)
        if prev is None:
            metric_worst[gate_metric] = score.score
        elif gate_cfg.higher_is_better:
            metric_worst[gate_metric] = min(prev, score.score)
        else:
            metric_worst[gate_metric] = max(prev, score.score)

    metric_scores = tuple(metric_worst.items())
    thresholds = tuple(
        (name, cfg.threshold, cfg.higher_is_better)
        for name, cfg in config.skill_gate_thresholds.items()
    )

    failing: set[str] = set()
    for metric_name, gate_cfg in config.skill_gate_thresholds.items():
        worst = metric_worst.get(metric_name)
        if worst is None:
            failing.add(metric_name)
        elif gate_cfg.higher_is_better:
            if worst < gate_cfg.threshold:
                failing.add(metric_name)
        else:
            if worst > gate_cfg.threshold:
                failing.add(metric_name)

    return SkillGateResult(
        model_artifact_id=model_artifact_id,
        metric_scores=metric_scores,
        thresholds=thresholds,
        failing_metrics=frozenset(failing),
    )


def create_station_assignment(
    station_id: StationId,
    model_id: ModelId,
    time_step: timedelta,
    priority: int,
    station_store: StationStore,
    clock: Callable[[], UtcDatetime],
) -> ModelAssignment:
    existing = station_store.fetch_model_assignments(station_id)
    for assignment in existing:
        if assignment.model_id == model_id:
            if assignment.status == ModelAssignmentStatus.INACTIVE:
                log.warning(
                    "model.assignment_skipped_inactive",
                    station_id=str(station_id),
                    model_id=str(model_id),
                )
                return assignment
            break

    new_assignment = ModelAssignment(
        station_id=station_id,
        model_id=model_id,
        time_step=time_step,
        status=ModelAssignmentStatus.ACTIVE,
        priority=priority,
        created_at=clock(),
    )
    station_store.store_model_assignment(new_assignment)
    return new_assignment


def create_group_assignment(
    group_id: StationGroupId,
    model_id: ModelId,
    time_step: timedelta,
    priority: int,
    group_store: StationGroupStore,
    clock: Callable[[], UtcDatetime],
) -> GroupModelAssignment:
    existing = group_store.fetch_group_model_assignments(group_id)
    for assignment in existing:
        if assignment.model_id == model_id:
            if assignment.status == ModelAssignmentStatus.INACTIVE:
                log.warning(
                    "model.assignment_skipped_inactive",
                    group_id=str(group_id),
                    model_id=str(model_id),
                )
                return assignment
            break

    new_assignment = GroupModelAssignment(
        group_id=group_id,
        model_id=model_id,
        time_step=time_step,
        status=ModelAssignmentStatus.ACTIVE,
        priority=priority,
        created_at=clock(),
    )
    group_store.store_group_model_assignment(new_assignment)
    return new_assignment


def determine_onboarding_scope(
    model_id: ModelId,
    model: ForecastModel,
    station_ids: frozenset[StationId] | None,
    group_ids: frozenset[StationGroupId] | None,
    station_store: StationStore,
    group_store: StationGroupStore,
    training_period_start: UtcDatetime,
    training_period_end: UtcDatetime,
    time_step: timedelta,
) -> tuple[TrainingUnit, ...]:
    scope = model.artifact_scope
    units: list[TrainingUnit] = []

    if scope == ArtifactScope.STATION:
        if station_ids is not None:
            resolved = station_ids
        else:
            all_stations = station_store.fetch_all_stations()
            resolved = frozenset(
                s.id
                for s in all_stations
                if s.station_status == StationStatus.OPERATIONAL
            )
        for sid in resolved:
            units.append(
                TrainingUnit(
                    model_id=model_id,
                    station_id=sid,
                    group_id=None,
                    station_ids=frozenset({sid}),
                    training_period_start=training_period_start,
                    training_period_end=training_period_end,
                    time_step=time_step,
                )
            )
    elif scope == ArtifactScope.GROUP:
        if group_ids is not None:
            for gid in group_ids:
                group = group_store.fetch_group(gid)
                member_ids = group.station_ids if group is not None else frozenset()
                units.append(
                    TrainingUnit(
                        model_id=model_id,
                        station_id=None,
                        group_id=gid,
                        station_ids=member_ids,
                        training_period_start=training_period_start,
                        training_period_end=training_period_end,
                        time_step=time_step,
                    )
                )
        else:
            groups = group_store.fetch_groups_for_model(model_id)
            if not groups:
                raise ConfigurationError(
                    f"No groups found for group-scoped model {model_id!r}. "
                    "Provide explicit group_ids for initial onboarding."
                )
            for group in groups:
                units.append(
                    TrainingUnit(
                        model_id=model_id,
                        station_id=None,
                        group_id=group.id,
                        station_ids=group.station_ids,
                        training_period_start=training_period_start,
                        training_period_end=training_period_end,
                        time_step=time_step,
                    )
                )

    return tuple(units)


def onboard_model(
    model_id: ModelId,
    model: ForecastModel,
    units: tuple[TrainingUnit, ...],
    model_store: ModelStore,
    station_store: StationStore,
    group_store: StationGroupStore,
    artifact_store: ModelArtifactStore,
    obs_store: ObservationStore,
    basin_store: BasinStore,
    hindcast_store: HindcastStore,
    skill_store: SkillStore,
    flow_regime_store: FlowRegimeConfigStore,
    forcing_source: WeatherReanalysisSource,
    config: DeploymentConfig,
    clock: Callable[[], UtcDatetime],
    rng: random.Random,
    assignment_priority: int = 0,
    run_hindcast_fn: Callable[..., list] | None = None,
    compute_skill_fn: Callable[..., None] | None = None,
    skip_smoke_test: bool = False,
) -> ModelOnboardingResult:
    from sapphire_flow.protocols.forecast_model import (
        GroupForecastModel,
        StationForecastModel,
    )
    from sapphire_flow.services.training import (
        promote_artifact,
        train_group_model,
        train_station_model,
    )
    from sapphire_flow.services.training_data import (
        assemble_group_training_data,
        assemble_station_training_data,
    )
    from sapphire_flow.types.enums import ArtifactScope
    from sapphire_flow.types.model import GroupTrainingData, StationTrainingData

    log.info("model.onboarding_started", model_id=str(model_id), unit_count=len(units))
    unit_results: list[OnboardingUnitResult] = []

    for unit in units:
        unit_start = time.monotonic()
        sid_str = str(unit.station_id) if unit.station_id else None
        gid_str = str(unit.group_id) if unit.group_id else None
        log.info(
            "model.onboarding_unit_started",
            model_id=str(model_id),
            station_id=sid_str,
            group_id=gid_str,
        )

        # Step 1: Compatibility check
        avail_static: dict[StationId, frozenset[str]] = {}
        for sid in unit.station_ids:
            station = station_store.fetch_station(sid)
            if station is not None and station.basin_id is not None:
                basin = basin_store.fetch_basin(station.basin_id)
                if basin is not None and basin.attributes:
                    avail_static[sid] = frozenset(basin.attributes.keys())
                else:
                    avail_static[sid] = frozenset()
            else:
                avail_static[sid] = frozenset()

        compat = validate_compatibility_for_unit(
            model_id=model_id,
            model=model,
            unit=unit,
            station_store=station_store,
            group_store=group_store,
            available_features=config.available_nwp_parameters,
            available_static_by_station=avail_static,
            requested_time_step=unit.time_step,
        )

        if not compat.is_compatible:
            log.info(
                "model.compatibility_failed",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
                is_compatible=False,
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.SKIPPED_COMPAT,
                    compatibility=compat,
                    artifact_id=None,
                    hindcast_steps=(),
                    skill_gate=None,
                )
            )
            continue

        log.info(
            "model.compatibility_completed",
            model_id=str(model_id),
            station_id=sid_str,
            group_id=gid_str,
            is_compatible=True,
        )

        # Step 1b: Smoke test (skipped when called from station onboarding)
        smoke_failed = False
        if skip_smoke_test:
            log.info(
                "model.smoke_test_skipped",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
            )
        else:
            try:
                smoke_test_model(model, rng)
                log.info(
                    "model.smoke_test_completed",
                    model_id=str(model_id),
                    station_id=sid_str,
                    group_id=gid_str,
                    passed=True,
                )
            except ModelSmokeTestError as exc:
                log.error(
                    "model.smoke_test_failed",
                    model_id=str(model_id),
                    station_id=sid_str,
                    group_id=gid_str,
                    error=str(exc),
                )
                smoke_failed = True
                unit_results.append(
                    OnboardingUnitResult(
                        unit=unit,
                        outcome=OnboardingOutcome.FAILED_SMOKE_TEST,
                        compatibility=compat,
                        artifact_id=None,
                        hindcast_steps=(),
                        skill_gate=None,
                        error=str(exc),
                    )
                )
                continue
        if smoke_failed:
            continue

        # Step 2: Assemble training data
        try:
            if unit.station_id is not None:
                training_data = assemble_station_training_data(
                    station_id=unit.station_id,
                    model=model,
                    period_start=unit.training_period_start,
                    period_end=unit.training_period_end,
                    time_step=unit.time_step,
                    forcing_source=forcing_source,
                    obs_store=obs_store,
                    basin_store=basin_store,
                    station_store=station_store,
                )
            else:
                # TrainingUnit.__post_init__ guarantees exactly-one-of
                # station_id / group_id; group_id is set in this branch.
                assert unit.group_id is not None
                group = group_store.fetch_group(unit.group_id)
                if group is None:
                    raise StoreError(
                        f"fetch_group({unit.group_id}) returned None — "
                        "group was deleted or never persisted; training unit "
                        "references a stale group_id"
                    )
                assert isinstance(model, GroupForecastModel)
                training_data = assemble_group_training_data(
                    group=group,
                    model=model,
                    period_start=unit.training_period_start,
                    period_end=unit.training_period_end,
                    time_step=unit.time_step,
                    forcing_source=forcing_source,
                    obs_store=obs_store,
                    basin_store=basin_store,
                    station_store=station_store,
                )
        except Exception as exc:
            log.error(
                "model.training_data_failed",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
                error=str(exc),
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.FAILED_TRAINING,
                    compatibility=compat,
                    artifact_id=None,
                    hindcast_steps=(),
                    skill_gate=None,
                    error=str(exc),
                )
            )
            continue

        if training_data is None:
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.SKIPPED_NO_DATA,
                    compatibility=compat,
                    artifact_id=None,
                    hindcast_steps=(),
                    skill_gate=None,
                )
            )
            continue

        # Step 3: Train
        try:
            if model.artifact_scope == ArtifactScope.STATION:
                assert isinstance(model, StationForecastModel)
                assert isinstance(training_data, StationTrainingData)
                artifact_bytes = train_station_model(model, training_data, {}, rng)
            else:
                assert isinstance(model, GroupForecastModel)
                assert isinstance(training_data, GroupTrainingData)
                artifact_bytes = train_group_model(model, training_data, {}, rng)
        except Exception as exc:
            log.error(
                "model.training_failed",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
                error=str(exc),
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.FAILED_TRAINING,
                    compatibility=compat,
                    artifact_id=None,
                    hindcast_steps=(),
                    skill_gate=None,
                    error=str(exc),
                )
            )
            continue

        # Step 4: Store artifact (TRAINING status — do NOT promote yet)
        try:
            from sapphire_flow.types.enums import ModelArtifactStatus

            artifact_id, _ = artifact_store.store_artifact(
                model_id=model_id,
                artifact_bytes=artifact_bytes,
                training_period_start=unit.training_period_start,
                training_period_end=unit.training_period_end,
                trained_at=clock(),
                station_id=unit.station_id,
                group_id=unit.group_id,
                status=ModelArtifactStatus.TRAINING,
            )
        except Exception as exc:
            log.error(
                "model.artifact_store_failed",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
                error=str(exc),
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.FAILED_TRAINING,
                    compatibility=compat,
                    artifact_id=None,
                    hindcast_steps=(),
                    skill_gate=None,
                    error=str(exc),
                )
            )
            continue

        # Step 5: Hindcast
        hindcast_steps: list = []
        if run_hindcast_fn is not None:
            try:
                hindcast_steps = run_hindcast_fn(
                    unit=unit,
                    model=model,
                    artifact_id=artifact_id,
                    artifact_store=artifact_store,
                    obs_store=obs_store,
                    hindcast_store=hindcast_store,
                    forcing_source=forcing_source,
                    station_store=station_store,
                    basin_store=basin_store,
                    clock=clock,
                    rng=rng,
                )
            except Exception as exc:
                log.error(
                    "model.hindcast_failed",
                    model_id=str(model_id),
                    station_id=sid_str,
                    group_id=gid_str,
                    error=str(exc),
                )
                unit_results.append(
                    OnboardingUnitResult(
                        unit=unit,
                        outcome=OnboardingOutcome.FAILED_HINDCAST,
                        compatibility=compat,
                        artifact_id=artifact_id,
                        hindcast_steps=(),
                        skill_gate=None,
                        error=str(exc),
                    )
                )
                continue

        # Step 6: Compute skill
        if compute_skill_fn is not None:
            try:
                compute_skill_fn(
                    unit=unit,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_store=hindcast_store,
                    obs_store=obs_store,
                    skill_store=skill_store,
                    flow_regime_store=flow_regime_store,
                    config=config,
                )
            except Exception as exc:
                log.error(
                    "model.skill_computation_failed",
                    model_id=str(model_id),
                    station_id=sid_str,
                    group_id=gid_str,
                    error=str(exc),
                )
                unit_results.append(
                    OnboardingUnitResult(
                        unit=unit,
                        outcome=OnboardingOutcome.FAILED_SKILL,
                        compatibility=compat,
                        artifact_id=artifact_id,
                        hindcast_steps=tuple(hindcast_steps),
                        skill_gate=None,
                        error=str(exc),
                    )
                )
                continue

        # Step 7: Evaluate skill gate
        try:
            skill_gate = evaluate_skill_gate(
                model_id=model_id,
                model_artifact_id=artifact_id,
                skill_store=skill_store,
                config=config,
            )
        except Exception as exc:
            log.error(
                "model.skill_gate_failed",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
                error=str(exc),
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.FAILED_SKILL,
                    compatibility=compat,
                    artifact_id=artifact_id,
                    hindcast_steps=tuple(hindcast_steps),
                    skill_gate=None,
                    error=str(exc),
                )
            )
            continue

        # Insufficient evaluation data — distinguish from gate rejection
        if len(skill_gate.metric_scores) == 0 and not skill_gate.passed:
            log.warning(
                "model.skill_gate_completed",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
                passed=False,
                failing_metrics=list(skill_gate.failing_metrics),
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.SKIPPED_INSUFFICIENT_EVAL,
                    compatibility=compat,
                    artifact_id=artifact_id,
                    hindcast_steps=tuple(hindcast_steps),
                    skill_gate=skill_gate,
                )
            )
            continue

        if not skill_gate.passed:
            log.warning(
                "model.skill_gate_completed",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
                passed=False,
                failing_metrics=list(skill_gate.failing_metrics),
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.GATE_REJECTED,
                    compatibility=compat,
                    artifact_id=artifact_id,
                    hindcast_steps=tuple(hindcast_steps),
                    skill_gate=skill_gate,
                )
            )
            continue

        log.info(
            "model.skill_gate_completed",
            model_id=str(model_id),
            station_id=sid_str,
            group_id=gid_str,
            passed=True,
            failing_metrics=[],
        )

        # Step 8: Promote artifact (TRAINING → ACTIVE)
        try:
            promote_artifact(
                artifact_store=artifact_store,
                model_id=model_id,
                new_id=artifact_id,
                station_id=unit.station_id,
                group_id=unit.group_id,
            )
            log.info(
                "model.promotion_completed",
                model_id=str(model_id),
                artifact_id=str(artifact_id),
                station_id=sid_str,
                group_id=gid_str,
            )
        except Exception as exc:
            log.error(
                "model.promotion_failed",
                model_id=str(model_id),
                artifact_id=str(artifact_id),
                station_id=sid_str,
                group_id=gid_str,
                error=str(exc),
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.FAILED_ASSIGNMENT,
                    compatibility=compat,
                    artifact_id=artifact_id,
                    hindcast_steps=tuple(hindcast_steps),
                    skill_gate=skill_gate,
                    error=str(exc),
                )
            )
            continue

        # Step 9: Create assignment
        try:
            if unit.station_id is not None:
                create_station_assignment(
                    station_id=unit.station_id,
                    model_id=model_id,
                    time_step=unit.time_step,
                    priority=assignment_priority,
                    station_store=station_store,
                    clock=clock,
                )
            else:
                # TrainingUnit.__post_init__ guarantees exactly-one-of
                # station_id / group_id; group_id is set in this branch.
                assert unit.group_id is not None
                create_group_assignment(
                    group_id=unit.group_id,
                    model_id=model_id,
                    time_step=unit.time_step,
                    priority=assignment_priority,
                    group_store=group_store,
                    clock=clock,
                )
        except Exception as exc:
            log.error(
                "model.assignment_failed",
                model_id=str(model_id),
                station_id=sid_str,
                group_id=gid_str,
                error=str(exc),
            )
            unit_results.append(
                OnboardingUnitResult(
                    unit=unit,
                    outcome=OnboardingOutcome.FAILED_ASSIGNMENT,
                    compatibility=compat,
                    artifact_id=artifact_id,
                    hindcast_steps=tuple(hindcast_steps),
                    skill_gate=skill_gate,
                    error=str(exc),
                )
            )
            continue

        duration_ms = int((time.monotonic() - unit_start) * 1000)
        log.info(
            "model.onboarding_unit_completed",
            model_id=str(model_id),
            station_id=sid_str,
            group_id=gid_str,
            outcome=OnboardingOutcome.PROMOTED.value,
            duration_ms=duration_ms,
        )
        unit_results.append(
            OnboardingUnitResult(
                unit=unit,
                outcome=OnboardingOutcome.PROMOTED,
                compatibility=compat,
                artifact_id=artifact_id,
                hindcast_steps=tuple(hindcast_steps),
                skill_gate=skill_gate,
            )
        )

    result = ModelOnboardingResult(
        model_id=model_id,
        units=tuple(unit_results),
    )
    log.info(
        "model.onboarding_completed",
        model_id=str(model_id),
        promoted_count=result.promoted_count(),
        failed_count=result.failed_count(),
        skipped_count=result.skipped_count(),
        gate_rejected_count=result.gate_rejected_count(),
    )
    return result
