"""LOCKED tests: FI adapter projects ``ensemble_mode`` onto ModelDataRequirements.

The FI ``FutureKnownVariable.ensemble_mode`` (``single`` | ``ensemble``) must be
projected by ``ForecastInterfaceAdapter._project_requirements`` onto the SAP3
domain field ``ModelDataRequirements.ensemble_mode`` (a SAP3-native enum mirroring
the FI values, per the codebase convention of mapping FI enums by ``.value``).

RED reason (pre-implementation): ``sapphire_flow.types.enums.EnsembleMode`` and the
``ModelDataRequirements.ensemble_mode`` field do not exist yet.
"""

from __future__ import annotations

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.adapters.forecast_interface import adapt_if_fi
from sapphire_flow.models.nwp_regression import NwpRainfallRunoff, NwpRegression
from sapphire_flow.types.enums import EnsembleMode
from tests.fakes.fake_fi_models import ReferenceFIForecastModel


def test_nwp_regression_adapter_projects_ensemble_mode() -> None:
    adapter = adapt_if_fi(NwpRegression())
    assert isinstance(adapter, fi_boundary.ForecastInterfaceAdapter)

    assert adapter.data_requirements.ensemble_mode is EnsembleMode.ENSEMBLE


def test_nwp_rainfall_runoff_adapter_projects_ensemble_mode() -> None:
    adapter = adapt_if_fi(NwpRainfallRunoff())
    assert isinstance(adapter, fi_boundary.ForecastInterfaceAdapter)

    assert adapter.data_requirements.ensemble_mode is EnsembleMode.ENSEMBLE


def test_single_mode_future_known_projects_single() -> None:
    # ReferenceFIForecastModel's future_known declares the default ensemble_mode
    # (SINGLE); the adapter must project SINGLE, not ENSEMBLE.
    adapter = fi_boundary.ForecastInterfaceAdapter(ReferenceFIForecastModel())

    assert adapter.data_requirements.ensemble_mode is EnsembleMode.SINGLE
