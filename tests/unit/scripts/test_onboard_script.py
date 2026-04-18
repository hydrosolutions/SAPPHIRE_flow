from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "onboard.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("onboard_script", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["onboard_script"] = module
    spec.loader.exec_module(module)
    return module


class TestOnboardScriptMain:
    def test_main_returns_nonzero_without_database_url(self, mod, monkeypatch) -> None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        result = mod.main([])
        assert result == 1

    def test_main_dry_run_returns_zero(self, mod, monkeypatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
        result = mod.main(["--dry-run"])
        assert result == 0

    def test_main_happy_path_invokes_camelsch_onboarder(self, mod, monkeypatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
        monkeypatch.setattr(mod.sa, "create_engine", MagicMock())
        monkeypatch.setattr(mod, "_run_migrations", MagicMock())
        monkeypatch.setattr(mod, "_load_qc_rules", MagicMock(return_value=[]))
        monkeypatch.setattr(mod, "_print_result", MagicMock())
        monkeypatch.setattr(
            "sapphire_flow.config.paths.resolve_artifact_dir",
            MagicMock(return_value=Path("/tmp")),
        )
        onboard_stub = MagicMock(return_value=MagicMock(errors=[]))
        monkeypatch.setattr(mod, "onboard_from_camelsch", onboard_stub)

        result = mod.main(["--data-dir", "/tmp/cam"])

        assert result == 0
        onboard_stub.assert_called_once()
        _, kwargs = onboard_stub.call_args
        assert kwargs["data_dir"] == Path("/tmp/cam")
