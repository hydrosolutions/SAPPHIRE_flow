"""Plan 318 T1 — a zero-rule group must be discoverable without reading logs.

Plan 272 shipped the counter and a `qc.no_rules_selected` warning; neither is
queryable. A station whose readings go unchecked is invisible to anyone not
tailing the container.

⛔ These assert on the record's CONTENT, never on its existence.
`_append_fetch_health_record` already writes a record on EVERY run, so a bare
"a record was written" assertion passes with the defect still in place — which
is how two earlier drafts of this test were wrong.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from sapphire_flow.flows.ingest_observations import ingest_observations_flow
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import (
    ObservationSource,
    PipelineCheckType,
    PipelineHealthStatus,
)
from sapphire_flow.types.observation import RawObservation
from tests.conftest import make_station_config
from tests.fakes.fake_adapters import FakeStationDataSource
from tests.fakes.fake_stores import (
    FakeClimBaselineStore,
    FakeObservationStore,
    FakePipelineHealthStore,
    FakeStationStore,
)

_NOW = ensure_utc(datetime(2026, 9, 24, 12, 0, tzinfo=UTC))

# Declares 600 s only. A group whose inferred median is anything else selects
# zero rules — the condition under test.
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


def _fixed_clock():
    return _NOW


def _run(spacing_minutes: int, health_store=None, *, stations: int = 1):
    """`stations` stations, three readings each, `spacing_minutes` apart.
    25 → 1500 s, declared nowhere; 10 → 600 s, declared.

    ⚠️ `stations` exists because a ONE-station fixture cannot distinguish
    "one record per run" from "one record per group" — a review found the
    original test passed either way.
    """
    configs = [
        make_station_config(
            code=f"213{i}", name=f"Station {i}", rng=random.Random(i + 1)
        )
        for i in range(stations)
    ]
    station_store = FakeStationStore()
    for c in configs:
        station_store.store_station(c)

    def obs(station_id, minutes_ago: int, value: float) -> RawObservation:
        return RawObservation(
            station_id=station_id,
            timestamp=ensure_utc(_NOW - timedelta(minutes=minutes_ago)),
            parameter="discharge",
            value=value,
            source=ObservationSource.MEASURED,
        )

    obs_store = FakeObservationStore()
    for c in configs:
        obs_store.store_raw_observations(
            [
                obs(c.id, spacing_minutes * 2, 10.0),
                obs(c.id, spacing_minutes, 11.0),
            ]
        )
    health = health_store if health_store is not None else FakePipelineHealthStore()
    result = ingest_observations_flow(
        station_store=station_store,
        obs_store=obs_store,
        baseline_store=FakeClimBaselineStore(),
        adapter=FakeStationDataSource([obs(c.id, 0, 12.0) for c in configs]),
        qc_rules=_QC_RULES,
        clock=_fixed_clock,
        pipeline_health_store=health,
    )
    return configs, result, health


def _zero_rule_records(health: FakePipelineHealthStore):
    """⛔ Filter by CHECK TYPE, not by a detail key — a record of the right
    type carrying the WRONG detail must reach the assertions, not be silently
    filtered out of them (a review found the detail-key filter hid exactly
    that case from the clean-run test)."""
    return [
        r
        for r in health.fetch_recent()
        if r.check_type is PipelineCheckType.OBSERVATION_QC_UNCHECKED
    ]


class TestZeroRuleHealthRecord:
    def test_the_record_names_the_station_parameter_and_inferred_cadence(
        self,
    ) -> None:
        """⛔ The RED assertion, and it is about CONTENT — asserting that *a*
        record exists passes today, because the fetch record is written on
        every run."""
        configs, result, health = _run(spacing_minutes=25)

        assert result.qc_unchecked >= 1, "precondition: the group must go unchecked"

        records = _zero_rule_records(health)
        assert records, "no record identifies any zero-rule group"
        groups = records[0].detail["zero_rule_groups"]
        assert any(
            g["station_id"] == str(configs[0].id)
            and g["parameter"] == "discharge"
            and g["inferred_time_step_seconds"] == 1500.0
            and g["reason"] == "no_rule_declares_it"
            for g in groups
        ), groups

    def test_one_record_per_run_carries_every_affected_group(self) -> None:
        """⛔ THREE stations, so this can actually fail. A one-station fixture
        passes whether the run writes one record or one per group — a review
        found the original test could not distinguish them."""
        configs, _, health = _run(spacing_minutes=25, stations=3)

        records = _zero_rule_records(health)
        assert len(records) == 1, f"expected ONE record per run, got {len(records)}"

        detail = records[0].detail
        groups = detail["zero_rule_groups"]
        assert {g["station_id"] for g in groups} == {str(c.id) for c in configs}, groups
        assert detail["groups_affected"] == 3

    def test_a_clean_run_writes_no_zero_rule_record(self) -> None:
        """No record at all on a clean run — an always-written record is noise,
        and it is what makes T2 a PRESENCE probe."""
        _, result, health = _run(spacing_minutes=10, stations=3)

        assert result.qc_unchecked == 0, "precondition: groups must resolve rules"
        assert _zero_rule_records(health) == []

    def test_the_record_is_a_warning(self) -> None:
        """272 T3 settles the severity."""
        _, _, health = _run(spacing_minutes=25)
        assert _zero_rule_records(health)[0].status is PipelineHealthStatus.WARNING

    def test_a_failing_health_store_does_not_fail_the_run(self) -> None:
        """The property the local helper's try/except exists for: telemetry
        must never fail the run it observes."""

        class _Raising(FakePipelineHealthStore):
            def append_health_record(self, record) -> None:
                raise RuntimeError("health store unavailable")

        _, result, _ = _run(spacing_minutes=25, health_store=_Raising())

        assert result.qc_unchecked >= 1
        assert result.stations_failed == 0
