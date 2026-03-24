from __future__ import annotations

from datetime import timedelta
from itertools import groupby
from statistics import median
from typing import TYPE_CHECKING

from sapphire_flow.types.domain import (
    ClimBaseline,
    QcFlag,
    QcRuleParams,
    QcRuleSet,
    StationQcOverride,
)
from sapphire_flow.types.enums import QcStatus

if TYPE_CHECKING:
    from sapphire_flow.types.ids import ObservationId, StationId
    from sapphire_flow.types.observation import Observation

_RULE_VERSION = "1.0"


def _merge_thresholds(
    rule: QcRuleParams,
    overrides: list[StationQcOverride],
    station_id: StationId,
) -> dict[str, float]:
    base = dict(rule.thresholds)
    for o in overrides:
        if (
            o.station_id == station_id
            and o.rule_id == rule.rule_id
            and o.parameter == rule.parameter
            and o.time_step == rule.time_step
        ):
            for k, v in o.thresholds.items():
                if v is not None:
                    base[k] = v
    return base


def _infer_time_step(obs: list[Observation]) -> timedelta:
    if len(obs) < 2:
        return timedelta(hours=1)
    diffs = [
        (obs[i].timestamp - obs[i - 1].timestamp).total_seconds()
        for i in range(1, len(obs))
    ]
    return timedelta(seconds=median(diffs))


def _apply_range_check(
    obs: Observation,
    thresholds: dict[str, float],
    rule: QcRuleParams,
) -> QcFlag | None:
    if obs.value is None:
        return None
    v_min = thresholds.get("value_min")
    v_max = thresholds.get("value_max")
    if (v_min is not None and obs.value < v_min) or (
        v_max is not None and obs.value > v_max
    ):
        return QcFlag(
            rule_id=rule.rule_id,
            rule_version=_RULE_VERSION,
            status=QcStatus.QC_FAILED,
            detail=f"value {obs.value} outside [{v_min}, {v_max}]",
        )
    return None


def _apply_rate_of_change(
    obs: Observation,
    prev: Observation | None,
    thresholds: dict[str, float],
    rule: QcRuleParams,
) -> QcFlag | None:
    if prev is None or obs.value is None or prev.value is None:
        return None
    max_rate = thresholds["max_rate"]
    if abs(obs.value - prev.value) > max_rate:
        return QcFlag(
            rule_id=rule.rule_id,
            rule_version=_RULE_VERSION,
            status=QcStatus.QC_SUSPECT,
            detail=(
                f"rate {abs(obs.value - prev.value):.4f} exceeds max_rate {max_rate}"
            ),
        )
    return None


def _apply_frozen_sensor(
    group: list[Observation],
    thresholds: dict[str, float],
    rule: QcRuleParams,
) -> dict[ObservationId, QcFlag]:
    tolerance = thresholds["tolerance"]
    min_consecutive = int(thresholds["min_consecutive"])
    flags: dict[ObservationId, QcFlag] = {}

    # Track run start index and reference value
    run_start = 0
    ref_val = group[0].value if group else None

    for i in range(1, len(group)):
        val = group[i].value
        prev_val = group[i - 1].value
        if val is None or prev_val is None:
            run_start = i
            ref_val = val
            continue
        if abs(val - ref_val) <= tolerance:  # type: ignore[operator]
            # still in the run
            pass
        else:
            run_start = i
            ref_val = val

        run_length = i - run_start + 1
        if run_length >= min_consecutive:
            # Flag all obs in the current run
            for j in range(run_start, i + 1):
                if group[j].value is not None:
                    flags[group[j].id] = QcFlag(
                        rule_id=rule.rule_id,
                        rule_version=_RULE_VERSION,
                        status=QcStatus.QC_SUSPECT,
                        detail=(
                            f"frozen sensor: {run_length} consecutive values "
                            f"within tolerance {tolerance}"
                        ),
                    )
    return flags


def _apply_spike(
    obs: Observation,
    prev: Observation | None,
    nxt: Observation | None,
    thresholds: dict[str, float],
    rule: QcRuleParams,
) -> QcFlag | None:
    if prev is None or nxt is None:
        return None
    if obs.value is None or prev.value is None or nxt.value is None:
        return None
    tolerance = thresholds["tolerance"]
    ref = abs(prev.value)
    if (
        abs(obs.value - prev.value) > tolerance * ref
        and abs(obs.value - nxt.value) > tolerance * ref
    ):
        return QcFlag(
            rule_id=rule.rule_id,
            rule_version=_RULE_VERSION,
            status=QcStatus.QC_SUSPECT,
            detail=(
                f"spike: value {obs.value} deviates from prev {prev.value} "
                f"and next {nxt.value} by >{tolerance:.2%} of |prev|"
            ),
        )
    return None


def _apply_gross_outlier(
    obs: Observation,
    thresholds: dict[str, float],
    baseline_index: dict[tuple[StationId, str, int], ClimBaseline],
    rule: QcRuleParams,
) -> QcFlag | None:
    if obs.value is None:
        return None
    doy = obs.timestamp.timetuple().tm_yday
    key = (obs.station_id, obs.parameter, doy)
    baseline = baseline_index.get(key)
    if baseline is None:
        return None
    k_sigma = thresholds["k_sigma"]
    if abs(obs.value - baseline.rolling_mean) > k_sigma * baseline.rolling_std:
        return QcFlag(
            rule_id=rule.rule_id,
            rule_version=_RULE_VERSION,
            status=QcStatus.QC_SUSPECT,
            detail=(
                f"gross outlier: value {obs.value} deviates from baseline "
                f"mean {baseline.rolling_mean:.4f} by >"
                f"{k_sigma}σ (std={baseline.rolling_std:.4f})"
            ),
        )
    return None


class Stage1QualityChecker:
    def check(
        self,
        observations: list[Observation],
        rule_set: QcRuleSet,
        overrides: list[StationQcOverride],
        baselines: list[ClimBaseline],
    ) -> dict[ObservationId, list[QcFlag]]:
        if not observations:
            return {}

        result: dict[ObservationId, list[QcFlag]] = {obs.id: [] for obs in observations}

        baseline_index: dict[tuple[StationId, str, int], ClimBaseline] = {
            (b.station_id, b.parameter, b.day_of_year): b for b in baselines
        }

        sorted_obs = sorted(
            observations, key=lambda o: (o.station_id, o.parameter, o.timestamp)
        )

        def group_key(o: Observation) -> tuple[StationId, str]:
            return (o.station_id, o.parameter)

        for (station_id, parameter), group_iter in groupby(sorted_obs, key=group_key):
            group = list(group_iter)
            time_step = _infer_time_step(group)
            rules = rule_set.rules_for(parameter, time_step)

            for rule in rules:
                thresholds = _merge_thresholds(rule, overrides, station_id)

                if rule.rule_id == "frozen_sensor":
                    frozen_flags = _apply_frozen_sensor(group, thresholds, rule)
                    for obs_id, flag in frozen_flags.items():
                        result[obs_id].append(flag)
                    continue

                for i, obs in enumerate(group):
                    prev = group[i - 1] if i > 0 else None
                    nxt = group[i + 1] if i < len(group) - 1 else None

                    flag: QcFlag | None = None
                    match rule.rule_id:
                        case "range_check":
                            flag = _apply_range_check(obs, thresholds, rule)
                        case "rate_of_change":
                            flag = _apply_rate_of_change(obs, prev, thresholds, rule)
                        case "spike":
                            flag = _apply_spike(obs, prev, nxt, thresholds, rule)
                        case "gross_outlier":
                            flag = _apply_gross_outlier(
                                obs, thresholds, baseline_index, rule
                            )

                    if flag is not None:
                        result[obs.id].append(flag)

        return result
