"""Guard the single instruction source and project safeguards."""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _normalized(path: Path) -> str:
    return " ".join(path.read_text().replace("**", "").split())


class TestAgentInstructionPolicy:
    def test_no_retired_workflow_machinery_in_agent_instructions(self) -> None:
        guidelines = _normalized(_REPO_ROOT / "AGENTS.md")

        assert "## Workflow authority" not in guidelines
        assert "globally installed skills" not in guidelines
        assert not (_REPO_ROOT / "docs/workflow.md").exists()
        assert not (_REPO_ROOT / "scripts/check_readiness.py").exists()
        assert "The ORCHESTRATOR sets" not in guidelines
        assert "## Plan Readiness" not in guidelines

    def test_owner_retains_approval_and_merge_authority(self) -> None:
        guidelines = _normalized(_REPO_ROOT / "AGENTS.md")

        assert "The owner approves and merges PRs" in guidelines
        assert "Agents do not merge or publish release identity." in guidelines
        assert (
            "Non-trivial plans and patches require independent Claude and Codex reviews"
            in guidelines
        )
        assert (
            "high-risk work also needs an owner-commissioned relevant "
            "independent review." in guidelines
        )
        assert "Missing reviews are not approvals." in guidelines


class TestTaggingPolicy:
    def test_release_identity_is_owner_published_after_merge(self) -> None:
        policy = _normalized(_REPO_ROOT / "docs/standards/cicd.md")

        assert (
            "Each non-docs-only PR advances the sole `RELEASE_TARGET` patch "
            "once from its tested base, including tooling PRs." in policy
        )
        assert (
            "head and merge targets must agree and equal base patch+1 with "
            "unchanged major/minor." in policy
        )
        assert "The only exemption is strictly docs-only changes." in policy
        assert (
            "API contracts (`docs/spec/`), requirements, examples, assets, "
            "scripts, configuration and dependencies are not exempt." in policy
        )
        assert "Further commits in the same PR do not bump again." in policy
        assert "This target is release intent, not package identity." in policy
        assert (
            "Package versions remain dynamic and come from Git release "
            "identity at build time." in policy
        )
        assert (
            "Do not edit literal package versions, `uv.lock` for version "
            "churn, or generated SCM version metadata." in policy
        )
        assert (
            "Agents never create release tags, publish release state, or merge PRs."
            in policy
        )
        assert (
            "After a reviewed merge, the human owner publishes release "
            "identity from a clean reviewed `main` checkout" in policy
        )
        assert "`tag-main.yml` publisher remains retired." in policy
        assert not (_REPO_ROOT / ".github/workflows/tag-main.yml").exists()
