from __future__ import annotations

import random
from unittest.mock import patch

import pytest

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.exceptions import ModelSmokeTestError
from sapphire_flow.flows.onboard_model import _smoke_test_model_task
from sapphire_flow.services.model_onboarding import assert_operational_floors
from tests.conftest import make_deployment_config
from tests.fakes.fake_fi_models import (
    REFERENCE_FI_QUANTILE_LEVELS,
    ReferenceFIForecastModel,
)
from tests.fakes.fake_models import FakeStationForecastModel


def _adapter(model: ReferenceFIForecastModel) -> fi_boundary.ForecastInterfaceAdapter:
    return fi_boundary.ForecastInterfaceAdapter(model)


def test_members_at_operational_floor_passes() -> None:
    adapter = _adapter(ReferenceFIForecastModel(member_count=20))

    assert_operational_floors(
        adapter,
        make_deployment_config(),
        random.Random(123),
    )


def test_members_below_operational_floor_raises_count_and_floor() -> None:
    adapter = _adapter(ReferenceFIForecastModel(member_count=8))

    with pytest.raises(ModelSmokeTestError) as exc:
        assert_operational_floors(
            adapter,
            make_deployment_config(),
            random.Random(123),
        )

    message = str(exc.value)
    assert "parameter 'discharge'" in message
    assert "observed_count=8" in message
    assert "representation=members" in message
    assert "required_floor=20" in message


def test_deterministic_only_single_member_raises() -> None:
    adapter = _adapter(ReferenceFIForecastModel(deterministic=True))

    with pytest.raises(ModelSmokeTestError) as exc:
        assert_operational_floors(
            adapter,
            make_deployment_config(),
            random.Random(123),
        )

    message = str(exc.value)
    assert "observed_count=1" in message
    assert "representation=members" in message
    assert "required_floor=20" in message


def test_quantiles_at_operational_floor_passes() -> None:
    adapter = _adapter(
        ReferenceFIForecastModel(quantile_levels=REFERENCE_FI_QUANTILE_LEVELS)
    )

    assert_operational_floors(
        adapter,
        make_deployment_config(),
        random.Random(123),
    )


def test_flow_smoke_task_runs_fi_conformance_and_operational_floors() -> None:
    adapter = _adapter(ReferenceFIForecastModel(member_count=20))
    config = make_deployment_config()
    rng = random.Random(123)

    with (
        patch(
            "sapphire_flow.flows.onboard_model.assert_model_conforms"
        ) as mock_conforms,
        patch(
            "sapphire_flow.flows.onboard_model.assert_operational_floors"
        ) as mock_floors,
        patch("sapphire_flow.flows.onboard_model.smoke_test_model") as mock_smoke,
    ):
        _smoke_test_model_task.fn(
            model=adapter,
            deployment_config=config,
            rng=rng,
        )

    mock_conforms.assert_called_once_with(adapter, rng)
    mock_floors.assert_called_once_with(model=adapter, config=config, rng=rng)
    mock_smoke.assert_not_called()


def test_flow_smoke_task_keeps_native_smoke_test_path() -> None:
    model = FakeStationForecastModel()
    config = make_deployment_config()
    rng = random.Random(123)

    with (
        patch("sapphire_flow.flows.onboard_model.smoke_test_model") as mock_smoke,
        patch(
            "sapphire_flow.flows.onboard_model.assert_model_conforms"
        ) as mock_conforms,
        patch(
            "sapphire_flow.flows.onboard_model.assert_operational_floors"
        ) as mock_floors,
    ):
        _smoke_test_model_task.fn(
            model=model,
            deployment_config=config,
            rng=rng,
        )

    mock_smoke.assert_called_once_with(model=model, rng=rng)
    mock_conforms.assert_not_called()
    mock_floors.assert_not_called()
