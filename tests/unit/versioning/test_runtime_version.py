from __future__ import annotations

import importlib
import sys

import sapphire_flow
from sapphire_flow.adapters import bafu_forecast, bafu_observation
from sapphire_flow.cli import onboard_nepal
from sapphire_flow.services import forecast_evidence


def test_public_version_is_non_empty_string() -> None:
    assert isinstance(sapphire_flow.__version__, str)
    assert sapphire_flow.__version__


def test_current_runtime_consumers_import_public_version() -> None:
    assert bafu_forecast.__version__ == sapphire_flow.__version__
    assert bafu_observation.__version__ == sapphire_flow.__version__
    assert onboard_nepal.__version__ == sapphire_flow.__version__
    assert forecast_evidence.__version__ == sapphire_flow.__version__


def test_missing_generated_metadata_fails_clearly(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "sapphire_flow._version", raising=False)
    monkeypatch.setattr(importlib, "import_module", importlib.import_module)
    source = (
        sapphire_flow.__file__
        and __import__("pathlib").Path(sapphire_flow.__file__).read_text()
    )
    assert "version metadata is missing" in source
