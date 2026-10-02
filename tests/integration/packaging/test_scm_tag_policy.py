from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOL_PATH = _REPO_ROOT / "tools" / "release_identity.py"
spec = importlib.util.spec_from_file_location("release_identity", _TOOL_PATH)
assert spec is not None and spec.loader is not None
release_identity = importlib.util.module_from_spec(spec)
sys.modules["release_identity"] = release_identity
spec.loader.exec_module(release_identity)


def _configured_tag_regex() -> re.Pattern[str]:
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
    pattern = data["tool"]["setuptools_scm"]["tag_regex"]
    return re.compile(pattern)


def test_canonical_v_tag_matches_strict_native_regex() -> None:
    match = _configured_tag_regex().fullmatch("v1.2.3")
    assert match is not None
    assert match.group("version") == "1.2.3"


@pytest.mark.parametrize(
    "tag", ["v0.1.281-review", "release-v1.2.3", "v01.2.3", "v1.02.3", "v1.2.03"]
)
def test_noncanonical_legacy_or_ambiguous_tags_do_not_match_native_regex(
    tag: str,
) -> None:
    assert _configured_tag_regex().fullmatch(tag) is None
    with pytest.raises(release_identity.ReleaseIdentityError):
        release_identity.Version.parse_tag(tag)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=False
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return proc.stdout.strip()


def _write_probe(repo: Path) -> None:
    describe = (
        "git describe --dirty --tags --long --abbrev=40 "
        "--match v[0-9]*.[0-9]*.[0-9]* --exclude *-*"
    )
    pyproject = f"""[project]
name = "version-probe-scm"
dynamic = ["version"]
requires-python = ">=3.12"
[build-system]
requires = [
    "setuptools==84.0.0",
    "setuptools-scm==10.3.4",
    "packaging==26.3",
    "vcs-versioning==2.5.0",
]
build-backend = "setuptools.build_meta"
[tool.setuptools]
package-dir = {{"" = "src"}}
[tool.setuptools.packages.find]
where = ["src"]
[tool.setuptools_scm]
tag_regex = '^v(?P<version>(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*))$'
git_describe_command = '{describe}'
version_file = "src/version_probe_scm/_version.py"
"""
    (repo / "pyproject.toml").write_text(pyproject)
    pkg = repo / "src" / "version_probe_scm"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        "from version_probe_scm._version import __version__\n"
    )
    (repo / "setup.py").write_text(
        "from __future__ import annotations\n\n"
        "from typing import Protocol\n\n"
        "from setuptools import setup\n\n\n"
        "class ScmVersionLike(Protocol):\n"
        "    exact: bool\n"
        "    node: str | None\n\n"
        "    def format_choice(\n"
        "        self,\n"
        "        clean_format: str,\n"
        "        dirty_format: str,\n"
        "        **kwargs: object,\n"
        "    ) -> str: ...\n\n\n"
        "def full_revision_local_scheme(version: ScmVersionLike) -> str:\n"
        "    if version.exact:\n"
        "        return ''\n"
        "    if version.node is None:\n"
        "        raise ValueError(\n"
        "            'Git revision is required for development versions'\n"
        "        )\n"
        "    return version.format_choice(\n"
        "        '+{full_node}',\n"
        "        '+{full_node}.d{time:%Y%m%d}',\n"
        "        full_node=version.node,\n"
        "    )\n\n\n"
        "setup(use_scm_version={'local_scheme': full_revision_local_scheme})\n"
    )


def _build_probe(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "build", "--no-cache", "--out-dir", str(repo / "dist")],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )


def test_native_setuptools_scm_canonical_tag_builds_release(tmp_path: Path) -> None:
    repo = tmp_path / "probe"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.invalid")
    _write_probe(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "tag", "v1.2.3")

    proc = _build_probe(repo)

    assert proc.returncode == 0, proc.stderr
    assert (repo / "dist" / "version_probe_scm-1.2.3-py3-none-any.whl").exists()


def test_native_setuptools_scm_dev_build_uses_full_revision(tmp_path: Path) -> None:
    repo = tmp_path / "probe"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.invalid")
    _write_probe(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "tag", "v1.2.3")
    (repo / "src" / "version_probe_scm" / "extra.py").write_text("VALUE = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "next")
    head = _git(repo, "rev-parse", "HEAD")

    proc = _build_probe(repo)

    assert proc.returncode == 0, proc.stderr
    built_names = "\n".join(p.name for p in (repo / "dist").glob("*"))
    assert f"g{head}" in built_names


def test_native_setuptools_scm_noncanonical_tag_fails_closed(tmp_path: Path) -> None:
    repo = tmp_path / "probe"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.invalid")
    _write_probe(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "tag", "v0.1.281-review")

    proc = _build_probe(repo)

    assert proc.returncode == 0, proc.stderr
    built_names = "\n".join(p.name for p in (repo / "dist").glob("*"))
    assert "0.1.281" not in built_names
    assert "review" not in built_names


def test_root_backend_restricts_git_describe_candidates() -> None:
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
    command = data["tool"]["setuptools_scm"]["git_describe_command"]
    assert "--match v[0-9]*.[0-9]*.[0-9]*" in command
    assert "--exclude *-*" in command
