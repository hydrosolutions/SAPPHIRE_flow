"""Guard the single instruction source and project safeguards."""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _normalized(path: Path) -> str:
    return " ".join(path.read_text().replace("**", "").split())


class TestAgentInstructionPolicy:
    def test_workflow_comes_from_global_skills(self) -> None:
        guidelines = _normalized(_REPO_ROOT / "AGENTS.md")

        assert (
            "Agent workflows come from the globally installed PCE skills." in guidelines
        )
        assert not (_REPO_ROOT / "docs/workflow.md").exists()
        assert not (_REPO_ROOT / "scripts/check_readiness.py").exists()
        assert "The ORCHESTRATOR sets" not in guidelines
        assert "## Plan Readiness" not in guidelines

    def test_owner_retains_approval_and_merge_authority(self) -> None:
        guidelines = _normalized(_REPO_ROOT / "AGENTS.md")

        assert "the human owner approves and merges every PR" in guidelines
        assert "No agent merges." in guidelines
        assert "owner-commissioned relevant independent review" in guidelines


class TestTaggingPolicy:
    def test_tagging_is_delegated_to_main_workflow(self) -> None:
        guidelines = _normalized(_REPO_ROOT / "AGENTS.md")
        policy = (
            "Every code commit includes a patch version bump. "
            "Never create a tag on a feature branch. "
            "On pushes to `main`, `.github/workflows/tag-main.yml` creates the version "
            "tag when absent."
        )

        assert policy in guidelines
