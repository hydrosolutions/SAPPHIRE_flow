from __future__ import annotations

import random  # noqa: TC003
from collections.abc import Callable  # noqa: TC003
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, cast
from uuid import UUID  # noqa: TC003

import structlog

from sapphire_flow.exceptions import ModelOutputError
from sapphire_flow.services.ensemble_fanout import (
    ensembles_only,
    fan_out_ensemble,
    reject_prior_state_for_fanout,
    reject_stateful_ensemble_states,
)
from sapphire_flow.services.horizon_semantics import resolve_required_steps
from sapphire_flow.services.input_quality import assess_input_quality
from sapphire_flow.services.nwp_coverage import assess_future_coverage, member_indices
from sapphire_flow.services.operational_inputs import (
    ModelRunContext,
    OperationalInputMetadata,  # noqa: TC001
    load_warm_up_state,
)
from sapphire_flow.services.qc_datum import (
    add_forecast_datum_details,
    forecast_skipped_rules,
    shift_ensemble_for_water_level_datum,
)
from sapphire_flow.services.track_assembly import (
    MissingTrackContext,
    ReadyContext,
    UnavailableTrackContext,
)
from sapphire_flow.types.enums import EnsembleMode, ForecastStatus, QcStatus
from sapphire_flow.types.forcing_track import FeatureName  # noqa: TC001
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.ids import (
    FALLBACK_MODEL_IDS,
    ArtifactId,
    ForecastId,
    ModelId,
    StationId,
)

if TYPE_CHECKING:
    import polars as pl

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.stores import ModelArtifactStore, ModelStateStore
    from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker
    from sapphire_flow.services.track_assembly import (
        AssignmentRunInput,
        ForcingContract,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import (
        ClimBaseline,
        ForecastQcRuleSet,
        QcFlag,
        StationForecastQcOverride,
    )
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import NwpCycleSource
    from sapphire_flow.types.model import StationModelInputs
    from sapphire_flow.types.station import ModelAssignment

log = structlog.get_logger()


@dataclass(frozen=True, kw_only=True, slots=True)
class StationForecastResult:
    station_id: StationId
    model_id: ModelId
    artifact_id: ArtifactId
    forecasts: list[OperationalForecast]
    new_state: bytes | None
    ensembles: dict[str, ForecastEnsemble]


class AssignmentFailureCause(Enum):
    """Why THIS assignment failed — assignment-level (Plan 150 D1), so Phase
    3's MISSING_CONTEXT/TRACK_UNAVAILABLE can be added as new members with no
    rework of AssignmentFailure/AssignmentOutcome/failed_models's type."""

    # --- the seven concrete anticipated causes that exist today ---
    MODEL_NOT_FOUND = auto()
    INSUFFICIENT_COVERAGE = auto()
    NO_ARTIFACT = auto()
    WARM_UP_LOAD_FAILED = auto()
    UNSUPPORTED_STATEFUL_ENSEMBLE = auto()  # shared by two return sites (D2)
    PREDICT_FAILED = auto()  # conflates FI ModelFailure + unexpected-in-predict
    QC_FAILED = auto()
    # --- added by the loop-level backstop (D3) only ---
    UNEXPECTED_EXCEPTION = auto()  # never returned by _run_single_model itself
    # --- Plan 151 T1 (D1, D3-mapping): additive, the runner consumption
    # seam (T7). Both are assignment-local and advance the fallback chain. ---
    MISSING_CONTEXT = auto()  # the track never resolved -> model NOT called
    TRACK_UNAVAILABLE = auto()  # the track resolved, this station unavailable


@dataclass(frozen=True, kw_only=True, slots=True)
class AssignmentSuccess:
    result: StationForecastResult


@dataclass(frozen=True, kw_only=True, slots=True)
class AssignmentFailure:
    cause: AssignmentFailureCause
    detail: str


AssignmentOutcome = AssignmentSuccess | AssignmentFailure


@dataclass(frozen=True, kw_only=True, slots=True)
class MultiModelForecastResult:
    station_id: StationId
    results: dict[ModelId, StationForecastResult]
    priorities: dict[ModelId, int]
    primary_model_id: ModelId | None
    failed_models: dict[ModelId, AssignmentFailure]

    @property
    def combinable_results(self) -> dict[ModelId, StationForecastResult]:
        return {
            mid: r for mid, r in self.results.items() if mid not in FALLBACK_MODEL_IDS
        }


def worst_qc_status(flags: list[QcFlag]) -> QcStatus:
    if not flags:
        return QcStatus.QC_PASSED
    priority = {
        QcStatus.QC_FAILED: 3,
        QcStatus.QC_SUSPECT: 2,
        QcStatus.QC_PASSED: 1,
        QcStatus.RAW: 0,
        QcStatus.MISSING: 0,
    }
    return max(flags, key=lambda f: priority.get(f.status, 0)).status


def _assert_consistent_member_set(
    future_dynamic: pl.DataFrame,
    features: frozenset[str],
    ensemble_mode: EnsembleMode,
    expected_member_ids: frozenset[int] | None = None,
) -> str | None:
    """Plan 151 D10a: the pre-run defensive re-check. `assess_future_coverage`
    called PER FEATURE no longer performs its own cross-feature "identical
    member set" comparison (`nwp_coverage.py:116-126`), so this recovers that
    invariant over the ASSEMBLED frame before `predict` is ever called. T5's
    per-station exact-`expected_member_ids` gate is strictly stronger and
    runs before persist — this is a defensive re-check, not the primary
    guard. Returns an error detail string if inconsistent, else ``None``.

    ``expected_member_ids`` (review fold-in — blocker), when supplied, is
    checked FIRST against every feature's actual member set — not merely
    features against each other. A cross-feature-only compare passes
    trivially for a single-feature model, or when every feature is
    UNIFORMLY short the same true member set (e.g. all features carry only
    30 of 51 expected members) — it can only ever catch DIVERGENCE between
    features, never agreement on a wrong set."""
    if ensemble_mode is not EnsembleMode.ENSEMBLE or not features:
        return None
    member_sets = {
        feature: member_indices(future_dynamic.columns, feature)
        for feature in sorted(features)
    }
    if expected_member_ids is not None:
        wrong = {
            feature: members
            for feature, members in sorted(member_sets.items())
            if members != expected_member_ids
        }
        if wrong:
            detail = ", ".join(
                f"{feature}={sorted(members)}" for feature, members in wrong.items()
            )
            return (
                f"ensemble member set(s) do not match expected "
                f"{sorted(expected_member_ids)}: {detail}"
            )
        return None
    reference = next(iter(member_sets.values()))
    if any(members != reference for members in member_sets.values()):
        detail = ", ".join(
            f"{feature}={sorted(members)}"
            for feature, members in sorted(member_sets.items())
        )
        return f"inconsistent ensemble member sets across features: {detail}"
    return None


def _run_single_model(
    station_id: StationId,
    assignment: ModelAssignment,
    inputs: StationModelInputs,
    observation_staleness_hours: float | None,
    nwp_age_hours: float | None,
    model_state_store: ModelStateStore,
    models: dict[ModelId, ForecastModel],
    artifact_store: ModelArtifactStore,
    qc_checker: ForecastOutputQualityChecker,
    qc_rules: ForecastQcRuleSet,
    qc_overrides: list[StationForecastQcOverride],
    baselines: list[ClimBaseline],
    water_level_datum_masl: float | None,
    nwp_cycle_reference_time: UtcDatetime | None,
    nwp_cycle_source: NwpCycleSource,
    config: DeploymentConfig,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
    rng: random.Random,
    forcing_contract: ForcingContract | None = None,
) -> AssignmentOutcome:
    """``forcing_contract`` is the Plan 151 D10a per-track substitution:
    ``None`` (the default) is the LEGACY route — every forcing read below
    comes from ``model.data_requirements``, byte-for-byte unchanged. A
    non-``None`` contract is the per-track ``ReadyContext`` route — coverage
    is evaluated PER FEATURE against the contract's own per-feature horizons
    (never the model's collapsed scalar), and ensemble dispatch / fan-out
    read the contract's ``ensemble_mode`` / ``future_dynamic_features``."""
    model = models.get(assignment.model_id)
    if model is None:
        log.warning(
            "run_station_forecast.model_not_found",
            station_id=str(station_id),
            model_id=str(assignment.model_id),
        )
        return AssignmentFailure(
            cause=AssignmentFailureCause.MODEL_NOT_FOUND,
            detail=f"model {assignment.model_id} not found in registry",
        )

    # Plan 090 D1/D2d/D3: post-download coverage safety net. A model that
    # declares future NWP forcing must receive >= its own forecast_horizon_steps
    # clean future daily buckets for EVERY required variable AND member, or it
    # must NOT forecast — otherwise a short/partial NWP frame silently truncates
    # the horizon (e.g. NwpRegression forecasts horizon = len(future_times), so a
    # 1-row frame becomes a 1-step forecast). Short coverage is treated like a
    # graceful predict failure: return a reason string so the PRIMARY chain moves
    # to the next (native/fallback) model — the station gets a runoff-only-style
    # forecast, never a truncated NWP one.
    if forcing_contract is None:
        # LEGACY route — byte-for-byte unchanged.
        future_features = model.data_requirements.future_dynamic_features
        if future_features:
            # Plan 159 T0d (INTERIM): a model's declared horizon may be a CEILING
            # rather than a floor. Strict by default; see `horizon_semantics.py`.
            horizon = resolve_required_steps(
                model,
                assignment.model_id,
                model.data_requirements.forecast_horizon_steps,
            )
            required_steps = horizon.steps
            coverage = assess_future_coverage(
                inputs.data.future_dynamic,
                required_features=future_features,
                required_steps=required_steps,
                ensemble_mode=model.data_requirements.ensemble_mode,
            )
            if not coverage.adequate:
                log.warning(
                    "nwp.insufficient_coverage",
                    station_id=str(station_id),
                    model_id=str(assignment.model_id),
                    required_steps=required_steps,
                    available_steps=coverage.available_steps,
                    detail=coverage.detail,
                )
                return AssignmentFailure(
                    cause=AssignmentFailureCause.INSUFFICIENT_COVERAGE,
                    detail=f"insufficient NWP coverage: {coverage.detail}",
                )
    elif forcing_contract.future_dynamic_features:
        # Plan 151 D10a: the per-track route. The CEILING is still resolved
        # ONCE per assignment from the model's own declaration (the Plan 159
        # seam is not per-feature, `horizon_semantics.py:119-125`) — only the
        # per-feature REQUIRED steps and the coverage CALL move per-feature.
        horizon = resolve_required_steps(
            model,
            assignment.model_id,
            model.data_requirements.forecast_horizon_steps,
        )
        ceiling = horizon.steps
        for feature in sorted(forcing_contract.future_dynamic_features):
            contract_horizon = forcing_contract.feature_horizons[
                FeatureName(feature)
            ].value
            required_steps = min(ceiling, contract_horizon)
            coverage = assess_future_coverage(
                inputs.data.future_dynamic,
                required_features=frozenset({feature}),
                required_steps=required_steps,
                ensemble_mode=forcing_contract.ensemble_mode,
            )
            if not coverage.adequate:
                log.warning(
                    "nwp.insufficient_coverage",
                    station_id=str(station_id),
                    model_id=str(assignment.model_id),
                    feature=feature,
                    required_steps=required_steps,
                    available_steps=coverage.available_steps,
                    detail=coverage.detail,
                )
                return AssignmentFailure(
                    cause=AssignmentFailureCause.INSUFFICIENT_COVERAGE,
                    detail=(
                        f"insufficient NWP coverage for {feature!r}: {coverage.detail}"
                    ),
                )
        member_check = _assert_consistent_member_set(
            inputs.data.future_dynamic,
            forcing_contract.future_dynamic_features,
            forcing_contract.ensemble_mode,
            forcing_contract.expected_member_ids,
        )
        if member_check is not None:
            log.warning(
                "nwp.insufficient_coverage",
                station_id=str(station_id),
                model_id=str(assignment.model_id),
                detail=member_check,
            )
            return AssignmentFailure(
                cause=AssignmentFailureCause.INSUFFICIENT_COVERAGE,
                detail=f"insufficient NWP coverage: {member_check}",
            )

    artifact_result = artifact_store.fetch_active_artifact_for_station(
        station_id, assignment.model_id
    )
    if artifact_result is None:
        log.warning(
            "run_station_forecast.no_active_artifact",
            station_id=str(station_id),
            model_id=str(assignment.model_id),
        )
        return AssignmentFailure(
            cause=AssignmentFailureCause.NO_ARTIFACT,
            detail=f"no active artifact for model {assignment.model_id}",
        )

    artifact_id, artifact_bytes = artifact_result

    # Plan 148 D3: resolve THIS assignment's warm-up state uniformly, after
    # every eligibility gate above, before the reject-guard first reads state
    # below. A store-read failure is assignment-local — it never aborts a
    # station whose earlier (higher-priority) assignment already succeeded.
    try:
        warm_up = load_warm_up_state(
            model_state_store, station_id, assignment.model_id, clock
        )
    except Exception as exc:
        log.warning(
            "run_station_forecast.warm_up_load_failed",
            station_id=str(station_id),
            model_id=str(assignment.model_id),
            error=str(exc),
        )
        return AssignmentFailure(
            cause=AssignmentFailureCause.WARM_UP_LOAD_FAILED,
            detail=f"warm-up state load failed: {exc}",
        )

    context = ModelRunContext(
        station_id=station_id,
        model_id=assignment.model_id,
        inputs=inputs,
        observation_staleness_hours=observation_staleness_hours,
        nwp_age_hours=nwp_age_hours,
        prior_state=warm_up.prior_state,
        warm_up_source=warm_up.warm_up_source,
        warm_up_state_age_hours=warm_up.warm_up_state_age_hours,
    )

    # Plan 151 D10a: on the per-track route, ensemble dispatch reads the
    # contract's OWN mode — a no-behaviour-delta today (T2's D4 sweep already
    # forces the contract's mode to equal `model.data_requirements`' for any
    # model that can reach this route, forecast_interface.py:511-521).
    is_ensemble = (
        forcing_contract.ensemble_mode
        if forcing_contract is not None
        else model.data_requirements.ensemble_mode  # type: ignore[union-attr]
    ) is EnsembleMode.ENSEMBLE
    if is_ensemble:
        # INPUT-side complement of the output-side stateful check below: the
        # fan-out would forward the SAME aggregate ``prior_state`` into every
        # member's ``predict`` (no way to split one aggregate state per member),
        # so a stateful ensemble model on the input side is unsupported.
        # Assignment-local (Plan 148 State-load failure semantics): caught
        # here rather than propagated, so a lower-priority stateful-ensemble
        # assignment never discards an already-succeeded higher-priority
        # primary. Catches ``ModelOutputError`` ONLY — never widened to span
        # ``predict`` below, so an FI ``ModelFailure`` (same exception class,
        # raised from inside ``predict``) still maps to ``predict_failed``.
        try:
            reject_prior_state_for_fanout(context.prior_state)
        except ModelOutputError as exc:
            log.warning(
                "run_station_forecast.unsupported_stateful_ensemble",
                station_id=str(station_id),
                model_id=str(assignment.model_id),
                error=str(exc),
            )
            return AssignmentFailure(
                cause=AssignmentFailureCause.UNSUPPORTED_STATEFUL_ENSEMBLE,
                detail=f"unsupported stateful ensemble: {exc}",
            )

    ensemble_member_states: list[bytes | None] | None = None
    try:
        artifact = model.deserialize_artifact(artifact_bytes)  # type: ignore[union-attr]
        if is_ensemble:
            # Member-suffixed forcing is fanned out into one N-member ensemble.
            # The fan-out maps ``predict`` over N members; each may return its own
            # ``new_state``. Capture them for the loud-fail check below.
            # ``prior_state`` is guaranteed ``None`` here (guard above).
            predict_fn = ensembles_only(
                model.predict,  # type: ignore[union-attr]
                artifact,
                None,
            )
            ensembles = fan_out_ensemble(
                predict_fn,
                context.inputs,
                rng,
                future_features=(
                    forcing_contract.future_dynamic_features
                    if forcing_contract is not None
                    else model.data_requirements.future_dynamic_features  # type: ignore[union-attr]
                ),
            )
            ensemble_member_states = predict_fn.states
            new_state = None
        else:
            ensembles, new_state = model.predict(  # type: ignore[union-attr]
                artifact,
                context.inputs,
                rng,
                prior_state=context.prior_state,
            )
    except Exception as exc:
        log.warning(
            "run_station_forecast.predict_failed",
            station_id=str(station_id),
            model_id=str(assignment.model_id),
            error=str(exc),
        )
        return AssignmentFailure(
            cause=AssignmentFailureCause.PREDICT_FAILED,
            detail=f"predict failed: {exc}",
        )

    # Combining N per-member warm-up states into one aggregate is ill-defined.
    # Stateless ensemble models (all per-member states ``None``) lose nothing —
    # report ``new_state = None``. But a NON-None per-member state means a stateful
    # ensemble model, which is unsupported. Assignment-local, same rationale as
    # the input-side guard above.
    if ensemble_member_states is not None:
        try:
            reject_stateful_ensemble_states(ensemble_member_states)
        except ModelOutputError as exc:
            log.warning(
                "run_station_forecast.unsupported_stateful_ensemble",
                station_id=str(station_id),
                model_id=str(assignment.model_id),
                error=str(exc),
            )
            return AssignmentFailure(
                cause=AssignmentFailureCause.UNSUPPORTED_STATEFUL_ENSEMBLE,
                detail=f"unsupported stateful ensemble: {exc}",
            )

    ensembles = cast("dict[str, ForecastEnsemble]", ensembles)
    new_state = cast("bytes | None", new_state)

    all_flags: dict[str, list[QcFlag]] = {}
    for param, ensemble in ensembles.items():
        datum = water_level_datum_masl if param == "water_level" else None
        qc_ensemble = shift_ensemble_for_water_level_datum(ensemble, datum=datum)
        skipped_rules = forecast_skipped_rules(param, datum)
        if skipped_rules:
            flags = qc_checker.check(
                qc_ensemble,
                qc_rules,
                qc_overrides,
                baselines,
                skipped_rule_ids=skipped_rules,
            )
        else:
            flags = qc_checker.check(qc_ensemble, qc_rules, qc_overrides, baselines)
        flags = add_forecast_datum_details(
            flags,
            raw_ensemble=ensemble,
            shifted_ensemble=qc_ensemble,
            datum=datum,
        )
        all_flags[param] = flags
        worst = worst_qc_status(flags)
        if worst == QcStatus.QC_FAILED:
            log.warning(
                "run_station_forecast.qc_failed",
                station_id=str(station_id),
                model_id=str(assignment.model_id),
                parameter=param,
            )
            return AssignmentFailure(
                cause=AssignmentFailureCause.QC_FAILED,
                detail=f"QC failed for parameter {param}",
            )

    iq_config = config.input_quality
    input_quality, input_quality_flags = assess_input_quality(
        observation_staleness_hours=context.observation_staleness_hours,
        warm_up_source=context.warm_up_source,
        warm_up_state_age_hours=context.warm_up_state_age_hours,
        nwp_cycle_source=nwp_cycle_source,
        nwp_age_hours=context.nwp_age_hours,  # type: ignore[arg-type]
        obs_partial_hours=config.observation_staleness_warning_hours,
        config=iq_config,
        warmup_partial_hours=iq_config.warmup_snapshot_age_partial_hours,
        warmup_degraded_hours=iq_config.warmup_snapshot_age_degraded_hours,
    )

    forecasts: list[OperationalForecast] = []
    now = clock()
    for param, ensemble in ensembles.items():
        flags = all_flags[param]
        qc_status = worst_qc_status(flags)
        forecast = OperationalForecast(
            id=ForecastId(id_gen()),
            station_id=station_id,
            model_id=assignment.model_id,
            model_artifact_id=artifact_id,
            issued_at=context.inputs.issue_time,
            nwp_cycle_reference_time=nwp_cycle_reference_time,
            nwp_cycle_source=nwp_cycle_source,
            representation=ensemble.representation,
            status=ForecastStatus.RAW,
            version=1,
            warm_up_source=context.warm_up_source,
            warm_up_state_age_hours=context.warm_up_state_age_hours,
            observation_staleness_hours=context.observation_staleness_hours,
            ensemble=ensemble,
            created_at=now,
            updated_at=now,
            qc_status=qc_status,
            qc_flags=tuple(flags),
            input_quality=input_quality,
            input_quality_flags=input_quality_flags,
        )
        forecasts.append(forecast)

    return AssignmentSuccess(
        result=StationForecastResult(
            station_id=station_id,
            model_id=assignment.model_id,
            artifact_id=artifact_id,
            forecasts=forecasts,
            new_state=new_state,
            ensembles=dict(ensembles),
        )
    )


def run_all_station_forecasts(
    station_id: StationId,
    inputs: StationModelInputs,
    input_metadata: OperationalInputMetadata,
    assignments: list[ModelAssignment],
    models: dict[ModelId, ForecastModel],
    artifact_store: ModelArtifactStore,
    qc_checker: ForecastOutputQualityChecker,
    qc_rules: ForecastQcRuleSet,
    qc_overrides: list[StationForecastQcOverride],
    baselines: list[ClimBaseline],
    nwp_cycle_reference_time: UtcDatetime | None,
    nwp_cycle_source: NwpCycleSource,
    config: DeploymentConfig,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
    rng: random.Random,
    model_state_store: ModelStateStore,
    water_level_datum_masl: float | None = None,
) -> MultiModelForecastResult:
    sorted_assignments = sorted(assignments, key=lambda a: a.priority)
    # Slim per-run scalars extracted ONCE from the shared ``input_metadata``
    # (Plan 148 D3) — every assignment's own warm-up state is read separately,
    # per-assignment, inside ``_run_single_model``.
    observation_staleness_hours = input_metadata.observation_staleness_hours
    nwp_age_hours = input_metadata.nwp_age_hours

    results: dict[ModelId, StationForecastResult] = {}
    priorities: dict[ModelId, int] = {}
    failed_models: dict[ModelId, AssignmentFailure] = {}
    primary_model_id: ModelId | None = None

    for assignment in sorted_assignments:
        priorities[assignment.model_id] = assignment.priority
        try:
            outcome = _run_single_model(
                station_id=station_id,
                assignment=assignment,
                inputs=inputs,
                observation_staleness_hours=observation_staleness_hours,
                nwp_age_hours=nwp_age_hours,
                model_state_store=model_state_store,
                models=models,
                artifact_store=artifact_store,
                qc_checker=qc_checker,
                qc_rules=qc_rules,
                qc_overrides=qc_overrides,
                baselines=baselines,
                water_level_datum_masl=water_level_datum_masl,
                nwp_cycle_reference_time=nwp_cycle_reference_time,
                nwp_cycle_source=nwp_cycle_source,
                config=config,
                clock=clock,
                id_gen=id_gen,
                rng=rng,
            )
        except Exception as exc:  # backstop for UNANTICIPATED bugs only (D3/D6)
            log.error(
                "run_station_forecast.unexpected_exception",
                station_id=str(station_id),
                model_id=str(assignment.model_id),
                error=str(exc),
            )
            outcome = AssignmentFailure(
                cause=AssignmentFailureCause.UNEXPECTED_EXCEPTION,
                detail=f"unexpected error: {exc}",
            )
        match outcome:
            case AssignmentSuccess(result=result):
                results[assignment.model_id] = result
                if primary_model_id is None:
                    primary_model_id = assignment.model_id
            case AssignmentFailure() as failure:
                failed_models[assignment.model_id] = failure

    return MultiModelForecastResult(
        station_id=station_id,
        results=results,
        priorities=priorities,
        primary_model_id=primary_model_id,
        failed_models=failed_models,
    )


def run_all_station_forecasts_per_track(
    station_id: StationId,
    run_inputs: dict[ModelId, AssignmentRunInput],
    assignments: list[ModelAssignment],
    models: dict[ModelId, ForecastModel],
    artifact_store: ModelArtifactStore,
    qc_checker: ForecastOutputQualityChecker,
    qc_rules: ForecastQcRuleSet,
    qc_overrides: list[StationForecastQcOverride],
    baselines: list[ClimBaseline],
    config: DeploymentConfig,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
    rng: random.Random,
    model_state_store: ModelStateStore,
    water_level_datum_masl: float | None = None,
) -> MultiModelForecastResult:
    """Plan 151 T7 (D10): the per-track counterpart of
    ``run_all_station_forecasts``. Instead of ONE shared ``inputs`` /
    ``nwp_cycle_reference_time`` for the whole station, each assignment
    supplies its OWN resolved ``AssignmentRunInput`` (built by T5/T6's track
    resolution + assembly, keyed by ``model_id``) — a heterogeneous station
    can therefore run each assignment against its own cycle. The two
    pre-run failure arms (``MissingTrackContext`` / ``UnavailableTrackContext``)
    are resolved HERE, before ``_run_single_model`` is invoked, and never
    touch the model registry, artifact store, model-state store, or QC.
    """
    sorted_assignments = sorted(assignments, key=lambda a: a.priority)

    results: dict[ModelId, StationForecastResult] = {}
    priorities: dict[ModelId, int] = {}
    failed_models: dict[ModelId, AssignmentFailure] = {}
    primary_model_id: ModelId | None = None

    for assignment in sorted_assignments:
        priorities[assignment.model_id] = assignment.priority
        run_input = run_inputs.get(assignment.model_id)
        if run_input is None:
            log.warning(
                "forecast.assignment_failed",
                station_id=str(station_id),
                model_id=str(assignment.model_id),
                cause="MISSING_CONTEXT",
                detail="no run input resolved for this assignment",
            )
            failed_models[assignment.model_id] = AssignmentFailure(
                cause=AssignmentFailureCause.MISSING_CONTEXT,
                detail="no run input resolved for this assignment",
            )
            continue

        match run_input:
            case MissingTrackContext():
                log.warning(
                    "forecast.fallback_advanced",
                    station_id=str(station_id),
                    model_id=str(assignment.model_id),
                    cause="MISSING_CONTEXT",
                )
                failed_models[assignment.model_id] = AssignmentFailure(
                    cause=AssignmentFailureCause.MISSING_CONTEXT,
                    detail=("forcing track never resolved within the walk-back bound"),
                )
                continue
            case UnavailableTrackContext(reason=reason):
                log.warning(
                    "forecast.fallback_advanced",
                    station_id=str(station_id),
                    model_id=str(assignment.model_id),
                    cause="TRACK_UNAVAILABLE",
                    reason=reason.name,
                )
                failed_models[assignment.model_id] = AssignmentFailure(
                    cause=AssignmentFailureCause.TRACK_UNAVAILABLE,
                    detail=f"station unavailable at resolved cycle: {reason.name}",
                )
                continue
            case ReadyContext(
                inputs=run_inputs_data,
                observation_staleness_hours=observation_staleness_hours,
                nwp_age_hours=nwp_age_hours,
                provenance=provenance,
                contract=contract,
            ):
                pass

        try:
            outcome = _run_single_model(
                station_id=station_id,
                assignment=assignment,
                inputs=run_inputs_data,
                observation_staleness_hours=observation_staleness_hours,
                nwp_age_hours=nwp_age_hours,
                model_state_store=model_state_store,
                models=models,
                artifact_store=artifact_store,
                qc_checker=qc_checker,
                qc_rules=qc_rules,
                qc_overrides=qc_overrides,
                baselines=baselines,
                water_level_datum_masl=water_level_datum_masl,
                nwp_cycle_reference_time=provenance.nwp_cycle_reference_time,
                nwp_cycle_source=provenance.nwp_cycle_source,
                config=config,
                clock=clock,
                id_gen=id_gen,
                rng=rng,
                forcing_contract=contract,
            )
        except Exception as exc:  # backstop for UNANTICIPATED bugs only (D3/D6)
            log.error(
                "run_station_forecast.unexpected_exception",
                station_id=str(station_id),
                model_id=str(assignment.model_id),
                error=str(exc),
            )
            outcome = AssignmentFailure(
                cause=AssignmentFailureCause.UNEXPECTED_EXCEPTION,
                detail=f"unexpected error: {exc}",
            )
        match outcome:
            case AssignmentSuccess(result=result):
                results[assignment.model_id] = result
                if primary_model_id is None:
                    primary_model_id = assignment.model_id
            case AssignmentFailure() as failure:
                failed_models[assignment.model_id] = failure

    return MultiModelForecastResult(
        station_id=station_id,
        results=results,
        priorities=priorities,
        primary_model_id=primary_model_id,
        failed_models=failed_models,
    )


def run_station_forecast(
    station_id: StationId,
    inputs: StationModelInputs,
    input_metadata: OperationalInputMetadata,
    assignments: list[ModelAssignment],
    models: dict[ModelId, ForecastModel],
    artifact_store: ModelArtifactStore,
    qc_checker: ForecastOutputQualityChecker,
    qc_rules: ForecastQcRuleSet,
    qc_overrides: list[StationForecastQcOverride],
    baselines: list[ClimBaseline],
    nwp_cycle_reference_time: UtcDatetime | None,
    nwp_cycle_source: NwpCycleSource,
    config: DeploymentConfig,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
    rng: random.Random,
    model_state_store: ModelStateStore,
    water_level_datum_masl: float | None = None,
) -> StationForecastResult | None:
    multi = run_all_station_forecasts(
        station_id=station_id,
        inputs=inputs,
        input_metadata=input_metadata,
        assignments=assignments,
        models=models,
        artifact_store=artifact_store,
        qc_checker=qc_checker,
        qc_rules=qc_rules,
        qc_overrides=qc_overrides,
        baselines=baselines,
        nwp_cycle_reference_time=nwp_cycle_reference_time,
        nwp_cycle_source=nwp_cycle_source,
        config=config,
        clock=clock,
        id_gen=id_gen,
        rng=rng,
        model_state_store=model_state_store,
        water_level_datum_masl=water_level_datum_masl,
    )
    if multi.primary_model_id is None:
        log.warning(
            "run_station_forecast.all_models_failed", station_id=str(station_id)
        )
        return None
    return multi.results[multi.primary_model_id]
