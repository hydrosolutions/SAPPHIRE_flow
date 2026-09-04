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
_OWNER_POLICY = "Only the owner may set its YAML `status: READY`."
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

    def test_only_owner_sets_ready(self) -> None:
        for path in (*_AGENT_GUIDES, _WORKFLOW):
            assert _OWNER_POLICY in _normalized(path)


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


class TestWorkflowPolicyCoherence:
    def test_context_policy_uses_manifest_references(self) -> None:
        policy = _read(_WORKFLOW)
        plan_source = _read(_REPO_ROOT / ".claude" / "workflows" / "plan.js")
        implement_source = _read(_REPO_ROOT / ".claude" / "workflows" / "implement.js")

        assert "plan path" in policy.lower()
        for source in (plan_source, implement_source):
            assert "planPath" in source
        for text in (policy, plan_source, implement_source):
            assert "manifest" in text.lower()
            assert "fingerprint" in text.lower()
        assert "every task contract" not in policy
        assert "exact reviewed plan snapshot" not in policy
        assert "parsed from the verbatim plan" not in policy
