"""Source-regression checks for the durable workflow policy."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AGENT_GUIDES = (_REPO_ROOT / "AGENTS.md", _REPO_ROOT / "CLAUDE.md")
_WORKFLOW = _REPO_ROOT / "docs/workflow.md"
_PLAN_INDEX = _REPO_ROOT / "docs/plans/README.md"
_CANONICAL_STATUSES = (
    "DRAFT",
    "READY",
    "BLOCKED",
    "DEFERRED",
    "PARTIAL",
    "SUPERSEDED",
    "COMPLETE",
)
_DRAFT_POLICY = (
    "Planning and independent-review agents may read a DRAFT plan, but "
    "implementation agents may not execute it."
)
_READY_POLICY = (
    "The ORCHESTRATOR sets `status: READY` after at least one independent review "
    "is complete (delegated by the owner 2026-09-17); no other agent may."
)
_SUPERSEDED_READY_POLICIES = (
    "Only the human owner sets READY",
    "owner-confirmed and available for implementation",
    "awaiting owner confirmation",
)
_TAGGING_POLICY = (
    "Every code commit includes a patch version bump. "
    "Never create a tag on a feature branch.\n"
    "On pushes to `main`, `.github/workflows/tag-main.yml` creates the version "
    "tag when absent."
)


def _read(path: Path) -> str:
    return path.read_text()


def _normalized(path: Path) -> str:
    return " ".join(_read(path).replace("**", "").split())


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, maxsplit=1)[1].split(end, maxsplit=1)[0]


class TestPlanReadinessPolicy:
    def test_draft_is_reviewable_but_not_implementable(self) -> None:
        for path in (*_AGENT_GUIDES, _WORKFLOW):
            assert _DRAFT_POLICY in _normalized(path)

    def test_orchestrator_sets_ready_after_independent_review(self) -> None:
        for path in (*_AGENT_GUIDES, _WORKFLOW):
            assert _READY_POLICY in _normalized(path)

    def test_superseded_ready_wording_is_absent(self) -> None:
        normalized_workflow = _normalized(_WORKFLOW)

        for stale_policy in _SUPERSEDED_READY_POLICIES:
            assert stale_policy not in normalized_workflow


class TestActiveStatusPolicy:
    def test_workflow_and_index_name_the_same_statuses(self) -> None:
        workflow_section = _section(
            _read(_WORKFLOW), "### Plan status vocabulary", "## Multi-Model Review"
        )
        index_section = _section(
            _read(_PLAN_INDEX), "## Status convention", "## Archived by"
        )

        for section in (workflow_section, index_section):
            for status in _CANONICAL_STATUSES:
                assert f"`{status}`" in section
            assert (
                "Do not use `IN_PROGRESS` or `DONE` as active-plan statuses" in section
            )
            assert "`ARCHIVED` is a location, not an active status" in section
            assert "missing active YAML status is reported as `NONE`" in section


class TestTaggingPolicy:
    def test_policy_documents_delegate_tagging_to_main_workflow(self) -> None:
        for path in (*_AGENT_GUIDES, _WORKFLOW):
            assert " ".join(_TAGGING_POLICY.split()) in _normalized(path)
