from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOL_PATH = _REPO_ROOT / "tools" / "release_identity.py"
spec = importlib.util.spec_from_file_location("release_identity", _TOOL_PATH)
assert spec is not None and spec.loader is not None
release_identity = importlib.util.module_from_spec(spec)
sys.modules["release_identity"] = release_identity
spec.loader.exec_module(release_identity)


def _isolated_git_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if (
            key == "GIT_CONFIG_PARAMETERS"
            or key == "GIT_CONFIG_COUNT"
            or key.startswith("GIT_CONFIG_KEY_")
            or key.startswith("GIT_CONFIG_VALUE_")
        ):
            env.pop(key)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=_isolated_git_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return proc.stdout.strip()


def _make_repo(tmp_path: Path) -> tuple[Path, Path, str, str, Path]:
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "--bare", str(bare))
    _git(tmp_path, "init", str(work))
    _git(work, "config", "user.name", "Test User")
    _git(work, "config", "user.email", "test@example.invalid")
    (work / "README.md").write_text("one\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "initial")
    ceiling = _git(work, "rev-parse", "HEAD")
    _git(work, "tag", "v0.1.0")
    (work / "README.md").write_text("two\n")
    _git(work, "commit", "-am", "second")
    source = _git(work, "rev-parse", "HEAD")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "origin", "main", "refs/tags/v0.1.0")
    evidence = tmp_path / "old-runs.json"
    evidence.write_text(
        json.dumps(
            {"complete": True, "paginated": True, "runs": [{"status": "completed"}]}
        )
    )
    return bare, work, ceiling, source, evidence


def _patch_local_publication(
    monkeypatch, runs: list[dict[str, object]] | None = None
) -> None:
    monkeypatch.setattr(release_identity, "validate_destination", lambda: None)
    monkeypatch.setattr(
        release_identity,
        "require_clean_helper_main",
        lambda: _git(Path.cwd(), "rev-parse", "HEAD"),
    )
    monkeypatch.setattr(release_identity, "confirm_destination", lambda *, yes: None)
    monkeypatch.setattr(
        release_identity,
        "query_old_workflow_runs",
        lambda: runs or [{"status": "completed"}],
    )


def _publish(version: str, source: str, *, bootstrap: bool = False) -> int:
    try:
        return release_identity.publish(
            argparse.Namespace(
                version=version, source=source, bootstrap=bootstrap, yes=True
            )
        )
    except release_identity.ReleaseIdentityError:
        return 2


class TestReleaseIdentityGit:
    def test_default_publish_with_state_absent_fails(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _, work, _, source, _ = _make_repo(tmp_path)
        monkeypatch.chdir(work)
        _patch_local_publication(monkeypatch)
        code = _publish("0.1.1", source)
        assert code == 2
        assert _git(work, "ls-remote", "origin", "refs/heads/release-state") == ""

    def test_bootstrap_requires_complete_old_publisher_evidence(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _, work, _, source, _ = _make_repo(tmp_path)
        monkeypatch.chdir(work)
        _patch_local_publication(monkeypatch, [{"status": "queued"}])
        code = _publish("0.1.1", source, bootstrap=True)
        assert code == 2
        assert _git(work, "ls-remote", "origin", "refs/heads/release-state") == ""

    def test_bootstrap_publishes_atomic_tag_and_state(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _, work, _, source, _ = _make_repo(tmp_path)
        monkeypatch.chdir(work)
        _patch_local_publication(monkeypatch)
        code = _publish("0.1.1", source, bootstrap=True)
        assert code == 0
        remote = _git(
            work,
            "ls-remote",
            "origin",
            "refs/tags/v0.1.1",
            "refs/tags/v0.1.1^{}",
            "refs/heads/release-state",
        )
        assert "refs/tags/v0.1.1" in remote
        assert "refs/tags/v0.1.1^{}" in remote
        assert "refs/heads/release-state" in remote

    def test_missing_state_after_helper_tag_fails_closed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _, work, _, source, _ = _make_repo(tmp_path)
        monkeypatch.chdir(work)
        _patch_local_publication(monkeypatch)
        assert _publish("0.1.1", source, bootstrap=True) == 0
        _git(work, "push", "origin", ":refs/heads/release-state")
        (work / "README.md").write_text("three\n")
        _git(work, "commit", "-am", "third")
        new_source = _git(work, "rev-parse", "HEAD")
        _git(work, "push", "origin", "main")
        assert _publish("0.1.2", new_source) == 2

    def test_bootstrap_rejects_requested_source_with_retired_workflow(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        bare = tmp_path / "remote.git"
        work = tmp_path / "work"
        _git(tmp_path, "init", "--bare", str(bare))
        _git(tmp_path, "init", str(work))
        _git(work, "config", "user.name", "Test User")
        _git(work, "config", "user.email", "test@example.invalid")
        workflow = work / ".github" / "workflows"
        workflow.mkdir(parents=True)
        (workflow / "tag-main.yml").write_text("name: old tagger\n")
        (work / "README.md").write_text("one\n")
        _git(work, "add", ".")
        _git(work, "commit", "-m", "legacy")
        _git(work, "tag", "v0.1.0")
        (work / "README.md").write_text("two\n")
        _git(work, "commit", "-am", "stale-source")
        stale_source = _git(work, "rev-parse", "HEAD")
        (workflow / "tag-main.yml").unlink()
        _git(work, "add", ".github/workflows/tag-main.yml")
        _git(work, "commit", "-m", "retire workflow")
        _git(work, "branch", "-M", "main")
        _git(work, "remote", "add", "origin", str(bare))
        _git(work, "push", "origin", "main", "refs/tags/v0.1.0")
        monkeypatch.chdir(work)
        _patch_local_publication(monkeypatch)

        assert _publish("0.1.1", stale_source, bootstrap=True) == 2


def _install_origin_transport(monkeypatch: pytest.MonkeyPatch, bare: Path) -> None:
    original_git = release_identity.git

    def transported_git(
        args: list[str],
        *,
        cwd: Path | None = None,
        input_text: str | None = None,
        harden: bool = False,
    ) -> str:
        if args and args[0] in {"ls-remote", "fetch", "push"}:
            mapped = [bare.as_uri() if arg == "origin" else arg for arg in args]
        else:
            mapped = args
        return original_git(mapped, cwd=cwd, input_text=input_text, harden=harden)

    monkeypatch.setattr(release_identity, "git", transported_git)


def _make_canonical_publication_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, str, str]:
    bare, work, ceiling, source, _ = _make_repo(tmp_path)
    _git(work, "remote", "set-url", "origin", release_identity.CANONICAL_HTTPS_URL)
    monkeypatch.chdir(work)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    _install_origin_transport(monkeypatch, bare)
    return bare, work, ceiling, source


def _install_fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "gh-calls.jsonl"
    script = bin_dir / "gh"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "from __future__ import annotations\n"
        "import json, pathlib, sys\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        "line = json.dumps(sys.argv[1:]) + '\\n'\n"
        "calls.write_text((calls.read_text() if calls.exists() else '') + line)\n"
        "method_ok = '--method' in sys.argv and "
        "sys.argv[sys.argv.index('--method') + 1] == 'GET'\n"
        "if not method_ok:\n"
        "    raise SystemExit(7)\n"
        "print(json.dumps({\n"
        "    'total_count': 1,\n"
        "    'workflow_runs': [{'status': 'completed', 'workflow_id': 339307243}],\n"
        "}))\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return calls


def _publish_raises(version: str, source: str, *, bootstrap: bool = False) -> str:
    try:
        release_identity.publish(
            argparse.Namespace(
                version=version, source=source, bootstrap=bootstrap, yes=True
            )
        )
    except release_identity.ReleaseIdentityError as exc:
        return str(exc)
    raise AssertionError("publish unexpectedly succeeded")


def _commit_next(work: Path, text: str) -> str:
    (work / "README.md").write_text(text)
    _git(work, "commit", "-am", text.strip())
    return _git(work, "rev-parse", "HEAD")


def _remote_tag_object(work: Path, tag: str) -> str:
    del work
    return release_identity.ls_remote([f"refs/tags/{tag}"])[f"refs/tags/{tag}"]


def _prepare_competing_publication(version: str, source: str) -> tuple[str, str, str]:
    main, state, tags = release_identity.fetch_remote_state()
    assert state is not None
    metadata = release_identity.validate_state(state, tags, main)
    ceiling = next(tag for tag in tags if tag.name == metadata.ceiling_tag)
    token_oid = release_identity.create_token_commit(
        source, version, ceiling.name, ceiling.peeled_oid
    )
    tag_oid = release_identity.create_annotated_tag_object(
        version, source, ceiling.name, ceiling.peeled_oid
    )
    return state, token_oid, tag_oid


def _fake_commit_with_readme(work: Path, parent: str, text: str) -> str:
    original = (work / "README.md").read_text()
    try:
        (work / "README.md").write_text(text)
        _git(work, "add", "README.md")
        tree = _git(work, "write-tree")
    finally:
        (work / "README.md").write_text(original)
        _git(work, "add", "README.md")
    return _git(work, "commit-tree", tree, "-p", parent, "-m", "replacement")


def _side_source(work: Path, ceiling: str) -> tuple[str, str]:
    current = _git(work, "rev-parse", "--abbrev-ref", "HEAD")
    _git(work, "checkout", "-b", "side", ceiling)
    (work / "README.md").write_text("side\n")
    _git(work, "commit", "-am", "side")
    side = _git(work, "rev-parse", "HEAD")
    _git(work, "checkout", current)
    main = _git(work, "rev-parse", "HEAD")
    return side, main


class TestReleaseIdentityNativeSafetyMatrix:
    def test_publish_path_rejects_push_instead_of_without_bypassing_destination(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, work, _, source = _make_canonical_publication_repo(tmp_path, monkeypatch)
        _git(
            work,
            "config",
            "url.https://evil.example/.pushInsteadOf",
            "https://github.com/",
        )

        message = _publish_raises("0.1.1", source, bootstrap=True)

        assert "pushInsteadOf" in message

    def test_bootstrap_uses_gh_get_query_without_replacing_query_function(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, _, _, source = _make_canonical_publication_repo(tmp_path, monkeypatch)
        calls = _install_fake_gh(tmp_path, monkeypatch)

        assert _publish("0.1.1", source, bootstrap=True) == 0

        argv = json.loads(calls.read_text().splitlines()[0])
        assert "--method" in argv
        assert argv[argv.index("--method") + 1] == "GET"
        assert "workflows/339307243/runs" in " ".join(argv)

    def test_bootstrap_rejects_release_state_that_appears_during_old_run_query(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bare, _, _, source = _make_canonical_publication_repo(tmp_path, monkeypatch)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        script = bin_dir / "gh"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "from __future__ import annotations\n"
            "import json, subprocess, sys\n"
            "method_ok = '--method' in sys.argv and "
            "sys.argv[sys.argv.index('--method') + 1] == 'GET'\n"
            "if not method_ok:\n"
            "    raise SystemExit(7)\n"
            f"subprocess.run(['git', '--git-dir', {str(bare)!r}, 'update-ref', "
            f"{release_identity.STATE_REF!r}, {source!r}], check=True)\n"
            "print(json.dumps({\n"
            "    'total_count': 1,\n"
            "    'workflow_runs': [\n"
            "        {'status': 'completed', 'workflow_id': 339307243}\n"
            "    ],\n"
            "}))\n"
        )
        script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

        message = _publish_raises("0.1.1", source, bootstrap=True)

        assert "release-state appeared during bootstrap checks" in message

    def test_state_token_ignores_ambient_commit_encoding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, work, ceiling, source = _make_canonical_publication_repo(
            tmp_path, monkeypatch
        )
        _git(work, "config", "i18n.commitEncoding", "ISO-8859-1")

        token = release_identity.create_token_commit(
            source, "v0.1.1", "v0.1.0", ceiling
        )
        raw = _git(work, "cat-file", "commit", token)

        assert "encoding ISO-8859-1" not in raw
        assert release_identity.HELPER_MARKER in raw

    def test_source_on_main_ignores_local_replace_refs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, work, ceiling, _ = _make_canonical_publication_repo(tmp_path, monkeypatch)
        side, main = _side_source(work, ceiling)
        replacement_main = _fake_commit_with_readme(work, side, "replacement main\n")
        _git(work, "replace", main, replacement_main)

        with pytest.raises(
            release_identity.ReleaseIdentityError, match="not on remote main"
        ):
            release_identity.ensure_source_on_main(side, main)

    def test_source_on_main_ignores_local_graft_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, work, ceiling, _ = _make_canonical_publication_repo(tmp_path, monkeypatch)
        side, main = _side_source(work, ceiling)
        git_dir = Path(_git(work, "rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = work / git_dir
        info = git_dir / "info"
        info.mkdir(exist_ok=True)
        (info / "grafts").write_text(f"{main} {side}\n")

        with pytest.raises(
            release_identity.ReleaseIdentityError, match="not on remote main"
        ):
            release_identity.ensure_source_on_main(side, main)

    def test_token_and_archive_reads_ignore_replaced_source_object(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, work, ceiling, source = _make_canonical_publication_repo(
            tmp_path, monkeypatch
        )
        expected_token = release_identity.create_token_commit(
            source, "v0.1.1", "v0.1.0", ceiling
        )
        replacement_source = _fake_commit_with_readme(
            work, ceiling, "replacement content\n"
        )
        _git(work, "replace", source, replacement_source)

        actual_token = release_identity.create_token_commit(
            source, "v0.1.1", "v0.1.0", ceiling
        )
        with release_identity.create_git_tree_context(source) as context_dir:
            archived_readme = (Path(context_dir) / "README.md").read_text()

        assert actual_token == expected_token
        assert archived_readme == "two\n"

    def test_lost_push_response_revalidates_fresh_remote_main(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bare, work, ceiling, source = _make_canonical_publication_repo(
            tmp_path, monkeypatch
        )
        _install_fake_gh(tmp_path, monkeypatch)
        assert _publish("0.1.1", source, bootstrap=True) == 0
        new_source = _commit_next(work, "three\n")
        _git(work, "push", str(bare), "main")
        original_git = release_identity.git
        failed_once = False

        def failing_after_success(
            args: list[str],
            *,
            cwd: Path | None = None,
            input_text: str | None = None,
            harden: bool = False,
        ) -> str:
            nonlocal failed_once
            if args and args[0] == "push" and not failed_once:
                failed_once = True
                output = original_git(
                    args, cwd=cwd, input_text=input_text, harden=harden
                )
                _git(bare, "update-ref", "refs/heads/main", ceiling)
                raise release_identity.ReleaseIdentityError(
                    f"simulated lost response after push: {output}"
                )
            return original_git(args, cwd=cwd, input_text=input_text, harden=harden)

        monkeypatch.setattr(release_identity, "git", failing_after_success)

        message = _publish_raises("0.1.2", new_source)

        assert failed_once
        assert "not on remote main" in message or "source" in message

    def test_successful_lost_push_response_reconciles_same_pair(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, work, _, source = _make_canonical_publication_repo(tmp_path, monkeypatch)
        _install_fake_gh(tmp_path, monkeypatch)
        assert _publish("0.1.1", source, bootstrap=True) == 0
        new_source = _commit_next(work, "three\n")
        release_identity.git(["push", "origin", "main"])
        original_git = release_identity.git
        failed_once = False

        def lost_response_after_success(
            args: list[str],
            *,
            cwd: Path | None = None,
            input_text: str | None = None,
            harden: bool = False,
        ) -> str:
            nonlocal failed_once
            if args and args[0] == "push" and not failed_once:
                failed_once = True
                output = original_git(
                    args, cwd=cwd, input_text=input_text, harden=harden
                )
                raise release_identity.ReleaseIdentityError(
                    f"simulated lost response after push: {output}"
                )
            return original_git(args, cwd=cwd, input_text=input_text, harden=harden)

        monkeypatch.setattr(release_identity, "git", lost_response_after_success)

        assert _publish("0.1.2", new_source) == 0
        assert failed_once

    def test_competing_different_version_publish_loses_real_stale_lease_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, work, _, source = _make_canonical_publication_repo(tmp_path, monkeypatch)
        _install_fake_gh(tmp_path, monkeypatch)
        assert _publish("0.1.1", source, bootstrap=True) == 0
        new_source = _commit_next(work, "three\n")
        release_identity.git(["push", "origin", "main"])
        old_state, winner_token, winner_tag = _prepare_competing_publication(
            "v0.1.3", new_source
        )
        original_git = release_identity.git
        loser_push: list[str] | None = None

        def publish_winner_then_loser(
            args: list[str],
            *,
            cwd: Path | None = None,
            input_text: str | None = None,
            harden: bool = False,
        ) -> str:
            nonlocal loser_push
            if args and args[0] == "push" and loser_push is None:
                loser_push = args
                original_git(
                    [
                        "push",
                        "--atomic",
                        f"--force-with-lease={release_identity.STATE_REF}:{old_state}",
                        "origin",
                        f"{winner_tag}:refs/tags/v0.1.3",
                        f"{winner_token}:{release_identity.STATE_REF}",
                    ]
                )
            return original_git(args, cwd=cwd, input_text=input_text, harden=harden)

        monkeypatch.setattr(release_identity, "git", publish_winner_then_loser)

        message = _publish_raises("0.1.2", new_source)

        assert loser_push is not None
        assert "stale info" in message or "failed" in message
        main, state, tags = release_identity.fetch_remote_state()
        assert state == winner_token
        assert any(
            tag.name == "v0.1.3" and tag.peeled_oid == new_source for tag in tags
        )
        assert all(tag.name != "v0.1.2" for tag in tags)
        release_identity.validate_state(state, tags, main)

    def test_competing_same_pair_publish_reconciles_after_real_stale_lease(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, work, _, source = _make_canonical_publication_repo(tmp_path, monkeypatch)
        _install_fake_gh(tmp_path, monkeypatch)
        assert _publish("0.1.1", source, bootstrap=True) == 0
        new_source = _commit_next(work, "three\n")
        release_identity.git(["push", "origin", "main"])
        old_state, winner_token, winner_tag = _prepare_competing_publication(
            "v0.1.2", new_source
        )
        original_git = release_identity.git
        loser_push: list[str] | None = None

        def publish_same_pair_then_loser(
            args: list[str],
            *,
            cwd: Path | None = None,
            input_text: str | None = None,
            harden: bool = False,
        ) -> str:
            nonlocal loser_push
            if args and args[0] == "push" and loser_push is None:
                loser_push = args
                original_git(
                    [
                        "push",
                        "--atomic",
                        f"--force-with-lease={release_identity.STATE_REF}:{old_state}",
                        "origin",
                        f"{winner_tag}:refs/tags/v0.1.2",
                        f"{winner_token}:{release_identity.STATE_REF}",
                    ]
                )
            return original_git(args, cwd=cwd, input_text=input_text, harden=harden)

        monkeypatch.setattr(release_identity, "git", publish_same_pair_then_loser)

        assert _publish("0.1.2", new_source) == 0

        assert loser_push is not None
        main, state, tags = release_identity.fetch_remote_state()
        assert state == winner_token
        assert [tag.name for tag in tags if tag.name == "v0.1.2"] == ["v0.1.2"]
        release_identity.validate_state(state, tags, main)

    def test_validate_state_rejects_helper_tag_at_or_below_claimed_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, work, ceiling, source = _make_canonical_publication_repo(
            tmp_path, monkeypatch
        )
        _install_fake_gh(tmp_path, monkeypatch)
        assert _publish("0.1.1", source, bootstrap=True) == 0
        helper_below_ceiling = release_identity.create_annotated_tag_object(
            "v0.0.9", ceiling, "v0.1.0", ceiling
        )
        release_identity.git(
            ["push", "origin", f"{helper_below_ceiling}:refs/tags/v0.0.9"]
        )

        main, state, tags = release_identity.fetch_remote_state()

        assert state is not None
        with pytest.raises(
            release_identity.ReleaseIdentityError,
            match="strictly beyond the frozen legacy ceiling",
        ):
            release_identity.validate_state(state, tags, main)

    def test_validate_state_rejects_helper_as_frozen_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, work, _, source = _make_canonical_publication_repo(tmp_path, monkeypatch)
        _install_fake_gh(tmp_path, monkeypatch)
        assert _publish("0.1.1", source, bootstrap=True) == 0
        new_source = _commit_next(work, "three\n")
        release_identity.git(["push", "origin", "main"])
        bad_token = release_identity.create_token_commit(
            new_source, "v0.1.2", "v0.1.1", source
        )
        bad_tag = release_identity.create_annotated_tag_object(
            "v0.1.2", new_source, "v0.1.1", source
        )
        release_identity.git(
            [
                "push",
                "--atomic",
                "origin",
                f"{bad_tag}:refs/tags/v0.1.2",
                f"+{bad_token}:{release_identity.STATE_REF}",
            ]
        )

        main, state, tags = release_identity.fetch_remote_state()

        assert state == bad_token
        with pytest.raises(
            release_identity.ReleaseIdentityError,
            match="frozen legacy ceiling cannot be helper-era tag",
        ):
            release_identity.validate_state(state, tags, main)

    def test_validate_state_rejects_changed_frozen_ceiling_across_helper_tags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, work, ceiling, source = _make_canonical_publication_repo(
            tmp_path, monkeypatch
        )
        _install_fake_gh(tmp_path, monkeypatch)
        assert _publish("0.1.1", source, bootstrap=True) == 0
        _git(work, "tag", "v0.0.9", ceiling)
        release_identity.git(["push", "origin", "refs/tags/v0.0.9"])
        new_source = _commit_next(work, "three\n")
        release_identity.git(["push", "origin", "main"])
        bad_token = release_identity.create_token_commit(
            new_source, "v0.1.2", "v0.0.9", ceiling
        )
        bad_tag = release_identity.create_annotated_tag_object(
            "v0.1.2", new_source, "v0.0.9", ceiling
        )
        release_identity.git(
            [
                "push",
                "--atomic",
                "origin",
                f"{bad_tag}:refs/tags/v0.1.2",
                f"+{bad_token}:{release_identity.STATE_REF}",
            ]
        )

        main, state, tags = release_identity.fetch_remote_state()

        assert state == bad_token
        with pytest.raises(
            release_identity.ReleaseIdentityError,
            match="changed the frozen legacy ceiling",
        ):
            release_identity.validate_state(state, tags, main)

    def test_fetch_remote_state_rejects_malformed_helper_marked_tag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, _, _, source = _make_canonical_publication_repo(tmp_path, monkeypatch)
        malformed_payload = (
            f"object {source}\n"
            "type commit\n"
            "tag v0.1.1\n"
            f"tagger {release_identity.IDENTITY_NAME} "
            f"<{release_identity.IDENTITY_EMAIL}> {release_identity.IDENTITY_TIME}\n"
            "\n"
            f"{release_identity.HELPER_MARKER}\n"
            "tag=v0.1.1\n"
        )
        malformed = release_identity.git(["mktag"], input_text=malformed_payload)
        release_identity.git(["push", "origin", f"{malformed}:refs/tags/v0.1.1"])

        with pytest.raises(
            release_identity.ReleaseIdentityError,
            match="malformed helper-era metadata",
        ):
            release_identity.fetch_remote_state()

    def test_token_and_tag_objects_ignore_ambient_git_config_overrides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, work, ceiling, source = _make_canonical_publication_repo(
            tmp_path, monkeypatch
        )
        baseline_token = release_identity.create_token_commit(
            source, "v0.1.1", "v0.1.0", ceiling
        )
        baseline_tag = release_identity.create_annotated_tag_object(
            "v0.1.1", source, "v0.1.0", ceiling
        )
        _git(work, "config", "i18n.commitEncoding", "ISO-8859-1")
        home = tmp_path / "home"
        home.mkdir()
        (home / ".gitconfig").write_text("[i18n]\n\tcommitEncoding = ISO-8859-1\n")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'i18n.commitEncoding=ISO-8859-1'")
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "i18n.commitEncoding")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "ISO-8859-1")

        assert (
            release_identity.create_token_commit(source, "v0.1.1", "v0.1.0", ceiling)
            == baseline_token
        )
        assert (
            release_identity.create_annotated_tag_object(
                "v0.1.1", source, "v0.1.0", ceiling
            )
            == baseline_tag
        )
        token_body = release_identity.git(["cat-file", "commit", baseline_token])
        assert "encoding ISO-8859-1" not in token_body

    def test_competing_different_source_publish_loses_real_stale_lease_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, work, _, source = _make_canonical_publication_repo(tmp_path, monkeypatch)
        _install_fake_gh(tmp_path, monkeypatch)
        assert _publish("0.1.1", source, bootstrap=True) == 0
        loser_source = _commit_next(work, "three\n")
        winner_source = _commit_next(work, "four\n")
        release_identity.git(["push", "origin", "main"])
        old_state, winner_token, winner_tag = _prepare_competing_publication(
            "v0.1.2", winner_source
        )
        original_git = release_identity.git
        loser_push: list[str] | None = None

        def publish_winner_then_loser(
            args: list[str],
            *,
            cwd: Path | None = None,
            input_text: str | None = None,
            harden: bool = False,
        ) -> str:
            nonlocal loser_push
            if args and args[0] == "push" and loser_push is None:
                loser_push = args
                original_git(
                    [
                        "push",
                        "--atomic",
                        f"--force-with-lease={release_identity.STATE_REF}:{old_state}",
                        "origin",
                        f"{winner_tag}:refs/tags/v0.1.2",
                        f"{winner_token}:{release_identity.STATE_REF}",
                    ]
                )
            return original_git(args, cwd=cwd, input_text=input_text, harden=harden)

        monkeypatch.setattr(release_identity, "git", publish_winner_then_loser)

        message = _publish_raises("0.1.2", loser_source)

        assert loser_push is not None
        assert "stale info" in message or "failed" in message
        main, state, tags = release_identity.fetch_remote_state()
        assert state == winner_token
        assert any(
            tag.name == "v0.1.2" and tag.peeled_oid == winner_source for tag in tags
        )
        release_identity.validate_state(state, tags, main)

    def test_same_source_different_version_competing_tag_blocks_publish(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, work, ceiling, source = _make_canonical_publication_repo(
            tmp_path, monkeypatch
        )
        _install_fake_gh(tmp_path, monkeypatch)
        assert _publish("0.1.1", source, bootstrap=True) == 0
        competing = release_identity.create_annotated_tag_object(
            "v0.1.2", source, "v0.1.0", ceiling
        )
        release_identity.git(["push", "origin", f"{competing}:refs/tags/v0.1.2"])
        new_source = _commit_next(work, "three\n")
        release_identity.git(["push", "origin", "main"])

        message = _publish_raises("0.1.3", new_source)

        assert "greatest canonical tag" in message

    def test_validate_state_rejects_future_tag_beyond_release_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, work, ceiling, source = _make_canonical_publication_repo(
            tmp_path, monkeypatch
        )
        _install_fake_gh(tmp_path, monkeypatch)
        assert _publish("0.1.1", source, bootstrap=True) == 0
        future = release_identity.create_annotated_tag_object(
            "v0.1.2", source, "v0.1.0", ceiling
        )
        release_identity.git(["push", "origin", f"{future}:refs/tags/v0.1.2"])

        main, state, tags = release_identity.fetch_remote_state()

        assert state is not None
        with pytest.raises(
            release_identity.ReleaseIdentityError, match="greatest canonical tag"
        ):
            release_identity.validate_state(state, tags, main)

    @pytest.mark.parametrize("mutation", ["delete", "retarget"])
    def test_validate_state_rejects_deleted_or_retargeted_legacy_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
    ) -> None:
        _, work, _, source = _make_canonical_publication_repo(tmp_path, monkeypatch)
        _install_fake_gh(tmp_path, monkeypatch)
        assert _publish("0.1.1", source, bootstrap=True) == 0
        if mutation == "delete":
            release_identity.git(["push", "origin", ":refs/tags/v0.1.0"])
        else:
            replacement = release_identity.create_annotated_tag_object(
                "v0.1.0", source, "v0.1.0", source
            )
            release_identity.git(
                ["push", "--force", "origin", f"{replacement}:refs/tags/v0.1.0"]
            )

        main, state, tags = release_identity.fetch_remote_state()

        assert state is not None
        with pytest.raises(
            release_identity.ReleaseIdentityError,
            match="frozen legacy ceiling tag is missing or points elsewhere",
        ):
            release_identity.validate_state(state, tags, main)

    def test_clean_context_rejects_wrong_head_dirty_tracked_and_untracked_leaks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, work, _, source = _make_canonical_publication_repo(tmp_path, monkeypatch)
        previous = _git(work, "rev-parse", "HEAD~1")
        _git(work, "checkout", previous)
        with pytest.raises(release_identity.ReleaseIdentityError, match="HEAD"):
            release_identity.ensure_clean_context(source)
        _git(work, "checkout", "main")

        (work / "README.md").write_text("tracked dirty\n")
        with pytest.raises(release_identity.ReleaseIdentityError, match="worktree"):
            release_identity.ensure_clean_context(source)
        (work / "README.md").write_text("two\n")

        (work / "leak.txt").write_text("do not package\n")
        with pytest.raises(release_identity.ReleaseIdentityError, match="worktree"):
            release_identity.ensure_clean_context(source)

        (work / "leak.txt").unlink()
        tree = release_identity.ensure_clean_context(source)
        with release_identity.create_git_tree_context(source) as context_dir:
            context = Path(context_dir)
            assert not (context / "leak.txt").exists()
            assert (context / "README.md").read_text() == "two\n"
            assert tree == release_identity.git(["rev-parse", f"{source}^{{tree}}"])

    def test_receipt_rejects_annotated_tag_object_replacement_with_same_source(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bare, work, ceiling, source = _make_canonical_publication_repo(
            tmp_path, monkeypatch
        )
        _install_fake_gh(tmp_path, monkeypatch)
        assert _publish("0.1.1", source, bootstrap=True) == 0
        before = _remote_tag_object(work, "v0.1.1")

        class MutatingBuilder:
            def build(
                self, context: Path, version: str, build_source: str
            ) -> list[release_identity.ReceiptImage]:
                metadata = release_identity.tag_message(
                    "v0.1.1", build_source, "v0.1.0", ceiling
                )
                payload = (
                    f"object {build_source}\n"
                    "type commit\n"
                    "tag v0.1.1\n"
                    "tagger Other <other@example.invalid> 946684801 +0000\n"
                    "\n"
                    f"{metadata}"
                )
                replacement = release_identity.git(
                    ["mktag"], cwd=bare, input_text=payload
                )
                _git(bare, "update-ref", "refs/tags/v0.1.1", replacement)
                return [
                    release_identity.ReceiptImage(
                        variant="default",
                        mutable_tag="default",
                        image_id="sha256:" + "a" * 64,
                        labels={},
                        runtime_import_version=version,
                    ),
                    release_identity.ReceiptImage(
                        variant="aquacast",
                        mutable_tag="aquacast",
                        image_id="sha256:" + "b" * 64,
                        labels={},
                        runtime_import_version=version,
                    ),
                ]

        with pytest.raises(
            release_identity.ReleaseIdentityError, match="tag object changed"
        ):
            release_identity.build_receipt(
                argparse.Namespace(
                    version="0.1.1",
                    source=source,
                    receipt=str(tmp_path / "receipt.json"),
                ),
                docker_builder=MutatingBuilder(),
            )

        after = _remote_tag_object(work, "v0.1.1")
        assert after != before

    def test_receipt_rejects_incomplete_image_payload_without_writing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, _, _, source = _make_canonical_publication_repo(tmp_path, monkeypatch)
        _install_fake_gh(tmp_path, monkeypatch)
        assert _publish("0.1.1", source, bootstrap=True) == 0
        receipt = tmp_path / "receipt.json"

        class MissingAquacastBuilder:
            def build(
                self, context: Path, version: str, build_source: str
            ) -> list[release_identity.ReceiptImage]:
                assert context.exists()
                assert build_source == source
                return [
                    release_identity.ReceiptImage(
                        variant="default",
                        mutable_tag="default",
                        image_id="sha256:" + "a" * 64,
                        labels={},
                        runtime_import_version=version,
                    )
                ]

        with pytest.raises(
            release_identity.ReleaseIdentityError,
            match="requires default and aquacast images",
        ):
            release_identity.build_receipt(
                argparse.Namespace(
                    version="0.1.1", source=source, receipt=str(receipt)
                ),
                docker_builder=MissingAquacastBuilder(),
            )

        assert not receipt.exists()

    def test_receipt_rejects_concurrent_writer_before_atomic_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, _, _, source = _make_canonical_publication_repo(tmp_path, monkeypatch)
        _install_fake_gh(tmp_path, monkeypatch)
        assert _publish("0.1.1", source, bootstrap=True) == 0
        receipt = tmp_path / "receipt.json"

        class ConcurrentWriterBuilder:
            def build(
                self, context: Path, version: str, build_source: str
            ) -> list[release_identity.ReceiptImage]:
                receipt.write_text("other writer\n")
                return [
                    release_identity.ReceiptImage(
                        variant="default",
                        mutable_tag="default",
                        image_id="sha256:" + "a" * 64,
                        labels={},
                        runtime_import_version=version,
                    ),
                    release_identity.ReceiptImage(
                        variant="aquacast",
                        mutable_tag="aquacast",
                        image_id="sha256:" + "b" * 64,
                        labels={},
                        runtime_import_version=version,
                    ),
                ]

        with pytest.raises(
            release_identity.ReleaseIdentityError, match="receipt path already exists"
        ):
            release_identity.build_receipt(
                argparse.Namespace(
                    version="0.1.1", source=source, receipt=str(receipt)
                ),
                docker_builder=ConcurrentWriterBuilder(),
            )

        assert receipt.read_text() == "other writer\n"
