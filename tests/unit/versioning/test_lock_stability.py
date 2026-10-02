from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_root_project_version_is_dynamic_not_literal() -> None:
    text = (_REPO_ROOT / "pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in text
    assert 'version = "0.1.' not in text


def test_lockfile_no_longer_records_root_package_version() -> None:
    text = (_REPO_ROOT / "uv.lock").read_text()
    marker = 'name = "sapphire-flow"'
    index = text.index(marker)
    end = text.find("\n[[package]]", index + 1)
    block = text[index:] if end == -1 else text[index:end]
    assert 'version = "' not in block
