from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "release_target", ROOT / "tools/release_target.py"
)
assert spec is not None and spec.loader is not None
target = importlib.util.module_from_spec(spec)
spec.loader.exec_module(target)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=target.child_environment(dict(os.environ)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repository(root: Path, raw: bytes = b"1.2.3\n") -> str:
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Target Test")
    git(root, "config", "user.email", "target@example.invalid")
    (root / "RELEASE_TARGET").write_bytes(raw)
    (root / ".bumpversion.toml").write_bytes((ROOT / ".bumpversion.toml").read_bytes())
    git(root, "add", ".")
    git(root, "commit", "-m", "base")
    return git(root, "rev-parse", "HEAD")


def merged_pr(root: Path, raw: bytes = b"1.2.4\n") -> tuple[str, str, str]:
    repository(root)
    git(root, "checkout", "-b", "pr")
    (root / "RELEASE_TARGET").write_bytes(raw)
    git(root, "commit", "-am", "PR target")
    head = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "main")
    (root / "other").write_text("concurrent unrelated change\n")
    git(root, "add", "other")
    git(root, "commit", "-m", "base advance")
    base = git(root, "rev-parse", "HEAD")
    git(root, "merge", "--no-ff", "pr", "-m", "tested merge")
    return base, head, git(root, "rev-parse", "HEAD")


class TestCheckPr:
    def test_exact_merge_pair_passes_without_head_containing_base(
        self, tmp_path: Path
    ) -> None:
        target.check_pr(tmp_path, *merged_pr(tmp_path))

    @pytest.mark.parametrize("raw", [b"1.2.5\n", b"1.3.4\n", b"1.2.4\r\n", b"1.2.4"])
    def test_bad_arithmetic_or_bytes_refuse(self, tmp_path: Path, raw: bytes) -> None:
        pair = merged_pr(tmp_path, raw)
        with pytest.raises(target.ReleaseIdentityError, match="target"):
            target.check_pr(tmp_path, *pair)

    def test_wrong_head_checkout_refuses(self, tmp_path: Path) -> None:
        base, head, merge = merged_pr(tmp_path)
        git(tmp_path, "checkout", head)
        with pytest.raises(target.ReleaseIdentityError, match="HEAD"):
            target.check_pr(tmp_path, base, head, merge)

    def test_reversed_parents_refuse(self, tmp_path: Path) -> None:
        base, head, merge = merged_pr(tmp_path)
        with pytest.raises(target.ReleaseIdentityError, match="parents"):
            target.check_pr(tmp_path, head, base, merge)

    def test_single_parent_refuses(self, tmp_path: Path) -> None:
        base = repository(tmp_path)
        (tmp_path / "RELEASE_TARGET").write_bytes(b"1.2.4\n")
        git(tmp_path, "commit", "-am", "single")
        head = git(tmp_path, "rev-parse", "HEAD")
        with pytest.raises(target.ReleaseIdentityError, match="parents"):
            target.check_pr(tmp_path, base, head, head)

    def test_merge_target_not_head_refuses(self, tmp_path: Path) -> None:
        base, head, _ = merged_pr(tmp_path)
        (tmp_path / "RELEASE_TARGET").write_bytes(b"1.2.5\n")
        git(tmp_path, "commit", "-am", "alter merge", "--amend")
        merge = git(tmp_path, "rev-parse", "HEAD")
        with pytest.raises(target.ReleaseIdentityError, match="differs"):
            target.check_pr(tmp_path, base, head, merge)

    def test_missing_base_target_is_not_generic_bootstrap(self, tmp_path: Path) -> None:
        repository(tmp_path)
        git(tmp_path, "rm", "RELEASE_TARGET")
        git(tmp_path, "commit", "-m", "missing")
        base = git(tmp_path, "rev-parse", "HEAD")
        with pytest.raises(
            target.ReleaseIdentityError, match="outside exact bootstrap"
        ):
            target.next_target(tmp_path, base)

    def test_exact_bootstrap_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository(tmp_path)
        git(tmp_path, "rm", "RELEASE_TARGET")
        git(tmp_path, "commit", "-m", "historical base")
        base = git(tmp_path, "rev-parse", "HEAD")
        monkeypatch.setattr(target, "BOOTSTRAP_BASE_SHA", base)
        assert target.next_target(tmp_path, base).text == "0.1.1068"

    def test_existing_bootstrap_base_target_uses_normal_arithmetic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = repository(tmp_path)
        monkeypatch.setattr(target, "BOOTSTRAP_BASE_SHA", base)
        assert target.next_target(tmp_path, base).text == "1.2.4"

    def test_second_same_target_pr_refuses_advanced_base(self, tmp_path: Path) -> None:
        repository(tmp_path, b"1.2.4\n")
        base = git(tmp_path, "rev-parse", "HEAD")
        git(tmp_path, "checkout", "-b", "second")
        (tmp_path / "other").write_text("second PR\n")
        git(tmp_path, "add", "other")
        git(tmp_path, "commit", "-m", "stale target")
        head = git(tmp_path, "rev-parse", "HEAD")
        git(tmp_path, "checkout", "main")
        git(tmp_path, "merge", "--no-ff", "second", "-m", "second merge")
        with pytest.raises(target.ReleaseIdentityError, match="exactly once"):
            target.check_pr(tmp_path, base, head, git(tmp_path, "rev-parse", "HEAD"))


class TestWorkingFiles:
    @pytest.mark.parametrize(
        "addition",
        ["setup_hooks = ['touch marker']", 'current_version = "1.2.3"', 'parse = ".*"'],
    )
    @pytest.mark.parametrize("raw", [b"1.2.3\n", b"1.2.4\n"])
    def test_unsafe_config_refuses_before_tool_and_noop(
        self, tmp_path: Path, addition: str, raw: bytes
    ) -> None:
        base = repository(tmp_path)
        (tmp_path / "RELEASE_TARGET").write_bytes(raw)
        config = tmp_path / ".bumpversion.toml"
        config.write_text(
            config.read_text().replace("tag = false", "tag = false\n" + addition)
        )
        with pytest.raises(target.ReleaseIdentityError, match="unsafe bump config"):
            target.bump(tmp_path, base)
        assert not (tmp_path / "marker").exists()
        assert (tmp_path / "RELEASE_TARGET").read_bytes() == raw

    def test_additional_file_rule_refuses(self, tmp_path: Path) -> None:
        base = repository(tmp_path)
        config = tmp_path / ".bumpversion.toml"
        config.write_text(
            config.read_text() + '\n[[tool.bumpversion.files]]\nfilename = "other"\n'
        )
        with pytest.raises(target.ReleaseIdentityError, match="unsafe bump config"):
            target.bump(tmp_path, base)

    def test_noop_does_not_need_uv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = repository(tmp_path)
        (tmp_path / "RELEASE_TARGET").write_bytes(b"1.2.4\n")
        monkeypatch.setattr(target.shutil, "which", lambda _: None)
        target.bump(tmp_path, base)

    def test_malformed_target_refuses_before_noop(self, tmp_path: Path) -> None:
        base = repository(tmp_path)
        (tmp_path / "RELEASE_TARGET").write_bytes(b"1.2.4\r\n")
        with pytest.raises(target.ReleaseIdentityError, match="canonical ASCII"):
            target.bump(tmp_path, base)

    @pytest.mark.parametrize("name", ["RELEASE_TARGET", ".bumpversion.toml"])
    def test_symlinks_refuse(self, tmp_path: Path, name: str) -> None:
        base = repository(tmp_path)
        path = tmp_path / name
        destination = tmp_path / "destination"
        path.rename(destination)
        path.symlink_to(destination)
        with pytest.raises(target.ReleaseIdentityError, match="regular file"):
            target.bump(tmp_path, base)

    def test_divergent_target_refuses(self, tmp_path: Path) -> None:
        base = repository(tmp_path)
        (tmp_path / "RELEASE_TARGET").write_bytes(b"1.2.9\n")
        with pytest.raises(target.ReleaseIdentityError, match="equal base"):
            target.bump(tmp_path, base)


class TestChildEnvironment:
    def test_only_explicit_keys_survive(self) -> None:
        env = target.child_environment(
            {
                "HOME": "/home",
                "PATH": "/bin",
                "PYTHONHOME": "/bad",
                "bumpversion_new_version": "9.9.9",
                "HTTPS_PROXY": "bad",
                "SSL_CERT_FILE": "bad",
                "FUTURE_OVERRIDE": "bad",
            }
        )
        assert env["HOME"] == "/home"
        assert set(env) == {
            "HOME",
            "PATH",
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_NO_REPLACE_OBJECTS",
            "GIT_GRAFT_FILE",
            "GIT_TERMINAL_PROMPT",
        }

    @pytest.mark.parametrize("dirty", [False, True])
    def test_native_tool_ignores_overrides_and_preserves_unrelated_files(
        self, tmp_path: Path, dirty: bool
    ) -> None:
        base = repository(tmp_path)
        tools = tmp_path / "tools"
        tools.mkdir()
        for name in ("release_target.py", "release_identity.py"):
            (tools / name).write_bytes((ROOT / "tools" / name).read_bytes())
        git(tmp_path, "add", "tools")
        git(tmp_path, "commit", "-m", "tool fixture")
        if dirty:
            (tmp_path / "unrelated").write_text("keep this edit\n")
        before_refs = git(tmp_path, "show-ref")
        before_config = (tmp_path / ".bumpversion.toml").read_bytes()
        env = dict(os.environ)
        env.update(
            {
                "BUMPVERSION_NEW_VERSION": "9.9.9",
                "bumpversion_new_version": "8.8.8",
                "BUMPVERSION_COMMIT": "true",
                "BUMPVERSION_TAG": "true",
                "UV_CONFIG_FILE": "/missing",
                "UV_PROJECT": "/missing",
                "UV_INDEX_URL": "https://invalid.example.invalid/simple",
                "SSL_CERT_FILE": "/missing",
                "HTTPS_PROXY": "http://127.0.0.1:1",
                "https_proxy": "http://127.0.0.1:1",
                "PYTHONHOME": "/missing",
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(tools / "release_target.py"),
                "bump",
                "--base-sha",
                base,
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "RELEASE_TARGET").read_bytes() == b"1.2.4\n"
        assert (tmp_path / ".bumpversion.toml").read_bytes() == before_config
        assert git(tmp_path, "show-ref") == before_refs
        if dirty:
            assert (tmp_path / "unrelated").read_text() == "keep this edit\n"


class TestGitTargetBlob:
    @pytest.mark.parametrize("kind", ["symlink", "tree", "gitlink"])
    def test_nonregular_entries_refuse(self, tmp_path: Path, kind: str) -> None:
        base = repository(tmp_path)
        path = tmp_path / "RELEASE_TARGET"
        path.unlink()
        git(tmp_path, "rm", "--cached", "RELEASE_TARGET")
        if kind == "symlink":
            path.symlink_to("other")
            git(tmp_path, "add", "RELEASE_TARGET")
        elif kind == "tree":
            path.mkdir()
            (path / "nested").write_text("1.2.3\n")
            git(tmp_path, "add", "RELEASE_TARGET")
        else:
            git(
                tmp_path,
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{base},RELEASE_TARGET",
            )
        git(tmp_path, "commit", "-m", "nonregular target")
        with pytest.raises(target.ReleaseIdentityError, match="regular Git blob"):
            target.read_release_target(git(tmp_path, "rev-parse", "HEAD"), cwd=tmp_path)

    def test_executable_regular_blob_is_valid(self, tmp_path: Path) -> None:
        repository(tmp_path)
        git(tmp_path, "update-index", "--chmod=+x", "RELEASE_TARGET")
        git(tmp_path, "commit", "-m", "executable blob")
        assert (
            target.read_release_target(
                git(tmp_path, "rev-parse", "HEAD"), cwd=tmp_path
            ).text
            == "1.2.3"
        )

    def test_replace_refs_do_not_change_source_target(self, tmp_path: Path) -> None:
        source = repository(tmp_path)
        (tmp_path / "RELEASE_TARGET").write_bytes(b"9.9.9\n")
        git(tmp_path, "commit", "-am", "replacement")
        replacement = git(tmp_path, "rev-parse", "HEAD")
        git(tmp_path, "replace", source, replacement)
        assert target.read_release_target(source, cwd=tmp_path).text == "1.2.3"


class TestExecutableResolution:
    def test_offroot_relative_path_uses_discovered_uv_not_root_decoy(
        self, tmp_path: Path
    ) -> None:
        import shutil

        real_uv = shutil.which("uv")
        assert real_uv is not None
        real_uv = str(Path(real_uv).resolve())
        root = tmp_path / "project"
        caller = tmp_path / "caller"
        root.mkdir()
        caller.mkdir()
        base = repository(root)
        tools = root / "tools"
        tools.mkdir()
        for name in ("release_target.py", "release_identity.py"):
            (tools / name).write_bytes((ROOT / "tools" / name).read_bytes())
        selected = tmp_path / "selected-marker"
        decoy = tmp_path / "decoy-marker"
        for directory, marker, action in (
            (caller, selected, f"os.execv({real_uv!r}, [{real_uv!r}, *sys.argv[1:]])"),
            (root, decoy, "raise SystemExit(73)"),
        ):
            bindir = directory / "bin"
            bindir.mkdir()
            shim = bindir / "uv"
            shim.write_text(
                f"#!{sys.executable}\nimport os, pathlib, sys\n"
                f"pathlib.Path({str(marker)!r}).write_text('invoked')\n{action}\n"
            )
            shim.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = "bin" + os.pathsep + env["PATH"]
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(tools / "release_target.py"),
                "bump",
                "--base-sha",
                base,
            ],
            cwd=caller,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert selected.exists(), f"decoy={decoy.exists()}: {result.stderr}"
        assert not decoy.exists()
        assert result.returncode == 0, result.stderr
        assert (root / "RELEASE_TARGET").read_bytes() == b"1.2.4\n"
