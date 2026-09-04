from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_TOOL_PATH = Path(__file__).parents[3] / "tools" / "standing_snapshot.py"


@pytest.fixture()
def mod() -> ModuleType:
    spec = importlib.util.spec_from_file_location("standing_snapshot_tool", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["standing_snapshot_tool"] = module
    spec.loader.exec_module(module)
    return module


def _write_plan(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


class TestParsePlanStatus:
    def test_active_plan_uses_yaml_status(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "231-example.md"
        _write_plan(
            plan_path,
            "---\nstatus: DRAFT\n---\n# Plan\n\n**Status**: READY\n",
        )

        plan = mod._parse_plan(plan_path)

        assert plan is not None
        assert plan.status == "DRAFT"

    def test_active_plan_without_yaml_status_is_none(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "231-example.md"
        _write_plan(plan_path, "# Plan\n\n**Status**: READY\n")

        plan = mod._parse_plan(plan_path)

        assert plan is not None
        assert plan.status == "NONE"

    def test_active_plan_with_unclosed_frontmatter_is_none(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "231-example.md"
        _write_plan(plan_path, "---\nstatus: READY\n# Plan\n")

        plan = mod._parse_plan(plan_path)

        assert plan is not None
        assert plan.status == "NONE"

    def test_active_plan_ignores_nested_yaml_status(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "231-example.md"
        _write_plan(plan_path, "---\nmetadata:\n  status: READY\n---\n# Plan\n")

        plan = mod._parse_plan(plan_path)

        assert plan is not None
        assert plan.status == "NONE"

    def test_active_plan_rejects_duplicate_yaml_status(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "231-example.md"
        _write_plan(plan_path, "---\nstatus: DRAFT\nstatus: READY\n---\n# Plan\n")

        plan = mod._parse_plan(plan_path)

        assert plan is not None
        assert plan.status == "NONE"

    def test_archived_duplicate_yaml_status_does_not_fall_back_to_body(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "067-example.md"
        _write_plan(
            plan_path,
            "---\nstatus: DRAFT\nstatus: READY\n---\n# Plan\n\n**Status**: COMPLETE\n",
        )

        plan = mod._parse_plan(plan_path, archived=True)

        assert plan is not None
        assert plan.status == "NONE"

    def test_archived_plan_may_use_legacy_body_status(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "067-example.md"
        _write_plan(plan_path, "# Plan\n\n**Status**: COMPLETE\n")

        plan = mod._parse_plan(plan_path, archived=True)

        assert plan is not None
        assert plan.status == "COMPLETE"

    def test_archived_yaml_status_wins_over_legacy_body_status(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "067-example.md"
        _write_plan(
            plan_path,
            "---\nstatus: SUPERSEDED\n---\n# Plan\n\n**Status**: COMPLETE\n",
        )

        plan = mod._parse_plan(plan_path, archived=True)

        assert plan is not None
        assert plan.status == "SUPERSEDED"
