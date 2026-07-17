from __future__ import annotations

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.model import ModelDataRequirements

_SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "audit_distribution_shift.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location(
        "audit_distribution_shift_script", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_distribution_shift_script"] = module
    spec.loader.exec_module(module)
    return module


def _reqs(
    *,
    past: frozenset[str] = frozenset(),
    future: frozenset[str] = frozenset(),
) -> ModelDataRequirements:
    return ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=past,
        future_dynamic_features=future,
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=24)}),
        lookback_steps=1,
        forecast_horizon_steps=1,
        spatial_input_type=SpatialRepresentation.POINT,
    )


class TestAuditRequirements:
    def test_future_dynamic_only_affected_param_is_flagged(self, mod) -> None:
        # The FI NWP model declares precip/temp as FUTURE-dynamic ONLY. The
        # reader flip still changes their source (training/hindcast fetch both
        # slots from the reanalysis), so it MUST be flagged even with no
        # past-dynamic feature. This asserts the §5C union enumeration.
        reqs = _reqs(future=frozenset({"precipitation"}))
        affected = mod.audit_requirements(reqs)
        assert affected == {"future": ["precipitation"]}

    def test_past_dynamic_affected_param_is_flagged(self, mod) -> None:
        reqs = _reqs(past=frozenset({"temperature"}))
        affected = mod.audit_requirements(reqs)
        assert affected == {"past": ["temperature"]}

    def test_both_slots_reported_separately(self, mod) -> None:
        reqs = _reqs(
            past=frozenset({"temperature"}),
            future=frozenset({"precipitation"}),
        )
        affected = mod.audit_requirements(reqs)
        assert affected == {"past": ["temperature"], "future": ["precipitation"]}

    def test_clear_when_no_affected_params(self, mod) -> None:
        reqs = _reqs(
            past=frozenset({"discharge"}),
            future=frozenset({"river_level"}),
        )
        assert mod.audit_requirements(reqs) == {}
