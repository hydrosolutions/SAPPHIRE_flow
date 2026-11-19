from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from sapphire_flow.config.qc_rules import resolve_qc_rules
from sapphire_flow.flows import ingest_observations as live
from sapphire_flow.services.station_observation_qc import (
    Stage1QcReport,
    run_stage1_qc,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import (
    ClimBaseline,
    QcRuleParams,
    QcRuleSet,
    StationQcOverride,
)
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.observation import Observation, RawObservation
from tests.fakes.fake_stores import FakeClimBaselineStore, FakeObservationStore

STATION = StationId(uuid4())
NETWORKS = {STATION: "synthetic"}
RULES = resolve_qc_rules()[0]
T0 = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
FIXTURES = Path(__file__).parents[2] / "fixtures" / "synthetic_station"


def _raws(values: list[float], step: timedelta, parameter: str) -> list[RawObservation]:
    return [
        RawObservation(
            station_id=STATION,
            timestamp=ensure_utc(T0 + i * step),
            parameter=parameter,
            value=v,
            source=ObservationSource.MEASURED,
        )
        for i, v in enumerate(values)
    ]


def _ramp(base: float) -> list[RawObservation]:
    values = [base + 0.01 * i for i in range(48)]
    return _raws(values, timedelta(hours=1), "water_level")


def _fixture(name: str) -> list[RawObservation]:
    with (FIXTURES / name).open() as handle:
        rows = list(csv.DictReader(handle))
    return [
        RawObservation(
            station_id=STATION,
            timestamp=ensure_utc(
                datetime.fromisoformat(r["time"].replace("Z", "+00:00")).replace(
                    tzinfo=UTC
                )
            ),
            parameter="discharge",
            value=float(r["value"]),
            source=ObservationSource.MEASURED,
        )
        for r in rows
    ]


def _loaded(
    raws: list[RawObservation],
) -> tuple[FakeObservationStore, list[Observation]]:
    store = FakeObservationStore()
    store.store_raw_observations(raws)
    reader = store.qc_context_reader()
    times = [r.timestamp for r in raws]
    window_end = ensure_utc(max(times) + timedelta(microseconds=1))
    obs = reader.fetch_observations(
        STATION,
        raws[0].parameter,
        ensure_utc(min(times) - timedelta(hours=2)),
        window_end,
    )
    return reader, obs


def _baselines(parameter: str, mean: float, std: float) -> FakeClimBaselineStore:
    store = FakeClimBaselineStore()
    store.store_baselines(
        [
            ClimBaseline(
                station_id=STATION,
                parameter=parameter,
                day_of_year=d,
                rolling_mean=mean,
                rolling_std=std,
                sample_count=30,
            )
            for d in range(1, 367)
        ]
    )
    return store


def _mine(
    raws: list[RawObservation],
    *,
    datum: float | None = None,
    baselines: FakeClimBaselineStore | None = None,
    overrides: tuple[StationQcOverride, ...] = (),
) -> tuple[Stage1QcReport, dict[UtcDatetime, Observation]]:
    reader, obs = _loaded(raws)
    report = run_stage1_qc(
        obs,
        reader,
        baselines or FakeClimBaselineStore(),
        STATION,
        raws[0].parameter,
        RULES,
        station_networks=NETWORKS,
        datum=datum,
        overrides=overrides,
        target_ids=frozenset(o.id for o in obs),
    )
    return report, {
        o.timestamp: o
        for o in reader.fetch_observations(
            STATION, raws[0].parameter, T0 - timedelta(days=1), T0 + timedelta(days=400)
        )
    }


def _status_by_time(
    raws: list[RawObservation], report: Stage1QcReport
) -> dict[Any, QcStatus]:
    _, obs = _loaded(raws)
    return {o.timestamp: report.statuses[o.id] for o in obs}


class TestRunStage1Qc:
    def test_gauge_height_with_datum_passes_range_check(self) -> None:
        raws = _raws(
            [1480.0 + 2 + 0.01 * i for i in range(48)],
            timedelta(hours=1),
            "water_level",
        )
        report, stored = run_with_stored(raws, datum=1480.0)
        assert report.rule_version == "1.2-datum"
        assert report.counts["failed"] == 0
        assert all(o.qc_status is not QcStatus.QC_FAILED for o in stored.values())

    def test_gauge_height_stored_without_the_datum_shift_fails_range_check(
        self,
    ) -> None:
        raws = _raws(
            [2 + 0.01 * i for i in range(48)], timedelta(hours=1), "water_level"
        )
        report, stored = run_with_stored(raws, datum=1480.0)
        assert report.counts["failed"] == 48
        flags = {f.rule_id for o in stored.values() for f in o.qc_flags}
        assert "range_check" in flags

    def test_spike_is_failed(self) -> None:
        values = [20.0] * 48
        values[24] = 90000.0 + 20.0
        report, stored = run_with_stored(_raws(values, timedelta(hours=1), "discharge"))
        assert stored[T0 + timedelta(hours=24)].qc_status in (
            QcStatus.QC_FAILED,
            QcStatus.QC_SUSPECT,
        )
        assert report.counts["failed"] + report.counts["suspect"] >= 1

    def test_sparse_series_is_unchecked_and_reported_as_zero_rule_group(self) -> None:
        raws = _raws([20.0, 21.0, 22.0], timedelta(days=7), "discharge")
        report, stored = run_with_stored(raws)
        assert report.counts["unchecked"] == 3
        assert {o.qc_status for o in stored.values()} == {QcStatus.QC_UNCHECKED}
        (group,) = report.zero_rule_groups
        assert group.parameter == "discharge"

    def test_no_datum_gives_skip_version(self) -> None:
        raws = _raws(
            [1480.0 + 2 + 0.01 * i for i in range(48)],
            timedelta(hours=1),
            "water_level",
        )
        report, _ = run_with_stored(raws, datum=None)
        assert report.rule_version == "1.2-datum-skip"

    def test_dry_run_writes_nothing(self) -> None:
        raws = _raws(
            [20.0 + 0.01 * i for i in range(48)], timedelta(hours=1), "discharge"
        )
        reader, obs = _loaded(raws)
        report = run_stage1_qc(
            obs,
            None,
            FakeClimBaselineStore(),
            STATION,
            "discharge",
            RULES,
            station_networks=NETWORKS,
            datum=None,
            overrides=(),
            target_ids=frozenset(o.id for o in obs),
        )
        assert report.counts["passed"] + report.counts["unjudged"] == 48
        assert {
            o.qc_status
            for o in reader.fetch_observations(
                STATION, "discharge", T0 - timedelta(days=1), T0 + timedelta(days=9)
            )
        } == {QcStatus.RAW}

    def test_already_checked_rows_are_not_re_evaluated(self) -> None:
        raws = _raws(
            [20.0 + 0.01 * i for i in range(48)], timedelta(hours=1), "discharge"
        )
        reader, obs = _loaded(raws)
        kwargs: dict[str, Any] = dict(
            station_networks=NETWORKS, datum=None, overrides=()
        )
        ids = frozenset(o.id for o in obs)
        run_stage1_qc(
            obs,
            reader,
            FakeClimBaselineStore(),
            STATION,
            "discharge",
            RULES,
            target_ids=ids,
            **kwargs,
        )
        again = reader.fetch_observations(
            STATION, "discharge", T0 - timedelta(days=1), T0 + timedelta(days=9)
        )
        second = run_stage1_qc(
            again,
            reader,
            FakeClimBaselineStore(),
            STATION,
            "discharge",
            RULES,
            target_ids=ids,
            **kwargs,
        )
        assert second.counts["newly_checked"] == 0
        assert second.statuses == {o.id: o.qc_status for o in again}


def run_with_stored(
    raws: list[RawObservation], *, datum: float | None = None
) -> tuple[Stage1QcReport, dict[UtcDatetime, Observation]]:
    return _mine(raws, datum=datum)


class _StaleStore(FakeObservationStore):
    def update_qc_batch_if_current(
        self, updates: Any, qc_rule_version: str | None = None
    ) -> bool:  # noqa: ANN401
        return False


@dataclass(frozen=True, kw_only=True)
class ParityCase:
    raws: list[RawObservation]
    datum: float | None = None
    baseline: bool = False
    stale: bool = False
    rules: QcRuleSet = RULES
    overrides: tuple[StationQcOverride, ...] = ()
    target_from: int = 0
    pre_unchecked: int = 0


def _live_and_mine(case: ParityCase) -> tuple[Any, Any, Any, Any]:
    raws = case.raws
    results = []
    for runner in ("live", "mine"):
        base = _StaleStore() if case.stale else FakeObservationStore()
        base.store_raw_observations(raws)
        reader = base.qc_context_reader()
        if case.stale:
            reader.update_qc_batch_if_current = base.update_qc_batch_if_current  # type: ignore[method-assign]
        times = [r.timestamp for r in raws]
        obs = reader.fetch_observations(
            STATION,
            raws[0].parameter,
            ensure_utc(min(times) - timedelta(hours=2)),
            ensure_utc(max(times) + timedelta(microseconds=1)),
        )
        for o in obs[: case.pre_unchecked]:
            base.update_qc(o.id, QcStatus.QC_UNCHECKED, [])
        if case.pre_unchecked:
            obs = reader.fetch_observations(
                STATION,
                raws[0].parameter,
                ensure_utc(min(times) - timedelta(hours=2)),
                ensure_utc(max(times) + timedelta(microseconds=1)),
            )
        ids = frozenset(o.id for o in obs[case.target_from :])
        bl = (
            _baselines(raws[0].parameter, 20.0, 1.0)
            if case.baseline
            else FakeClimBaselineStore()
        )
        if runner == "live":
            out: Any = live._evaluate_qc_window(  # pyright: ignore[reportPrivateUsage]
                obs,
                reader,
                bl,  # pyright: ignore[reportArgumentType]
                STATION,
                raws[0].parameter,
                case.rules,
                station_networks=NETWORKS,
                datum=case.datum,
                overrides=case.overrides,
                target_ids=ids,
            )
        else:
            out = run_stage1_qc(
                obs,
                reader,
                bl,
                STATION,
                raws[0].parameter,
                case.rules,
                station_networks=NETWORKS,
                datum=case.datum,
                overrides=case.overrides,
                target_ids=ids,
            )
        stored = {
            o.timestamp: (
                o.qc_status,
                o.qc_rule_version,
                [(f.rule_id, f.status, f.detail) for f in o.qc_flags],
            )
            for o in reader.fetch_observations(
                STATION,
                raws[0].parameter,
                T0 - timedelta(days=1),
                T0 + timedelta(days=500),
            )
        }
        results.append((out, stored))
    return results[0][0], results[0][1], results[1][0], results[1][1]


def _spiked() -> list[RawObservation]:
    values = [20.0 + 0.01 * i for i in range(72)]
    values[30] = 400.0
    values[50] = 20.0 + 1e6
    return _raws(values, timedelta(hours=1), "discharge")


def _hourly_discharge() -> list[RawObservation]:
    return _raws([20.0 + 0.01 * i for i in range(72)], timedelta(hours=1), "discharge")


def _rules(*rules: tuple[str, str, timedelta, dict[str, float]]) -> QcRuleSet:
    return QcRuleSet(
        version="custom",
        rules=tuple(
            QcRuleParams(
                rule_id=rule_id,  # type: ignore[arg-type]
                rule_version="1",
                parameter=parameter,
                time_step=step,
                thresholds=thresholds,
            )
            for rule_id, parameter, step, thresholds in rules
        ),
    )


HOUR, DAY = timedelta(hours=1), timedelta(days=1)
_TIGHT_RANGE = StationQcOverride(
    station_id=STATION,
    rule_id="range_check",
    parameter="discharge",
    time_step=HOUR,
    thresholds={"value_max": 20.3},
)

PARITY_CASES: dict[str, ParityCase] = {
    "synthetic_hourly": ParityCase(raws=_fixture("discharge_hourly.csv")),
    "synthetic_daily": ParityCase(raws=_fixture("discharge_daily.csv")),
    "spiked_hourly": ParityCase(raws=_spiked()),
    "sparse_zero_rule": ParityCase(
        raws=_raws([20.0, 21.0, 22.0], timedelta(days=7), "discharge")
    ),
    "single_reading": ParityCase(raws=_raws([20.0], DAY, "discharge")),
    "water_level_datum": ParityCase(raws=_ramp(1482.0), datum=1480.0),
    "water_level_unshifted": ParityCase(raws=_ramp(2.0), datum=1480.0),
    "water_level_no_datum": ParityCase(raws=_ramp(1482.0)),
    "with_baseline": ParityCase(raws=_spiked(), baseline=True),
    "stale": ParityCase(raws=_spiked(), stale=True),
    # Context rows outside the target ids, and a station override that bites
    # only the target rows.
    "context_outside_targets_with_override": ParityCase(
        raws=_hourly_discharge(), target_from=48, overrides=(_TIGHT_RANGE,)
    ),
    "override_on_all_rows": ParityCase(
        raws=_hourly_discharge(), overrides=(_TIGHT_RANGE,)
    ),
    # Rows an earlier load left qc_unchecked are not re-judged.
    "already_unchecked_rows": ParityCase(raws=_hourly_discharge(), pre_unchecked=24),
    # A group that selects exactly one rule is not a zero-rule group ...
    "one_rule_group": ParityCase(
        raws=_hourly_discharge(),
        rules=_rules(("range_check", "discharge", HOUR, {"value_max": 100.0})),
    ),
    # ... but one that selects none is.
    "zero_rule_group_custom_rules": ParityCase(
        raws=_hourly_discharge(),
        rules=_rules(("range_check", "discharge", DAY, {"value_max": 100.0})),
    ),
    # Latent: with no datum range_check/gross_outlier are skipped, which can
    # empty the group (live: unchecked + zero-rule group).
    "skip_empties_the_group": ParityCase(
        raws=_ramp(1482.0),
        rules=_rules(
            (
                "range_check",
                "water_level",
                HOUR,
                {"value_min": -2.0, "value_max": 20.0},
            ),
            ("gross_outlier", "water_level", HOUR, {"k_sigma": 5.0}),
        ),
    ),
}


class TestParityWithLiveIngest:
    @pytest.mark.parametrize("case", list(PARITY_CASES))
    def test_same_statuses_flags_version_counts_and_zero_rule_output(
        self, case: str
    ) -> None:
        spec = PARITY_CASES[case]
        live_out, live_stored, my_out, my_stored = _live_and_mine(spec)
        assert my_stored == live_stored
        assert my_out.counts == live_out.counts
        assert [
            (g.parameter, g.inferred_time_step_seconds, g.reason)
            for g in my_out.zero_rule_groups
        ] == [
            (g.parameter, g.inferred_time_step_seconds, g.reason)
            for g in live_out.zero_rule_groups
        ]
        if spec.stale:
            assert my_out.stale
            assert my_out.counts["stale_evaluation"] == len(spec.raws)
        else:
            stored_versions = {v for _, v, _ in my_stored.values()} - {None}
            assert stored_versions == {my_out.rule_version}

    def test_the_corpus_exercises_every_outcome(self) -> None:
        seen: set[str] = set()
        for spec in PARITY_CASES.values():
            live_out, _, _, _ = _live_and_mine(spec)
            seen |= {k for k, v in live_out.counts.items() if v}
            if live_out.zero_rule_groups:
                seen.add("zero_rule_group")
        assert {
            "passed",
            "failed",
            "unchecked",
            "unjudged",
            "zero_rule_group",
            "stale_evaluation",
        } <= seen

    def test_new_cases_actually_differ_from_the_plain_run(self) -> None:
        outcomes = {
            name: _live_and_mine(PARITY_CASES[name])[0].counts
            for name in (
                "context_outside_targets_with_override",
                "already_unchecked_rows",
                "override_on_all_rows",
            )
        }
        assert outcomes["context_outside_targets_with_override"]["newly_checked"] == 24
        assert outcomes["already_unchecked_rows"]["newly_checked"] == 48
        assert outcomes["override_on_all_rows"]["failed"] > 0
        zero = _live_and_mine(PARITY_CASES["skip_empties_the_group"])[0]
        assert zero.counts["unchecked"] == 48 and zero.zero_rule_groups
        assert not _live_and_mine(PARITY_CASES["one_rule_group"])[0].zero_rule_groups
