"""Plan 082 Task 2H-dep: recap-dg-client git-pin dependency.

Structural checks only (TOML parse, never a substring scan): the parsed
``pyproject.toml`` ``[project.dependencies]`` requirement names and
``[tool.uv.sources]`` keys, and the ``uv.lock`` package source, must all name
``recap-dg-client`` with a pinned git revision.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, cast

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PACKAGE_NAME = "recap-dg-client"


def _load_pyproject() -> dict[str, Any]:
    with (_REPO_ROOT / "pyproject.toml").open("rb") as f:
        return cast("dict[str, Any]", tomllib.load(f))


def _load_uv_lock() -> dict[str, Any]:
    with (_REPO_ROOT / "uv.lock").open("rb") as f:
        return cast("dict[str, Any]", tomllib.load(f))


class TestGitPin:
    def test_dependency_listed_in_project_dependencies(self) -> None:
        data = _load_pyproject()
        deps = cast("list[str]", data["project"]["dependencies"])
        names = {canonicalize_name(Requirement(dep).name) for dep in deps}
        assert canonicalize_name(_PACKAGE_NAME) in names

    def test_git_source_declared_in_uv_sources(self) -> None:
        data = _load_pyproject()
        sources = cast("dict[str, Any]", data["tool"]["uv"]["sources"])
        assert _PACKAGE_NAME in sources
        source = sources[_PACKAGE_NAME]
        assert isinstance(source, dict)
        assert "git" in source
        assert "rev" in source
        assert source["rev"] != ""

    def test_uv_lock_records_a_git_pin_with_rev(self) -> None:
        lock = _load_uv_lock()
        packages = cast("list[dict[str, Any]]", lock["package"])
        matches = [p for p in packages if canonicalize_name(p["name"]) == _PACKAGE_NAME]
        assert len(matches) == 1, (
            f"expected exactly one {_PACKAGE_NAME!r} entry in uv.lock, got {matches!r}"
        )
        source = matches[0]["source"]
        assert isinstance(source, dict)
        git_url = source.get("git", "")
        assert isinstance(git_url, str)
        assert "rev=" in git_url
