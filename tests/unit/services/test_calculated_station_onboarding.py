from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from sapphire_flow.config.onboarding import CalculatedStationSpec, ComponentSpec
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.calculated_station_onboarding import (
    onboard_calculated_station,
)
from sapphire_flow.services.onboarding import _run_onboarding
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import (
    GaugingStatus,
    ObservationSource,
    QcStatus,
    StationStatus,
)
from sapphire_flow.types.ids import StationId, TenantId
from sapphire_flow.types.observation import RawObservation
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.conftest import make_station_config
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeClimBaselineStore,
    FakeFlowRegimeConfigStore,
    FakeFormulaStore,
    FakeHistoricalForcingStore,
    FakeObservationStore,
    FakeStationStore,
)

_EPOCH = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
_WINDOW_START = ensure_utc(datetime(2000, 1, 1, tzinfo=UTC))
_WINDOW_END = ensure_utc(datetime(2030, 1, 1, tzinfo=UTC))


def _clock() -> UtcDatetime:
    return _EPOCH


def _day(i: int) -> UtcDatetime:
    return ensure_utc(_EPOCH + timedelta(days=i))


def _seed_component(
    station_store: FakeStationStore,
    obs_store: FakeObservationStore,
    code: str,
    values: dict[int, float],
    *,
    gauging: GaugingStatus = GaugingStatus.GAUGED,
    status: StationStatus = StationStatus.OPERATIONAL,
    source: ObservationSource = ObservationSource.MEASURED,
    qc: QcStatus = QcStatus.QC_PASSED,
    network: str = "bafu",
) -> StationId:
    station = make_station_config(
        station_id=StationId(uuid.uuid4()),
        code=code,
        network=network,
        gauging_status=gauging,
        station_status=status,
    )
    station_store.store_station(station)
    raws = [
        RawObservation(
            station_id=station.id,
            timestamp=_day(i),
            parameter="discharge",
            value=v,
            source=source,
        )
        for i, v in values.items()
    ]
    obs_store.store_raw_observations(raws)
    for o in obs_store.observations():
        if o.station_id == station.id and o.qc_status == QcStatus.RAW:
            obs_store.update_qc(o.id, qc, [])
    return station.id


def _spec(
    components: list[ComponentSpec],
    *,
    code: str = "CALC-1",
    effective_from: str | None = None,
    network: str = "bafu",
) -> CalculatedStationSpec:
    return CalculatedStationSpec(
        code=code,
        name="Calc Station",
        network=network,
        parameter="discharge",
        lon=8.5,
        lat=47.4,
        components=tuple(components),
        effective_from=effective_from,
    )


def _qc_rules() -> QcRuleSet:
    return QcRuleSet(
        version="test",
        rules=(
            QcRuleParams(
                rule_id="range_check",
                rule_version="1.0",
                parameter="discharge",
                time_step=timedelta(days=1),
                thresholds={"value_min": 0.0, "value_max": 10000.0},
            ),
        ),
    )


def _derived(obs_store: FakeObservationStore, calc_id: StationId) -> list:
    return sorted(
        (
            o
            for o in obs_store.observations()
            if o.station_id == calc_id
            and o.source == ObservationSource.COMPONENT_DERIVED
        ),
        key=lambda o: o.timestamp,
    )


class TestOnboardCalculatedStationTenant:
    """Plan 147 Slice A: the resolved tenant is threaded explicitly into the
    calculated station's StationConfig — not silently defaulted."""

    def test_stamps_the_explicit_tenant_on_the_calculated_station(self) -> None:
        ss, os_ = FakeStationStore(), FakeObservationStore()
        fs = FakeFormulaStore()
        _seed_component(ss, os_, "GAUGE-A", {0: 10.0, 1: 11.0})
        _seed_component(ss, os_, "GAUGE-B", {0: 20.0, 1: 21.0})
        other_tenant = TenantId(uuid.uuid4())

        outcome = onboard_calculated_station(
            _spec(
                [
                    ComponentSpec(code="GAUGE-A", weight=0.5),
                    ComponentSpec(code="GAUGE-B", weight=0.5),
                ]
            ),
            None,
            ss,
            os_,
            fs,
            _clock,
            _WINDOW_START,
            _WINDOW_END,
            tenant_id=other_tenant,
        )

        assert outcome.station.tenant_id == other_tenant

    def test_run_onboarding_threads_tenant_to_calculated_station(self) -> None:
        ss, os_ = FakeStationStore(), FakeObservationStore()
        fs = FakeFormulaStore()
        _seed_component(ss, os_, "GAUGE-A", {0: 10.0, 1: 11.0})
        _seed_component(ss, os_, "GAUGE-B", {0: 20.0, 1: 21.0})
        other_tenant = TenantId(uuid.uuid4())

        _run_onboarding(
            stations=[],
            basins=[],
            obs_by_station={},
            forcing_by_station={},
            basin_store=FakeBasinStore(),
            station_store=ss,
            obs_store=os_,
            forcing_store=FakeHistoricalForcingStore(),
            baseline_store=FakeClimBaselineStore(),
            flow_regime_store=FakeFlowRegimeConfigStore(),
            qc_rules=_qc_rules(),
            clock=_clock,
            start_utc=_WINDOW_START,
            end_utc=_WINDOW_END,
            formula_store=fs,
            calculated_specs=[
                _spec(
                    [
                        ComponentSpec(code="GAUGE-A", weight=0.5),
                        ComponentSpec(code="GAUGE-B", weight=0.5),
                    ]
                )
            ],
            tenant_id=other_tenant,
        )

        calc = ss.fetch_station_by_code("CALC-1", "bafu")
        assert calc is not None
        assert calc.tenant_id == other_tenant

    def test_defaults_to_the_sapphire_tenant_when_unset(self) -> None:
        ss, os_ = FakeStationStore(), FakeObservationStore()
        fs = FakeFormulaStore()
        _seed_component(ss, os_, "GAUGE-A", {0: 10.0, 1: 11.0})
        _seed_component(ss, os_, "GAUGE-B", {0: 20.0, 1: 21.0})

        outcome = onboard_calculated_station(
            _spec(
                [
                    ComponentSpec(code="GAUGE-A", weight=0.5),
                    ComponentSpec(code="GAUGE-B", weight=0.5),
                ]
            ),
            None,
            ss,
            os_,
            fs,
            _clock,
            _WINDOW_START,
            _WINDOW_END,
        )

        assert outcome.station.tenant_id == DEFAULT_TENANT_ID


class TestOnboardCalculatedStation:
    def test_configures_formula_and_bootstraps_history(self) -> None:
        ss, os_ = FakeStationStore(), FakeObservationStore()
        fs = FakeFormulaStore()
        a = _seed_component(ss, os_, "GAUGE-A", {0: 10.0, 1: 11.0, 2: 12.0})
        b = _seed_component(ss, os_, "GAUGE-B", {0: 20.0, 1: 21.0, 2: 22.0})

        outcome = onboard_calculated_station(
            _spec(
                [
                    ComponentSpec(code="GAUGE-A", weight=0.6),
                    ComponentSpec(code="GAUGE-B", weight=0.4),
                ]
            ),
            None,
            ss,
            os_,
            fs,
            _clock,
            _WINDOW_START,
            _WINDOW_END,
        )

        assert outcome.station.gauging_status == GaugingStatus.CALCULATED
        assert outcome.station.station_status == StationStatus.ONBOARDING
        assert outcome.formula_configured is True
        assert outcome.observations_derived == 3
        assert outcome.observations_missing == 0
        # formula stored, current, parameter-scoped
        current = fs.fetch_current_formula(outcome.station.id, "discharge")
        assert {w.component_station_id for w in current} == {a, b}
        # effective_from defaulted to the earliest component observation (day 0)
        assert min(w.effective_from for w in current) == _day(0)
        derived = _derived(os_, outcome.station.id)
        assert [o.value for o in derived] == pytest.approx(
            [0.6 * 10 + 0.4 * 20, 0.6 * 11 + 0.4 * 21, 0.6 * 12 + 0.4 * 22]
        )
        assert all(o.qc_status == QcStatus.QC_PASSED for o in derived)

    def test_effective_from_override_leaves_earlier_underived(self) -> None:
        # Plan 015 5.C3: timestamps before effective_from are left underived.
        ss, os_ = FakeStationStore(), FakeObservationStore()
        fs = FakeFormulaStore()
        _seed_component(ss, os_, "GAUGE-A", {0: 10.0, 1: 11.0, 2: 12.0, 3: 13.0})
        _seed_component(ss, os_, "GAUGE-B", {0: 20.0, 1: 21.0, 2: 22.0, 3: 23.0})

        outcome = onboard_calculated_station(
            _spec(
                [
                    ComponentSpec(code="GAUGE-A", weight=1.0),
                    ComponentSpec(code="GAUGE-B", weight=1.0),
                ],
                effective_from=_day(2).isoformat(),
            ),
            None,
            ss,
            os_,
            fs,
            _clock,
            _WINDOW_START,
            _WINDOW_END,
        )

        derived = _derived(os_, outcome.station.id)
        # only day 2 and day 3 are covered by the formula validity window
        assert [o.timestamp for o in derived] == [_day(2), _day(3)]
        assert outcome.observations_derived == 2

    def test_missing_component_writes_missing_placeholder(self) -> None:
        ss, os_ = FakeStationStore(), FakeObservationStore()
        fs = FakeFormulaStore()
        _seed_component(ss, os_, "GAUGE-A", {0: 10.0, 1: 11.0})
        _seed_component(ss, os_, "GAUGE-B", {0: 20.0})  # missing day 1

        outcome = onboard_calculated_station(
            _spec(
                [
                    ComponentSpec(code="GAUGE-A", weight=1.0),
                    ComponentSpec(code="GAUGE-B", weight=1.0),
                ]
            ),
            None,
            ss,
            os_,
            fs,
            _clock,
            _WINDOW_START,
            _WINDOW_END,
        )

        assert outcome.observations_derived == 1
        assert outcome.observations_missing == 1
        derived = _derived(os_, outcome.station.id)
        by_ts = {o.timestamp: o for o in derived}
        assert by_ts[_day(0)].value == pytest.approx(30.0)
        assert by_ts[_day(1)].value is None
        assert by_ts[_day(1)].qc_status == QcStatus.MISSING

    def test_source_precedence_prefers_measured(self) -> None:
        ss, os_ = FakeStationStore(), FakeObservationStore()
        fs = FakeFormulaStore()
        a = _seed_component(ss, os_, "GAUGE-A", {0: 10.0})
        _seed_component(ss, os_, "GAUGE-B", {0: 20.0})
        # a competing manual_import row for A at day 0, QC_PASSED, different value
        os_.store_raw_observations(
            [
                RawObservation(
                    station_id=a,
                    timestamp=_day(0),
                    parameter="discharge",
                    value=999.0,
                    source=ObservationSource.MANUAL_IMPORT,
                )
            ]
        )
        for o in os_.observations():
            if (
                o.source == ObservationSource.MANUAL_IMPORT
                and o.qc_status == QcStatus.RAW
            ):
                os_.update_qc(o.id, QcStatus.QC_PASSED, [])

        outcome = onboard_calculated_station(
            _spec(
                [
                    ComponentSpec(code="GAUGE-A", weight=1.0),
                    ComponentSpec(code="GAUGE-B", weight=1.0),
                ]
            ),
            None,
            ss,
            os_,
            fs,
            _clock,
            _WINDOW_START,
            _WINDOW_END,
        )
        assert _derived(os_, outcome.station.id)[0].value == pytest.approx(30.0)

    def test_rejects_missing_component(self) -> None:
        ss, os_ = FakeStationStore(), FakeObservationStore()
        with pytest.raises(ConfigurationError, match="not found"):
            onboard_calculated_station(
                _spec([ComponentSpec(code="NOPE", weight=1.0)]),
                None,
                ss,
                os_,
                FakeFormulaStore(),
                _clock,
                _WINDOW_START,
                _WINDOW_END,
            )

    def test_rejects_ungauged_component(self) -> None:
        ss, os_ = FakeStationStore(), FakeObservationStore()
        _seed_component(ss, os_, "UG", {0: 1.0}, gauging=GaugingStatus.CALCULATED)
        with pytest.raises(ConfigurationError, match="gauged"):
            onboard_calculated_station(
                _spec([ComponentSpec(code="UG", weight=1.0)]),
                None,
                ss,
                os_,
                FakeFormulaStore(),
                _clock,
                _WINDOW_START,
                _WINDOW_END,
            )

    def test_rejects_suspended_component(self) -> None:
        ss, os_ = FakeStationStore(), FakeObservationStore()
        _seed_component(ss, os_, "SUS", {0: 1.0}, status=StationStatus.SUSPENDED)
        with pytest.raises(ConfigurationError, match="operational"):
            onboard_calculated_station(
                _spec([ComponentSpec(code="SUS", weight=1.0)]),
                None,
                ss,
                os_,
                FakeFormulaStore(),
                _clock,
                _WINDOW_START,
                _WINDOW_END,
            )

    def test_rejects_when_nothing_stored_on_validation_failure(self) -> None:
        ss, os_ = FakeStationStore(), FakeObservationStore()
        with pytest.raises(ConfigurationError):
            onboard_calculated_station(
                _spec([ComponentSpec(code="NOPE", weight=1.0)]),
                None,
                ss,
                os_,
                FakeFormulaStore(),
                _clock,
                _WINDOW_START,
                _WINDOW_END,
            )
        # No orphan calc station created
        assert ss.fetch_station_by_code("CALC-1", "bafu") is None

    def test_idempotent_rerun_skips_reconfiguration(self) -> None:
        ss, os_ = FakeStationStore(), FakeObservationStore()
        fs = FakeFormulaStore()
        _seed_component(ss, os_, "GAUGE-A", {0: 10.0})
        _seed_component(ss, os_, "GAUGE-B", {0: 20.0})
        spec = _spec(
            [
                ComponentSpec(code="GAUGE-A", weight=1.0),
                ComponentSpec(code="GAUGE-B", weight=1.0),
            ]
        )

        first = onboard_calculated_station(
            spec, None, ss, os_, fs, _clock, _WINDOW_START, _WINDOW_END
        )
        second = onboard_calculated_station(
            spec, None, ss, os_, fs, _clock, _WINDOW_START, _WINDOW_END
        )

        assert first.formula_configured is True
        assert first.created is True
        assert second.formula_configured is False
        assert second.created is False
        # only one current formula set, no duplicate rows
        assert len(fs.fetch_current_formula(first.station.id, "discharge")) == 2

    def test_rejects_code_collision_with_gauged_station(self) -> None:
        ss, os_ = FakeStationStore(), FakeObservationStore()
        _seed_component(ss, os_, "GAUGE-A", {0: 10.0})
        # a gauged station already owns the calc code "CALC-1"
        ss.store_station(
            make_station_config(
                station_id=StationId(uuid.uuid4()), code="CALC-1", network="bafu"
            )
        )
        with pytest.raises(ConfigurationError, match="non-calculated"):
            onboard_calculated_station(
                _spec([ComponentSpec(code="GAUGE-A", weight=1.0)]),
                None,
                ss,
                os_,
                FakeFormulaStore(),
                _clock,
                _WINDOW_START,
                _WINDOW_END,
            )

    def test_rejects_reconfiguration_with_different_formula(self) -> None:
        ss, os_ = FakeStationStore(), FakeObservationStore()
        fs = FakeFormulaStore()
        _seed_component(ss, os_, "GAUGE-A", {0: 10.0})
        _seed_component(ss, os_, "GAUGE-B", {0: 20.0})
        onboard_calculated_station(
            _spec(
                [
                    ComponentSpec(code="GAUGE-A", weight=1.0),
                    ComponentSpec(code="GAUGE-B", weight=1.0),
                ]
            ),
            None,
            ss,
            os_,
            fs,
            _clock,
            _WINDOW_START,
            _WINDOW_END,
        )
        # re-run with a DIFFERENT weight → must be rejected, not silently accepted
        with pytest.raises(ConfigurationError, match="close it"):
            onboard_calculated_station(
                _spec(
                    [
                        ComponentSpec(code="GAUGE-A", weight=0.5),
                        ComponentSpec(code="GAUGE-B", weight=1.0),
                    ]
                ),
                None,
                ss,
                os_,
                fs,
                _clock,
                _WINDOW_START,
                _WINDOW_END,
            )

    def test_rejects_reconfiguration_with_different_effective_from(self) -> None:
        # Same components + weights but a changed validity start is a reconfiguration.
        ss, os_ = FakeStationStore(), FakeObservationStore()
        fs = FakeFormulaStore()
        _seed_component(ss, os_, "GAUGE-A", {0: 10.0, 1: 11.0})
        _seed_component(ss, os_, "GAUGE-B", {0: 20.0, 1: 21.0})
        comps = [
            ComponentSpec(code="GAUGE-A", weight=1.0),
            ComponentSpec(code="GAUGE-B", weight=1.0),
        ]
        onboard_calculated_station(
            _spec(comps), None, ss, os_, fs, _clock, _WINDOW_START, _WINDOW_END
        )
        with pytest.raises(ConfigurationError, match="close it"):
            onboard_calculated_station(
                _spec(comps, effective_from=_day(1).isoformat()),
                None,
                ss,
                os_,
                fs,
                _clock,
                _WINDOW_START,
                _WINDOW_END,
            )


class TestRunOnboardingCalculatedEndToEnd:
    def test_calculated_station_flows_through_baseline_and_regime_tail(self) -> None:
        # Components pre-exist as gauged+operational with a long history (prior run).
        ss, os_ = FakeStationStore(), FakeObservationStore()
        fs = FakeFormulaStore()
        many = {i: float(10 + i % 40) for i in range(800)}
        many_b = {i: float(20 + i % 40) for i in range(800)}
        _seed_component(ss, os_, "GAUGE-A", many)
        _seed_component(ss, os_, "GAUGE-B", many_b)

        rules = QcRuleSet(
            version="test",
            rules=(
                QcRuleParams(
                    rule_id="range_check",
                    rule_version="1.0",
                    parameter="discharge",
                    time_step=timedelta(days=1),
                    thresholds={"value_min": 0.0, "value_max": 10000.0},
                ),
            ),
        )
        spec = _spec(
            [
                ComponentSpec(code="GAUGE-A", weight=0.6),
                ComponentSpec(code="GAUGE-B", weight=0.4),
            ]
        )

        result = _run_onboarding(
            stations=[],
            basins=[],
            obs_by_station={},
            forcing_by_station={},
            basin_store=FakeBasinStore(),
            station_store=ss,
            obs_store=os_,
            forcing_store=FakeHistoricalForcingStore(),
            baseline_store=FakeClimBaselineStore(),
            flow_regime_store=FakeFlowRegimeConfigStore(),
            qc_rules=rules,
            clock=_clock,
            start_utc=_WINDOW_START,
            end_utc=_WINDOW_END,
            formula_store=fs,
            calculated_specs=[spec],
        )

        # calc station created + formula configured + history bootstrapped
        calc = ss.fetch_station_by_code("CALC-1", "bafu")
        assert calc is not None
        assert calc.gauging_status == GaugingStatus.CALCULATED
        assert len(fs.fetch_current_formula(calc.id, "discharge")) == 2
        assert len(_derived(os_, calc.id)) == 800
        # the existing tail computed baselines + a flow regime for the calc station
        assert result.baselines_computed > 0
        assert result.flow_regimes_computed == 1
