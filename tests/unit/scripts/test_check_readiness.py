from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "check_readiness.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location(
        "check_readiness_script", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_readiness_script"] = module
    spec.loader.exec_module(module)
    return module


class TestParseFrontmatter:
    def test_yaml_frontmatter_path_unchanged(self, mod) -> None:
        text = "---\nstatus: READY\n---\n# Plan\n"
        result = mod.parse_frontmatter(text)
        assert result.get("status") == "READY"

    def test_markdown_body_status_detected_when_no_yaml(self, mod) -> None:
        text = "# Plan 067 — Something\n\n**Status**: READY\n**Date**: 2026-04-21\n"
        result = mod.parse_frontmatter(text)
        assert result.get("status") == "READY"

    def test_yaml_wins_when_both_present(self, mod) -> None:
        text = "---\nstatus: DRAFT\n---\n# Plan\n\n**Status**: READY\n"
        result = mod.parse_frontmatter(text)
        assert result.get("status") == "DRAFT"

    def test_neither_present_returns_empty(self, mod) -> None:
        text = "# Plan\n\nSome content without any status field.\n"
        result = mod.parse_frontmatter(text)
        assert "status" not in result

    def test_markdown_body_other_keys_exposed(self, mod) -> None:
        text = "# Plan\n\n**Status**: READY\n**Date**: 2026-04-21\n**Scope**: narrow\n"
        result = mod.parse_frontmatter(text)
        assert result.get("date") == "2026-04-21"
        assert result.get("scope") == "narrow"

    def test_markdown_body_status_draft(self, mod) -> None:
        text = "# Plan 071\n\n**Status**: DRAFT\n**Date**: 2026-04-22\n"
        result = mod.parse_frontmatter(text)
        assert result.get("status") == "DRAFT"

    def test_yaml_empty_block_falls_through_to_markdown_body(self, mod) -> None:
        text = "---\nauthor: alice\n---\n# Plan\n\n**Status**: READY\n"
        result = mod.parse_frontmatter(text)
        assert result.get("status") == "READY"
        assert result.get("author") == "alice"
