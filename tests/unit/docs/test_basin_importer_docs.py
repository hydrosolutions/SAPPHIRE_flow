"""Plan 120 Task 3B — docs anchor checks (not full-text assertions) for the
basin/static importer runbook + contract/schema/spec doc sync."""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNBOOK_PATH = _REPO_ROOT / "docs" / "operations" / "basin-static-importer-runbook.md"
_CONTRACT_PATH = (
    _REPO_ROOT / "docs" / "requirements" / "04-basin-static-artifact-contract.md"
)
_DATABASE_SCHEMA_PATH = _REPO_ROOT / "docs" / "spec" / "database-schema.md"
_ARCHITECTURE_CONTEXT_PATH = _REPO_ROOT / "docs" / "architecture-context.md"
_TYPES_AND_PROTOCOLS_PATH = _REPO_ROOT / "docs" / "spec" / "types-and-protocols.md"

_REQUIRED_RUNBOOK_ANCHORS = [
    "package layout",
    "basin_static_packages",
    "acceptance report",
    "correction",
]


def _headings(text: str) -> list[str]:
    return [
        line.lstrip("#").strip()
        for line in text.splitlines()
        if re.match(r"^#{1,6}\s", line)
    ]


class TestImporterDocs:
    def test_runbook_exists(self) -> None:
        assert _RUNBOOK_PATH.is_file(), f"missing runbook: {_RUNBOOK_PATH}"

    def test_runbook_has_required_operator_anchors_as_headings(self) -> None:
        text = _RUNBOOK_PATH.read_text()
        headings_lower = [h.lower() for h in _headings(text)]
        for anchor in _REQUIRED_RUNBOOK_ANCHORS:
            assert any(anchor in h for h in headings_lower), (
                f"anchor {anchor!r} not found as a runbook heading; "
                f"headings were: {headings_lower}"
            )

    def test_runbook_mentions_the_cli_entrypoint(self) -> None:
        text = _RUNBOOK_PATH.read_text()
        assert "sapphire_flow.cli.import_basin_package" in text

    def test_database_schema_mentions_basin_versions_and_package_id(self) -> None:
        text = _DATABASE_SCHEMA_PATH.read_text()
        assert "basin_versions" in text
        assert "basin_static_packages" in text
        assert "model_artifact_basin_versions" in text
        assert "package_id" in text

    def test_architecture_context_mentions_basin_versions_and_package_id(self) -> None:
        text = _ARCHITECTURE_CONTEXT_PATH.read_text()
        assert "basin_versions" in text
        assert "basin_static_packages" in text
        assert "package_id" in text

    def test_types_and_protocols_documents_lineage_helper(self) -> None:
        text = _TYPES_AND_PROTOCOLS_PATH.read_text()
        assert "record_artifact_basin_lineage" in text

    def test_types_and_protocols_store_artifact_return_type_is_tuple(self) -> None:
        text = _TYPES_AND_PROTOCOLS_PATH.read_text()
        assert "-> tuple[ArtifactId, str]" in text
        # The stale single-value return type must not appear anywhere as the
        # actual `store_artifact` signature return.
        assert not re.search(r"store_artifact\([^)]*\)\s*->\s*ArtifactId:", text)

    def test_contract_5a_no_longer_describes_persistence_as_open_gap(self) -> None:
        text = _CONTRACT_PATH.read_text()
        section = text.split("## 5a.", 1)[1].split("\n## 6", 1)[0]
        assert "Still\nopen:" not in section
        assert "**Still\nopen:**" not in section
        assert "is not yet built" not in section
        assert "RESOLVED" in section

    def test_contract_6_2a_no_longer_defers_provenance_to_implementing_plan(
        self,
    ) -> None:
        text = _CONTRACT_PATH.read_text()
        section = text.split("### 6.2a", 1)[1].split("\n### 6.3", 1)[0]
        assert "is left to the implementing plan" not in section
        assert "RESOLVED" in section

    def test_contract_11_documents_the_persistence_target(self) -> None:
        text = _CONTRACT_PATH.read_text()
        section = text.split("## 11. Versioning", 1)[1].split("\n## 12", 1)[0]
        assert "basin_static_packages" in section
        assert "model_artifact_basin_versions" in section
        assert "RESOLVED" in section
