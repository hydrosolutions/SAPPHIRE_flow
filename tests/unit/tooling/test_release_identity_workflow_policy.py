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


def test_pr_target_check_binds_event_merge_and_uses_existing_interpreter() -> None:
    text = (_REPO_ROOT / ".github/workflows/ci.yml").read_text()
    step = text.split("- name: Check PR release target", 1)[1].split("      - run:", 1)[
        0
    ]
    assert "if: github.event_name == 'pull_request'" in step
    assert "PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}" in step
    assert "PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in step
    assert "PR_MERGE_SHA: ${{ github.sha }}" in step
    assert ".venv/bin/python -I tools/release_target.py check" in step
    assert (
        ' --base-sha "$PR_BASE_SHA" --head-sha "$PR_HEAD_SHA"'
        ' --merge-sha "$PR_MERGE_SHA"' in step
    )
    assert "fetch-depth: 0" in text.split("- name: Check PR release target", 1)[0]
    assert "uv run" not in step


def test_bootstrap_seed_and_safe_config_are_reviewed_constants() -> None:
    import tomllib

    config = tomllib.loads((_REPO_ROOT / ".bumpversion.toml").read_text())
    assert config == {
        "tool": {
            "bumpversion": {
                "commit": False,
                "tag": False,
                "files": [
                    {
                        "filename": "RELEASE_TARGET",
                        "search": "{current_version}\n",
                        "replace": "{new_version}\n",
                    }
                ],
            }
        }
    }
    text = (_REPO_ROOT / "tools/release_target.py").read_text()
    assert 'BOOTSTRAP_BASE_SHA = "259f16e1f32dec0379f4ce9d2175657f944a2dab"' in text
    assert "BOOTSTRAP_TARGET = Version(0, 1, 1068)" in text
