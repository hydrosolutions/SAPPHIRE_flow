"""Plan 540 M3b: Stage 1 QC for observations loaded by the station tools.

⛔ A COPY of ``flows/ingest_observations.py::_evaluate_qc_window`` (and the
logic of its private ``_aggregate_qc_status``), deliberately not shared: the live
ingest path stays untouched. Keep the two in lockstep; the parity test
``tests/unit/services/test_station_observation_qc.py::TestParityWithLiveIngest``
is the only guard against drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sapphire_flow.services.qc import Stage1QualityChecker, resolve_selection
from sapphire_flow.services.qc_datum import (
    add_observation_datum_details,
    obs_qc_rule_version,
    obs_skipped_rules,
    shift_observations_for_water_level_datum,
)
from sapphire_flow.types.enums import QcStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sapphire_flow.protocols.stores import ClimBaselineStore, ObservationStore
    from sapphire_flow.types.domain import QcFlag, QcRuleSet, StationQcOverride
    from sapphire_flow.types.ids import ObservationId, StationId
    from sapphire_flow.types.observation import Observation

DATUM_SKIP_VERSION_SUFFIX = "-datum-skip"
USABLE_STATUSES: frozenset[QcStatus] = frozenset(
    {QcStatus.QC_PASSED, QcStatus.QC_UNCHECKED}
)


@dataclass(frozen=True, kw_only=True, slots=True)
class QcZeroRuleGroup:
    station_id: StationId
    parameter: str
    inferred_time_step_seconds: float | None
    reason: Literal["no_cadence_inferable", "no_rule_declares_it"]


@dataclass(frozen=True, kw_only=True, slots=True)
class Stage1QcReport:
    counts: dict[str, int]
    rule_version: str
    zero_rule_groups: tuple[QcZeroRuleGroup, ...]
    stale: bool
    baseline_ran: bool
    statuses: dict[ObservationId, QcStatus]


def _empty_counts() -> dict[str, int]:
    return {
        "passed": 0,
        "failed": 0,
        "suspect": 0,
        "unchecked": 0,
        "unjudged": 0,
        "rechecked": 0,
        "newly_checked": 0,
        "stale_evaluation": 0,
    }


def _aggregate(flags: list[QcFlag], *, rules_ran: bool) -> QcStatus:
    if not flags:
        return QcStatus.QC_PASSED if rules_ran else QcStatus.QC_UNCHECKED
    if any(f.status == QcStatus.QC_FAILED for f in flags):
        return QcStatus.QC_FAILED
    return QcStatus.QC_SUSPECT


def run_stage1_qc(
    all_obs: list[Observation],
    obs_store: ObservationStore | None,
    baseline_store: ClimBaselineStore,
    station_id: StationId,
    parameter: str,
    qc_rules: QcRuleSet,
    *,
    station_networks: Mapping[StationId, str],
    datum: float | None,
    overrides: tuple[StationQcOverride, ...],
    target_ids: frozenset[ObservationId],
) -> Stage1QcReport:
    """``obs_store=None`` evaluates in memory (dry run) and writes nothing."""
    version = obs_qc_rule_version(parameter, datum)
    statuses = {o.id: o.qc_status for o in all_obs}
    pending_obs = [
        o for o in all_obs if o.id in target_ids and o.qc_status is QcStatus.RAW
    ]
    baselines = baseline_store.fetch_baselines(station_id, parameter)
    if not pending_obs:
        return Stage1QcReport(
            counts=_empty_counts(),
            rule_version=version,
            zero_rule_groups=(),
            stale=False,
            baseline_ran=bool(baselines),
            statuses=statuses,
        )

    pending_ids = {o.id for o in pending_obs}
    qc_observations = shift_observations_for_water_level_datum(
        all_obs, parameter=parameter, datum=datum
    )
    skipped = obs_skipped_rules(parameter, datum)
    checked = Stage1QualityChecker().check_with_coverage(
        observations=qc_observations,
        rule_set=qc_rules,
        overrides=list(overrides),
        baselines=baselines,
        station_networks=station_networks,
        skipped_rule_ids=skipped,
    )
    flags = add_observation_datum_details(
        checked.flags,
        raw_observations=all_obs,
        shifted_observations=qc_observations,
        parameter=parameter,
        datum=datum,
    )
    selection = resolve_selection(
        qc_observations,
        qc_rules,
        station_networks=station_networks,
        skipped_rule_ids=skipped,
    )
    unresolved = {key for key, (_, n_rules) in selection.items() if n_rules == 0}
    zero_rule_groups: list[QcZeroRuleGroup] = []
    for station_key, parameter_key in sorted(
        unresolved, key=lambda k: (str(k[0]), k[1])
    ):
        inferred, _ = selection[(station_key, parameter_key)]
        reason: Literal["no_cadence_inferable", "no_rule_declares_it"] = (
            "no_cadence_inferable" if inferred is None else "no_rule_declares_it"
        )
        zero_rule_groups.append(
            QcZeroRuleGroup(
                station_id=station_key,
                parameter=parameter_key,
                inferred_time_step_seconds=(
                    inferred.total_seconds() if inferred is not None else None
                ),
                reason=reason,
            )
        )

    obs_by_id = {obs.id: obs for obs in all_obs}
    updates = [
        (
            obs_by_id[obs_id],
            _aggregate(
                obs_flags,
                rules_ran=(obs_by_id[obs_id].station_id, obs_by_id[obs_id].parameter)
                not in unresolved
                and obs_id in checked.judged,
            ),
            obs_flags,
        )
        for obs_id, obs_flags in flags.items()
        if obs_id in pending_ids
    ]
    if obs_store is not None and not obs_store.update_qc_batch_if_current(
        updates, qc_rule_version=version
    ):
        counts = _empty_counts()
        counts["stale_evaluation"] = len(updates)
        return Stage1QcReport(
            counts=counts,
            rule_version=version,
            zero_rule_groups=(),
            stale=True,
            baseline_ran=bool(baselines),
            statuses=statuses,
        )

    rechecked_ids = {o.id for o in pending_obs if o.qc_status == QcStatus.QC_UNCHECKED}
    counts = _empty_counts()
    for obs, status, _ in updates:
        statuses[obs.id] = status
        zero_rule = (obs.station_id, obs.parameter) in unresolved
        counts["rechecked" if obs.id in rechecked_ids else "newly_checked"] += 1
        if status == QcStatus.QC_PASSED:
            counts["passed"] += 1
        elif status == QcStatus.QC_FAILED:
            counts["failed"] += 1
        elif status == QcStatus.QC_UNCHECKED and zero_rule:
            counts["unchecked"] += 1
        elif status == QcStatus.QC_UNCHECKED:
            counts["unjudged"] += 1
        else:
            counts["suspect"] += 1
    return Stage1QcReport(
        counts=counts,
        rule_version=version,
        zero_rule_groups=tuple(zero_rule_groups),
        stale=False,
        baseline_ran=bool(baselines),
        statuses=statuses,
    )
