"""Plan 241 T4 — an incoherent horizon declaration must be unrepresentable.

`resolve_required_steps` treats any non-boolean int as a floor, so a `0` would
quietly have meant "require nothing". These pin the rejections at construction
time, where a frozen dataclass can still refuse.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.model import ModelDataRequirements


def _requirements(**overrides: object) -> ModelDataRequirements:
    base: dict[str, object] = {
        "target_parameters": frozenset({"discharge"}),
        "past_dynamic_features": frozenset({"discharge"}),
        "future_dynamic_features": frozenset({"precipitation"}),
        "static_features": frozenset({"area"}),
        "supported_time_steps": frozenset({timedelta(days=1)}),
        "lookback_steps": 3,
        "forecast_horizon_steps": 15,
        "spatial_input_type": SpatialRepresentation.BASIN_AVERAGE,
    }
    base.update(overrides)
    return ModelDataRequirements(**base)  # type: ignore[arg-type]


class TestHorizonDeclarationValidation:
    def test_undeclared_is_accepted(self) -> None:
        req = _requirements()
        assert req.declared_horizon_semantics is None
        assert req.declared_min_future_steps is None

    def test_at_most_with_a_floor_is_accepted(self) -> None:
        req = _requirements(
            declared_horizon_semantics="at_most", declared_min_future_steps=1
        )
        assert req.declared_min_future_steps == 1

    def test_exact_without_a_floor_is_accepted(self) -> None:
        assert _requirements(declared_horizon_semantics="exact") is not None

    def test_unknown_semantics_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be 'exact', 'at_most' or None"):
            _requirements(declared_horizon_semantics="AT_MOST")

    def test_a_floor_without_at_most_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="only meaningful with"):
            _requirements(
                declared_horizon_semantics="exact", declared_min_future_steps=5
            )

    def test_a_floor_without_any_semantics_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="only meaningful with"):
            _requirements(declared_min_future_steps=5)

    @pytest.mark.parametrize("floor", [0, -1])
    def test_a_floor_below_one_is_rejected(self, floor: int) -> None:
        """A 0 would mean 'require nothing' rather than 'require one step'."""
        with pytest.raises(ValueError, match="must be ≥ 1"):
            _requirements(
                declared_horizon_semantics="at_most", declared_min_future_steps=floor
            )
