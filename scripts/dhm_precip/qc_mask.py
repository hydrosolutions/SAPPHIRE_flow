"""Task 2a/2b (Plan 173, M-A3) — the fit-for-purpose QC mask, its removal
accounting, and the M-A6 exclusion list.

D1: the deliverable is a timestamp mask, not a cleaned dataset — a set of
`(station, timestamp)` pairs to drop.

D3c: two PASSES with pass-specific rule sets, not one call with
`skipped_rule_ids` — both `frozen_sensor` instances share `rule_id`, and the
checker selects rules only by `rule_id` (`services/qc.py:256`), so a single
call cannot apply one instance and skip the other. Pass A (`range_check` +
stuck-value `frozen_sensor`) runs over a station's whole series; pass B
(long-zero-run `frozen_sensor` only) runs one JJAS season at a time. The
mask is the union of both passes' flagged keys.

D3b: seasonal scope is applied OUTSIDE the checker — `_apply_frozen_sensor`
has no season parameter, and the checker applies every matching rule to a
station's WHOLE series. Pass B's input is pre-filtered to one
`(station, year)` JJAS window per call, so a run can never merge across a
season boundary: the checker never sees the out-of-season observations at
all, whether they're a genuine gap or simply outside the filter.

D5: the mask builder RAISES on a `time_step` mismatch. The checker infers
the step from the observations and `rules_for` matches on it, so a rule
whose `time_step` differs is silently skipped — no error, no flags, an empty
mask indistinguishable from clean data. This is the plan's most dangerous
failure mode; guarded here before either pass runs. The guard checks EVERY
rule in the pass-specific rule set individually — not just whether at least
one rule happens to match — so a single mismatched rule mixed into an
otherwise-matching set (e.g. one 30-minute rule among hourly rules) still
raises instead of being silently and partially omitted.

D8: accounting is THREE-WAY (`source_missing` / `qc_removed` /
`retained_nonmissing`), cross-classified by `(station, season, hour_of_day,
category)` — not two separate marginals — because Rule 1 needs hour-of-day
exposure and M-A7's whole subject is diurnal structure. Retention divides by
`retained_nonmissing + qc_removed` only; `source_missing` never enters the
denominator (it is "never observed", not "we masked it").
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from statistics import median
from typing import TYPE_CHECKING

from sapphire_flow.services.qc import Stage1QualityChecker
from sapphire_flow.types.enums import QcStatus
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.qc_ruleset import (
    PASS_A_RULE_VERSIONS,
    PASS_B_RULE_VERSIONS,
    build_precipitation_qc_rule_set,
    rule_subset,
)
from scripts.dhm_precip.seasons import Season, season_for

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import timedelta

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import QcRuleSet
    from sapphire_flow.types.observation import Observation
    from scripts.dhm_precip.params import DhmPrecipParams

MaskKey = tuple[Station, "UtcDatetime"]


class TimeStepMismatchError(ValueError):
    """D5 — the mask builder's own guard against the checker's silent skip:
    a rule whose declared `time_step` doesn't match what the observations
    imply is silently dropped by `QcRuleSet.rules_for`, producing an empty
    mask indistinguishable from clean data. Raised before either pass runs."""


class ReconciliationError(ValueError):
    """D8 — the cross-classified accounting table must reconcile EXACTLY to
    the number of observations it was built from; every axis row lands in
    exactly one `(season, category)` bin."""


class RetentionCategory(StrEnum):
    SOURCE_MISSING = "source_missing"
    QC_REMOVED = "qc_removed"
    RETAINED_NONMISSING = "retained_nonmissing"


class ExclusionReason(StrEnum):
    JJAS_RETENTION_BELOW_MINIMUM = "jjas_retention_below_minimum"
    NO_OBSERVED_JJAS_HOURS = "no_observed_jjas_hours"


@dataclass(frozen=True, kw_only=True, slots=True)
class RemovalAccountingRow:
    station: Station
    season: Season
    hour_of_day: int
    category: RetentionCategory
    count: int


@dataclass(frozen=True, kw_only=True, slots=True)
class ExclusionListEntry:
    station: Station
    jjas_retained_fraction: float | None
    """`None` only when the denominator (`retained_nonmissing + qc_removed`)
    is zero — a station with no observed JJAS hours at all."""
    reason: ExclusionReason


@dataclass(frozen=True, kw_only=True, slots=True)
class RuleProvenanceRow:
    """M-I2 needs the mask definition and version to package the dataset —
    the mask reduction collapses flags to timestamps and discards which rule
    fired, so this is the manifest's own record of the exact executed rule
    definitions, per pass (D3c)."""

    pass_name: str
    rule_id: str
    rule_version: str
    time_step_seconds: int
    scope: str
    thresholds: dict[str, float]


def _inferred_time_step(observations: list[Observation]) -> timedelta:
    """Mirrors `services.qc._infer_time_step` exactly — same fallback (1h
    when fewer than 2 observations), same median-of-diffs formula — so the
    guard raises precisely when, and only when, the production checker
    would have silently matched no rules."""
    from datetime import timedelta as _timedelta

    if len(observations) < 2:
        return _timedelta(hours=1)
    diffs = [
        (b.timestamp - a.timestamp).total_seconds()
        for a, b in zip(observations, observations[1:], strict=False)
    ]
    return _timedelta(seconds=median(diffs))


def _raise_on_time_step_mismatch(
    observations: list[Observation], rule_set: QcRuleSet
) -> None:
    """D5 — reject EVERY rule whose declared `time_step` doesn't match the
    inferred step, not just whether at least one rule happens to match.
    `QcRuleSet.rules_for` filters per-rule, so a rule set containing a mix of
    matching and mismatched rules would otherwise let the mismatched ones be
    silently and partially dropped while the matching ones still ran —
    exactly the failure this guard exists to prevent."""
    if not observations:
        return
    inferred = _inferred_time_step(observations)
    mismatched = [r for r in rule_set.rules if r.time_step != inferred]
    if mismatched:
        offending = sorted(f"{r.rule_version} ({r.time_step})" for r in mismatched)
        raise TimeStepMismatchError(
            f"observations imply a {inferred} step; the following declared "
            f"rules do not match it and would be silently skipped by "
            f"Stage1QualityChecker.rules_for(), producing a mask that omits "
            f"them without error (D5): {offending}"
        )


def _jjas_seasons(
    observations: list[Observation], params: DhmPrecipParams
) -> list[tuple[int, list[Observation]]]:
    """D3b — one `(station, year)` JJAS window per entry. Filtering BEFORE
    grouping (rather than passing the whole series and hoping run detection
    respects a season) is what makes a cross-boundary merge structurally
    impossible: the checker never sees the excluded months at all."""
    by_year: dict[int, list[Observation]] = {}
    for obs in observations:
        if obs.timestamp.month in params.jjas_months:
            by_year.setdefault(obs.timestamp.year, []).append(obs)
    return [
        (year, sorted(items, key=lambda o: o.timestamp))
        for year, items in sorted(by_year.items())
    ]


def _station_mask(
    station: Station,
    observations: list[Observation],
    pass_a_rules: QcRuleSet,
    pass_b_rules: QcRuleSet,
    checker: Stage1QualityChecker,
    params: DhmPrecipParams,
) -> frozenset[MaskKey]:
    """D1/D3c — one station's flagged `(station, timestamp)` keys, both
    passes unioned. Raises `TimeStepMismatchError` (D5) before running
    either pass on this station (or one of its seasons) whose observations
    don't imply the rule set's declared step. Factored out of `build_mask`
    so the streaming entry point (`iter_station_results`, D7) can compute
    one station's mask without ever holding another station's observations."""
    if not observations:
        return frozenset()
    ordered = sorted(observations, key=lambda o: o.timestamp)

    dropped: set[MaskKey] = set()
    _raise_on_time_step_mismatch(ordered, pass_a_rules)
    flags_a = checker.check(ordered, pass_a_rules, [], [])
    dropped.update((station, obs.timestamp) for obs in ordered if flags_a[obs.id])

    for _year, season_obs in _jjas_seasons(ordered, params):
        _raise_on_time_step_mismatch(season_obs, pass_b_rules)
        flags_b = checker.check(season_obs, pass_b_rules, [], [])
        dropped.update(
            (station, obs.timestamp) for obs in season_obs if flags_b[obs.id]
        )

    return frozenset(dropped)


def build_mask(
    obs_by_station: Mapping[Station, list[Observation]],
    params: DhmPrecipParams,
) -> frozenset[MaskKey]:
    """D1/D3c — run both passes over every station and union the flagged
    `(station, timestamp)` keys. Raises `TimeStepMismatchError` (D5) before
    running either pass on a station (or season) whose observations don't
    imply the rule set's declared step.

    Takes a fully materialised `Mapping` — convenient for tests and small
    inputs. The production pipeline (`pipeline._qc_mask_tables`) uses
    `iter_station_results` instead, which never holds more than one
    station's observations in memory at once (D7)."""
    rule_set = build_precipitation_qc_rule_set(params)
    pass_a_rules = rule_subset(rule_set, PASS_A_RULE_VERSIONS)
    pass_b_rules = rule_subset(rule_set, PASS_B_RULE_VERSIONS)
    checker = Stage1QualityChecker()

    dropped: set[MaskKey] = set()
    for station, observations in obs_by_station.items():
        dropped.update(
            _station_mask(
                station, observations, pass_a_rules, pass_b_rules, checker, params
            )
        )
    return frozenset(dropped)


def _station_accounting_counts(
    station: Station,
    observations: list[Observation],
    station_mask_timestamps: frozenset[UtcDatetime],
    params: DhmPrecipParams,
) -> tuple[dict[tuple[Station, Season, int, RetentionCategory], int], int]:
    """D8 — one station's contribution to the cross-classified accounting
    counts, plus the number of observations it was built from. Factored out
    of `build_removal_accounting` so the streaming entry point can reconcile
    incrementally without holding every station's observations at once."""
    counts: dict[tuple[Station, Season, int, RetentionCategory], int] = {}
    total = 0
    for obs in observations:
        total += 1
        season = season_for(obs.timestamp, params)
        hour = obs.timestamp.hour
        if obs.qc_status == QcStatus.MISSING:
            category = RetentionCategory.SOURCE_MISSING
        elif obs.timestamp in station_mask_timestamps:
            category = RetentionCategory.QC_REMOVED
        else:
            category = RetentionCategory.RETAINED_NONMISSING
        key = (station, season, hour, category)
        counts[key] = counts.get(key, 0) + 1
    return counts, total


def _rows_from_counts(
    counts: dict[tuple[Station, Season, int, RetentionCategory], int],
    total: int,
) -> tuple[RemovalAccountingRow, ...]:
    rows = tuple(
        RemovalAccountingRow(
            station=key[0],
            season=key[1],
            hour_of_day=key[2],
            category=key[3],
            count=count,
        )
        for key, count in sorted(
            counts.items(),
            key=lambda kv: (kv[0][0], kv[0][1].value, kv[0][2], kv[0][3].value),
        )
    )
    reconciled = sum(row.count for row in rows)
    if reconciled != total:
        raise ReconciliationError(
            f"cross-classified accounting totals {reconciled} rows, "
            f"expected {total} observations"
        )
    return rows


def build_removal_accounting(
    obs_by_station: Mapping[Station, list[Observation]],
    mask: frozenset[MaskKey],
    params: DhmPrecipParams,
) -> tuple[RemovalAccountingRow, ...]:
    """D8 — the three-way, cross-classified accounting table. Raises
    `ReconciliationError` if the emitted rows don't sum back to exactly the
    number of observations supplied — a silent miscount here would corrupt
    every retention figure downstream.

    Takes a fully materialised `Mapping` and the whole-run `mask` — the
    production pipeline uses `iter_station_results` instead (D7)."""
    counts: dict[tuple[Station, Season, int, RetentionCategory], int] = {}
    total = 0
    for station, observations in obs_by_station.items():
        station_mask_timestamps = frozenset(ts for st, ts in mask if st == station)
        station_counts, station_total = _station_accounting_counts(
            station, observations, station_mask_timestamps, params
        )
        for key, count in station_counts.items():
            counts[key] = counts.get(key, 0) + count
        total += station_total
    return _rows_from_counts(counts, total)


def iter_station_results(
    station_observations: Iterable[tuple[Station, list[Observation]]],
    params: DhmPrecipParams,
) -> tuple[frozenset[MaskKey], tuple[RemovalAccountingRow, ...]]:
    """D7 — the memory-bounded production entry point. Consumes an
    `Iterable` (typically a generator, see `observations.iter_observations_by_station`)
    that yields ONE station's observation list at a time: for each station,
    computes that station's mask keys and accounting rows immediately, then
    discards its `Observation` objects before the next station is produced —
    so peak memory is bounded to one station's observations
    (~52k/station), never all ~1.37M at once. `build_mask` and
    `build_removal_accounting` remain available for tests and small,
    fully-materialised inputs; this function is what the pipeline calls."""
    rule_set = build_precipitation_qc_rule_set(params)
    pass_a_rules = rule_subset(rule_set, PASS_A_RULE_VERSIONS)
    pass_b_rules = rule_subset(rule_set, PASS_B_RULE_VERSIONS)
    checker = Stage1QualityChecker()

    dropped: set[MaskKey] = set()
    counts: dict[tuple[Station, Season, int, RetentionCategory], int] = {}
    total = 0

    for station, observations in station_observations:
        station_mask = _station_mask(
            station, observations, pass_a_rules, pass_b_rules, checker, params
        )
        dropped.update(station_mask)

        station_mask_timestamps = frozenset(ts for _st, ts in station_mask)
        station_counts, station_total = _station_accounting_counts(
            station, observations, station_mask_timestamps, params
        )
        for key, count in station_counts.items():
            counts[key] = counts.get(key, 0) + count
        total += station_total

    rows = _rows_from_counts(counts, total)
    return frozenset(dropped), rows


def _jjas_retained_fraction(
    rows: tuple[RemovalAccountingRow, ...], station: Station
) -> float | None:
    """D8 — retention is computed over OBSERVED JJAS rows only:
    `retained_nonmissing / (retained_nonmissing + qc_removed)`.
    `source_missing` is excluded from the denominator."""
    retained = sum(
        row.count
        for row in rows
        if row.station == station
        and row.season == Season.JJAS
        and row.category == RetentionCategory.RETAINED_NONMISSING
    )
    removed = sum(
        row.count
        for row in rows
        if row.station == station
        and row.season == Season.JJAS
        and row.category == RetentionCategory.QC_REMOVED
    )
    denominator = retained + removed
    if denominator == 0:
        return None
    return retained / denominator


def build_exclusion_list(
    rows: tuple[RemovalAccountingRow, ...], params: DhmPrecipParams
) -> tuple[ExclusionListEntry, ...]:
    """D8 — a station is excluded from M-A6 when its JJAS retained fraction
    is STRICTLY below `minimum_jjas_retained_fraction` (equality passes), or
    when it has no observed JJAS hours at all (zero denominator, recorded
    with that reason rather than dividing by zero). Expect this to be EMPTY
    on real data — that is not a bug (D8)."""
    stations = sorted({row.station for row in rows})
    entries: list[ExclusionListEntry] = []
    for station in stations:
        fraction = _jjas_retained_fraction(rows, station)
        if fraction is None:
            entries.append(
                ExclusionListEntry(
                    station=station,
                    jjas_retained_fraction=None,
                    reason=ExclusionReason.NO_OBSERVED_JJAS_HOURS,
                )
            )
        elif fraction < params.minimum_jjas_retained_fraction:
            entries.append(
                ExclusionListEntry(
                    station=station,
                    jjas_retained_fraction=fraction,
                    reason=ExclusionReason.JJAS_RETENTION_BELOW_MINIMUM,
                )
            )
    return tuple(entries)


_PASS_NAME_BY_VERSION: dict[str, str] = {
    **dict.fromkeys(PASS_A_RULE_VERSIONS, "A"),
    **dict.fromkeys(PASS_B_RULE_VERSIONS, "B"),
}
_SCOPE_BY_VERSION: dict[str, str] = {
    **dict.fromkeys(PASS_A_RULE_VERSIONS, "whole_series"),
    **dict.fromkeys(PASS_B_RULE_VERSIONS, "jjas_only"),
}


def rule_provenance_rows(params: DhmPrecipParams) -> tuple[RuleProvenanceRow, ...]:
    """D9/M-I2 — the exact executed rule definitions: id, `rule_version`,
    thresholds and scope, per pass. Derived from the SAME rule set
    `build_mask` runs — never redeclared."""
    rule_set = build_precipitation_qc_rule_set(params)
    return tuple(
        RuleProvenanceRow(
            pass_name=_PASS_NAME_BY_VERSION[rule.rule_version],
            rule_id=rule.rule_id,
            rule_version=rule.rule_version,
            time_step_seconds=int(rule.time_step.total_seconds()),
            scope=_SCOPE_BY_VERSION[rule.rule_version],
            thresholds=dict(rule.thresholds),
        )
        for rule in rule_set.rules
    )
