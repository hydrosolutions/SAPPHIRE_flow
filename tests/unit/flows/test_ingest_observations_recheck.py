"""Plan 317 T1 — a reading stored `QC_UNCHECKED` must be re-judged later.

`QC_UNCHECKED` means "no rule could be selected for this group", which is
usually TRANSIENT: a station's first row of the day, a feed catching up after
an outage, a window that straddled a gap. The QC step only ever re-judged rows
stored `RAW`, so a passing cause became a permanent verdict — and since Plan
316 that verdict follows the row into every consumer for its life.

⛔ These tests assert the DESIRED behaviour (the row IS re-judged), never the
defect ("the row stays unchecked") — the latter would PASS before the fix and
prove nothing.

⭐ `test_already_checked_neighbours_stay_in_the_group` is the guard against the
REJECTED approach. Filtering the FETCH to `{RAW, QC_UNCHECKED}` instead of the
in-memory pick-up would drop the already-checked neighbours that cadence
inference needs; that fixture's cadence is inferable ONLY with them, so the
test goes red if anyone ever narrows the fetch.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from sapphire_flow.flows.ingest_observations import (
    _run_qc_task,
    ingest_observations_flow,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.observation import RawObservation
from tests.conftest import make_station_config
from tests.fakes.fake_adapters import FakeStationDataSource
from tests.fakes.fake_stores import (
    FakeClimBaselineStore,
    FakeObservationStore,
    FakeStationStore,
)

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.observation import Observation

_NOW = ensure_utc(datetime(2026, 9, 24, 12, 0, tzinfo=UTC))

# Declares 600 s only: a group whose inferred median cadence is anything else
# selects zero rules, which is how these fixtures manufacture `QC_UNCHECKED`.
_QC_RULES = QcRuleSet(
    version="test",
    rules=(
        QcRuleParams(
            rule_id="range_check",
            rule_version="1.0",
            parameter="discharge",
            time_step=timedelta(seconds=600),
            thresholds={"value_min": 0.0, "value_max": 5000.0},
        ),
    ),
)


# `rate_of_change` flags QC_SUSPECT where `range_check` flags QC_FAILED, so the
# third verdict arm needs its own set rather than a wider shared fixture.
_QC_RULES_WITH_RATE = QcRuleSet(
    version="test-rate",
    rules=(
        *_QC_RULES.rules,
        QcRuleParams(
            rule_id="rate_of_change",
            rule_version="1.0",
            parameter="discharge",
            time_step=timedelta(seconds=600),
            thresholds={"max_rate": 1.0},
        ),
    ),
)


def _fixed_clock() -> UtcDatetime:
    return _NOW


def _obs(station_id: StationId, minutes_ago: int, value: float) -> RawObservation:
    return RawObservation(
        station_id=station_id,
        timestamp=ensure_utc(_NOW - timedelta(minutes=minutes_ago)),
        parameter="discharge",
        value=value,
        source=ObservationSource.MEASURED,
    )


def _station() -> tuple[FakeStationStore, StationId]:
    config = make_station_config(code="2135", name="Aare Bern", rng=random.Random(1))
    store = FakeStationStore()
    store.store_station(config)
    return store, config.id


def _by_minutes_ago(store: FakeObservationStore) -> dict[int, Observation]:
    return {
        round((_NOW - o.timestamp).total_seconds() / 60): o
        for o in store.observations()
    }


def _run(
    obs_store: FakeObservationStore,
    station_store: FakeStationStore,
    delivered: list[RawObservation],
    qc_rules: QcRuleSet = _QC_RULES,
):
    return ingest_observations_flow(
        station_store=station_store,
        obs_store=obs_store,
        baseline_store=FakeClimBaselineStore(),
        adapter=FakeStationDataSource(delivered),
        qc_rules=qc_rules,
        clock=_fixed_clock,
    )


class TestUncheckedIsReExamined:
    def test_an_unchecked_row_is_rejudged_once_the_window_holds_context(self) -> None:
        """⛔ THE red test. Cycle 1 delivers a station's first reading, so the
        group is a single instant, no cadence is measurable and the row is
        stored `QC_UNCHECKED`. Cycle 2's window holds four readings at a
        declared cadence — the row must be re-judged, not left unchecked for
        life."""
        station_store, station_id = _station()
        obs_store = FakeObservationStore()

        first = _run(obs_store, station_store, [_obs(station_id, 70, 10.0)])
        assert first.qc_unchecked == 1, "precondition: the first row goes unchecked"
        assert _by_minutes_ago(obs_store)[70].qc_status is QcStatus.QC_UNCHECKED

        _run(
            obs_store,
            station_store,
            [
                _obs(station_id, 60, 11.0),
                _obs(station_id, 50, 12.0),
                _obs(station_id, 40, 13.0),
            ],
        )

        rows = _by_minutes_ago(obs_store)
        assert rows[70].qc_status is QcStatus.QC_PASSED, (
            "the unchecked row was never re-judged"
        )
        assert rows[70].qc_rule_version is not None

    def test_a_rejudged_row_takes_whatever_the_rules_now_say(self) -> None:
        """Re-examination is not a promotion. The same fixture with a value the
        range rule rejects must come back `QC_FAILED` — the row takes the
        verdict the rules give it, overwriting the earlier one (272:847)."""
        station_store, station_id = _station()
        obs_store = FakeObservationStore()

        first = _run(obs_store, station_store, [_obs(station_id, 70, 9000.0)])
        assert first.qc_unchecked == 1, "precondition: the first row goes unchecked"

        result = _run(
            obs_store,
            station_store,
            [
                _obs(station_id, 60, 11.0),
                _obs(station_id, 50, 12.0),
                _obs(station_id, 40, 13.0),
            ],
        )

        rows = _by_minutes_ago(obs_store)
        assert rows[70].qc_status is QcStatus.QC_FAILED
        assert [flag.rule_id for flag in rows[70].qc_flags] == ["range_check"]
        assert result.qc_failed == 1

    def test_a_rejudged_row_can_come_back_suspect(self) -> None:
        """The third verdict arm. Plan 317 verifies the row takes `QC_PASSED`,
        `QC_SUSPECT` or `QC_FAILED` as the rules dictate; passed and failed are
        covered above, and both reviewers of this task named suspect as the
        gap. Here the unchecked row is the LATEST reading, so re-examination
        gives it a predecessor and `rate_of_change` a pair to judge."""
        station_store, station_id = _station()
        obs_store = FakeObservationStore()

        first = _run(
            obs_store,
            station_store,
            [_obs(station_id, 40, 10.0)],
            _QC_RULES_WITH_RATE,
        )
        assert first.qc_unchecked == 1, "precondition: the lone row goes unchecked"

        result = _run(
            obs_store,
            station_store,
            [
                _obs(station_id, 70, 11.0),
                _obs(station_id, 60, 12.0),
                _obs(station_id, 50, 13.0),
            ],
            _QC_RULES_WITH_RATE,
        )

        rows = _by_minutes_ago(obs_store)
        assert rows[40].qc_status is QcStatus.QC_SUSPECT
        assert [flag.rule_id for flag in rows[40].qc_flags] == ["rate_of_change"]
        assert result.qc_suspect == 1
        assert result.qc_rechecked == 1

    def test_a_group_that_still_resolves_zero_rules_stays_unchecked(self) -> None:
        """Re-examination is not a promotion: a window that still selects no
        rule leaves the honest verdict in place."""
        station_store, station_id = _station()
        obs_store = FakeObservationStore()
        [oid] = obs_store.store_raw_observations([_obs(station_id, 50, 10.0)])
        obs_store.update_qc(oid, QcStatus.QC_UNCHECKED, [])

        # 25 min apart ⇒ an inferred 1500 s, declared by no rule.
        result = _run(obs_store, station_store, [_obs(station_id, 25, 11.0)])

        rows = _by_minutes_ago(obs_store)
        assert rows[50].qc_status is QcStatus.QC_UNCHECKED
        assert rows[25].qc_status is QcStatus.QC_UNCHECKED
        assert result.qc_unchecked == 2

    def test_already_checked_neighbours_stay_in_the_group(self) -> None:
        """⭐ The guard on the REJECTED approach — the fetch must stay
        unfiltered.

        The four `QC_PASSED` neighbours are what makes the cadence inferable:
        with them the distinct stamps are 70/60/50/40/30/10 min ago, gaps
        [600, 600, 600, 600, 1200] s, median 600 s — declared. Drop them (as a
        fetch filtered to `{RAW, QC_UNCHECKED}` would) and only 30 and 10 are
        left, a lone 1200 s gap that no rule declares, so nothing is checked at
        all. Nothing else in the suite notices that.
        """
        station_store, station_id = _station()
        obs_store = FakeObservationStore()
        for minutes_ago in (70, 60, 50, 40):
            [oid] = obs_store.store_raw_observations(
                [_obs(station_id, minutes_ago, 10.0)]
            )
            obs_store.update_qc(oid, QcStatus.QC_PASSED, [], qc_rule_version="1.0")
        [unchecked_id] = obs_store.store_raw_observations([_obs(station_id, 30, 11.0)])
        obs_store.update_qc(unchecked_id, QcStatus.QC_UNCHECKED, [])

        with structlog.testing.capture_logs() as captured:
            _run(obs_store, station_store, [_obs(station_id, 10, 12.0)])

        rows = _by_minutes_ago(obs_store)
        assert rows[30].qc_status is QcStatus.QC_PASSED, (
            "the unchecked row was not re-judged"
        )
        assert rows[10].qc_status is QcStatus.QC_PASSED
        assert [
            e["event"] for e in captured if e["event"] == "qc.no_rules_selected"
        ] == [], "the cadence resolved only because the checked neighbours were fetched"
        # A row already QC_PASSED is untouched — 272 D3, no re-judgement.
        assert all(
            rows[m].qc_status is QcStatus.QC_PASSED and rows[m].qc_rule_version == "1.0"
            for m in (70, 60, 50, 40)
        )

    def test_the_catch_up_widening_readmits_an_older_unchecked_row(self) -> None:
        """The fetched window widens to cover a catch-up delivery, so a row
        older than `now - context_window_hours` DOES re-enter a later window —
        the residual is narrower than "anything older is lost for ever"."""
        _, station_id = _station()
        obs_store = FakeObservationStore()
        [unchecked_id] = obs_store.store_raw_observations(
            [_obs(station_id, 300, 10.0)]  # 5 h ago: outside the 2 h base window
        )
        obs_store.update_qc(unchecked_id, QcStatus.QC_UNCHECKED, [])
        recovered = [_obs(station_id, 320, 9.0), _obs(station_id, 310, 9.5)]
        obs_store.store_raw_observations(recovered)

        outcome = _run_qc_task.fn(
            obs_store,
            FakeClimBaselineStore(),
            station_id,
            "discharge",
            qc_rules=_QC_RULES,
            now=_NOW,
            station_networks={station_id: "bafu"},
            context_window_hours=2.0,
            fetched_times=tuple(o.timestamp for o in recovered),
        )

        assert _by_minutes_ago(obs_store)[300].qc_status is QcStatus.QC_PASSED
        assert outcome.counts["rechecked"] == 1

    def test_the_result_distinguishes_newly_checked_from_rechecked(self) -> None:
        """The effect must be visible, not inferred from a total."""
        station_store, station_id = _station()
        obs_store = FakeObservationStore()

        _run(obs_store, station_store, [_obs(station_id, 70, 10.0)])
        result = _run(
            obs_store,
            station_store,
            [
                _obs(station_id, 60, 11.0),
                _obs(station_id, 50, 12.0),
                _obs(station_id, 40, 13.0),
            ],
        )

        assert result.qc_rechecked == 1
        assert result.qc_newly_checked == 3
        assert result.qc_passed == 4
