from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import timedelta
    from uuid import UUID

import numpy as np
import polars as pl
import structlog

from sapphire_flow.services.skill.diagrams import (
    compute_rank_histogram,
    compute_reliability_diagram,
    compute_roc_curve,
)
from sapphire_flow.services.skill.metrics import (
    compute_bss,
    compute_contingency,
    compute_crps,
    compute_kge,
    compute_mae,
    compute_nse,
    compute_pbias,
    compute_peak_timing_error,
    compute_sharpness,
)
from sapphire_flow.services.training_data import (
    aggregation_method_for,
    resample_to_time_step,
)
from sapphire_flow.types.enums import EnsembleRepresentation, FlowRegime, SkillFreshness

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import SeasonDefinition, StationThreshold
    from sapphire_flow.types.enums import ForcingType, SkillSource
    from sapphire_flow.types.forecast import HindcastForecast
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.skill import FlowRegimeConfig, SkillDiagram, SkillScore

log = structlog.get_logger(__name__)

# Plan 228 review fixer round (blocker): the natural key on `skill_scores`/
# `skill_diagrams` includes `computation_version` (`uq_skill_scores_natural_key`,
# `db/metadata.py`), and `store_skill_scores`/`store_skill_diagrams` insert with
# `ON CONFLICT DO NOTHING`. `mark_stale` (`skill_store.py`) only flips
# `freshness`, it never changes `computation_version` — so recomputing at the
# SAME version collides with the now-stale row's still-live natural key and
# the corrected row is silently dropped, never inserted. Bumping the version
# here gives every post-Plan-228 recompute a natural key the old (stale) rows
# never occupy, so the insert succeeds AND `fetch_latest_scores`'s
# `max(computation_version)` picks the new rows up automatically.
_COMPUTATION_VERSION = 2
_DEFAULT_DECISION_PROBABILITY = 0.5


def _ensemble_matrix(hindcast: HindcastForecast, valid_time: object) -> np.ndarray:
    df = hindcast.ensemble.values
    if hindcast.ensemble.representation == EnsembleRepresentation.MEMBERS:
        filtered = df.filter(df["valid_time"] == valid_time)
        return filtered["value"].to_numpy()
    # QUANTILES: use quantile values as pseudo-members
    filtered = df.filter(df["valid_time"] == valid_time)
    return filtered["value"].to_numpy()


def _infer_forecast_time_step(hindcasts: list[HindcastForecast]) -> timedelta | None:
    """The forecast's own time_step (Plan 228 D2): the smallest gap between
    two distinct ``valid_time``s inside a single hindcast's own ensemble —
    i.e. its lead-step size, which for every model equals its declared
    ``time_step``. Falls back to the gap between successive hindcast issue
    times (``hindcast_step``, spaced exactly ``time_step`` apart by
    ``_issue_times``) only for a horizon-1 model, whose ensembles never carry
    two valid_times to diff. Returns ``None`` when neither signal is
    available (a single hindcast with a single lead step).
    """
    within_hindcast_gaps: list[timedelta] = []
    for hc in hindcasts:
        vts = sorted(hc.ensemble.values["valid_time"].unique().to_list())
        within_hindcast_gaps.extend(
            b - a for a, b in zip(vts, vts[1:], strict=False) if b > a
        )
    if within_hindcast_gaps:
        return min(within_hindcast_gaps)

    issue_steps = sorted({hc.hindcast_step for hc in hindcasts})
    issue_gaps = [
        b - a for a, b in zip(issue_steps, issue_steps[1:], strict=False) if b > a
    ]
    return min(issue_gaps) if issue_gaps else None


def _forecast_valid_time_anchor(
    hindcasts: list[HindcastForecast],
) -> UtcDatetime | None:
    """The phase every forecast ``valid_time`` shares (Plan 228 review fixer
    round): any single ``valid_time`` works as the anchor, since a model
    issues on a fixed cadence and every ``valid_time`` therefore falls at the
    same time-of-day modulo its ``time_step``. Returns ``None`` only when no
    hindcast carries any ``valid_time`` at all (empty ensembles).
    """
    for hc in hindcasts:
        vts = hc.ensemble.values["valid_time"].to_list()
        if vts:
            return vts[0]
    return None


def _resample_observations_to_forecast_step(
    observations: list[Observation],
    parameter: str,
    time_step: timedelta,
    anchor: UtcDatetime | None,
) -> dict[object, float]:
    """Aggregate raw observations to ``time_step`` (Plan 228 P2/D2), keyed by
    the resulting bucket ``timestamp`` for an exact-key lookup against a
    forecast's ``valid_time`` — the SAME resampling ``_assemble_hindcast_inputs``
    applies to the model's own lookback (P1/D1), so a step already at
    ``time_step`` cadence (nothing to fix) passes through unchanged.

    A daily-mean forecast compared against a single instantaneous reading is
    a pure quantity mismatch present in every skill score regardless of
    forecast quality (measured: median 6.4%, p95 48.6% error). This
    aggregates with the SAME method the model trains against
    (``aggregation_method_for``) rather than substituting a different one.

    ``anchor`` (review fixer round) pins the resulting bucket labels to the
    forecast's own ``valid_time`` phase — without it, buckets fall on
    UTC-midnight boundaries regardless of what hour the forecast is valid at,
    so a non-midnight ``valid_time`` never exact-matches any key here and
    ``_build_strata`` silently finds no observation for it at all.
    """
    valued = [(o.timestamp, o.value) for o in observations if o.value is not None]
    if not valued:
        return {}
    df = pl.DataFrame(
        {
            "timestamp": [t for t, _ in valued],
            parameter: [v for _, v in valued],
        }
    )
    resampled = resample_to_time_step(
        df,
        time_step,
        aggregation_methods={parameter: aggregation_method_for(parameter)},
        anchor=anchor,
    )
    return dict(
        zip(
            resampled["timestamp"].to_list(),
            resampled[parameter].to_list(),
            strict=True,
        )
    )


def _classify_flow_regime(
    obs_value: float,
    config: FlowRegimeConfig,
) -> FlowRegime:
    if obs_value > config.p90:
        return FlowRegime.FLOOD
    if obs_value > config.p50:
        return FlowRegime.HIGH
    return FlowRegime.LOW


def _find_season(timestamp: UtcDatetime, seasons: list[SeasonDefinition]) -> str | None:
    month = timestamp.month
    for s in seasons:
        if month in s.months:
            return s.name
    return None


StratumKey = tuple[int, str | None, FlowRegime | None]


def _build_strata(
    hindcasts: list[HindcastForecast],
    obs_lookup: dict[object, float],
    seasons: list[SeasonDefinition],
    flow_regime_config: FlowRegimeConfig | None,
) -> dict[StratumKey, tuple[list[np.ndarray], list[float]]]:
    # Returns: stratum_key -> (list of ensemble_1d arrays, list of observed values)
    # Stratum key: (lead_time_hours, season_name | None, flow_regime | None)
    # None season = "all seasons" aggregate; None flow_regime = "all regimes" aggregate

    # raw_pairs[lead_time_hours] = list of (ensemble_1d, obs_value, hindcast_step)
    raw_pairs: dict[int, list[tuple[np.ndarray, float, object]]] = defaultdict(list)

    for hc in hindcasts:
        df = hc.ensemble.values
        valid_times = df["valid_time"].unique().sort()
        for vt in valid_times:
            if vt not in obs_lookup:
                continue
            obs_val = obs_lookup[vt]
            members = _ensemble_matrix(hc, vt)
            if len(members) == 0:
                continue
            dt_diff = (vt - hc.hindcast_step).total_seconds()
            lead_hours = int(round(dt_diff / 3600))
            raw_pairs[lead_hours].append((members, obs_val, hc.hindcast_step))

    strata: dict[StratumKey, tuple[list[np.ndarray], list[float]]] = defaultdict(
        lambda: ([], [])
    )

    for lead_hours, pairs in raw_pairs.items():
        for members, obs_val, hindcast_step in pairs:
            season = _find_season(hindcast_step, seasons)  # type: ignore[arg-type]
            regime = (
                _classify_flow_regime(obs_val, flow_regime_config)
                if flow_regime_config is not None
                else None
            )

            # All-season + all-regime aggregate
            strata[(lead_hours, None, None)][0].append(members)
            strata[(lead_hours, None, None)][1].append(obs_val)

            # Season-specific + all-regime
            if season is not None:
                strata[(lead_hours, season, None)][0].append(members)
                strata[(lead_hours, season, None)][1].append(obs_val)

            # All-season + regime-specific
            if regime is not None:
                strata[(lead_hours, None, regime)][0].append(members)
                strata[(lead_hours, None, regime)][1].append(obs_val)

            # Season-specific + regime-specific
            if season is not None and regime is not None:
                strata[(lead_hours, season, regime)][0].append(members)
                strata[(lead_hours, season, regime)][1].append(obs_val)

    return strata


def _compute_scores(
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId | None,
    parameter: str,
    lead_hours: int,
    season: str | None,
    regime: FlowRegime | None,
    ensemble_list: list[np.ndarray],
    obs_list: list[float],
    thresholds: list[StationThreshold],
    skill_source: SkillSource,
    forcing_type: ForcingType | None,
    flow_regime_config_id: UUID | None,
    eval_start: UtcDatetime,
    eval_end: UtcDatetime,
    clock: Callable[[], UtcDatetime],
    uuid_factory: Callable[[], UUID],
) -> list[SkillScore]:
    from sapphire_flow.types.skill import SkillScore

    obs_arr = np.array(obs_list, dtype=float)
    sample_size = len(obs_arr)
    now = clock()

    scores: list[SkillScore] = []

    def _add(metric: str, value: float) -> None:
        scores.append(
            SkillScore(
                id=uuid_factory(),
                station_id=station_id,
                model_id=model_id,
                parameter=parameter,
                model_artifact_id=artifact_id,
                skill_source=skill_source,
                forcing_type=forcing_type,
                computation_version=_COMPUTATION_VERSION,
                computed_at=now,
                lead_time_hours=lead_hours,
                season=season,
                flow_regime=regime,
                flow_regime_config_id=flow_regime_config_id,
                metric=metric,
                score=value,
                sample_size=sample_size,
                freshness=SkillFreshness.CURRENT,
                eval_period_start=eval_start,
                eval_period_end=eval_end,
                created_at=now,
            )
        )

    # Per-timestep CRPS → average
    crps_values = [
        compute_crps(m, o) for m, o in zip(ensemble_list, obs_list, strict=True)
    ]
    _add("crps", float(np.mean(crps_values)))

    # Deterministic metrics using ensemble median
    medians = np.array([float(np.median(m)) for m in ensemble_list])
    _add("nse", compute_nse(medians, obs_arr))
    _add("kge", compute_kge(medians, obs_arr))
    _add("pbias", compute_pbias(medians, obs_arr))
    _add("mae", compute_mae(medians, obs_arr))

    # Sharpness (requires 2D array — pad/align members by stacking)
    if sample_size > 0:
        min_n = min(len(m) for m in ensemble_list)
        ens_2d = np.stack([m[:min_n] for m in ensemble_list])  # (n_times, n_members)
        sharp_p10_p90, sharp_p25_p75, ens_range = compute_sharpness(ens_2d)
        _add("sharpness_p10_p90", sharp_p10_p90)
        _add("sharpness_p25_p75", sharp_p25_p75)
        _add("ensemble_range", ens_range)

        # Peak timing error
        peak_threshold = float(np.percentile(obs_arr, 90))
        pta = compute_peak_timing_error(medians, obs_arr, peak_threshold)
        if pta is not None:
            _add("peak_timing_error", pta)

        # Threshold-dependent metrics
        for thr in thresholds:
            ens_2d_full = np.stack([m for m in ensemble_list])
            bss = compute_bss(ens_2d_full, obs_arr, thr.value)
            _add(f"bss_danger_{thr.danger_level}", bss)
            pod, far, csi = compute_contingency(
                ens_2d_full, obs_arr, thr.value, _DEFAULT_DECISION_PROBABILITY
            )
            _add(f"pod_danger_{thr.danger_level}", pod)
            _add(f"far_danger_{thr.danger_level}", far)
            _add(f"csi_danger_{thr.danger_level}", csi)

    return scores


def _compute_diagrams(
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId | None,
    parameter: str,
    lead_hours: int,
    season: str | None,
    regime: FlowRegime | None,
    ensemble_list: list[np.ndarray],
    obs_list: list[float],
    thresholds: list[StationThreshold],
    skill_source: SkillSource,
    flow_regime_config_id: UUID | None,
    eval_start: UtcDatetime,
    eval_end: UtcDatetime,
    clock: Callable[[], UtcDatetime],
    uuid_factory: Callable[[], UUID],
) -> list[SkillDiagram]:
    from sapphire_flow.types.skill import SkillDiagram

    if len(ensemble_list) == 0:
        return []

    obs_arr = np.array(obs_list, dtype=float)
    now = clock()

    min_n = min(len(m) for m in ensemble_list)
    ens_2d = np.stack([m[:min_n] for m in ensemble_list])

    diagrams: list[SkillDiagram] = []

    def _add(
        diagram_type: str,
        data: dict,
        threshold_level: str | None = None,
    ) -> None:
        diagrams.append(
            SkillDiagram(
                id=uuid_factory(),
                station_id=station_id,
                model_id=model_id,
                parameter=parameter,
                model_artifact_id=artifact_id,
                skill_source=skill_source,
                computation_version=_COMPUTATION_VERSION,
                lead_time_hours=lead_hours,
                season=season,
                flow_regime=regime,
                flow_regime_config_id=flow_regime_config_id,
                diagram_type=diagram_type,  # type: ignore[arg-type]
                threshold_level=threshold_level,
                data=data,
                eval_period_start=eval_start,
                eval_period_end=eval_end,
                created_at=now,
            )
        )

    # Rank histogram (no threshold required)
    _add("rank_histogram", compute_rank_histogram(ens_2d, obs_arr))

    # Per-threshold diagrams
    for thr in thresholds:
        _add(
            "reliability",
            compute_reliability_diagram(ens_2d, obs_arr, thr.value),
            threshold_level=thr.danger_level,
        )
        _add(
            "roc",
            compute_roc_curve(ens_2d, obs_arr, thr.value),
            threshold_level=thr.danger_level,
        )

    return diagrams


def compute_skill_for_station(
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId | None,
    hindcasts: list[HindcastForecast],
    observations: list[Observation],
    thresholds: list[StationThreshold],
    flow_regime_config: FlowRegimeConfig | None,
    seasons: list[SeasonDefinition],
    skill_source: SkillSource,
    forcing_type: ForcingType | None,
    clock: Callable[[], UtcDatetime],
    uuid_factory: Callable[[], UUID],
    *,
    parameter: str,
) -> tuple[list[SkillScore], list[SkillDiagram]]:
    if not hindcasts or not observations:
        return [], []

    mismatched = [hc for hc in hindcasts if hc.ensemble.parameter != parameter]
    if mismatched:
        raise ValueError(
            f"compute_skill_for_station received hindcasts with parameters "
            f"other than '{parameter}': "
            f"{sorted({hc.ensemble.parameter for hc in mismatched})}"
        )

    # Plan 228 P2/D2: the forecast is a step-mean; the observation must be
    # aggregated to the SAME step before comparison, never joined against a
    # raw instantaneous reading.
    time_step = _infer_forecast_time_step(hindcasts)
    if time_step is None:
        # Review fixer round (minor): a single hindcast with a single lead
        # step (a station/model right after onboarding, or any horizon-1
        # model's first scoring pass) has no gap to infer a time_step from.
        # The old fallback here re-joined against RAW instantaneous
        # observations — exactly the P2 defect this plan exists to remove,
        # silently, with only a log line to mark it. Producing NO score is
        # preferable to producing a WRONG one: skip this station/model until
        # a second hindcast (or a multi-step ensemble) makes the time_step
        # inferable.
        log.warning(
            "compute_skill_for_station.time_step_unknown_no_scores",
            station_id=str(station_id),
            model_id=str(model_id),
            hindcast_count=len(hindcasts),
        )
        return [], []

    anchor = _forecast_valid_time_anchor(hindcasts)
    obs_lookup = _resample_observations_to_forecast_step(
        observations, parameter, time_step, anchor
    )

    strata = _build_strata(hindcasts, obs_lookup, seasons, flow_regime_config)

    if not strata:
        return [], []

    # Determine eval period from hindcast steps
    hindcast_steps = [hc.hindcast_step for hc in hindcasts]
    eval_start = min(hindcast_steps)
    eval_end = max(hindcast_steps)

    flow_regime_config_id = flow_regime_config.id if flow_regime_config else None

    all_scores: list[SkillScore] = []
    all_diagrams: list[SkillDiagram] = []

    for (lead_hours, season, regime), (ensemble_list, obs_list) in strata.items():
        if len(obs_list) == 0:
            continue

        all_scores.extend(
            _compute_scores(
                station_id=station_id,
                model_id=model_id,
                artifact_id=artifact_id,
                parameter=parameter,
                lead_hours=lead_hours,
                season=season,
                regime=regime,
                ensemble_list=ensemble_list,
                obs_list=obs_list,
                thresholds=thresholds,
                skill_source=skill_source,
                forcing_type=forcing_type,
                flow_regime_config_id=flow_regime_config_id,
                eval_start=eval_start,
                eval_end=eval_end,
                clock=clock,
                uuid_factory=uuid_factory,
            )
        )
        all_diagrams.extend(
            _compute_diagrams(
                station_id=station_id,
                model_id=model_id,
                artifact_id=artifact_id,
                parameter=parameter,
                lead_hours=lead_hours,
                season=season,
                regime=regime,
                ensemble_list=ensemble_list,
                obs_list=obs_list,
                thresholds=thresholds,
                skill_source=skill_source,
                flow_regime_config_id=flow_regime_config_id,
                eval_start=eval_start,
                eval_end=eval_end,
                clock=clock,
                uuid_factory=uuid_factory,
            )
        )

    return all_scores, all_diagrams
