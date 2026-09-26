from __future__ import annotations

from datetime import timedelta
from itertools import groupby
from statistics import median
from typing import TYPE_CHECKING

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services._qc_helpers import merge_thresholds
from sapphire_flow.types.domain import (
    ClimBaseline,
    QcFlag,
    QcRuleParams,
    QcRuleSet,
    StationQcOverride,
)
from sapphire_flow.types.enums import QcStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sapphire_flow.types.ids import ObservationId, StationId
    from sapphire_flow.types.observation import Observation


def _merge_thresholds(
    rule: QcRuleParams,
    overrides: list[StationQcOverride],
    station_id: StationId,
) -> dict[str, float]:
    return merge_thresholds(
        rule.thresholds,
        overrides,
        station_id,
        rule.rule_id,
        rule.parameter,
        rule.time_step,
    )


def infer_time_step(obs: list[Observation]) -> timedelta | None:
    """Plan 272 T2: the cadence a group reports at, or ``None`` when it cannot
    be inferred.

    ⛔ **The ``< 2 rows ⟹ 1 h`` fallback is deliberately gone.** It fabricated a
    cadence no rule may have declared, and `rules_for` then matched on it by
    exact equality — so a one-row group silently selected the hourly rules, or
    none at all, and either way the caller could not tell. The return type is
    widened rather than guarded at the call site so the type checker refuses the
    mistake: an inferred cadence and "no cadence" are now different things.
    """
    # 🔑 DISTINCT timestamps. The observations natural key includes `source`
    # (`db/metadata.py`), so one instant can carry several rows — a `measured`
    # reading and a `manual_import` or rating-curve-derived one. Counting the
    # zero gap between them drags the median off the real cadence: two rows ten
    # minutes apart plus a same-instant sibling gives gaps [600, 0] and a median
    # of 300 s, which no rule declares. The cadence is a property of the SERIES,
    # not of how many sources reported each point.
    stamps = sorted({o.timestamp for o in obs})
    if len(stamps) < 2:
        return None
    diffs = [(stamps[i] - stamps[i - 1]).total_seconds() for i in range(1, len(stamps))]
    return timedelta(seconds=median(diffs))


def group_key(obs: Observation) -> tuple[StationId, str]:
    """The `(station, parameter)` grouping QC selection works on."""
    return (obs.station_id, obs.parameter)


def resolve_selection(
    observations: list[Observation],
    rule_set: QcRuleSet,
    *,
    station_networks: Mapping[StationId, str],
    skipped_rule_ids: frozenset[str] = frozenset(),
) -> dict[tuple[StationId, str], tuple[timedelta | None, int]]:
    """Plan 272 T3: what `check` will select, per group, **before** it runs.

    Returns the inferred cadence and the number of rules that resolve for it.
    A group with ``0`` rules is one for which QC cannot run at all — the
    condition that used to be stored as a clean pass.

    🔑 Deliberately a separate, pure function over the SAME observation list the
    caller passes to `check`, so both see one row set and agree by construction.
    """
    resolved: dict[tuple[StationId, str], tuple[timedelta | None, int]] = {}
    for key, group_iter in groupby(
        sorted(observations, key=lambda o: (o.station_id, o.parameter, o.timestamp)),
        key=group_key,
    ):
        group = list(group_iter)
        step = infer_time_step(group)
        network = _network_for_station(key[0], station_networks)
        rules = (
            rule_set.rules_for(group[0].parameter, step, network=network)
            if step
            else ()
        )
        # ⚠️ The SKIP filter must be applied here too. `check` skips these rules
        # (`:303`), so a group whose only matching rule is skipped — a water
        # level with no datum, say — executes nothing while a naive count
        # reports one, and the caller stores QC_PASSED over an unchecked row.
        # That is the very defect this function exists to expose.
        runnable = [r for r in rules if r.rule_id not in skipped_rule_ids]
        resolved[key] = (step, len(runnable))
    return resolved


def _network_for_station(
    station_id: StationId, station_networks: Mapping[StationId, str]
) -> str:
    try:
        return station_networks[station_id]
    except KeyError as exc:
        raise ConfigurationError(
            f"missing network mapping for station {station_id}"
        ) from exc


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
            rule_version=rule.rule_version,
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
            rule_version=rule.rule_version,
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
    # D8 (Plan 172, M-I1): a value at or below this threshold never starts
    # or extends a frozen run — lets precipitation ignore normal dry
    # spells while still catching a stuck sensor. Absent from `thresholds`
    # (the key does not exist in the dict), every value is eligible again —
    # today's behaviour exactly (D8's backwards-compatibility requirement).
    exclude_at_or_below = thresholds.get("exclude_at_or_below")

    def _effective(value: float | None) -> float | None:
        if value is None:
            return None
        if exclude_at_or_below is not None and value <= exclude_at_or_below:
            return None
        return value

    flags: dict[ObservationId, QcFlag] = {}

    # Track run start index and reference value
    run_start = 0
    ref_val = _effective(group[0].value) if group else None

    for i in range(1, len(group)):
        val = _effective(group[i].value)
        prev_val = _effective(group[i - 1].value)
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

        # Count DISTINCT instants, not rows: the natural key includes `source`,
        # so one timestamp can carry several reports and a six-sample run of
        # duplicated readings would otherwise satisfy `min_consecutive=12`.
        run_length = len({o.timestamp for o in group[run_start : i + 1]})
        if run_length >= min_consecutive:
            # Flag all obs in the current run
            for j in range(run_start, i + 1):
                if group[j].value is not None:
                    flags[group[j].id] = QcFlag(
                        rule_id=rule.rule_id,
                        rule_version=rule.rule_version,
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
    if "max_delta" in thresholds:
        max_delta = thresholds["max_delta"]
        if (
            abs(obs.value - prev.value) > max_delta
            and abs(obs.value - nxt.value) > max_delta
        ):
            return QcFlag(
                rule_id=rule.rule_id,
                rule_version=rule.rule_version,
                status=QcStatus.QC_SUSPECT,
                detail=(
                    f"spike: value {obs.value} deviates from prev {prev.value} "
                    f"and next {nxt.value} by >{max_delta}"
                ),
            )
        return None
    tolerance = thresholds["tolerance"]
    ref = abs(prev.value)
    if ref == 0.0:
        return None
    if (
        abs(obs.value - prev.value) > tolerance * ref
        and abs(obs.value - nxt.value) > tolerance * ref
    ):
        return QcFlag(
            rule_id=rule.rule_id,
            rule_version=rule.rule_version,
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
            rule_version=rule.rule_version,
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
        *,
        station_networks: Mapping[StationId, str],
        skipped_rule_ids: frozenset[str] = frozenset(),
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
            time_step = infer_time_step(group)
            network = _network_for_station(station_id, station_networks)
            # `None` means no cadence could be inferred, so no rule can be
            # selected for this group — NOT that an hourly one should be.
            rules = (
                rule_set.rules_for(parameter, time_step, network=network)
                if time_step
                else ()
            )

            for rule in rules:
                if rule.rule_id in skipped_rule_ids:
                    continue
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
