"""Plan 323 T4 (D4, D5): a reading passes only if some selected rule could judge it.

Swiss river stations carry no water-level datum, so `range_check` and
`gross_outlier` are skipped for water level and only neighbour rules remain. A
reading with no usable neighbour — the first after an outage — used to be stored
`QC_PASSED` although nothing judged it.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from sapphire_flow.config.qc_rules import load_qc_rules
from sapphire_flow.flows.ingest_observations import (
    _run_qc_task,
    ingest_observations_flow,
)
from sapphire_flow.services.qc import (
    Judgement,
    _apply_frozen_sensor,
    _apply_gross_outlier,
    _apply_spike,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import ClimBaseline, QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import ObservationSource, PipelineCheckType, QcStatus
from sapphire_flow.types.ids import ObservationId
from sapphire_flow.types.observation import Observation, RawObservation
from tests.conftest import make_station_config
from tests.fakes.fake_adapters import FakeStationDataSource
from tests.fakes.fake_stores import (
    FakeClimBaselineStore,
    FakeObservationStore,
    FakePipelineHealthStore,
    FakeStationStore,
)

if TYPE_CHECKING:
    from sapphire_flow.flows.ingest_observations import IngestResult, QcTaskOutcome
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.pipeline import PipelineHealthRecord
    from sapphire_flow.types.station import StationConfig

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHIPPED_RULES = load_qc_rules(_REPO_ROOT / "config.toml")
_T0 = ensure_utc(datetime(2026, 9, 24, 12, 0, tzinfo=UTC))
_NOW = _T0 + timedelta(minutes=5)


def _station(datum: float | None = None) -> StationConfig:
    return make_station_config(
        code="2135",
        name="Aare Bern",
        rng=random.Random(1),
        water_level_datum_masl=datum,
    )


def _raw(
    station_id: StationId,
    minutes_before_t0: int,
    value: float,
    parameter: str = "water_level",
) -> RawObservation:
    return RawObservation(
        station_id=station_id,
        timestamp=ensure_utc(_T0 - timedelta(minutes=minutes_before_t0)),
        parameter=parameter,
        value=value,
        source=ObservationSource.MEASURED,
    )


def _missing(station_id: StationId, minutes_before_t0: int) -> Observation:
    """A stored reading with no value (`MISSING`, as calculated-station
    derivation writes one) — a raw reading cannot carry `None`."""
    timestamp = ensure_utc(_T0 - timedelta(minutes=minutes_before_t0))
    return Observation(
        id=ObservationId(uuid4()),
        station_id=station_id,
        timestamp=timestamp,
        parameter="water_level",
        value=None,
        source=ObservationSource.MEASURED,
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=QcStatus.MISSING,
        qc_flags=[],
        qc_rule_version=None,
        created_at=timestamp,
    )


def _level(minutes_before_t0: int) -> float:
    # Rising gently, so no two readings are equal and nothing is flat.
    return 500.0 - 0.01 * minutes_before_t0


def _by_minutes(store: FakeObservationStore) -> dict[int, Observation]:
    return {
        int((_T0 - o.timestamp).total_seconds() // 60): o for o in store.observations()
    }


def _run_task(
    store: FakeObservationStore,
    station_id: StationId,
    *,
    datum: float | None = None,
    rules: QcRuleSet = _SHIPPED_RULES,
    parameter: str = "water_level",
) -> QcTaskOutcome:
    return _run_qc_task.fn(
        store,
        FakeClimBaselineStore(),
        station_id,
        parameter,
        qc_rules=rules,
        now=_NOW,
        datum=datum,
    )


def _fixed_clock() -> UtcDatetime:
    return _NOW


class TestFirstReadingsAfterAnOutage:
    """600 s, no datum: the datum skip leaves `rate_of_change`, `spike` and
    `frozen_sensor`; the oldest reading has no neighbour and four instants are
    fewer than `frozen_sensor`'s 12."""

    def test_the_oldest_reading_is_unchecked_not_passed(self) -> None:
        station_id = _station().id
        store = FakeObservationStore()
        store.store_raw_observations(
            [_raw(station_id, m, _level(m)) for m in (30, 20, 10, 0)]
        )

        _run_task(store, station_id)

        assert _by_minutes(store)[30].qc_status is QcStatus.QC_UNCHECKED

    def test_the_readings_with_a_neighbour_get_a_real_verdict(self) -> None:
        station_id = _station().id
        store = FakeObservationStore()
        store.store_raw_observations(
            [_raw(station_id, m, _level(m)) for m in (30, 20, 10, 0)]
        )

        _run_task(store, station_id)

        rows = _by_minutes(store)
        assert [rows[m].qc_status for m in (20, 10, 0)] == [QcStatus.QC_PASSED] * 3

    def test_the_outcome_reports_the_unjudged_reading(self) -> None:
        station_id = _station().id
        store = FakeObservationStore()
        store.store_raw_observations(
            [_raw(station_id, m, _level(m)) for m in (30, 20, 10, 0)]
        )

        outcome = _run_task(store, station_id)

        [group] = outcome.unjudged_groups
        assert group.observation_ids == (_by_minutes(store)[30].id,)
        assert group.inferred_time_step_seconds == 600.0
        assert outcome.counts["unjudged"] == 1
        assert outcome.counts["unchecked"] == 0
        assert outcome.zero_rule_groups == ()

    def test_with_a_datum_range_check_judges_the_oldest_reading(self) -> None:
        station = _station(datum=495.0)
        store = FakeObservationStore()
        store.store_raw_observations(
            [_raw(station.id, m, _level(m)) for m in (30, 20, 10, 0)]
        )

        outcome = _run_task(store, station.id, datum=495.0)

        assert _by_minutes(store)[30].qc_status is QcStatus.QC_PASSED
        assert outcome.unjudged_groups == ()

    def test_a_valueless_neighbour_leaves_the_next_reading_unjudged(self) -> None:
        station_id = _station().id
        store = FakeObservationStore()
        store.store_raw_observations(
            [_raw(station_id, m, _level(m)) for m in (30, 20, 0)]
        )
        store.store_observations([_missing(station_id, 10)])

        _run_task(store, station_id)

        assert _by_minutes(store)[0].qc_status is QcStatus.QC_UNCHECKED

    def test_a_judged_context_row_is_not_reported_again(self) -> None:
        station_id = _station().id
        store = FakeObservationStore()
        [oldest] = store.store_raw_observations([_raw(station_id, 30, _level(30))])
        store.update_qc(oldest, QcStatus.QC_PASSED, [])
        store.store_raw_observations(
            [_raw(station_id, m, _level(m)) for m in (20, 10, 0)]
        )

        outcome = _run_task(store, station_id)

        assert outcome.unjudged_groups == ()
        assert _by_minutes(store)[20].qc_status is QcStatus.QC_PASSED


def _frozen_rule(min_consecutive: int) -> QcRuleParams:
    return QcRuleParams(
        rule_id="frozen_sensor",
        rule_version="1.0",
        parameter="water_level",
        time_step=timedelta(seconds=600),
        thresholds={"tolerance": 0.001, "min_consecutive": float(min_consecutive)},
    )


def _judged_minutes(store: FakeObservationStore, min_consecutive: int) -> set[int]:
    group = sorted(store.observations(), key=lambda o: o.timestamp)
    rule = _frozen_rule(min_consecutive)
    _, judged = _apply_frozen_sensor(group, dict(rule.thresholds), rule)  # type: ignore[arg-type]
    by_id = {o.id: o for o in group}
    return {int((_T0 - by_id[i].timestamp).total_seconds() // 60) for i in judged}


class TestFrozenSensorJudgesPerReading:
    """A reading is judged by `frozen_sensor` only inside a stretch of at least
    `min_consecutive` distinct instants with values. Asserted on the rule's
    whole judged set — through the flow, `rate_of_change` would mask a reading
    `frozen_sensor` failed to cover."""

    def test_every_reading_of_a_qualifying_stretch_is_judged(self) -> None:
        station_id = _station().id
        store = FakeObservationStore()
        minutes = range(110, -1, -10)  # exactly 12 instants
        store.store_raw_observations([_raw(station_id, m, _level(m)) for m in minutes])

        assert _judged_minutes(store, 12) == set(minutes)

    def test_a_missing_reading_splits_the_stretch(self) -> None:
        station_id = _station().id
        store = FakeObservationStore()
        minutes = range(230, -1, -10)  # 24 instants; the 13th is missing
        store.store_raw_observations(
            [_raw(station_id, m, _level(m)) for m in minutes if m != 110]
        )
        store.store_observations([_missing(station_id, 110)])

        # 12 valued instants before the gap qualify; the 11 after it do not.
        assert _judged_minutes(store, 12) == set(range(230, 119, -10))

    def test_duplicate_instants_count_once(self) -> None:
        station_id = _station().id
        store = FakeObservationStore()
        minutes = range(50, -1, -10)  # 6 instants, two sources each = 12 rows
        store.store_raw_observations(
            [_raw(station_id, m, _level(m)) for m in minutes]
            + [
                RawObservation(
                    station_id=station_id,
                    timestamp=ensure_utc(_T0 - timedelta(minutes=m)),
                    parameter="water_level",
                    value=_level(m),
                    source=ObservationSource.MANUAL_IMPORT,
                )
                for m in minutes
            ]
        )

        assert _judged_minutes(store, 12) == set()

    def test_excluded_values_end_a_stretch(self) -> None:
        station_id = _station().id
        store = FakeObservationStore()
        store.store_raw_observations(
            [
                _raw(station_id, m, 0.0 if m % 20 == 0 else 1.0 + m / 1000)
                for m in range(230, -1, -10)
            ]
        )
        group = sorted(store.observations(), key=lambda o: o.timestamp)
        rule = _frozen_rule(3)

        _, judged = _apply_frozen_sensor(
            group,
            {"tolerance": 0.001, "min_consecutive": 3, "exclude_at_or_below": 0.0},
            rule,
        )

        # Every other reading is excluded, so no stretch reaches 3 instants.
        assert judged == frozenset()

    def test_through_the_task_the_oldest_reading_of_a_long_stretch_passes(
        self,
    ) -> None:
        station_id = _station().id
        store = FakeObservationStore()
        store.store_raw_observations(
            [_raw(station_id, m, _level(m)) for m in range(110, -1, -10)]
        )

        outcome = _run_task(store, station_id)

        assert _by_minutes(store)[110].qc_status is QcStatus.QC_PASSED
        assert outcome.unjudged_groups == ()


class TestZeroRuleWins:
    """A cadence no rule declares — 1800 s, which no row will ever add — makes
    a zero-rule group; its readings are counted and reported as zero-rule only."""

    def test_a_zero_rule_reading_is_not_also_unjudged(self) -> None:
        station_id = _station().id
        store = FakeObservationStore()
        store.store_raw_observations(
            [_raw(station_id, m, _level(m)) for m in (90, 60, 30, 0)]
        )

        outcome = _run_task(store, station_id)

        assert outcome.counts["unchecked"] == 4
        assert outcome.counts["unjudged"] == 0
        assert outcome.unjudged_groups == ()
        [group] = outcome.zero_rule_groups
        assert group.inferred_time_step_seconds == 1800.0


class _FailingUnjudgedWrites(FakePipelineHealthStore):
    def append_health_record(self, record: PipelineHealthRecord) -> None:
        if record.check_type is PipelineCheckType.OBSERVATION_QC_UNJUDGED:
            raise RuntimeError("health store unavailable")
        super().append_health_record(record)


class TestThroughTheFlow:
    def _flow(
        self,
        delivered: list[RawObservation],
        station: StationConfig,
        health: FakePipelineHealthStore,
        obs_store: FakeObservationStore,
    ) -> IngestResult:
        station_store = FakeStationStore()
        station_store.store_station(station)
        return ingest_observations_flow(
            station_store=station_store,
            obs_store=obs_store,
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource(delivered),
            qc_rules=_SHIPPED_RULES,
            clock=_fixed_clock,
            pipeline_health_store=health,
        )

    def test_the_unjudged_record_lists_the_reading_and_no_zero_rule_record(
        self,
    ) -> None:
        station = _station()
        health = FakePipelineHealthStore()
        obs_store = FakeObservationStore()

        result = self._flow(
            [_raw(station.id, m, _level(m)) for m in (30, 20, 10, 0)],
            station,
            health,
            obs_store,
        )

        oldest = _by_minutes(obs_store)[30]
        [record] = health.fetch_recent(
            check_type=PipelineCheckType.OBSERVATION_QC_UNJUDGED
        )
        assert record.detail["reason"] == "no_check_could_run"
        assert record.detail["unjudged_groups"][0]["observation_ids"] == [
            str(oldest.id)
        ]
        assert record.detail["observations_unjudged"] == 1
        assert (
            health.fetch_recent(check_type=PipelineCheckType.OBSERVATION_QC_UNCHECKED)
            == []
        )
        assert result.qc_unchecked == 0
        assert result.qc_unjudged == 1

    def test_the_record_survives_json_serialisation(self) -> None:
        station = _station()
        health = FakePipelineHealthStore()

        self._flow(
            [_raw(station.id, m, _level(m)) for m in (30, 20, 10, 0)],
            station,
            health,
            FakeObservationStore(),
        )

        [record] = health.fetch_recent(
            check_type=PipelineCheckType.OBSERVATION_QC_UNJUDGED
        )
        assert json.loads(json.dumps(record.detail)) == record.detail

    def test_no_record_when_every_reading_was_judged(self) -> None:
        station = _station(datum=495.0)
        health = FakePipelineHealthStore()

        self._flow(
            [_raw(station.id, m, _level(m)) for m in (30, 20, 10, 0)],
            station,
            health,
            FakeObservationStore(),
        )

        assert (
            health.fetch_recent(check_type=PipelineCheckType.OBSERVATION_QC_UNJUDGED)
            == []
        )

    def test_a_judged_context_row_produces_no_record(self) -> None:
        station = _station()
        health = FakePipelineHealthStore()
        obs_store = FakeObservationStore()
        [oldest] = obs_store.store_raw_observations([_raw(station.id, 30, _level(30))])
        obs_store.update_qc(oldest, QcStatus.QC_PASSED, [])

        self._flow(
            [_raw(station.id, m, _level(m)) for m in (20, 10, 0)],
            station,
            health,
            obs_store,
        )

        assert (
            health.fetch_recent(check_type=PipelineCheckType.OBSERVATION_QC_UNJUDGED)
            == []
        )

    def test_zero_rule_and_unjudged_readings_stay_in_their_own_records(
        self,
    ) -> None:
        station = _station()
        health = FakePipelineHealthStore()
        obs_store = FakeObservationStore()
        delivered = [_raw(station.id, m, _level(m)) for m in (30, 20, 10, 0)] + [
            _raw(station.id, m, 10.0 + 0.01 * m, parameter="discharge")
            for m in (90, 60, 30, 0)
        ]

        result = self._flow(delivered, station, health, obs_store)

        stored_unchecked = sum(
            o.qc_status is QcStatus.QC_UNCHECKED for o in obs_store.observations()
        )
        assert result.qc_unchecked + result.qc_unjudged == stored_unchecked
        assert (result.qc_unchecked, result.qc_unjudged) == (4, 1)
        [zero_rule] = health.fetch_recent(
            check_type=PipelineCheckType.OBSERVATION_QC_UNCHECKED
        )
        assert zero_rule.detail["observations_unchecked"] == 4
        assert "observations_unjudged" not in zero_rule.detail
        assert [g["parameter"] for g in zero_rule.detail["zero_rule_groups"]] == [
            "discharge"
        ]
        [unjudged] = health.fetch_recent(
            check_type=PipelineCheckType.OBSERVATION_QC_UNJUDGED
        )
        assert [g["parameter"] for g in unjudged.detail["unjudged_groups"]] == [
            "water_level"
        ]
        assert unjudged.detail["observations_unjudged"] == 1

    def test_a_failed_record_write_does_not_fail_the_run(self) -> None:
        station = _station()
        obs_store = FakeObservationStore()

        result = self._flow(
            [_raw(station.id, m, _level(m)) for m in (30, 20, 10, 0)],
            station,
            _FailingUnjudgedWrites(),
            obs_store,
        )

        assert result.qc_unjudged == 1
        assert _by_minutes(obs_store)[30].qc_status is QcStatus.QC_UNCHECKED


def _discharge_rows(values: tuple[float, float, float]) -> dict[int, Observation]:
    station_id = _station().id
    store = FakeObservationStore()
    store.store_raw_observations(
        [
            _raw(station_id, m, v, parameter="discharge")
            for m, v in zip((20, 10, 0), values, strict=True)
        ]
    )
    return _by_minutes(store)


def _rule(rule_id: str, thresholds: dict[str, float]) -> QcRuleParams:
    return QcRuleParams(
        rule_id=rule_id,
        rule_version="1.0",
        parameter="discharge",
        time_step=timedelta(seconds=600),
        thresholds=thresholds,
    )


class TestRuleJudgements:
    """Each rule reports whether it could judge a reading. Asserted on the rule
    itself: through the flow another rule can hide a wrong answer."""

    def test_relative_spike_with_a_zero_reference_is_not_evaluable(self) -> None:
        rows = _discharge_rows((0.0, 5.0, 0.0))
        thresholds = {"tolerance": 0.1}

        judgement, flag = _apply_spike(
            rows[10], rows[20], rows[0], thresholds, _rule("spike", thresholds)
        )

        assert (judgement, flag) == (Judgement.NOT_EVALUABLE, None)

    def test_absolute_spike_on_a_clean_reading_is_judged(self) -> None:
        rows = _discharge_rows((10.0, 10.1, 10.2))
        thresholds = {"max_delta": 1.0}

        judgement, flag = _apply_spike(
            rows[10], rows[20], rows[0], thresholds, _rule("spike", thresholds)
        )

        assert (judgement, flag) == (Judgement.JUDGED, None)

    def test_gross_outlier_without_a_baseline_is_not_evaluable(self) -> None:
        rows = _discharge_rows((10.0, 10.1, 10.2))
        thresholds = {"k_sigma": 5.0}

        judgement, flag = _apply_gross_outlier(
            rows[10], thresholds, {}, _rule("gross_outlier", thresholds)
        )

        assert (judgement, flag) == (Judgement.NOT_EVALUABLE, None)

    def test_gross_outlier_with_a_baseline_is_judged(self) -> None:
        rows = _discharge_rows((10.0, 10.1, 10.2))
        obs = rows[10]
        doy = obs.timestamp.timetuple().tm_yday
        baseline = ClimBaseline(
            station_id=obs.station_id,
            parameter="discharge",
            day_of_year=doy,
            rolling_mean=10.0,
            rolling_std=1.0,
            sample_count=30,
        )
        thresholds = {"k_sigma": 5.0}

        judgement, flag = _apply_gross_outlier(
            obs,
            thresholds,
            {(obs.station_id, "discharge", doy): baseline},
            _rule("gross_outlier", thresholds),
        )

        assert (judgement, flag) == (Judgement.JUDGED, None)
