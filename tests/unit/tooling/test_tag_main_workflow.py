"""Plan 197 — main tags itself.

Structural assertions over the parsed `tag-main.yml` workflow, in the manner
of `test_ci_credential_absence_guard.py`: steps selected by name, `run:`
scripts asserted by substring/ordering rather than executed. This workflow
never runs in CI (there is no GitHub runner in this repo's test suite), so
the contract that matters is the YAML shape and the shell script's ordering
— that IS what determined whether Round 1's blocker (no git identity) and
D6's race handling actually work.

`on:` parses as the boolean key `True` under PyYAML's `safe_load` (YAML 1.1
treats bare `on`/`off`/`yes`/`no` as booleans) — see `_on()`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW_PATH = _REPO_ROOT / ".github/workflows/tag-main.yml"
_JOB_NAME = "tag"


def _workflow() -> dict[str, Any]:
    if not _WORKFLOW_PATH.exists():
        return {}
    parsed = yaml.safe_load(_WORKFLOW_PATH.read_text())
    return parsed if isinstance(parsed, dict) else {}


def _on() -> dict[str, Any]:
    return _workflow().get(True, {})


def _job() -> dict[str, Any]:
    return _workflow().get("jobs", {}).get(_JOB_NAME, {})


def _steps() -> list[dict[str, Any]]:
    return _job().get("steps", [])


def _step(name: str) -> dict[str, Any]:
    for step in _steps():
        if step.get("name") == name:
            return step
    return {}


def _tag_step_run() -> str:
    return _step("Tag the version if not already tagged").get("run", "")


class TestFileExists:
    def test_workflow_file_exists(self) -> None:
        assert _WORKFLOW_PATH.exists(), (
            f"{_WORKFLOW_PATH} does not exist — Plan 197 T1 not implemented"
        )


class TestTriggerIsPushToMainOnlyD1:
    """D1 — trigger on push to main, NOT gated on CI success. The reversed
    draft used `workflow_run`; that machinery must be entirely absent."""

    def test_triggers_on_push_to_branch_main(self) -> None:
        push = _on().get("push", {})
        assert push.get("branches") == ["main"]

    def test_no_workflow_run_trigger(self) -> None:
        assert "workflow_run" not in _on()

    def test_no_pull_request_trigger(self) -> None:
        assert "pull_request" not in _on()


class TestNoConcurrencyGroupD6:
    """D6 — a concurrency group would LOSE tags (a third queued run
    supersedes a pending one); the race is tolerated instead, so no
    concurrency block may exist at all."""

    def test_workflow_has_no_concurrency_block(self) -> None:
        assert "concurrency" not in _workflow()


class TestJobPermissionsD5:
    """D5 — least privilege, job-scoped: contents: write on the job only."""

    def test_job_declares_contents_write(self) -> None:
        assert _job().get("permissions") == {"contents": "write"}

    def test_no_workflow_level_permissions_block(self) -> None:
        # Matches ci.yml's existing convention: permissions are scoped per
        # job, never declared at the top level of the workflow.
        assert "permissions" not in _workflow()


class TestGitIdentityConfiguredBeforeTagCreation:
    """Round 1 BLOCKER — a clean runner has no git identity; `git tag -a`
    fails with "Committer identity unknown" unless user.name/user.email are
    set first. This is the single fact that decided whether the workflow
    works on its first real run."""

    def test_run_script_sets_git_identity(self) -> None:
        run = _tag_step_run()
        assert "git config user.name" in run
        assert "git config user.email" in run

    def test_identity_is_set_before_tag_creation(self) -> None:
        run = _tag_step_run()
        assert "git config user.name" in run
        assert "git tag -a" in run
        identity_idx = run.index("git config user.name")
        tag_idx = run.index("git tag -a")
        assert identity_idx < tag_idx, (
            "git identity must be configured BEFORE `git tag -a` runs, or "
            "annotated tag creation fails with 'Committer identity unknown'"
        )


class TestIdempotentSkipD2:
    """D2 — idempotent by construction: skip (exit cleanly) if the tag
    already exists, checked BEFORE any tag is created. Must fail red
    against an implementation that always creates the tag unconditionally."""

    def test_run_script_checks_existence_before_creating(self) -> None:
        run = _tag_step_run()
        assert "git ls-remote" in run
        assert "git tag -a" in run
        check_idx = run.index("git ls-remote")
        create_idx = run.index("git tag -a")
        assert check_idx < create_idx, (
            "the existence check must precede tag creation — an "
            "implementation that always creates would put `git tag -a` "
            "first or omit the check entirely"
        )

    def test_run_script_exits_cleanly_when_tag_already_exists(self) -> None:
        run = _tag_step_run()
        assert "exit 0" in run


class TestAnnotatedTagAtGithubShaD4:
    """D4 — annotated (not lightweight) tags, created at github.sha."""

    def test_uses_annotated_tag_flag(self) -> None:
        run = _tag_step_run()
        assert "git tag -a" in run

    def test_tag_created_at_github_sha(self) -> None:
        run = _tag_step_run()
        assert "${{ github.sha }}" in run

    def test_version_read_from_pyproject_toml(self) -> None:
        run = _tag_step_run()
        assert "pyproject.toml" in run


class TestPushRejectionTreatedAsSuccessD6:
    """D6 — two runs may both observe the tag absent; one loses the push
    with `already exists`. That rejection must be treated as success by
    re-checking the remote, not as a hard failure."""

    def test_run_script_pushes_the_tag(self) -> None:
        run = _tag_step_run()
        assert "git push" in run

    def test_run_script_rechecks_remote_after_a_failed_push(self) -> None:
        run = _tag_step_run()
        assert run.count("git ls-remote") >= 2, (
            "the tag must be re-checked on the remote after a failed push "
            "(the race-losing run treats the rejection as success), not "
            "just checked once up front"
        )

    def test_only_the_unrecovered_failure_path_exits_nonzero(self) -> None:
        run = _tag_step_run()
        assert "git push" in run
        push_idx = run.index("git push")
        after_push = run[push_idx:]
        # The re-check for "lost the race" must appear before any exit 1
        # in the post-push logic, or a legitimate race loss would be
        # reported as a hard failure.
        assert "git ls-remote" in after_push
        recheck_idx = after_push.index("git ls-remote")
        if "exit 1" in after_push:
            fail_idx = after_push.index("exit 1")
            assert recheck_idx < fail_idx


class TestCheckoutStepPresent:
    def test_a_checkout_step_exists(self) -> None:
        assert any("actions/checkout" in step.get("uses", "") for step in _steps())
