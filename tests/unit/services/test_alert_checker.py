from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sapphire_flow.services.alert_checker import (
    _STRATEGY_FALLBACK_WARNED,
    _process_results,
    _resolve_strategy_and_filter,
    check_station_alerts,
)
from sapphire_flow.services.alert_strategy import (
    PooledEnsembleStrategy,
    PrimaryModelStrategy,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import DangerLevelDefinition, StationThreshold
from sapphire_flow.types.enums import (
    AlertSource,
    AlertStatus,
    EnsembleRepresentation,
    ModelCombinationStrategy,
    ThresholdDirection,
    ThresholdSource,
)
from sapphire_flow.types.ids import ModelId, StationId
from tests.conftest import make_alert, make_deployment_config, make_forecast_ensemble
from tests.fakes.fake_stores import FakeAlertStore

_STATION = StationId(uuid4())
_EPOCH = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_TIME_STEP = timedelta(hours=1)


def _clock() -> UtcDatetime:
    return _EPOCH


def _make_danger_level(
    name: str = "Moderate",
    trigger_prob: float = 0.5,
    direction: ThresholdDirection = ThresholdDirection.ABOVE,
) -> DangerLevelDefinition:
    return DangerLevelDefinition(
        name=name,
        display_order=2,
        trigger_probability=trigger_prob,
        resolve_probability=trigger_prob * 0.6,
        min_trigger_duration=timedelta(0),
        min_resolve_duration=timedelta(0),
        direction=direction,
    )


def _make_threshold(
    station_id: StationId = _STATION,
    danger_level: str = "Moderate",
    parameter: str = "discharge",
    value: float = 100.0,
) -> StationThreshold:
    return StationThreshold(
        station_id=station_id,
        danger_level=danger_level,
        parameter=parameter,
        value=value,
        source=ThresholdSource.AUTHORITY,
        created_at=_EPOCH,
        updated_at=_EPOCH,
    )


@pytest.fixture(autouse=True)
def _clear_fallback_warned() -> None:
    _STRATEGY_FALLBACK_WARNED.clear()
    yield  # type: ignore[misc]
    _STRATEGY_FALLBACK_WARNED.clear()


class TestResolveStrategyAndFilter:
    def test_bma_falls_back_to_pooled(self) -> None:
        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        param_ensembles = {
            mid_a: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_a,
            ),
            mid_b: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_b,
            ),
        }
        representations = {EnsembleRepresentation.MEMBERS}

        strategy, effective = _resolve_strategy_and_filter(
            preferred=ModelCombinationStrategy.BMA,
            param_ensembles=param_ensembles,
            representations=representations,
            priorities={},
        )

        assert isinstance(strategy, PooledEnsembleStrategy)
        assert set(effective.keys()) == {mid_a, mid_b}

    def test_consensus_falls_back_to_pooled(self) -> None:
        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        param_ensembles = {
            mid_a: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_a,
            ),
            mid_b: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_b,
            ),
        }
        representations = {EnsembleRepresentation.MEMBERS}

        strategy, effective = _resolve_strategy_and_filter(
            preferred=ModelCombinationStrategy.CONSENSUS,
            param_ensembles=param_ensembles,
            representations=representations,
            priorities={},
        )

        assert isinstance(strategy, PooledEnsembleStrategy)

    def test_pooled_falls_back_to_primary_with_single_model(self) -> None:
        mid = ModelId("only_model")
        param_ensembles = {
            mid: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid,
            ),
        }
        representations = {EnsembleRepresentation.MEMBERS}

        strategy, effective = _resolve_strategy_and_filter(
            preferred=ModelCombinationStrategy.POOLED,
            param_ensembles=param_ensembles,
            representations=representations,
            priorities={},
        )

        # Single model → PrimaryModelStrategy (n_models <= 1)
        assert isinstance(strategy, PrimaryModelStrategy)

    def test_consensus_falls_back_to_primary_with_single_model(self) -> None:
        mid = ModelId("only_model")
        param_ensembles = {
            mid: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid,
            ),
        }
        representations = {EnsembleRepresentation.MEMBERS}

        strategy, _ = _resolve_strategy_and_filter(
            preferred=ModelCombinationStrategy.CONSENSUS,
            param_ensembles=param_ensembles,
            representations=representations,
            priorities={},
        )

        assert isinstance(strategy, PrimaryModelStrategy)

    def test_consensus_falls_back_to_primary_with_mixed_representations(self) -> None:
        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        param_ensembles = {
            mid_a: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_a,
            ),
            mid_b: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.QUANTILES,
                model_id=mid_b,
            ),
        }
        representations = {
            EnsembleRepresentation.MEMBERS,
            EnsembleRepresentation.QUANTILES,
        }

        strategy, effective = _resolve_strategy_and_filter(
            preferred=ModelCombinationStrategy.CONSENSUS,
            param_ensembles=param_ensembles,
            representations=representations,
            priorities={mid_a: 0, mid_b: 1},
        )

        assert isinstance(strategy, PrimaryModelStrategy)
        assert len(effective) == 1

    def test_pooled_falls_back_to_primary_with_mixed_representations(self) -> None:
        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        param_ensembles = {
            mid_a: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_a,
            ),
            mid_b: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.QUANTILES,
                model_id=mid_b,
            ),
        }
        representations = {
            EnsembleRepresentation.MEMBERS,
            EnsembleRepresentation.QUANTILES,
        }

        strategy, effective = _resolve_strategy_and_filter(
            preferred=ModelCombinationStrategy.POOLED,
            param_ensembles=param_ensembles,
            representations=representations,
            priorities={mid_a: 0, mid_b: 1},
        )

        assert isinstance(strategy, PrimaryModelStrategy)
        assert len(effective) == 1

    def test_bma_falls_back_to_primary_with_mixed_representations(self) -> None:
        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        param_ensembles = {
            mid_a: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_a,
            ),
            mid_b: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.QUANTILES,
                model_id=mid_b,
            ),
        }
        representations = {
            EnsembleRepresentation.MEMBERS,
            EnsembleRepresentation.QUANTILES,
        }

        strategy, effective = _resolve_strategy_and_filter(
            preferred=ModelCombinationStrategy.BMA,
            param_ensembles=param_ensembles,
            representations=representations,
            priorities={mid_a: 0, mid_b: 1},
        )

        assert isinstance(strategy, PrimaryModelStrategy)
        assert len(effective) == 1

    def test_primary_always_works(self) -> None:
        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        param_ensembles = {
            mid_a: make_forecast_ensemble(station_id=_STATION, model_id=mid_a),
            mid_b: make_forecast_ensemble(station_id=_STATION, model_id=mid_b),
        }
        representations = {EnsembleRepresentation.MEMBERS}

        strategy, effective = _resolve_strategy_and_filter(
            preferred=ModelCombinationStrategy.PRIMARY,
            param_ensembles=param_ensembles,
            representations=representations,
            priorities={mid_a: 0, mid_b: 1},
        )

        assert isinstance(strategy, PrimaryModelStrategy)

    def test_primary_returns_single_model_ensemble(self) -> None:
        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        param_ensembles = {
            mid_a: make_forecast_ensemble(station_id=_STATION, model_id=mid_a),
            mid_b: make_forecast_ensemble(station_id=_STATION, model_id=mid_b),
        }
        representations = {EnsembleRepresentation.MEMBERS}

        _, effective = _resolve_strategy_and_filter(
            preferred=ModelCombinationStrategy.PRIMARY,
            param_ensembles=param_ensembles,
            representations=representations,
            priorities={mid_a: 0, mid_b: 1},
        )

        assert len(effective) == 1

    def test_fallback_warning_logged_once(self) -> None:
        import structlog.testing

        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        param_ensembles = {
            mid_a: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_a,
            ),
            mid_b: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_b,
            ),
        }
        representations = {EnsembleRepresentation.MEMBERS}

        with structlog.testing.capture_logs() as cap:
            _resolve_strategy_and_filter(
                preferred=ModelCombinationStrategy.BMA,
                param_ensembles=param_ensembles,
                representations=representations,
                priorities={},
            )
            _resolve_strategy_and_filter(
                preferred=ModelCombinationStrategy.BMA,
                param_ensembles=param_ensembles,
                representations=representations,
                priorities={},
            )

        warning_count = sum(
            1 for e in cap if e.get("event") == "alert.strategy_degraded"
        )
        assert warning_count == 1

    def test_fallback_warning_distinguishes_actual(self) -> None:
        """BMA→pooled and BMA→primary produce separate warning keys."""
        import structlog.testing

        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        members_ensembles = {
            mid_a: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_a,
            ),
            mid_b: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_b,
            ),
        }
        mixed_ensembles = {
            mid_a: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_a,
            ),
            mid_b: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.QUANTILES,
                model_id=mid_b,
            ),
        }

        with structlog.testing.capture_logs() as cap:
            _resolve_strategy_and_filter(
                preferred=ModelCombinationStrategy.BMA,
                param_ensembles=members_ensembles,
                representations={EnsembleRepresentation.MEMBERS},
                priorities={mid_a: 0, mid_b: 1},
            )
            _resolve_strategy_and_filter(
                preferred=ModelCombinationStrategy.BMA,
                param_ensembles=mixed_ensembles,
                representations={
                    EnsembleRepresentation.MEMBERS,
                    EnsembleRepresentation.QUANTILES,
                },
                priorities={mid_a: 0, mid_b: 1},
            )

        warning_count = sum(
            1 for e in cap if e.get("event") == "alert.strategy_degraded"
        )
        assert warning_count == 2


class TestCheckStationAlerts:
    def test_skipped_when_forecast_alerts_disabled(self) -> None:
        store = FakeAlertStore()
        config = make_deployment_config(enable_forecast_alerts=False)
        mid = ModelId("m")
        ens = make_forecast_ensemble(station_id=_STATION, model_id=mid)
        all_ensembles = {_STATION: {mid: {"discharge": ens}}}

        check_station_alerts(
            all_ensembles=all_ensembles,
            all_thresholds={},
            danger_levels=[_make_danger_level()],
            all_priorities={},
            config=config,
            alert_store=store,
            clock=_clock,
        )

        assert store.fetch_active_alerts() == []

    def test_skipped_when_threshold_check_mode_not_raw(self) -> None:
        store = FakeAlertStore()
        config = make_deployment_config(
            enable_forecast_alerts=True,
            threshold_check_mode="published",
        )
        mid = ModelId("m")
        ens = make_forecast_ensemble(station_id=_STATION, model_id=mid)
        all_ensembles = {_STATION: {mid: {"discharge": ens}}}

        check_station_alerts(
            all_ensembles=all_ensembles,
            all_thresholds={},
            danger_levels=[_make_danger_level()],
            all_priorities={},
            config=config,
            alert_store=store,
            clock=_clock,
        )

        assert store.fetch_active_alerts() == []

    def test_below_direction_danger_levels_filtered(self) -> None:
        """BELOW-direction danger levels must be silently skipped."""
        store = FakeAlertStore()
        config = make_deployment_config(
            enable_forecast_alerts=True,
            min_operational_ensemble_size=1,
        )
        mid = ModelId("m")
        ens = make_forecast_ensemble(
            station_id=_STATION, model_id=mid, n_members=21, n_steps=3
        )
        all_ensembles = {_STATION: {mid: {"discharge": ens}}}
        threshold = _make_threshold(
            station_id=_STATION, danger_level="LowFlow", value=5.0
        )
        below_level = _make_danger_level(
            name="LowFlow", trigger_prob=0.5, direction=ThresholdDirection.BELOW
        )

        check_station_alerts(
            all_ensembles=all_ensembles,
            all_thresholds={_STATION: [threshold]},
            danger_levels=[below_level],
            all_priorities={},
            config=config,
            alert_store=store,
            clock=_clock,
        )

        assert store.fetch_active_alerts() == []

    def test_multi_parameter_dispatch(self) -> None:
        """Both discharge and water_level ensembles are evaluated."""
        store = FakeAlertStore()
        config = make_deployment_config(
            enable_forecast_alerts=True,
            min_operational_ensemble_size=1,
        )
        mid = ModelId("m")
        ens_q = make_forecast_ensemble(
            station_id=_STATION, model_id=mid, parameter="discharge", n_steps=3
        )
        ens_h = make_forecast_ensemble(
            station_id=_STATION, model_id=mid, parameter="water_level", n_steps=3
        )
        all_ensembles = {_STATION: {mid: {"discharge": ens_q, "water_level": ens_h}}}
        thresholds = [
            _make_threshold(danger_level="DL1", parameter="discharge", value=1.0),
            _make_threshold(danger_level="DL1", parameter="water_level", value=1.0),
        ]
        dl = _make_danger_level(name="DL1", trigger_prob=0.1)

        check_station_alerts(
            all_ensembles=all_ensembles,
            all_thresholds={_STATION: thresholds},
            danger_levels=[dl],
            all_priorities={},
            config=config,
            alert_store=store,
            clock=_clock,
        )

        active = store.fetch_active_alerts(station_id=_STATION)
        assert len(active) == 1
        assert active[0].alert_level == "DL1"

    def test_skipped_when_effective_ensemble_too_small(self) -> None:
        store = FakeAlertStore()
        config = make_deployment_config(
            enable_forecast_alerts=True,
            min_operational_ensemble_size=50,
        )
        mid = ModelId("m")
        ens = make_forecast_ensemble(station_id=_STATION, model_id=mid, n_members=21)
        all_ensembles = {_STATION: {mid: {"discharge": ens}}}
        threshold = _make_threshold(danger_level="DL1", value=1.0)
        dl = _make_danger_level(name="DL1", trigger_prob=0.1)

        check_station_alerts(
            all_ensembles=all_ensembles,
            all_thresholds={_STATION: [threshold]},
            danger_levels=[dl],
            all_priorities={},
            config=config,
            alert_store=store,
            clock=_clock,
        )

        assert store.fetch_active_alerts() == []

    def test_skipped_evaluation_preserves_active_alerts(self) -> None:
        """When ALL parameters are skipped, _process_results is NOT called and
        existing active alerts must be preserved unchanged."""
        store = FakeAlertStore()
        existing = make_alert(
            station_id=_STATION,
            source=AlertSource.FORECAST,
            alert_level="DL3",
            status=AlertStatus.RAISED,
        )
        store.upsert_alert(existing)

        # min size much higher than ensemble size → all parameters skipped
        config = make_deployment_config(
            enable_forecast_alerts=True,
            min_operational_ensemble_size=100,
        )
        mid = ModelId("m")
        ens = make_forecast_ensemble(station_id=_STATION, model_id=mid, n_members=20)
        all_ensembles = {_STATION: {mid: {"discharge": ens}}}
        threshold = _make_threshold(danger_level="DL3", value=1.0)
        dl = _make_danger_level(name="DL3", trigger_prob=0.1)

        check_station_alerts(
            all_ensembles=all_ensembles,
            all_thresholds={_STATION: [threshold]},
            danger_levels=[dl],
            all_priorities={},
            config=config,
            alert_store=store,
            clock=_clock,
        )

        active = store.fetch_active_alerts(
            station_id=_STATION, source=AlertSource.FORECAST
        )
        assert len(active) == 1
        assert active[0].alert_level == "DL3"
        assert active[0].status == AlertStatus.RAISED

    def test_skipped_when_quantile_levels_below_config_minimum(self) -> None:
        # ForecastEnsemble.from_quantiles() enforces a minimum of 7 levels at
        # construction time. Set min_operational_quantile_levels=15 so that a
        # normal 9-level ensemble passes construction but fails the config check.
        store = FakeAlertStore()
        config = make_deployment_config(
            enable_forecast_alerts=True,
            min_operational_quantile_levels=15,
        )
        mid = ModelId("m")
        ens = make_forecast_ensemble(
            station_id=_STATION,
            model_id=mid,
            representation=EnsembleRepresentation.QUANTILES,
        )
        # Default ensemble has 9 quantile levels < config minimum of 15 → skip
        all_ensembles = {_STATION: {mid: {"discharge": ens}}}
        threshold = _make_threshold(danger_level="DL1", value=1.0)
        dl = _make_danger_level(name="DL1", trigger_prob=0.1)

        check_station_alerts(
            all_ensembles=all_ensembles,
            all_thresholds={_STATION: [threshold]},
            danger_levels=[dl],
            all_priorities={},
            config=config,
            alert_store=store,
            clock=_clock,
        )

        assert store.fetch_active_alerts() == []

    def test_quantile_ensemble_not_skipped_at_default_member_threshold(self) -> None:
        """Quantile ensemble is not checked against member count threshold."""
        store = FakeAlertStore()
        config = make_deployment_config(
            enable_forecast_alerts=True,
            min_operational_ensemble_size=21,
            min_operational_quantile_levels=7,
        )
        mid = ModelId("m")
        # 9 quantile levels: passes quantile threshold (≥7)
        ens = make_forecast_ensemble(
            station_id=_STATION,
            model_id=mid,
            representation=EnsembleRepresentation.QUANTILES,
        )
        all_ensembles = {_STATION: {mid: {"discharge": ens}}}
        threshold = _make_threshold(danger_level="DL1", value=1.0)
        dl = _make_danger_level(name="DL1", trigger_prob=0.1)

        # Quantile path uses min_operational_quantile_levels, not member size
        check_station_alerts(
            all_ensembles=all_ensembles,
            all_thresholds={_STATION: [threshold]},
            danger_levels=[dl],
            all_priorities={},
            config=config,
            alert_store=store,
            clock=_clock,
        )


class TestProcessResults:
    def test_exceeded_result_upserts_alert(self) -> None:
        from sapphire_flow.types.domain import ExceedanceResult

        store = FakeAlertStore()
        mid = ModelId("m")
        result = ExceedanceResult(
            station_id=_STATION,
            danger_level="DL1",
            parameter="discharge",
            threshold_value=100.0,
            exceedance_probability=0.8,
            observed_value=None,
            exceeded=True,
            model_ids=(mid,),
            strategy=ModelCombinationStrategy.PRIMARY,
        )
        threshold = _make_threshold(
            danger_level="DL1", parameter="discharge", value=100.0
        )

        _process_results([result], _STATION, {"discharge"}, [threshold], store, _clock)

        active = store.fetch_active_alerts(station_id=_STATION)
        assert len(active) == 1
        assert active[0].alert_level == "DL1"
        assert active[0].status == AlertStatus.RAISED
        assert active[0].trigger_probability == pytest.approx(0.8)

    def test_trigger_probability_populated_from_exceedance(self) -> None:
        from sapphire_flow.types.domain import ExceedanceResult

        store = FakeAlertStore()
        mid = ModelId("m")
        # 17 out of 21 members exceed → exceedance_probability ≈ 0.810
        exceedance_prob = 17 / 21
        result = ExceedanceResult(
            station_id=_STATION,
            danger_level="DL2",
            parameter="discharge",
            threshold_value=50.0,
            exceedance_probability=exceedance_prob,
            observed_value=None,
            exceeded=True,
            model_ids=(mid,),
            strategy=ModelCombinationStrategy.PRIMARY,
        )
        threshold = _make_threshold(
            danger_level="DL2", parameter="discharge", value=50.0
        )

        _process_results([result], _STATION, {"discharge"}, [threshold], store, _clock)

        active = store.fetch_active_alerts(station_id=_STATION)
        assert len(active) == 1
        assert active[0].trigger_probability == pytest.approx(exceedance_prob)

    def test_trigger_probability_max_across_parameters(self) -> None:
        """When two parameters exceed at different probabilities, trigger_probability
        is the max across results for that danger level."""
        from sapphire_flow.types.domain import ExceedanceResult

        store = FakeAlertStore()
        mid = ModelId("m")
        thresholds = [
            _make_threshold(danger_level="DL1", parameter="discharge", value=100.0),
            _make_threshold(danger_level="DL1", parameter="water_level", value=2.0),
        ]
        results = [
            ExceedanceResult(
                station_id=_STATION,
                danger_level="DL1",
                parameter="discharge",
                threshold_value=100.0,
                exceedance_probability=0.6,
                observed_value=None,
                exceeded=True,
                model_ids=(mid,),
                strategy=ModelCombinationStrategy.PRIMARY,
            ),
            ExceedanceResult(
                station_id=_STATION,
                danger_level="DL1",
                parameter="water_level",
                threshold_value=2.0,
                exceedance_probability=0.9,
                observed_value=None,
                exceeded=True,
                model_ids=(mid,),
                strategy=ModelCombinationStrategy.PRIMARY,
            ),
        ]

        _process_results(
            results, _STATION, {"discharge", "water_level"}, thresholds, store, _clock
        )

        active = store.fetch_active_alerts(station_id=_STATION)
        assert len(active) == 1
        # Max of 0.6 and 0.9 → 0.9
        assert active[0].trigger_probability == pytest.approx(0.9)

    def test_previously_raised_alert_resolved_when_not_exceeded(self) -> None:
        store = FakeAlertStore()
        existing = make_alert(
            station_id=_STATION,
            source=AlertSource.FORECAST,
            alert_level="DL1",
            status=AlertStatus.RAISED,
        )
        store.upsert_alert(existing)
        threshold = _make_threshold(
            danger_level="DL1", parameter="discharge", value=100.0
        )

        _process_results([], _STATION, {"discharge"}, [threshold], store, _clock)

        active = store.fetch_active_alerts(
            station_id=_STATION, source=AlertSource.FORECAST
        )
        assert len(active) == 0

    def test_no_resolution_when_no_active_alerts(self) -> None:
        store = FakeAlertStore()
        threshold = _make_threshold(
            danger_level="DL1", parameter="discharge", value=100.0
        )

        _process_results([], _STATION, {"discharge"}, [threshold], store, _clock)

        assert store.fetch_active_alerts() == []

    def test_cross_parameter_no_false_resolution(self) -> None:
        """discharge not exceeded but water_level exceeded → DL3 alert NOT resolved."""
        from sapphire_flow.types.domain import ExceedanceResult

        store = FakeAlertStore()
        mid = ModelId("m")
        existing = make_alert(
            station_id=_STATION,
            source=AlertSource.FORECAST,
            alert_level="DL3",
            status=AlertStatus.RAISED,
        )
        store.upsert_alert(existing)

        thresholds = [
            _make_threshold(danger_level="DL3", parameter="discharge", value=100.0),
            _make_threshold(danger_level="DL3", parameter="water_level", value=2.0),
        ]
        # water_level exceeded at DL3
        result = ExceedanceResult(
            station_id=_STATION,
            danger_level="DL3",
            parameter="water_level",
            threshold_value=2.0,
            exceedance_probability=0.9,
            observed_value=None,
            exceeded=True,
            model_ids=(mid,),
            strategy=ModelCombinationStrategy.PRIMARY,
        )

        _process_results(
            [result],
            _STATION,
            {"discharge", "water_level"},
            thresholds,
            store,
            _clock,
        )

        active = store.fetch_active_alerts(station_id=_STATION)
        assert any(a.alert_level == "DL3" for a in active)

    def test_partial_model_failure_preserves_alert(self) -> None:
        """water_level evaluated (discharge absent) → alert preserved."""

        store = FakeAlertStore()
        existing = make_alert(
            station_id=_STATION,
            source=AlertSource.FORECAST,
            alert_level="DL3",
            status=AlertStatus.RAISED,
        )
        store.upsert_alert(existing)

        # Only discharge threshold configured; water_level not configured
        thresholds = [
            _make_threshold(danger_level="DL3", parameter="discharge", value=100.0),
        ]
        # water_level NOT evaluated — discharge missing → can't resolve

        # No results for DL3
        _process_results(
            [],
            _STATION,
            {"water_level"},  # discharge not evaluated
            thresholds,
            store,
            _clock,
        )

        active = store.fetch_active_alerts(station_id=_STATION)
        assert any(a.alert_level == "DL3" for a in active)

    def test_model_ids_union_across_parameters(self) -> None:
        """discharge (A,B) + water_level (B,C) → upsert with (A,B,C)."""
        from sapphire_flow.types.domain import ExceedanceResult

        store = FakeAlertStore()
        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        mid_c = ModelId("model_c")
        thresholds = [
            _make_threshold(danger_level="DL1", parameter="discharge", value=100.0),
            _make_threshold(danger_level="DL1", parameter="water_level", value=2.0),
        ]
        results = [
            ExceedanceResult(
                station_id=_STATION,
                danger_level="DL1",
                parameter="discharge",
                threshold_value=100.0,
                exceedance_probability=0.9,
                observed_value=None,
                exceeded=True,
                model_ids=(mid_a, mid_b),
                strategy=ModelCombinationStrategy.POOLED,
            ),
            ExceedanceResult(
                station_id=_STATION,
                danger_level="DL1",
                parameter="water_level",
                threshold_value=2.0,
                exceedance_probability=0.8,
                observed_value=None,
                exceeded=True,
                model_ids=(mid_b, mid_c),
                strategy=ModelCombinationStrategy.POOLED,
            ),
        ]

        _process_results(
            results,
            _STATION,
            {"discharge", "water_level"},
            thresholds,
            store,
            _clock,
        )

        active = store.fetch_active_alerts(station_id=_STATION)
        assert len(active) == 1
        assert set(active[0].model_ids) == {mid_a, mid_b, mid_c}

    def test_alert_level_danger_level_field_mapping(self) -> None:
        """Alert.alert_level must equal ExceedanceResult.danger_level (which equals
        DangerLevelDefinition.name used to produce that result)."""
        from sapphire_flow.types.domain import ExceedanceResult

        store = FakeAlertStore()
        mid = ModelId("m")
        danger_level_name = "DL2"
        result = ExceedanceResult(
            station_id=_STATION,
            danger_level=danger_level_name,
            parameter="discharge",
            threshold_value=100.0,
            exceedance_probability=0.75,
            observed_value=None,
            exceeded=True,
            model_ids=(mid,),
            strategy=ModelCombinationStrategy.PRIMARY,
        )
        threshold = _make_threshold(
            danger_level=danger_level_name, parameter="discharge", value=100.0
        )

        _process_results([result], _STATION, {"discharge"}, [threshold], store, _clock)

        active = store.fetch_active_alerts(station_id=_STATION)
        assert len(active) == 1
        assert active[0].alert_level == danger_level_name

    def test_model_ids_sorted_deterministically(self) -> None:
        """model_ids on the alert are sorted lexicographically."""
        from sapphire_flow.types.domain import ExceedanceResult

        store = FakeAlertStore()
        mid_c = ModelId("model_c")
        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        threshold = _make_threshold(
            danger_level="DL1", parameter="discharge", value=100.0
        )
        result = ExceedanceResult(
            station_id=_STATION,
            danger_level="DL1",
            parameter="discharge",
            threshold_value=100.0,
            exceedance_probability=0.9,
            observed_value=None,
            exceeded=True,
            model_ids=(mid_c, mid_a, mid_b),
            strategy=ModelCombinationStrategy.POOLED,
        )

        _process_results([result], _STATION, {"discharge"}, [threshold], store, _clock)

        active = store.fetch_active_alerts(station_id=_STATION)
        assert active[0].model_ids == (mid_a, mid_b, mid_c)


class TestResolveStrategyAndFilterPooled:
    def test_pooled_two_models_homogeneous_members_uses_pooled_strategy(self) -> None:
        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        param_ensembles = {
            mid_a: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_a,
            ),
            mid_b: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_b,
            ),
        }
        representations = {EnsembleRepresentation.MEMBERS}

        strategy, effective = _resolve_strategy_and_filter(
            preferred=ModelCombinationStrategy.POOLED,
            param_ensembles=param_ensembles,
            representations=representations,
            priorities={mid_a: 0, mid_b: 1},
        )

        assert isinstance(strategy, PooledEnsembleStrategy)
        assert set(effective.keys()) == {mid_a, mid_b}

    def test_pooled_does_not_fall_back_to_primary_with_homogeneous_members(
        self,
    ) -> None:
        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        param_ensembles = {
            mid_a: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_a,
            ),
            mid_b: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_b,
            ),
        }
        representations = {EnsembleRepresentation.MEMBERS}

        strategy, _ = _resolve_strategy_and_filter(
            preferred=ModelCombinationStrategy.POOLED,
            param_ensembles=param_ensembles,
            representations=representations,
            priorities={},
        )

        assert not isinstance(strategy, PrimaryModelStrategy)


class TestResolveStrategyAndFilterUnknown:
    def test_unknown_strategy_raises_value_error(self) -> None:
        mid = ModelId("model_a")
        mid_b = ModelId("model_b")
        param_ensembles = {
            mid: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid,
            ),
            mid_b: make_forecast_ensemble(
                station_id=_STATION,
                representation=EnsembleRepresentation.MEMBERS,
                model_id=mid_b,
            ),
        }
        representations = {EnsembleRepresentation.MEMBERS}

        with pytest.raises(ValueError, match="Unhandled strategy"):
            _resolve_strategy_and_filter(
                preferred=99,  # type: ignore[arg-type]
                param_ensembles=param_ensembles,
                representations=representations,
                priorities={},
            )


class TestCheckStationAlertsMultiStation:
    def test_multi_station_dispatch_checks_each_independently(self) -> None:
        from pathlib import Path

        from sapphire_flow.tools.record_fixtures import parse_stations_toml

        station_configs = parse_stations_toml(
            Path(__file__).parent.parent.parent / "fixtures/reference/stations.toml"
        )
        sid_a = station_configs[0].id
        sid_b = station_configs[1].id

        store = FakeAlertStore()
        config = make_deployment_config(
            enable_forecast_alerts=True,
            threshold_check_mode="raw",
            min_operational_ensemble_size=1,
        )
        mid = ModelId("m")

        # Both stations have forecasts well above their thresholds (trigger_prob=0.1)
        ens_a = make_forecast_ensemble(
            station_id=sid_a, model_id=mid, n_members=21, n_steps=3
        )
        ens_b = make_forecast_ensemble(
            station_id=sid_b, model_id=mid, n_members=21, n_steps=3
        )
        all_ensembles = {
            sid_a: {mid: {"discharge": ens_a}},
            sid_b: {mid: {"discharge": ens_b}},
        }
        # Thresholds set very low (1.0 m3/s) so all members exceed them
        all_thresholds = {
            sid_a: [_make_threshold(station_id=sid_a, danger_level="DL1", value=1.0)],
            sid_b: [_make_threshold(station_id=sid_b, danger_level="DL1", value=1.0)],
        }
        dl = _make_danger_level(name="DL1", trigger_prob=0.1)

        check_station_alerts(
            all_ensembles=all_ensembles,
            all_thresholds=all_thresholds,
            danger_levels=[dl],
            all_priorities={},
            config=config,
            alert_store=store,
            clock=_clock,
        )

        active_a = store.fetch_active_alerts(station_id=sid_a)
        active_b = store.fetch_active_alerts(station_id=sid_b)
        assert len(active_a) == 1
        assert len(active_b) == 1
        assert active_a[0].station_id == sid_a
        assert active_b[0].station_id == sid_b
