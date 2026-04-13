from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from sapphire_flow.services.forecast_combination import (
    _BMA_TARGET_MEMBERS,
    build_combined_forecasts,
    combine_ensembles_bma,
    combine_ensembles_pooled,
)
from sapphire_flow.services.run_station_forecast import (
    MultiModelForecastResult,
    StationForecastResult,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    ModelCombinationStrategy,
    NwpCycleSource,
)
from sapphire_flow.types.ids import (
    BMA_MODEL_ID,
    POOLED_MODEL_ID,
    ArtifactId,
    ModelId,
    StationId,
)
from tests.conftest import make_forecast_ensemble

_STATION = StationId(uuid4())
_MODEL_A = ModelId("model-a")
_MODEL_B = ModelId("model-b")
_MODEL_C = ModelId("model-c")
_NOW = ensure_utc(datetime(2025, 6, 1, 6, 0, tzinfo=UTC))


def _clock() -> object:
    return _NOW


def _uuid_seq() -> object:
    ids = [uuid4() for _ in range(20)]
    idx = [0]

    def gen() -> UUID:
        val = ids[idx[0]]
        idx[0] += 1
        return val

    return gen


def _make_result(
    model_id: ModelId,
    params: list[str] | None = None,
    representation: EnsembleRepresentation = EnsembleRepresentation.MEMBERS,
    n_members: int = 5,
) -> StationForecastResult:
    params = params or ["discharge"]
    ensembles = {
        p: make_forecast_ensemble(
            station_id=_STATION,
            representation=representation,
            n_members=n_members,
            n_steps=10,
            parameter=p,
            model_id=model_id,
        )
        for p in params
    }
    return StationForecastResult(
        station_id=_STATION,
        model_id=model_id,
        artifact_id=ArtifactId(uuid4()),
        forecasts=[],
        new_state=None,
        ensembles=ensembles,
    )


def _make_multi(
    results: dict[ModelId, StationForecastResult],
    priorities: dict[ModelId, int] | None = None,
) -> MultiModelForecastResult:
    priorities = priorities or {mid: 1 for mid in results}
    return MultiModelForecastResult(
        station_id=_STATION,
        results=results,
        priorities=priorities,
        primary_model_id=next(iter(results), None),
        failed_models={},
    )


class TestCombineEnsemblesPooled:
    def test_two_members_ensembles_merged(self) -> None:
        ens_a = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=5,
            n_steps=10,
            model_id=_MODEL_A,
        )
        ens_b = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=3,
            n_steps=10,
            model_id=_MODEL_B,
        )
        result = combine_ensembles_pooled(
            {_MODEL_A: {"discharge": ens_a}, _MODEL_B: {"discharge": ens_b}}
        )

        assert "discharge" in result
        combined = result["discharge"]
        assert combined.member_count == 8  # 5 + 3
        assert combined.model_id == POOLED_MODEL_ID

    def test_quantiles_model_skipped(self) -> None:
        ens_members = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=5,
            n_steps=10,
            model_id=_MODEL_A,
        )
        ens_quantiles = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.QUANTILES,
            n_members=9,
            n_steps=10,
            model_id=_MODEL_B,
        )
        result = combine_ensembles_pooled(
            {
                _MODEL_A: {"discharge": ens_members},
                _MODEL_B: {"discharge": ens_quantiles},
            }
        )

        assert "discharge" in result
        combined = result["discharge"]
        # Only 5 members from model A (quantiles model skipped)
        assert combined.member_count == 5

    def test_two_parameters_both_in_result(self) -> None:
        ens_a_q = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=4,
            n_steps=10,
            parameter="discharge",
            model_id=_MODEL_A,
        )
        ens_a_wl = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=4,
            n_steps=10,
            parameter="water_level",
            model_id=_MODEL_A,
        )
        ens_b_q = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=4,
            n_steps=10,
            parameter="discharge",
            model_id=_MODEL_B,
        )
        ens_b_wl = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=4,
            n_steps=10,
            parameter="water_level",
            model_id=_MODEL_B,
        )
        result = combine_ensembles_pooled(
            {
                _MODEL_A: {"discharge": ens_a_q, "water_level": ens_a_wl},
                _MODEL_B: {"discharge": ens_b_q, "water_level": ens_b_wl},
            }
        )

        assert set(result.keys()) == {"discharge", "water_level"}
        assert result["discharge"].member_count == 8
        assert result["water_level"].member_count == 8

    def test_empty_input_returns_empty(self) -> None:
        result = combine_ensembles_pooled({})
        assert result == {}


class TestCombineEnsemblesBma:
    def test_two_models_weighted(self) -> None:
        ens_a = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=50,
            n_steps=10,
            model_id=_MODEL_A,
        )
        ens_b = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=50,
            n_steps=10,
            model_id=_MODEL_B,
        )
        result = combine_ensembles_bma(
            {_MODEL_A: {"discharge": ens_a}, _MODEL_B: {"discharge": ens_b}},
            weights={_MODEL_A: 0.7, _MODEL_B: 0.3},
        )

        assert "discharge" in result
        combined = result["discharge"]
        assert combined.model_id == BMA_MODEL_ID
        assert combined.member_count == _BMA_TARGET_MEMBERS

    def test_member_count_equals_target(self) -> None:
        ens_a = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=50,
            n_steps=5,
            model_id=_MODEL_A,
        )
        ens_b = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=50,
            n_steps=5,
            model_id=_MODEL_B,
        )
        result = combine_ensembles_bma(
            {_MODEL_A: {"discharge": ens_a}, _MODEL_B: {"discharge": ens_b}},
            weights={_MODEL_A: 0.6, _MODEL_B: 0.4},
        )

        assert result["discharge"].member_count == _BMA_TARGET_MEMBERS

    def test_zero_weight_model_excluded(self) -> None:
        ens_a = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=50,
            n_steps=10,
            model_id=_MODEL_A,
        )
        ens_b = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=50,
            n_steps=10,
            model_id=_MODEL_B,
        )
        ens_c = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=50,
            n_steps=10,
            model_id=_MODEL_C,
        )
        result = combine_ensembles_bma(
            {
                _MODEL_A: {"discharge": ens_a},
                _MODEL_B: {"discharge": ens_b},
                _MODEL_C: {"discharge": ens_c},
            },
            weights={_MODEL_A: 0.6, _MODEL_B: 0.4, _MODEL_C: 0.0},
        )

        assert "discharge" in result
        # Total still equals target, model C excluded
        assert result["discharge"].member_count == _BMA_TARGET_MEMBERS

    def test_all_zero_weight_returns_empty(self) -> None:
        ens_a = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=5,
            n_steps=5,
            model_id=_MODEL_A,
        )
        result = combine_ensembles_bma(
            {_MODEL_A: {"discharge": ens_a}},
            weights={_MODEL_A: 0.0},
        )
        assert result == {}

    def test_quantiles_model_skipped(self) -> None:
        ens_members = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=50,
            n_steps=10,
            model_id=_MODEL_A,
        )
        ens_quantiles = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.QUANTILES,
            n_members=9,
            n_steps=10,
            model_id=_MODEL_B,
        )
        # model_b has non-zero weight but only quantiles — should be skipped
        result = combine_ensembles_bma(
            {
                _MODEL_A: {"discharge": ens_members},
                _MODEL_B: {"discharge": ens_quantiles},
            },
            weights={_MODEL_A: 0.7, _MODEL_B: 0.3},
        )

        assert "discharge" in result
        assert result["discharge"].model_id == BMA_MODEL_ID


class TestBuildCombinedForecasts:
    def test_pooled_two_models_returns_forecasts(self) -> None:
        result_a = _make_result(_MODEL_A, n_members=5)
        result_b = _make_result(_MODEL_B, n_members=3)
        multi = _make_multi({_MODEL_A: result_a, _MODEL_B: result_b})

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
        )

        assert len(forecasts) == 1
        fc = forecasts[0]
        assert fc.combination_strategy == "pooled"
        assert set(fc.source_model_ids) == {_MODEL_A, _MODEL_B}  # type: ignore[arg-type]
        assert fc.model_id == POOLED_MODEL_ID
        assert fc.model_artifact_id is None
        assert fc.ensemble.member_count == 8

    def test_pooled_one_combinable_model_returns_empty(self) -> None:
        result_a = _make_result(_MODEL_A, n_members=5)
        # Only one model — not enough to combine
        multi = _make_multi({_MODEL_A: result_a})

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
        )

        assert forecasts == []

    def test_primary_strategy_returns_empty(self) -> None:
        result_a = _make_result(_MODEL_A)
        result_b = _make_result(_MODEL_B)
        multi = _make_multi({_MODEL_A: result_a, _MODEL_B: result_b})

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.PRIMARY,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
        )

        assert forecasts == []

    def test_bma_strategy_returns_forecasts(self) -> None:
        result_a = _make_result(_MODEL_A, n_members=50)
        result_b = _make_result(_MODEL_B, n_members=50)
        multi = _make_multi({_MODEL_A: result_a, _MODEL_B: result_b})

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.BMA,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            weights={_MODEL_A: 0.7, _MODEL_B: 0.3},
        )

        assert len(forecasts) == 1
        fc = forecasts[0]
        assert fc.combination_strategy == "bma"
        assert fc.model_id == BMA_MODEL_ID
        assert fc.ensemble.member_count == _BMA_TARGET_MEMBERS

    def test_bma_without_weights_raises(self) -> None:
        result_a = _make_result(_MODEL_A)
        result_b = _make_result(_MODEL_B)
        multi = _make_multi({_MODEL_A: result_a, _MODEL_B: result_b})

        with pytest.raises(ValueError, match="BMA strategy requires weights"):
            build_combined_forecasts(
                station_id=_STATION,
                multi_result=multi,
                strategy=ModelCombinationStrategy.BMA,
                nwp_cycle_reference_time=_NOW,
                nwp_cycle_source=NwpCycleSource.PRIMARY,
                clock=_clock,  # type: ignore[arg-type]
                uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            )
