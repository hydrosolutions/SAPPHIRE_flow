from __future__ import annotations

import pytest

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.exceptions import ConfigurationError


def test_version_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    assert fi_boundary.check_fi_version() is None

    monkeypatch.setattr(fi_boundary, "SUPPORTED_FI_VERSION", "0.0.0")

    with pytest.raises(
        ConfigurationError,
        match="supported forecastinterface==0.0.0",
    ):
        fi_boundary.check_fi_version()
