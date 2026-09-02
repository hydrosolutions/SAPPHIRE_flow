"""Plan 151 T6: per-assignment operational-input assembly (D9).

Unlike the legacy ``assemble_station_operational_inputs`` (one shared frame
per station, superset requirements, one ``cycle_time``), this module
assembles ONE frame per assignment: past_targets/past_dynamic/static come
from THIS assignment's own ``model.data_requirements`` (never a
station-wide superset), and ``future_dynamic`` comes from THIS assignment's
forcing track's own resolved records, sliced to the assignment's own
per-feature horizons (<= the track's fetch_horizons, D5/D8/D26).

Also defines the runner-boundary discriminated union (D10): ``ReadyContext``
(T6-built) plus ``MissingTrackContext`` / ``UnavailableTrackContext``
(T7-constructed) narrow to ``AssignmentRunInput``. These stay service-local
— not part of the locked spec — mirroring ``ModelRunContext``'s own
service-local precedent (Plan 148 D1, D1-types-location).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl
import structlog

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.caravan_statics import resolve_shared_static_frame
from sapphire_flow.services.operational_inputs import (
    build_future_dynamic_frame,
    observations_to_wide_dataframe,
    raw_forcing_to_dataframe,
)
from sapphire_flow.services.training_data import (
    aligned_lookback_bounds,
    resample_to_time_step,
    validate_time_step_cadence,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    EnsembleMode,
    ForcingRoute,
    NwpCycleSource,
    QcStatus,
)
from sapphire_flow.types.forcing_track import (
    AssignmentKey,
    ForcingRequired,
    NoForcingRequired,
    StationTrackAvailable,
    StationTrackUnavailable,
    StationUnavailableReason,
)
from sapphire_flow.types.forecast import ForecastProvenance
from sapphire_flow.types.model import StationInputData, StationModelInputs

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import timedelta

    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.protocols.forecast_model import StationForecastModel
    from sapphire_flow.protocols.stores import (
        BasinStore,
        ObservationStore,
        StationStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forcing_track import (
        FeatureFetchHorizons,
        StationTrackOutcome,
        TrackProjection,
    )
    from sapphire_flow.types.ids import ModelId, StationId
    from sapphire_flow.types.model import ModelDataRequirements
    from sapphire_flow.types.observation import Observation

log = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True, slots=True)
class ForcingContract:
    """Per-assignment per-feature forcing contract (D10a) — what the runner
    reads coverage/dispatch/fan-out from on the per-track ``ReadyContext``
    route, REPLACING ``model.data_requirements``' forcing reads on that
    route only.

    ``expected_member_ids`` (review fold-in — blocker) is the source-derived
    exact member set the assembled track satisfied (``None`` for a
    non-``ENSEMBLE`` contract or a ``NoForcingRequired`` assignment) — the
    runner's defensive re-check validates each feature's ACTUAL member set
    against this ground truth, not merely against another feature's set
    (which passes trivially for a single-feature model or when every
    feature is uniformly short the same way).
    """

    feature_horizons: FeatureFetchHorizons
    ensemble_mode: EnsembleMode
    future_dynamic_features: frozenset[str]
    expected_member_ids: frozenset[int] | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ReadyContext:
    """The per-track route's runner input for ONE assignment (D10): T6's
    own assembled inputs/metadata + its own provenance + the per-feature
    forcing contract (``None`` for a ``NoForcingRequired`` assignment — no
    future forcing at all).

    Carries loose ``inputs``/``observation_staleness_hours``/``nwp_age_hours``
    fields rather than a prebuilt ``ModelRunContext`` (review fold-in —
    major): warm-up state has exactly ONE owner, ``_run_single_model``'s
    existing assignment-local ``load_warm_up_state`` call (unchanged from
    the legacy route) — T6 does not read the state store at all, so there is
    no second, uncaught-outside-``WARM_UP_LOAD_FAILED`` load to disagree
    with it or double the store reads.
    """

    inputs: StationModelInputs
    observation_staleness_hours: float | None
    nwp_age_hours: float | None
    provenance: ForecastProvenance
    contract: ForcingContract | None


@dataclass(frozen=True, kw_only=True, slots=True)
class MissingTrackContext:
    """The track never resolved within the walk-back bound (D3-mapping) —
    the model is NOT called for this assignment."""

    assignment: AssignmentKey


@dataclass(frozen=True, kw_only=True, slots=True)
class UnavailableTrackContext:
    """The track resolved, but THIS station is unavailable at the accepted
    cycle (D3-mapping)."""

    assignment: AssignmentKey
    reason: StationUnavailableReason


AssignmentRunInput = ReadyContext | MissingTrackContext | UnavailableTrackContext


def _single_time_step(reqs: ModelDataRequirements) -> timedelta:
    """The model's own supported time_step — Phase 3's per-track assignments
    are single-resolution (D1/D2); ``supported_time_steps`` carries exactly
    one entry for any model that reaches this route."""
    if len(reqs.supported_time_steps) != 1:
        raise ValueError(
            "per-track assembly requires exactly one supported_time_steps "
            f"entry, got {reqs.supported_time_steps!r}"
        )
    return next(iter(reqs.supported_time_steps))


def assemble_assignment_inputs(
    *,
    station_id: StationId,
    model_id: ModelId,
    model: StationForecastModel,
    projection: TrackProjection,
    track_outcome: StationTrackOutcome | None,
    issue_time: UtcDatetime,
    obs_store: ObservationStore,
    station_store: StationStore,
    basin_store: BasinStore,
    forcing_source: WeatherReanalysisSource,
    clock: Callable[[], UtcDatetime],
    static_naming_models: list[object] | None = None,
    expected_member_ids: frozenset[int] | None = None,
) -> ReadyContext | MissingTrackContext | UnavailableTrackContext:
    """Assemble ONE assignment's own frame (D9).

    ``track_outcome`` is ``None`` iff ``projection`` is ``NoForcingRequired``
    (a fallback/skill model with no future forcing — its context is
    assembled independently of track resolution, D2). For a
    ``ForcingRequired`` projection, ``track_outcome`` must be the resolved
    track's OWN per-station outcome — ``StationTrackAvailable`` yields a
    ``ReadyContext``; ``StationTrackUnavailable`` yields an
    ``UnavailableTrackContext`` (the model is never called, D10). The caller
    is responsible for ``MissingTrackContext`` (the track never resolved at
    all — T6 has nothing to assemble from in that case).

    ``expected_member_ids`` (review fold-in — blocker) is the source-derived
    exact member set the caller resolved this track against (T5's
    ``CandidateAwareForecastSource.expected_member_ids``) — threaded onto
    the ``ForcingContract`` so the runner's defensive re-check can validate
    each feature's actual member set against ground truth rather than only
    against another feature's set. ``None`` for a ``NoForcingRequired``
    assignment or a non-``ENSEMBLE`` track (no member axis to check).

    Does NOT load warm-up state — that stays the sole responsibility of
    ``_run_single_model``'s existing assignment-local
    ``load_warm_up_state`` call (review fold-in — major: a second,
    independent load here previously discarded the assignment-local
    ``WARM_UP_LOAD_FAILED`` safety net and could race the first read).
    """
    assignment_key = AssignmentKey((station_id, model_id))
    now = clock()
    reqs = model.data_requirements
    time_step = _single_time_step(reqs)

    resolved_cycle: UtcDatetime | None = None
    nwp_age_hours: float | None = None
    contract: ForcingContract | None = None
    future_dynamic = pl.DataFrame()
    forecast_horizon_steps = reqs.forecast_horizon_steps
    cycle_source = NwpCycleSource.RUNOFF_ONLY

    match projection, track_outcome:
        case NoForcingRequired(), None:
            pass
        case ForcingRequired(), StationTrackUnavailable(reason=reason):
            return UnavailableTrackContext(assignment=assignment_key, reason=reason)
        case (
            ForcingRequired(assignment_horizons=horizons),
            StationTrackAvailable(
                cycle=cycle, records=records, provenance=cycle_source
            ),
        ):
            resolved_cycle = cycle
            forecast_horizon_steps = max(h.value for h in horizons.values())
            station_records = [r for r in records if r.station_id == station_id]
            feature_horizons = {
                str(name): steps.value for name, steps in horizons.items()
            }
            future_dynamic = build_future_dynamic_frame(
                station_records,
                time_step=time_step,
                issue_time=issue_time,
                forecast_horizon_steps=forecast_horizon_steps,
                future_dynamic_features=frozenset(horizons),
                ensemble_mode=reqs.ensemble_mode,
                feature_horizons=feature_horizons,
            )
            nwp_age_hours = (now - cycle).total_seconds() / 3600.0
            if nwp_age_hours < 0:
                log.warning(
                    "track_assembly.nwp_cycle_in_future", nwp_age_hours=nwp_age_hours
                )
                nwp_age_hours = 0.0
            contract = ForcingContract(
                feature_horizons=horizons,
                ensemble_mode=reqs.ensemble_mode,
                future_dynamic_features=frozenset(horizons),
                expected_member_ids=(
                    expected_member_ids
                    if reqs.ensemble_mode is EnsembleMode.ENSEMBLE
                    else None
                ),
            )
        case _:
            raise ValueError(
                f"inconsistent projection/track_outcome pair: {projection!r}, "
                f"{track_outcome!r}"
            )

    lookback_start = ensure_utc(issue_time - reqs.lookback_steps * time_step)
    # Plan 228 D4: see `operational_inputs.py` — past_targets bounds are
    # ALIGNED and EXTENDED to complete UTC-calendar buckets, never `past_
    # dynamic`'s unresampled `lookback_start`/`issue_time` window (below).
    past_targets_start, past_targets_end = aligned_lookback_bounds(
        issue_time, reqs.lookback_steps, time_step
    )

    target_parameters = list(reqs.target_parameters)
    all_observations: list[Observation] = []
    for parameter in target_parameters:
        all_observations.extend(
            obs_store.fetch_observations(
                station_id=station_id,
                parameter=parameter,
                start=past_targets_start,
                end=past_targets_end,
                qc_status=QcStatus.QC_PASSED,
            )
        )
    past_targets = observations_to_wide_dataframe(all_observations, target_parameters)
    past_targets = resample_to_time_step(
        past_targets, time_step, aggregation_methods=None
    )
    # Plan 228 D1(C): backstop shared with the hindcast assembler — see
    # `validate_time_step_cadence` for why this is scoped to past_targets.
    #
    # Review fixer round (major): a real BAFU/SwissMetNet lookback window
    # can legitimately contain an isolated missing bucket (sensor/comms
    # gap) — a KNOWN per-station data-availability condition, not a bug.
    # `INCOMPLETE_AT_CYCLE` already exists for exactly this (assigned in
    # `track_resolution.py` for the analogous forcing-side condition);
    # stamp it here directly rather than letting the exception fall through
    # to the caller's generic `_contained_assemble` catch-all, which would
    # mislabel it `ASSEMBLY_FAILED` (a genuine programming bug, `log.exception`
    # stack trace and all) instead of an expected, recoverable data gap.
    try:
        validate_time_step_cadence(
            past_targets, time_step, context="track_assembly.past_targets"
        )
    except ConfigurationError as exc:
        log.warning(
            "track_assembly.cadence_mismatch_skip",
            station_id=str(station_id),
            model_id=str(model_id),
            issue_time=str(issue_time),
            error=str(exc),
        )
        return UnavailableTrackContext(
            assignment=assignment_key,
            reason=StationUnavailableReason.INCOMPLETE_AT_CYCLE,
        )

    latest_obs_ts = max((o.timestamp for o in all_observations), default=None)
    observation_staleness_hours: float | None = None
    if latest_obs_ts is not None:
        observation_staleness_hours = (now - latest_obs_ts).total_seconds() / 3600.0
    else:
        log.warning(
            "track_assembly.no_observations",
            station_id=str(station_id),
            issue_time=str(issue_time),
        )

    past_dynamic_features = list(reqs.past_dynamic_features)
    if past_dynamic_features:
        reanalysis_bindings = station_store.fetch_reanalysis_bindings(station_id)
        raw_forcing = forcing_source.fetch_reanalysis(
            station_configs=reanalysis_bindings,
            start=lookback_start,
            end=issue_time,
            parameters=past_dynamic_features,
        )
        past_dynamic = raw_forcing_to_dataframe(
            raw_forcing, station_id, past_dynamic_features
        )
        if past_dynamic is None:
            log.warning(
                "track_assembly.no_past_dynamic",
                station_id=str(station_id),
                issue_time=str(issue_time),
            )
            past_dynamic = pl.DataFrame()
    else:
        past_dynamic = pl.DataFrame()

    static_df: pl.DataFrame | None = None
    station_config = station_store.fetch_station(station_id)
    if station_config is not None and station_config.basin_id is not None:
        basin = basin_store.fetch_basin(station_config.basin_id)
        if basin is not None and basin.attributes:
            static_df = pl.DataFrame(
                [
                    resolve_shared_static_frame(
                        basin.attributes,
                        [model, *(static_naming_models or ())],
                        station_code=station_config.code,
                    )
                ]
            )

    inputs = StationModelInputs(
        station_id=station_id,
        data=StationInputData(
            past_targets=past_targets,
            past_dynamic=past_dynamic,
            future_dynamic=future_dynamic,
            static=static_df,
        ),
        issue_time=issue_time,
        forecast_horizon_steps=forecast_horizon_steps,
        time_step=time_step,
        # Plan 151 D10: the explicit per-track discriminant. Set HERE and
        # nowhere else — it is what tells the FI boundary that D9's
        # per-variable ``future_steps`` slice applies to these inputs.
        # ``contract is None`` (a ``NoForcingRequired`` assignment) does NOT
        # demote the route: the route is which assembler built the inputs,
        # never an inference from an absent contract.
        forcing_route=ForcingRoute.PER_TRACK,
    )
    provenance = ForecastProvenance(
        nwp_cycle_source=cycle_source,
        nwp_cycle_reference_time=resolved_cycle,
    )
    return ReadyContext(
        inputs=inputs,
        observation_staleness_hours=observation_staleness_hours,
        nwp_age_hours=nwp_age_hours,
        provenance=provenance,
        contract=contract,
    )
