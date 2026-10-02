from __future__ import annotations

import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOWS = [
    _REPO_ROOT / ".github/workflows/ci.yml",
    _REPO_ROOT / ".github/workflows/integration-nightly.yml",
    _REPO_ROOT / ".github/workflows/live-lindas-weekly.yml",
    _REPO_ROOT / ".github/workflows/dependency-safety.yml",
]


def test_workflows_set_full_sha_scm_override_after_head_verification() -> None:
    for path in _WORKFLOWS:
        text = path.read_text()
        if "uv sync" not in text:
            continue
        assert "git rev-parse HEAD" in text
        assert '"$head_sha" != "$GITHUB_SHA"' in text
        assert (
            "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SAPPHIRE_FLOW=0.dev0+g${GITHUB_SHA}"
            in text
        )


def test_native_uv_project_cache_keys_include_version_override() -> None:
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
    cache_keys = data["tool"]["uv"]["cache-keys"]

    assert {"file": "pyproject.toml"} in cache_keys
    assert {"file": "setup.py"} in cache_keys
    assert {"git": {"commit": True, "tags": True}} in cache_keys
    assert {"env": "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SAPPHIRE_FLOW"} in cache_keys


def test_setup_uv_download_cache_is_not_per_commit_suffixed() -> None:
    text = "\n".join(path.read_text() for path in _WORKFLOWS)
    assert "cache-suffix:" not in text


def test_private_tokens_are_not_cache_keys_or_build_args() -> None:
    text = "\n".join(path.read_text() for path in _WORKFLOWS)
    for line in text.splitlines():
        if "cache-suffix" in line or "build-args" in line:
            assert "TOKEN" not in line
