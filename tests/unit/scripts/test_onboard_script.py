from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "onboard.py"


@pytest.fixture()
def _build_parser():
    spec = importlib.util.spec_from_file_location("onboard_script", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["onboard_script"] = mod
    spec.loader.exec_module(mod)
    return mod._build_parser


class TestOnboardScriptParser:
    def test_data_dir_default_is_none(self, _build_parser) -> None:
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.data_dir is None

    def test_data_dir_explicit_arg_parsed(self, _build_parser) -> None:
        parser = _build_parser()
        args = parser.parse_args(["--data-dir", "/some/path"])
        assert args.data_dir == Path("/some/path")
