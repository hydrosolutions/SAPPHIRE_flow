"""Plan 402 T4 D14 — tools/check_map_contract_version.py, end-to-end
against a real git repo (mirrors tests/unit/tools/test_dependency_safety.py
::TestMainSkipPassEndToEnd)."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

from tools.check_map_contract_version import _FILE, main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_DOC_V1 = {"info": {"version": "1.0"}, "paths": {}}


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_contract(repo: Path, doc: dict) -> None:
    path = repo / _FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))


class TestCheckMapContractVersion:
    def test_absent_at_base_passes(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / "README.md").write_text("base\n")
        base_sha = _commit(repo, "base")

        _write_contract(repo, _DOC_V1)
        _commit(repo, "add contract")

        monkeypatch.chdir(repo)
        assert main(["--base-ref", base_sha]) == 0
        assert "new file" in capsys.readouterr().out

    def test_unchanged_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _write_contract(repo, _DOC_V1)
        base_sha = _commit(repo, "base")

        (repo / "README.md").write_text("unrelated\n")
        _commit(repo, "unrelated change")

        monkeypatch.chdir(repo)
        assert main(["--base-ref", base_sha]) == 0

    def test_changed_with_the_same_version_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _write_contract(repo, _DOC_V1)
        base_sha = _commit(repo, "base")

        _write_contract(repo, {"info": {"version": "1.0"}, "paths": {"/x": {}}})
        _commit(repo, "change contract, same version")

        monkeypatch.chdir(repo)
        assert main(["--base-ref", base_sha]) == 1
        assert "bump info.version" in capsys.readouterr().out

    def test_changed_with_a_greater_version_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _write_contract(repo, _DOC_V1)
        base_sha = _commit(repo, "base")

        _write_contract(repo, {"info": {"version": "1.1"}, "paths": {"/x": {}}})
        _commit(repo, "change contract, minor bump")

        monkeypatch.chdir(repo)
        assert main(["--base-ref", base_sha]) == 0

    def test_1_9_to_1_10_counts_as_greater(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _write_contract(repo, {"info": {"version": "1.9"}, "paths": {}})
        base_sha = _commit(repo, "base")

        _write_contract(repo, {"info": {"version": "1.10"}, "paths": {"/x": {}}})
        _commit(repo, "change contract, 1.9 -> 1.10")

        monkeypatch.chdir(repo)
        assert main(["--base-ref", base_sha]) == 0

    def test_major_bump_from_a_lower_minor_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A same-or-lower MINOR is not itself disqualifying once the MAJOR
        is greater — the comparison is the (major, minor) tuple."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        _write_contract(repo, {"info": {"version": "1.9"}, "paths": {}})
        base_sha = _commit(repo, "base")

        _write_contract(repo, {"info": {"version": "2.0"}, "paths": {"/x": {}}})
        _commit(repo, "change contract, major bump")

        monkeypatch.chdir(repo)
        assert main(["--base-ref", base_sha]) == 0
