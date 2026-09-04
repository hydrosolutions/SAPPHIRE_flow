from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "check_readiness.py"


@pytest.fixture()
def mod() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_readiness_script", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_readiness_script"] = module
    spec.loader.exec_module(module)
    return module


class TestParseFrontmatter:
    def test_reads_leading_top_level_scalars(self, mod: ModuleType) -> None:
        text = "---\nstatus: READY\ntitle: Example\n---\n# Plan\n"

        assert mod.parse_frontmatter(text) == {"status": "READY", "title": "Example"}

    @pytest.mark.parametrize(
        "text",
        [
            "# Plan\n\nstatus: READY\n",
            "---\nstatus: READY\n# missing close\n",
            "---\nmetadata:\n  status: READY\n---\n",
        ],
    )
    def test_does_not_infer_status(self, mod: ModuleType, text: str) -> None:
        assert "status" not in mod.parse_frontmatter(text)

    def test_duplicate_status_is_invalid(self, mod: ModuleType) -> None:
        text = "---\nstatus: DRAFT\nstatus: READY\n---\n"

        assert mod.parse_frontmatter(text)["status"] == "__INVALID_DUPLICATE__"


class TestCheckReadiness:
    def test_accepts_only_exact_ready(self, mod: ModuleType, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text("---\nstatus: READY\n---\n", encoding="utf-8")

        assert mod.check_readiness(plan) == (
            True,
            "YAML frontmatter status is READY",
            "READY",
        )

    @pytest.mark.parametrize(
        "status", ["DRAFT", "ready", "READY # confirmed", '"READY"', "NONE"]
    )
    def test_rejects_other_status_values(
        self, mod: ModuleType, tmp_path: Path, status: str
    ) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(f"---\nstatus: {status}\n---\n", encoding="utf-8")

        is_ready, reason, parsed = mod.check_readiness(plan)

        assert is_ready is False
        assert "expected 'READY'" in reason
        assert parsed == status

    def test_rejects_missing_status(self, mod: ModuleType, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text("---\ntitle: Example\n---\n", encoding="utf-8")

        assert mod.check_readiness(plan) == (
            False,
            "No 'status' field in YAML frontmatter",
            "NONE",
        )

    def test_rejects_missing_file(self, mod: ModuleType, tmp_path: Path) -> None:
        plan = tmp_path / "missing.md"

        assert mod.check_readiness(plan) == (False, f"File not found: {plan}", "N/A")


class TestCli:
    def test_ready_returns_zero(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text("---\nstatus: READY\n---\n", encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(_SCRIPT_PATH), str(plan)],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "Verdict: READY" in result.stdout

    def test_draft_returns_nonzero(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text("---\nstatus: DRAFT\n---\n", encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(_SCRIPT_PATH), str(plan)],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1
        assert "Verdict: NOT READY" in result.stdout
