from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FULL_SHA_RE = re.compile(r"\+g[0-9a-f]{40}(?:\.d\d{8})?$")
_UV_FIXTURE_ENV_BLOCKLIST = {"UV_FROZEN", "UV_LOCKED", "UV_NO_SYNC", "UV_OFFLINE"}


def _run(
    repo: Path,
    args: list[str],
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SETUPTOOLS_SCM_PRETEND_VERSION")
        and key not in _UV_FIXTURE_ENV_BLOCKLIST
    }
    command_env.update(env or {})
    proc = subprocess.run(
        args,
        cwd=repo,
        env=command_env,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"command failed: {args}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def _git(repo: Path, *args: str) -> str:
    return _run(repo, ["git", *args]).stdout.strip()


def _actual_scm_config() -> tuple[list[str], str, dict[str, str]]:
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
    build_system = pyproject["build-system"]
    scm = pyproject["tool"]["setuptools_scm"]
    return (
        build_system["requires"],
        build_system["build-backend"],
        {
            "tag_regex": scm["tag_regex"],
            "git_describe_command": scm["git_describe_command"],
            "version_file": scm["version_file"],
        },
    )


def _write_local_dependency(path: Path, name: str, version: str = "1.0.0") -> None:
    package = name.replace("-", "_")
    (path / package).mkdir(parents=True)
    (path / package / "__init__.py").write_text("VALUE = 1\n")
    (path / "pyproject.toml").write_text(
        "\n".join(
            [
                "[build-system]",
                'requires = ["setuptools==84.0.0"]',
                'build-backend = "setuptools.build_meta"',
                "",
                "[project]",
                f"name = {json.dumps(name)}",
                f"version = {json.dumps(version)}",
                f"description = {json.dumps(name)}",
                'requires-python = ">=3.12"',
                "",
            ]
        )
    )


def _write_fixture_repo(repo: Path, deps_root: Path) -> None:
    build_requires, build_backend, scm = _actual_scm_config()
    (repo / "src" / "sapphire_flow").mkdir(parents=True)
    (repo / "src" / "sapphire_flow" / "__init__.py").write_text(
        "from ._version import __version__\n"
    )
    (repo / "README.md").write_text("merge regression fixture\n")
    (repo / ".gitignore").write_text(
        "src/sapphire_flow/_version.py\nsrc/sapphire_flow.egg-info/\ndist/\nbuild/\n"
    )
    (repo / "setup.py").write_text((_REPO_ROOT / "setup.py").read_text())
    (repo / "pyproject.toml").write_text(
        "\n".join(
            [
                "[build-system]",
                f"requires = {json.dumps(build_requires)}",
                f"build-backend = {json.dumps(build_backend)}",
                "",
                "[project]",
                'name = "sapphire-flow"',
                'dynamic = ["version"]',
                'description = "merge regression fixture"',
                'readme = "README.md"',
                'requires-python = ">=3.12"',
                "dependencies = []",
                "",
                "[tool.setuptools.packages.find]",
                'where = ["src"]',
                "",
                "[tool.setuptools_scm]",
                f"tag_regex = {json.dumps(scm['tag_regex'])}",
                f"git_describe_command = {json.dumps(scm['git_describe_command'])}",
                f"version_file = {json.dumps(scm['version_file'])}",
                "",
                "[dependency-groups]",
                "compat_a = []",
                "compat_b = []",
                "conflict_a = []",
                "conflict_b = []",
                "",
            ]
        )
    )
    _write_local_dependency(deps_root / "compat-a", "compat-a")
    _write_local_dependency(deps_root / "compat-b", "compat-b")
    _write_local_dependency(deps_root / "conflict-v1", "conflict-pkg", "1.0.0")
    _write_local_dependency(deps_root / "conflict-v2", "conflict-pkg", "2.0.0")


def _init_fixture_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    deps_root = tmp_path / "deps"
    repo.mkdir()
    deps_root.mkdir()
    _write_fixture_repo(repo, deps_root)
    _run(repo, ["git", "init"])
    _run(repo, ["git", "config", "user.email", "test@example.invalid"])
    _run(repo, ["git", "config", "user.name", "Release Identity Test"])
    _run(repo, ["git", "add", "."])
    _run(repo, ["git", "commit", "-m", "base dynamic package"])
    _run(repo, ["git", "tag", "v0.1.0"])
    _run(repo, ["uv", "lock"])
    base_lock = (repo / "uv.lock").read_text()
    _run(repo, ["git", "add", "uv.lock"])
    _run(repo, ["git", "commit", "-m", "lock dynamic package"])
    base_commit = _git(repo, "rev-parse", "HEAD")
    return repo, base_commit, hashlib.sha256(base_lock.encode()).hexdigest()


def _replace_line(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def _commit_code_branch(repo: Path, base_commit: str, branch: str, module: str) -> None:
    _run(repo, ["git", "checkout", base_commit])
    _run(repo, ["git", "checkout", "-b", branch])
    (repo / "src" / "sapphire_flow" / f"{module}.py").write_text(
        f"TRACE = {json.dumps(branch)}\n"
    )
    _run(repo, ["git", "add", "."])
    _run(repo, ["git", "commit", "-m", f"code branch {branch}"])


def _commit_dependency_branch(
    repo: Path, base_commit: str, branch: str, old: str, new: str
) -> None:
    pyproject = repo / "pyproject.toml"
    _run(repo, ["git", "checkout", base_commit])
    _run(repo, ["git", "checkout", "-b", branch])
    _replace_line(pyproject, old, new)
    _run(repo, ["uv", "lock"])
    _run(repo, ["git", "add", "pyproject.toml", "uv.lock"])
    _run(repo, ["git", "commit", "-m", f"dependency branch {branch}"])


def _wheel_version(repo: Path) -> str:
    shutil.rmtree(repo / "dist", ignore_errors=True)
    _run(repo, ["uv", "build", "--wheel", "--out-dir", "dist"])
    wheels = sorted((repo / "dist").glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        metadata_name = next(
            name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = wheel.read(metadata_name).decode()
    version_line = next(
        line for line in metadata.splitlines() if line.startswith("Version: ")
    )
    return version_line.removeprefix("Version: ")


def _assert_versionless_root_lock(repo: Path, expected_lock_hash: str) -> None:
    lock = (repo / "uv.lock").read_text()
    assert hashlib.sha256(lock.encode()).hexdigest() == expected_lock_hash
    marker = 'name = "sapphire-flow"'
    start = lock.index(marker)
    end = lock.find("\n[[package]]", start + 1)
    root_block = lock[start:] if end == -1 else lock[start:end]
    assert 'version = "' not in root_block


def _resolve_dependency_integration(repo: Path, pyproject_text: str) -> None:
    (repo / "pyproject.toml").write_text(pyproject_text)
    _run(repo, ["git", "checkout", "HEAD", "--", "uv.lock"], check=False)
    _run(repo, ["git", "add", "pyproject.toml", "uv.lock"])


def _controlled_integrate_dependency_branches(
    repo: Path,
    base_commit: str,
    branch_a: str,
    branch_b: str,
    pyproject_text: str,
) -> subprocess.CompletedProcess[str]:
    _run(repo, ["git", "checkout", base_commit])
    _run(repo, ["git", "checkout", "-b", f"integrate-{branch_a}-{branch_b}"])
    _run(repo, ["git", "merge", "--no-ff", branch_a, "-m", f"merge {branch_a}"])
    merge = _run(
        repo,
        ["git", "merge", "--no-ff", branch_b, "-m", f"merge {branch_b}"],
        check=False,
    )
    if merge.returncode != 0:
        assert "CONFLICT" in merge.stdout
        _resolve_dependency_integration(repo, pyproject_text)
    return merge


def test_code_only_sibling_merge_orders_keep_lock_and_traceable_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SETUPTOOLS_SCM_PRETEND_VERSION", "9.9.9")
    monkeypatch.setenv("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SAPPHIRE_FLOW", "9.9.9")
    repo, base_commit, expected_lock_hash = _init_fixture_repo(tmp_path)
    _commit_code_branch(repo, base_commit, "code-a", "branch_a")
    _commit_code_branch(repo, base_commit, "code-b", "branch_b")

    seen_versions: set[str] = set()
    for branch, first, second in [
        ("merge-a-then-b", "code-a", "code-b"),
        ("merge-b-then-a", "code-b", "code-a"),
    ]:
        _run(repo, ["git", "checkout", base_commit])
        _run(repo, ["git", "checkout", "-b", branch])
        _run(repo, ["git", "merge", "--no-ff", first, "-m", f"merge {first}"])
        _run(repo, ["git", "merge", "--no-ff", second, "-m", f"merge {second}"])
        _run(repo, ["uv", "lock", "--check"])
        _assert_versionless_root_lock(repo, expected_lock_hash)
        source_revision = _git(repo, "rev-parse", "HEAD")
        version = _wheel_version(repo)

        assert version != "9.9.9"
        assert source_revision in version
        assert _FULL_SHA_RE.search(version)
        seen_versions.add(version)

    assert len(seen_versions) == 2


def test_compatible_dependency_branches_integrate_and_lock_both_constraints(
    tmp_path: Path,
) -> None:
    repo, base_commit, _expected_lock_hash = _init_fixture_repo(tmp_path)
    deps_root = tmp_path / "deps"
    compat_a = f'compat_a = ["compat-a @ file://{deps_root / "compat-a"}"]'
    compat_b = f'compat_b = ["compat-b @ file://{deps_root / "compat-b"}"]'
    _commit_dependency_branch(repo, base_commit, "dep-a", "compat_a = []", compat_a)
    _commit_dependency_branch(repo, base_commit, "dep-b", "compat_b = []", compat_b)
    combined = (repo / "pyproject.toml").read_text()
    combined = combined.replace("compat_a = []", compat_a).replace(
        "compat_b = []", compat_b
    )

    _controlled_integrate_dependency_branches(
        repo, base_commit, "dep-a", "dep-b", combined
    )
    _run(repo, ["uv", "lock"])
    _run(repo, ["git", "add", "pyproject.toml", "uv.lock"])
    _run(repo, ["git", "commit", "-m", "resolve compatible dependency integration"])
    lock = (repo / "uv.lock").read_text()

    assert 'name = "compat-a"' in lock
    assert 'name = "compat-b"' in lock
    assert "compat-a" in (repo / "pyproject.toml").read_text()
    assert "compat-b" in (repo / "pyproject.toml").read_text()


def test_incompatible_dependency_branches_are_valid_but_combined_lock_fails(
    tmp_path: Path,
) -> None:
    repo, base_commit, _expected_lock_hash = _init_fixture_repo(tmp_path)
    deps_root = tmp_path / "deps"
    conflict_a = f'conflict_a = ["conflict-pkg @ file://{deps_root / "conflict-v1"}"]'
    conflict_b = f'conflict_b = ["conflict-pkg @ file://{deps_root / "conflict-v2"}"]'
    _commit_dependency_branch(
        repo, base_commit, "conflict-a", "conflict_a = []", conflict_a
    )
    _commit_dependency_branch(
        repo, base_commit, "conflict-b", "conflict_b = []", conflict_b
    )
    combined = (repo / "pyproject.toml").read_text()
    combined = combined.replace("conflict_a = []", conflict_a).replace(
        "conflict_b = []", conflict_b
    )

    _controlled_integrate_dependency_branches(
        repo, base_commit, "conflict-a", "conflict-b", combined
    )
    proc = _run(repo, ["uv", "lock"], check=False)

    assert proc.returncode != 0
    assert "conflicting URLs" in proc.stderr
    assert "conflict-pkg" in proc.stderr
    assert str(deps_root / "conflict-v1") in proc.stderr
    assert str(deps_root / "conflict-v2") in proc.stderr
