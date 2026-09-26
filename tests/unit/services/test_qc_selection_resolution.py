"""Plan 272 — a group for which NO rule can be selected must not read as a clean pass.

The defect: `check` inferred a cadence from the rows in front of it, `rules_for`
matched it by EXACT equality, and when nothing matched the group silently
selected zero rules. The flow then aggregated an empty flag list to
`QC_PASSED`. "Checked and clean" and "never checked" were the same stored row.

Every test here asserts on what `resolve_selection` reports or on the stored
status — never on the absence of flags, which is the ambiguity itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.ingest_observations import _aggregate_qc_status
from sapphire_flow.services.qc import Stage1QualityChecker, resolve_selection
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation import Observation

_STATION = StationId(uuid4())
_START = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)


def _obs(
    minutes: int, value: float = 1.0, *, station_id: StationId = _STATION
) -> Observation:
    return Observation(
        id=ObservationId(uuid4()),
        station_id=station_id,
        timestamp=_START + timedelta(minutes=minutes),
        parameter="discharge",
        value=value,
        source=ObservationSource.MEASURED,
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=QcStatus.RAW,
        qc_flags=[],
        qc_rule_version=None,
        created_at=_START,
    )


def _rule_set(step_seconds: int) -> QcRuleSet:
    return QcRuleSet(
        version="test",
        rules=(
            QcRuleParams(
                rule_id="range_check",
                rule_version="1.0",
                parameter="discharge",
                time_step=timedelta(seconds=step_seconds),
                thresholds={"value_min": 0.0, "value_max": 100.0},
            ),
        ),
    )


def _resolve_selection(
    observations: list[Observation],
    rule_set: QcRuleSet,
    *,
    skipped_rule_ids: frozenset[str] = frozenset(),
) -> dict[tuple[StationId, str], tuple[timedelta | None, int]]:
    networks = {obs.station_id: "bafu" for obs in observations}
    return resolve_selection(
        observations,
        rule_set,
        station_networks=networks,
        skipped_rule_ids=skipped_rule_ids,
    )


class TestResolveSelection:
    def test_checker_and_selection_reject_missing_station_network(self) -> None:
        observations = [_obs(0), _obs(10), _obs(20)]
        rules = _rule_set(600)

        with pytest.raises(ConfigurationError, match="missing network mapping"):
            Stage1QualityChecker().check(
                observations, rules, [], [], station_networks={}
            )
        with pytest.raises(ConfigurationError, match="missing network mapping"):
            resolve_selection(observations, rules, station_networks={})

    def test_a_matching_cadence_selects_its_rules(self) -> None:
        """The control: when inference matches a declared step, rules resolve."""
        obs = [_obs(0), _obs(10), _obs(20)]
        step, n_rules = _resolve_selection(obs, _rule_set(600))[(_STATION, "discharge")]

        assert step == timedelta(seconds=600)
        assert n_rules == 1

    def test_a_cadence_no_rule_declares_selects_nothing(self) -> None:
        """The defect's first half. 15-minute rows against a 10-minute rule set:
        the cadence is inferable, it just matches nothing. Exact equality is
        deliberate (Plan 272 D1) — the fix is to SAY so, not to match loosely."""
        obs = [_obs(0), _obs(15), _obs(30)]
        step, n_rules = _resolve_selection(obs, _rule_set(600))[(_STATION, "discharge")]

        assert step == timedelta(seconds=900)
        assert n_rules == 0

    def test_a_single_row_infers_no_cadence_rather_than_fabricating_one_hour(
        self,
    ) -> None:
        """⛔ The regression this fix exists to prevent. `infer_time_step` used
        to return one hour for a group it could not measure, so a one-row group
        silently selected whatever the hourly rules happened to be — or none —
        and the caller could not tell which."""
        step, n_rules = _resolve_selection([_obs(0)], _rule_set(3600))[
            (_STATION, "discharge")
        ]

        assert step is None, "a cadence was fabricated for an unmeasurable group"
        assert n_rules == 0

    def test_groups_are_resolved_independently(self) -> None:
        """One unresolvable group must not condemn a healthy one beside it."""
        other = StationId(uuid4())
        obs = [_obs(0), _obs(10), _obs(20)]
        lone = _obs(0, station_id=other)
        resolved = _resolve_selection([*obs, lone], _rule_set(600))

        assert resolved[(_STATION, "discharge")][1] == 1
        assert resolved[(other, "discharge")] == (None, 0)

    def test_a_same_instant_sibling_row_does_not_skew_the_cadence(self) -> None:
        """Found by an existing derivation test, not by design. The observations
        natural key includes `source`, so one instant can carry a `measured` row
        AND a `manual_import` one. Counting the zero gap between them gives
        gaps [600, 0] and a median of 300 s, which no rule declares — so a
        perfectly regular ten-minute series resolved NOTHING because a second
        source reported one of its points."""
        sibling = _obs(10, 99.0)
        obs = [_obs(0), _obs(10), sibling, _obs(20)]
        step, n_rules = _resolve_selection(obs, _rule_set(600))[(_STATION, "discharge")]

        assert step == timedelta(seconds=600), (
            "a same-instant sibling skewed the median"
        )
        assert n_rules == 1

    def test_a_skipped_rule_does_not_count_as_selected(self) -> None:
        """A rule `check` will skip has not run. Counting it reports a check
        that never happened — a water level with no datum skips `range_check`,
        and if that is the only matching rule the group is UNCHECKED."""
        obs = [_obs(0), _obs(10), _obs(20)]

        step, n_rules = _resolve_selection(
            obs, _rule_set(600), skipped_rule_ids=frozenset({"range_check"})
        )[(_STATION, "discharge")]

        assert step == timedelta(seconds=600)
        assert n_rules == 0

    def test_mixed_network_check_and_selection_agree_after_skips(self) -> None:
        swiss_id = _STATION
        dhm_id = StationId(uuid4())
        observations = [
            _obs(minutes, value, station_id=station_id)
            for station_id, value in ((swiss_id, 1.0), (dhm_id, 1.0))
            for minutes, value in ((0, value), (10, 5.0), (20, value))
        ]
        generic_range = QcRuleParams(
            rule_id="range_check",
            rule_version="generic-v1",
            parameter="discharge",
            time_step=timedelta(minutes=10),
            thresholds={"value_min": 0.0, "value_max": 10.0},
        )
        dhm_range = QcRuleParams(
            rule_id="range_check",
            rule_version="dhm-v1",
            parameter="discharge",
            time_step=timedelta(minutes=10),
            thresholds={"value_min": 0.0, "value_max": 3.0},
            network="dhm",
        )
        skipped_spike = QcRuleParams(
            rule_id="spike",
            rule_version="generic-v1",
            parameter="discharge",
            time_step=timedelta(minutes=10),
            thresholds={"tolerance": 0.1},
        )
        rules = QcRuleSet(
            version="test-v1", rules=(generic_range, dhm_range, skipped_spike)
        )
        networks = {swiss_id: "bafu", dhm_id: "dhm"}
        skipped = frozenset({"spike"})

        flags = Stage1QualityChecker().check(
            observations,
            rules,
            [],
            [],
            station_networks=networks,
            skipped_rule_ids=skipped,
        )
        selected = resolve_selection(
            observations,
            rules,
            station_networks=networks,
            skipped_rule_ids=skipped,
        )

        assert selected[(swiss_id, "discharge")] == (timedelta(minutes=10), 1)
        assert selected[(dhm_id, "discharge")] == (timedelta(minutes=10), 1)
        assert flags[observations[1].id] == []
        assert [(f.rule_id, f.rule_version) for f in flags[observations[4].id]] == [
            ("range_check", "dhm-v1")
        ]


class TestAggregateQcStatus:
    def test_no_flags_with_rules_run_is_passed(self) -> None:
        assert _aggregate_qc_status([], rules_ran=True) is QcStatus.QC_PASSED

    def test_no_flags_with_no_rules_run_is_unchecked(self) -> None:
        """The defect's second half, and the whole point of the plan: these two
        cases produced an identical stored row."""
        assert _aggregate_qc_status([], rules_ran=False) is QcStatus.QC_UNCHECKED

    def test_the_two_cases_are_distinguishable(self) -> None:
        assert _aggregate_qc_status([], rules_ran=True) is not _aggregate_qc_status(
            [], rules_ran=False
        )
