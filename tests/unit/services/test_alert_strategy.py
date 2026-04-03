from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import polars as pl
import pytest

from sapphire_flow.services.alert_strategy import (
    PooledEnsembleStrategy,
    PrimaryModelStrategy,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import (
    DangerLevelDefinition,
    ForecastParameter,
    StationThreshold,
)
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    ThresholdDirection,
    ThresholdSource,
)
from sapphire_flow.types.ids import ModelId, StationId

_STATION = StationId(uuid4())
_EPOCH = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_TIME_STEP = timedelta(hours=1)


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
    parameter: ForecastParameter = "discharge",
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


def _make_members_ensemble(
    station_id: StationId = _STATION,
    model_id: ModelId | None = None,
    n_members: int = 21,
    value: float = 50.0,
    n_steps: int = 3,
) -> ForecastEnsemble:
    """Build a MEMBERS ensemble with all values fixed at `value`."""
    rows = [
        {
            "valid_time": ensure_utc(
                datetime.fromtimestamp(_EPOCH.timestamp() + (s + 1) * 3600, tz=UTC)
            ),
            "member_id": m,
            "value": value,
        }
        for s in range(n_steps)
        for m in range(n_members)
    ]
    df = pl.DataFrame(rows).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
        pl.col("member_id").cast(pl.Int32),
    )
    return ForecastEnsemble.from_members(
        station_id=station_id,
        issued_at=_EPOCH,
        parameter="discharge",
        units="m3/s",
        time_step=_TIME_STEP,
        values=df,
        model_id=model_id,
    )


def _make_quantiles_ensemble(
    station_id: StationId = _STATION,
    model_id: ModelId | None = None,
    value: float = 50.0,
    n_steps: int = 3,
) -> ForecastEnsemble:
    """Build a QUANTILES ensemble with all quantile values fixed at `value`."""
    quantile_levels = [0.02, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.98]
    rows = [
        {
            "valid_time": ensure_utc(
                datetime.fromtimestamp(_EPOCH.timestamp() + (s + 1) * 3600, tz=UTC)
            ),
            "quantile": q,
            "value": value,
        }
        for s in range(n_steps)
        for q in quantile_levels
    ]
    df = pl.DataFrame(rows).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
    )
    return ForecastEnsemble.from_quantiles(
        station_id=station_id,
        issued_at=_EPOCH,
        parameter="discharge",
        units="m3/s",
        time_step=_TIME_STEP,
        values=df,
        model_id=model_id,
    )


class TestPrimaryModelStrategy:
    def test_selects_lowest_priority_model(self) -> None:
        mid0 = ModelId("model_a")
        mid1 = ModelId("model_b")
        mid2 = ModelId("model_c")
        model_ensembles = {
            mid0: _make_members_ensemble(model_id=mid0, value=200.0),
            mid1: _make_members_ensemble(model_id=mid1, value=200.0),
            mid2: _make_members_ensemble(model_id=mid2, value=200.0),
        }
        priorities = {mid0: 0, mid1: 1, mid2: 2}
        threshold = _make_threshold(value=100.0)
        danger_level = _make_danger_level(trigger_prob=0.5)

        results = PrimaryModelStrategy().evaluate(
            _STATION,
            "discharge",
            model_ensembles,
            [threshold],
            [danger_level],
            priorities,
        )

        assert len(results) == 1
        assert results[0].model_ids == (mid0,)

    def test_deterministic_tie_breaking(self) -> None:
        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        model_ensembles = {
            mid_b: _make_members_ensemble(model_id=mid_b, value=200.0),
            mid_a: _make_members_ensemble(model_id=mid_a, value=200.0),
        }
        priorities = {mid_a: 0, mid_b: 0}
        threshold = _make_threshold(value=100.0)
        danger_level = _make_danger_level(trigger_prob=0.5)

        results = PrimaryModelStrategy().evaluate(
            _STATION,
            "discharge",
            model_ensembles,
            [threshold],
            [danger_level],
            priorities,
        )

        assert results[0].model_ids == (mid_a,)

    def test_warns_when_priorities_missing(self) -> None:
        import structlog.testing

        mid = ModelId("model_x")
        model_ensembles = {mid: _make_members_ensemble(model_id=mid)}
        threshold = _make_threshold(value=100.0)
        danger_level = _make_danger_level()

        with structlog.testing.capture_logs() as cap:
            PrimaryModelStrategy().evaluate(
                _STATION, "discharge", model_ensembles, [threshold], [danger_level], {}
            )

        assert any(e.get("event") == "alert.priorities_not_found" for e in cap)

    def test_single_model_returns_that_model(self) -> None:
        mid = ModelId("only_model")
        model_ensembles = {mid: _make_members_ensemble(model_id=mid, value=200.0)}
        priorities = {mid: 0}
        threshold = _make_threshold(value=100.0)
        danger_level = _make_danger_level(trigger_prob=0.5)

        results = PrimaryModelStrategy().evaluate(
            _STATION,
            "discharge",
            model_ensembles,
            [threshold],
            [danger_level],
            priorities,
        )

        assert len(results) == 1
        assert results[0].model_ids == (mid,)

    def test_empty_ensembles_returns_empty(self) -> None:
        results = PrimaryModelStrategy().evaluate(_STATION, "discharge", {}, [], [], {})
        assert results == []

    def test_model_ids_contains_only_primary(self) -> None:
        mid_primary = ModelId("primary")
        mid_secondary = ModelId("secondary")
        model_ensembles = {
            mid_primary: _make_members_ensemble(model_id=mid_primary, value=200.0),
            mid_secondary: _make_members_ensemble(model_id=mid_secondary, value=200.0),
        }
        priorities = {mid_primary: 0, mid_secondary: 1}
        threshold = _make_threshold(value=100.0)
        danger_level = _make_danger_level(trigger_prob=0.5)

        results = PrimaryModelStrategy().evaluate(
            _STATION,
            "discharge",
            model_ensembles,
            [threshold],
            [danger_level],
            priorities,
        )

        assert results[0].model_ids == (mid_primary,)
        assert mid_secondary not in results[0].model_ids


class TestPooledEnsembleStrategy:
    def test_pools_all_members(self) -> None:
        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        model_ensembles = {
            mid_a: _make_members_ensemble(model_id=mid_a, n_members=21, value=200.0),
            mid_b: _make_members_ensemble(model_id=mid_b, n_members=21, value=200.0),
        }
        threshold = _make_threshold(value=100.0)
        danger_level = _make_danger_level(trigger_prob=0.5)

        results = PooledEnsembleStrategy().evaluate(
            _STATION, "discharge", model_ensembles, [threshold], [danger_level], {}
        )

        assert len(results) == 1
        # All 42 members exceed threshold — probability should be 1.0
        assert results[0].exceedance_probability == pytest.approx(1.0)

    def test_exceedance_probability_from_pooled_set(self) -> None:
        """Half the members exceed threshold → probability ~0.5."""
        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")

        # model_a: all values below threshold (50 < 100)
        ens_a = _make_members_ensemble(model_id=mid_a, n_members=21, value=50.0)
        # model_b: all values above threshold (200 > 100)
        ens_b = _make_members_ensemble(model_id=mid_b, n_members=21, value=200.0)

        model_ensembles = {mid_a: ens_a, mid_b: ens_b}
        threshold = _make_threshold(value=100.0)
        danger_level = _make_danger_level(trigger_prob=0.4)

        results = PooledEnsembleStrategy().evaluate(
            _STATION, "discharge", model_ensembles, [threshold], [danger_level], {}
        )

        assert len(results) == 1
        assert results[0].exceedance_probability == pytest.approx(0.5)

    def test_model_ids_contains_all_models(self) -> None:
        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        model_ensembles = {
            mid_a: _make_members_ensemble(model_id=mid_a, value=200.0),
            mid_b: _make_members_ensemble(model_id=mid_b, value=200.0),
        }
        threshold = _make_threshold(value=100.0)
        danger_level = _make_danger_level(trigger_prob=0.5)

        results = PooledEnsembleStrategy().evaluate(
            _STATION, "discharge", model_ensembles, [threshold], [danger_level], {}
        )

        assert set(results[0].model_ids) == {mid_a, mid_b}

    def test_rejects_mixed_representations(self) -> None:
        mid_a = ModelId("model_a")
        mid_b = ModelId("model_b")
        model_ensembles = {
            mid_a: _make_members_ensemble(model_id=mid_a),
            mid_b: _make_quantiles_ensemble(model_id=mid_b),
        }

        with pytest.raises(ValueError, match="homogeneous MEMBERS"):
            PooledEnsembleStrategy().evaluate(
                _STATION, "discharge", model_ensembles, [], [], {}
            )
