from __future__ import annotations

import importlib.util
import json
import re
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
    def test_reads_leading_yaml_frontmatter(self, mod: ModuleType) -> None:
        text = "---\nstatus: READY\ntitle: Example\n---\n# Plan\n"
        assert mod.parse_frontmatter(text) == {
            "status": "READY",
            "title": "Example",
        }

    def test_rejects_body_only_status(self, mod: ModuleType) -> None:
        text = "# Plan\n\n**Status**: READY\n"
        assert mod.parse_frontmatter(text) == {}

    def test_does_not_fill_missing_yaml_status_from_body(self, mod: ModuleType) -> None:
        text = "---\ntitle: Example\n---\n# Plan\n\n**Status**: READY\n"
        assert "status" not in mod.parse_frontmatter(text)

    def test_ignores_nested_status(self, mod: ModuleType) -> None:
        text = "---\nmetadata:\n  status: READY\n---\n# Plan\n"

        assert "status" not in mod.parse_frontmatter(text)

    def test_marks_duplicate_top_level_status_invalid(self, mod: ModuleType) -> None:
        text = "---\nstatus: DRAFT\nstatus: READY\n---\n# Plan\n"

        assert mod.parse_frontmatter(text)["status"] == "__INVALID_DUPLICATE__"


class TestCheckReadiness:
    def test_accepts_exact_yaml_ready_without_review_history(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text("---\nstatus: READY\n---\n# Plan\n", encoding="utf-8")

        assert mod.check_readiness(plan) == (
            True,
            "YAML frontmatter status is READY",
            "READY",
        )

    @pytest.mark.parametrize("status", ["DRAFT", "ready", "READY # confirmed"])
    def test_rejects_any_status_other_than_exact_ready(
        self, mod: ModuleType, tmp_path: Path, status: str
    ) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(f"---\nstatus: {status}\n---\n", encoding="utf-8")

        is_ready, reason, parsed_status = mod.check_readiness(plan)

        assert is_ready is False
        assert "expected 'READY'" in reason
        assert parsed_status == status.strip()

    def test_rejects_body_only_ready(self, mod: ModuleType, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text("# Plan\n\n**Status**: READY\n", encoding="utf-8")

        is_ready, reason, status = mod.check_readiness(plan)

        assert is_ready is False
        assert reason == "No 'status' field in YAML frontmatter"
        assert status == "MISSING"

    @pytest.mark.parametrize(
        "frontmatter",
        [
            "metadata:\n  status: READY",
            "status: READY\nstatus: READY",
            "status: DRAFT\nstatus: READY",
        ],
    )
    def test_rejects_nested_or_duplicate_ready_status(
        self, mod: ModuleType, tmp_path: Path, frontmatter: str
    ) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(f"---\n{frontmatter}\n---\n# Plan\n", encoding="utf-8")

        is_ready, _, _ = mod.check_readiness(plan)

        assert is_ready is False

    def test_rejects_missing_file(self, mod: ModuleType, tmp_path: Path) -> None:
        plan = tmp_path / "missing.md"

        is_ready, reason, status = mod.check_readiness(plan)

        assert is_ready is False
        assert reason == f"File not found: {plan}"
        assert status == "N/A"


class TestManifestJson:
    def test_draft_plan_emits_valid_compact_manifest(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(
            """---
status: DRAFT
---
# Plan

## Tasks

### T1 — Example

**Outcome:** one result.

**In:** `one.py`.

**Out:** everything else.

**Pre-change:** N/A — documentation gate.

**Verification:** `uv run pytest tests/example.py -q`.

## Exit gates

```bash
uv run pytest tests/example.py -q
```
""",
            encoding="utf-8",
        )

        is_valid, manifest = mod.inspect_plan(plan)

        assert is_valid is True
        assert manifest == {
            "status": "DRAFT",
            "documentFingerprint": mod.fingerprint(plan.read_text(encoding="utf-8")),
            "tasks": [
                {
                    "id": "T1",
                    "preChangeMode": "N/A",
                    "preChangeFingerprint": mod.fingerprint(
                        "N/A — documentation gate."
                    ),
                    "verificationFingerprint": mod.fingerprint(
                        "`uv run pytest tests/example.py -q`."
                    ),
                }
            ],
            "exitGates": [
                {
                    "id": "G1",
                    "fingerprint": mod.fingerprint("uv run pytest tests/example.py -q"),
                }
            ],
            "valid": True,
            "diagnostics": [],
        }
        assert len(mod.serialize_manifest(manifest).encode("utf-8")) <= 8192

    def test_invalid_contract_fails_closed(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(
            """---
status: READY
---
## Tasks
### T1 — Missing fields
**Outcome:** incomplete.
## Exit gates
```bash
true
```
""",
            encoding="utf-8",
        )

        is_valid, manifest = mod.inspect_plan(plan)

        assert is_valid is False
        assert manifest["valid"] is False
        assert any("T1" in item for item in manifest["diagnostics"])

    def test_rejects_duplicate_required_task_fields(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(
            """---
status: READY
---
## Tasks
### T1 — Duplicate verification
**Outcome:** result.
**In:** one file.
**Out:** other files.
**Pre-change:** run pre-change.
**Verification:** run first check.
**Verification:** run second check.
## Exit gates
```bash
true
```
""",
            encoding="utf-8",
        )

        is_valid, manifest = mod.inspect_plan(plan)

        assert is_valid is False
        assert any(
            "duplicate fields: Verification" in item for item in manifest["diagnostics"]
        )

    def test_verification_fingerprint_includes_bold_subsections(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        plan = tmp_path / "plan.md"
        verification = "run the check.\n\n**Details:** keep this part of the contract."
        plan.write_text(
            f"""---
status: READY
---
## Tasks
### T1 — Bold subsection
**Outcome:** result.
**In:** one file.
**Out:** other files.
**Pre-change:** run pre-change.
**Verification:** {verification}
## Exit gates
```bash
true
```
""",
            encoding="utf-8",
        )

        is_valid, manifest = mod.inspect_plan(plan)

        assert is_valid is True
        assert manifest["tasks"][0]["verificationFingerprint"] == mod.fingerprint(
            verification
        )

    def test_rejects_multiple_exit_gate_fences(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(
            """---
status: READY
---
## Tasks
### T1 — Example
**Outcome:** result.
**In:** one file.
**Out:** other files.
**Pre-change:** run pre-change.
**Verification:** run verification.
## Exit gates
```bash
true
```
```sh
false
```
""",
            encoding="utf-8",
        )

        is_valid, manifest = mod.inspect_plan(plan)

        assert is_valid is False
        assert any(
            "exactly one bash/sh command fence" in item
            for item in manifest["diagnostics"]
        )

    def test_oversized_manifest_returns_bounded_invalid_result(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        tasks = "\n".join(
            f"""### T{index} — Task
**Outcome:** result.
**In:** one file.
**Out:** other files.
**Pre-change:** run check {index}.
**Verification:** run verification {index}.
"""
            for index in range(200)
        )
        plan = tmp_path / "plan.md"
        plan.write_text(
            f"""---
status: READY
---
## Tasks
{tasks}
## Exit gates
```bash
true
```
""",
            encoding="utf-8",
        )

        is_valid, manifest = mod.inspect_plan(plan)
        serialized = mod.serialize_manifest(manifest)

        assert is_valid is False
        assert manifest["valid"] is False
        assert manifest["tasks"] == []
        assert "exceeds 8192 bytes" in manifest["diagnostics"][0]
        assert len(serialized.encode("utf-8")) <= 8192

    def test_valid_synthetic_manifests_cover_current_plan_scale(
        self, mod: ModuleType, tmp_path: Path
    ) -> None:
        plans = list((_SCRIPT_PATH.parents[1] / "docs" / "plans").glob("*.md"))
        active = [
            path
            for path in plans
            if re.search(
                r"^status: (?:DRAFT|READY|BLOCKED|DEFERRED|PARTIAL)$",
                path.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        ]
        manifests = {path: mod.inspect_plan(path)[1] for path in active}
        largest_source_bytes = max(path.stat().st_size for path in active)
        greatest_task_count = max(
            len(manifest["tasks"]) for manifest in manifests.values()
        )

        def canonical_plan(task_count: int, padding: str = "") -> str:
            tasks = "\n".join(
                f"""### T{index} — Task
**Outcome:** result.
**In:** one file.
**Out:** other files.
**Pre-change:** run pre-change {index}.
**Verification:** run verification {index}.
"""
                for index in range(1, task_count + 1)
            )
            return f"""---
status: READY
---
{padding}
## Tasks
{tasks}
## Exit gates
```bash
true
```
"""

        source_scale = tmp_path / "source-scale.md"
        source_scale.write_text(
            canonical_plan(1, "x" * largest_source_bytes), encoding="utf-8"
        )
        task_scale = tmp_path / "task-scale.md"
        task_scale.write_text(
            canonical_plan(max(greatest_task_count, 1)), encoding="utf-8"
        )

        for plan in (source_scale, task_scale):
            is_valid, manifest = mod.inspect_plan(plan)
            assert is_valid is True
            assert manifest["valid"] is True
            assert len(mod.serialize_manifest(manifest).encode("utf-8")) <= 8192

        assert source_scale.stat().st_size >= largest_source_bytes
        assert len(mod.inspect_plan(task_scale)[1]["tasks"]) >= greatest_task_count

    def test_fingerprint_has_cross_runtime_known_value(self, mod: ModuleType) -> None:
        assert mod.fingerprint("hello") == "4f9f2cab"
        assert json.loads(mod.serialize_manifest({"valid": True})) == {"valid": True}

    def test_inspection_cli_accepts_structurally_valid_draft(
        self, tmp_path: Path
    ) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text(
            """---
status: DRAFT
---
## Tasks
### T1 — Example
**Outcome:** result.
**In:** one file.
**Out:** other files.
**Pre-change:** N/A — review gate.
**Verification:** run verification.
## Exit gates
```bash
true
```
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(_SCRIPT_PATH), "--inspect-json", str(plan)],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert json.loads(result.stdout)["status"] == "DRAFT"
