from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_automatic_main_tag_workflow_is_retired() -> None:
    assert not (_REPO_ROOT / ".github/workflows/tag-main.yml").exists()


def test_no_privileged_release_workflow_dispatch_replacement() -> None:
    for path in (_REPO_ROOT / ".github/workflows").glob("*.yml"):
        text = path.read_text()
        assert "workflow_dispatch" not in text or "release" not in path.name.lower()
        assert "contents: write" not in text or path.name != "tag-main.yml"
