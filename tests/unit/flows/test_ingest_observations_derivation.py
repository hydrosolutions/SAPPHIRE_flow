from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from sapphire_flow.flows.ingest_observations import ingest_observations_flow
from sapphire_flow.types.calculated_station import ComponentWeight
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import (
    GaugingStatus,
    ObservationSource,
    QcStatus,
    StationStatus,
)
from sapphire_flow.types.ids import FormulaId, StationId
from sapphire_flow.types.observation import Observation, RawObservation
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from sapphire_flow.types.station import StationConfig
from tests.fakes.fake_adapters import FakeStationDataSource
from tests.fakes.fake_stores import (
    FakeClimBaselineStore,
    FakeFormulaStore,
    FakeObservationStore,
    FakeStationStore,
)

_NOW = ensure_utc(datetime(2026, 4, 8, 14, 20, tzinfo=UTC))

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
        QcRuleParams(
            rule_id="rate_of_change",
            rule_version="1.0",
            parameter="discharge",
            time_step=timedelta(seconds=600),
            thresholds={"max_rate": 50.0},
        ),
    ),
)


def _clock() -> UtcDatetime:
    return _NOW


def _raw(
    station: StationId,
    value: float,
    *,
    source: ObservationSource = ObservationSource.MEASURED,
    offset_minutes: int = 0,
) -> RawObservation:
    return RawObservation(
        station_id=station,
        timestamp=ensure_utc(_NOW - timedelta(minutes=offset_minutes)),
        parameter="discharge",
        value=value,
        source=source,
    )


def _weight(calc: StationId, component: StationId, weight: float) -> ComponentWeight:
    return ComponentWeight(
        id=FormulaId(uuid.uuid4()),
        calculated_station_id=calc,
        component_station_id=component,
        parameter="discharge",
        weight=weight,
        effective_from=ensure_utc(_NOW - timedelta(days=365)),
        effective_to=None,
        created_at=ensure_utc(_NOW - timedelta(days=365)),
    )


def _seed_history_qc_passed(
    obs_store: FakeObservationStore, station: StationId, v: float
) -> None:
    # A prior QC_PASSED point so rate_of_change has a reference and a time_step.
    obs_store.store_raw_observations([_raw(station, v, offset_minutes=10)])
    for o in obs_store.observations():
        if o.station_id == station and o.qc_status == QcStatus.RAW:
            obs_store.update_qc(o.id, QcStatus.QC_PASSED, [])


def _derived_for(obs_store: FakeObservationStore, calc: StationId) -> list[Observation]:
    return [
        o
        for o in obs_store.observations()
        if o.station_id == calc and o.source == ObservationSource.COMPONENT_DERIVED
    ]


def _run(
    stations: list[StationConfig],
    formulas: list[ComponentWeight],
    raw_obs: list[RawObservation],
    obs_store: FakeObservationStore,
):
    station_store = FakeStationStore()
    for s in stations:
        station_store.store_station(s)
    formula_store = FakeFormulaStore()
    if formulas:
        formula_store.store_formula(formulas)
    return ingest_observations_flow(
        station_store=station_store,
        obs_store=obs_store,
        baseline_store=FakeClimBaselineStore(),
        adapter=FakeStationDataSource(raw_obs),
        qc_rules=_QC_RULES,
        clock=_clock,
        formula_store=formula_store,
    )


class TestCalculatedStationDerivation:
    def test_derives_weighted_sum(self) -> None:
        c1 = make_station_config(station_id=StationId(uuid.uuid4()), code="C1")
        c2 = make_station_config(station_id=StationId(uuid.uuid4()), code="C2")
        calc = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="CALC",
            gauging_status=GaugingStatus.CALCULATED,
        )
        obs_store = FakeObservationStore()
        _seed_history_qc_passed(obs_store, c1.id, 9.8)
        _seed_history_qc_passed(obs_store, c2.id, 19.5)

        result = _run(
            [c1, c2, calc],
            [_weight(calc.id, c1.id, 0.6), _weight(calc.id, c2.id, 0.4)],
            [_raw(c1.id, 10.0), _raw(c2.id, 20.0)],
            obs_store,
        )

        assert result.observations_derived == 1
        assert result.observations_missing == 0
        derived = _derived_for(obs_store, calc.id)
        assert len(derived) == 1
        assert derived[0].value == pytest.approx(0.6 * 10.0 + 0.4 * 20.0)
        assert derived[0].qc_status == QcStatus.QC_PASSED
        assert derived[0].timestamp == _NOW

    def test_missing_component_writes_missing_placeholder(self) -> None:
        c1 = make_station_config(station_id=StationId(uuid.uuid4()), code="C1")
        c2 = make_station_config(station_id=StationId(uuid.uuid4()), code="C2")
        calc = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="CALC",
            gauging_status=GaugingStatus.CALCULATED,
        )
        obs_store = FakeObservationStore()
        _seed_history_qc_passed(obs_store, c1.id, 9.8)

        # Only c1 reports this run; c2 has no observation at _NOW.
        result = _run(
            [c1, c2, calc],
            [_weight(calc.id, c1.id, 0.6), _weight(calc.id, c2.id, 0.4)],
            [_raw(c1.id, 10.0)],
            obs_store,
        )

        assert result.observations_derived == 0
        assert result.observations_missing == 1
        derived = _derived_for(obs_store, calc.id)
        assert len(derived) == 1
        assert derived[0].value is None
        assert derived[0].qc_status == QcStatus.MISSING

    def test_source_precedence_prefers_measured(self) -> None:
        c1 = make_station_config(station_id=StationId(uuid.uuid4()), code="C1")
        c2 = make_station_config(station_id=StationId(uuid.uuid4()), code="C2")
        calc = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="CALC",
            gauging_status=GaugingStatus.CALCULATED,
        )
        obs_store = FakeObservationStore()
        _seed_history_qc_passed(obs_store, c1.id, 9.8)
        _seed_history_qc_passed(obs_store, c2.id, 19.5)
        # A competing manual_import row for c1 at _NOW, already QC_PASSED with a
        # different value. Precedence must pick the measured row, not this one.
        obs_store.store_raw_observations(
            [_raw(c1.id, 999.0, source=ObservationSource.MANUAL_IMPORT)]
        )
        for o in obs_store.observations():
            if (
                o.source == ObservationSource.MANUAL_IMPORT
                and o.qc_status == QcStatus.RAW
            ):
                obs_store.update_qc(o.id, QcStatus.QC_PASSED, [])

        result = _run(
            [c1, c2, calc],
            [_weight(calc.id, c1.id, 0.6), _weight(calc.id, c2.id, 0.4)],
            [_raw(c1.id, 10.0), _raw(c2.id, 20.0)],
            obs_store,
        )

        assert result.observations_derived == 1
        derived = _derived_for(obs_store, calc.id)
        # measured 10.0 → 14.0; if manual 999 had won it would be ~605.8.
        assert derived[0].value == pytest.approx(14.0)

    def test_suspect_component_propagates_suspect(self) -> None:
        c1 = make_station_config(station_id=StationId(uuid.uuid4()), code="C1")
        c2 = make_station_config(station_id=StationId(uuid.uuid4()), code="C2")
        calc = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="CALC",
            gauging_status=GaugingStatus.CALCULATED,
        )
        obs_store = FakeObservationStore()
        _seed_history_qc_passed(obs_store, c1.id, 9.8)
        # c2 reported earlier and is stored QC_SUSPECT at _NOW; it does NOT report this
        # run, so QC does not re-process it. Derivation must read it and propagate.
        obs_store.store_raw_observations([_raw(c2.id, 20.0)])
        for o in obs_store.observations():
            if o.station_id == c2.id and o.qc_status == QcStatus.RAW:
                obs_store.update_qc(o.id, QcStatus.QC_SUSPECT, [])

        result = _run(
            [c1, c2, calc],
            [_weight(calc.id, c1.id, 0.6), _weight(calc.id, c2.id, 0.4)],
            [_raw(c1.id, 10.0)],
            obs_store,
        )

        assert result.observations_derived == 1
        derived = _derived_for(obs_store, calc.id)
        assert derived[0].value == pytest.approx(14.0)
        assert derived[0].qc_status == QcStatus.QC_SUSPECT

    def test_ineligible_suspended_component_writes_missing(self) -> None:
        c1 = make_station_config(station_id=StationId(uuid.uuid4()), code="C1")
        # c2 is gauged but SUSPENDED → read-time check treats it as missing.
        c2 = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="C2",
            station_status=StationStatus.SUSPENDED,
        )
        calc = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="CALC",
            gauging_status=GaugingStatus.CALCULATED,
        )
        obs_store = FakeObservationStore()
        _seed_history_qc_passed(obs_store, c1.id, 9.8)
        _seed_history_qc_passed(obs_store, c2.id, 19.5)

        result = _run(
            [c1, c2, calc],
            [_weight(calc.id, c1.id, 0.6), _weight(calc.id, c2.id, 0.4)],
            [_raw(c1.id, 10.0), _raw(c2.id, 20.0)],
            obs_store,
        )

        assert result.observations_derived == 0
        assert result.observations_missing == 1
        assert _derived_for(obs_store, calc.id)[0].qc_status == QcStatus.MISSING

    def test_no_formula_store_is_noop(self) -> None:
        c1 = make_station_config(station_id=StationId(uuid.uuid4()), code="C1")
        calc = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="CALC",
            gauging_status=GaugingStatus.CALCULATED,
        )
        obs_store = FakeObservationStore()
        _seed_history_qc_passed(obs_store, c1.id, 9.8)
        station_store = FakeStationStore()
        for s in (c1, calc):
            station_store.store_station(s)

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=obs_store,
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource([_raw(c1.id, 10.0)]),
            qc_rules=_QC_RULES,
            clock=_clock,
            formula_store=None,
        )

        assert result.observations_derived == 0
        assert _derived_for(obs_store, calc.id) == []

    def test_eligibility_is_reread_at_derivation_time(self) -> None:
        # A component operational at step 2.0 but suspended by the time step 2.5 runs
        # must be caught by the derivation-time re-read (Flow 2 is AUTOCOMMIT), not the
        # stale flow-start snapshot. The store reports c2 operational for the kind-
        # filtered step-2.0 reads and suspended for the no-kind derivation re-read.
        c1 = make_station_config(station_id=StationId(uuid.uuid4()), code="C1")
        c2 = make_station_config(station_id=StationId(uuid.uuid4()), code="C2")
        calc = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="CALC",
            gauging_status=GaugingStatus.CALCULATED,
        )

        class _SuspendOnRereadStore(FakeStationStore):
            def fetch_all_stations(self, kind: object = None) -> list[StationConfig]:
                stations = super().fetch_all_stations(kind)  # type: ignore[arg-type]
                if kind is None:  # the derivation-time re-read
                    return [
                        replace(s, station_status=StationStatus.SUSPENDED)
                        if s.id == c2.id
                        else s
                        for s in stations
                    ]
                return stations

        station_store = _SuspendOnRereadStore()
        for s in (c1, c2, calc):
            station_store.store_station(s)
        formula_store = FakeFormulaStore()
        formula_store.store_formula(
            [_weight(calc.id, c1.id, 0.6), _weight(calc.id, c2.id, 0.4)]
        )
        obs_store = FakeObservationStore()
        _seed_history_qc_passed(obs_store, c1.id, 9.8)
        _seed_history_qc_passed(obs_store, c2.id, 19.5)

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=obs_store,
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource([_raw(c1.id, 10.0), _raw(c2.id, 20.0)]),
            qc_rules=_QC_RULES,
            clock=_clock,
            formula_store=formula_store,
        )

        assert result.observations_derived == 0
        assert result.observations_missing == 1
        assert _derived_for(obs_store, calc.id)[0].qc_status == QcStatus.MISSING
