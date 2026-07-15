"""Unit tests for tools.dependency_safety — Plan 119 dependency-bump safety gate.

Regression fixtures below are keyed to the exact deceptions the classifier
must not fall for (see docs/plans/119-dependency-bump-safety-gate.md §1):
comment-vs-field drift (PR #78 shape) and "major digit unchanged" tag schemes
(CPython minor-axis risk). Each fixture pair states, in its docstring, which
wrong implementation it catches.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from tools.dependency_safety import (
    Finding,
    Verdict,
    classify_ci_workflow_diff,
    classify_docker_compose_diff,
    classify_dockerfile_diff,
    classify_pr,
    classify_pyproject_diff,
    classify_uv_lock_diff,
    load_allowlist,
    main,
    parse_cpython_tag,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _only(findings: list[Finding]) -> Finding:
    assert len(findings) == 1, findings
    return findings[0]


class TestClassifyDockerComposePostgisBlock:
    """PR #78 shape: real tag bumps major, trailing comment stays stale."""

    _OLD = """\
services:
  postgres:
    image: postgis/postgis:16-3.4@sha256:aaa  # postgis/postgis:16-3.4
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
    _NEW_MAJOR_BUMP_STALE_COMMENT = """\
services:
  postgres:
    image: postgis/postgis:17-3.4@sha256:bbb  # postgis/postgis:16-3.4
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
    _COMMENT_FIXED_IMAGE_UNCHANGED = """\
services:
  postgres:
    image: postgis/postgis:17-3.4@sha256:bbb  # postgis/postgis:17-3.4
    volumes:
      - pgdata:/var/lib/postgresql/data
"""

    def test_blocks_on_real_tag_bump_despite_stale_comment(self) -> None:
        """Soundness: a classifier reading the `# name:tag` comment sees ZERO
        diff here (comment is identical before/after) and would silently
        pass PR #78 — the exact miss this plan exists to catch. Ours reads
        the `image:` field and fires.
        """
        findings = classify_docker_compose_diff(
            self._OLD, self._NEW_MAJOR_BUMP_STALE_COMMENT
        )
        finding = _only(findings)
        assert finding.verdict is Verdict.BLOCK
        assert finding.key == "docker-compose.yml:postgis/postgis"
        assert "16-3.4" in finding.message
        assert "17-3.4" in finding.message
        assert "Plan 118" in finding.message

    def test_comment_only_change_is_silent(self) -> None:
        """Soundness (converse): fixing the stale comment with the image field
        UNCHANGED must not itself trigger a finding — proves the classifier
        keys off the field, not comment text (a comment-diffing classifier
        would flag this instead, a false positive on a no-op fix).
        """
        findings = classify_docker_compose_diff(
            self._NEW_MAJOR_BUMP_STALE_COMMENT, self._COMMENT_FIXED_IMAGE_UNCHANGED
        )
        assert findings == []


class TestClassifyDockerComposeGenericStatefulRule:
    def test_prefect_major_bump_blocks_generic_rule(self) -> None:
        old = """\
services:
  prefect-server:
    image: prefecthq/prefect:3-python3.11@sha256:aaa  # prefecthq/prefect:3-python3.11
    volumes:
      - prefect_data:/data/prefect
"""
        new = """\
services:
  prefect-server:
    image: prefecthq/prefect:4-python3.11@sha256:bbb  # prefecthq/prefect:3-python3.11
    volumes:
      - prefect_data:/data/prefect
"""
        finding = _only(classify_docker_compose_diff(old, new))
        assert finding.verdict is Verdict.BLOCK
        assert finding.key == "docker-compose.yml:prefecthq/prefect"
        assert "Plan 118" not in finding.message

    def test_non_volume_service_major_bump_is_silent(self) -> None:
        """No `volumes:` mount -> not a stateful service -> ALLOW (silent)."""
        old = """\
services:
  api:
    image: sapphire-flow:1.0.0@sha256:aaa  # sapphire-flow:1.0.0
"""
        new = """\
services:
  api:
    image: sapphire-flow:2.0.0@sha256:bbb  # sapphire-flow:2.0.0
"""
        assert classify_docker_compose_diff(old, new) == []

    def test_patch_bump_on_stateful_image_is_silent(self) -> None:
        old = """\
services:
  postgres:
    image: postgis/postgis:16-3.4@sha256:aaa  # postgis/postgis:16-3.4
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
        new = """\
services:
  postgres:
    image: postgis/postgis:16-3.4@sha256:bbb  # postgis/postgis:16-3.4
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
        assert classify_docker_compose_diff(old, new) == []


class TestClassifyDockerfileMinorAxisBlock:
    """The risk axis for CPython tags is the MINOR (Y), not semver-major (X)."""

    _OLD = """\
FROM python:3.14.6-slim@sha256:aaa AS builder
RUN true
FROM python:3.14.6-slim@sha256:aaa
"""
    _NEW = """\
FROM python:3.15.0-slim@sha256:bbb AS builder
RUN true
FROM python:3.15.0-slim@sha256:bbb
"""

    def test_major_digit_unchanged_yet_blocks(self) -> None:
        """Soundness: assert X is unchanged (3 -> 3) via the same parser the
        classifier uses, then assert the classifier STILL blocks — proving
        the flag comes from Y, not a generic 'semver-major increased' test
        (which would see X==X and silently pass, per §1: 'do not reuse a
        generic semver-major-increased test here').
        """
        old_parts = parse_cpython_tag("3.14.6-slim")
        new_parts = parse_cpython_tag("3.15.0-slim")
        assert old_parts is not None and new_parts is not None
        assert old_parts[0] == new_parts[0] == 3  # major digit unchanged
        assert old_parts[1] != new_parts[1]  # minor changed: 14 -> 15

        finding = _only(classify_dockerfile_diff(self._OLD, self._NEW))
        assert finding.verdict is Verdict.BLOCK
        assert finding.key == "Dockerfile:python-base-image"
        assert "3.14.6-slim" in finding.message
        assert "3.15.0-slim" in finding.message

    @staticmethod
    def _two_stage_from(tag: str, digest: str = "aaa") -> str:
        stage = f"FROM python:{tag}@sha256:{digest}"
        return f"{stage} AS builder\n{stage}\n"

    def test_patch_only_bump_is_silent(self) -> None:
        old = self._two_stage_from("3.14.6-slim")
        new = self._two_stage_from("3.14.7-slim", digest="bbb")
        assert classify_dockerfile_diff(old, new) == []

    def test_digest_only_rebuild_is_silent(self) -> None:
        old = self._two_stage_from("3.14.6-slim", digest="aaa")
        new = self._two_stage_from("3.14.6-slim", digest="zzz")
        assert classify_dockerfile_diff(old, new) == []

    def test_flavor_change_blocks(self) -> None:
        old = self._two_stage_from("3.14.6-slim")
        new = (
            "FROM python:3.14.6@sha256:bbb AS builder\nFROM python:3.14.6@sha256:bbb\n"
        )
        finding = _only(classify_dockerfile_diff(old, new))
        assert finding.verdict is Verdict.BLOCK

    def test_base_image_family_change_blocks(self) -> None:
        old = "FROM python:3.14.6-slim@sha256:aaa AS builder\n"
        new = "FROM debian:13-slim@sha256:bbb AS builder\n"
        finding = _only(classify_dockerfile_diff(old, new))
        assert finding.verdict is Verdict.BLOCK
        assert finding.key == "Dockerfile:debian-base-image"


class TestClassifyPyproject:
    def test_requires_python_change_blocks(self) -> None:
        old = 'requires-python = ">=3.12"\n'
        new = 'requires-python = ">=3.13"\n'
        finding = _only(classify_pyproject_diff(old, new))
        assert finding.verdict is Verdict.BLOCK
        assert finding.key == "pyproject.toml:requires-python"
        assert ">=3.12" in finding.message
        assert ">=3.13" in finding.message

    def test_requires_python_unchanged_is_silent(self) -> None:
        text = 'requires-python = ">=3.12"\n'
        assert classify_pyproject_diff(text, text) == []

    def test_fi_git_pin_rev_change_reviews(self) -> None:
        old = (
            "[tool.uv.sources]\n"
            'forecastinterface = { git = "https://github.com/hydrosolutions/'
            'ForecastInterface.git", rev = "v0.1.17" }\n'
        )
        new = (
            "[tool.uv.sources]\n"
            'forecastinterface = { git = "https://github.com/hydrosolutions/'
            'ForecastInterface.git", rev = "v0.1.18" }\n'
        )
        finding = _only(classify_pyproject_diff(old, new))
        assert finding.verdict is Verdict.REVIEW
        assert finding.key == "pyproject.toml:forecastinterface-git-pin"


class TestClassifyUvLock:
    def test_normal_library_minor_bump_is_silent(self) -> None:
        """Ordinary pure-Python library majors/minors are ALLOW — §1 explicitly
        rejects flagging every library bump (rubber-stamp fatigue)."""
        old = '[[package]]\nname = "pydantic"\nversion = "2.12.5"\n'
        new = '[[package]]\nname = "pydantic"\nversion = "2.13.0"\n'
        assert classify_uv_lock_diff(old, new) == []

    def test_normal_library_major_bump_is_silent(self) -> None:
        old = '[[package]]\nname = "pydantic"\nversion = "2.12.5"\n'
        new = '[[package]]\nname = "pydantic"\nversion = "3.0.0"\n'
        assert classify_uv_lock_diff(old, new) == []

    def _native_ext_lock(self, name: str, version: str) -> str:
        return f'[[package]]\nname = "{name}"\nversion = "{version}"\n'

    def test_cfgrib_major_bump_reviews(self) -> None:
        old = self._native_ext_lock("cfgrib", "0.9.15.1")
        new = self._native_ext_lock("cfgrib", "1.0.0")
        finding = _only(classify_uv_lock_diff(old, new))
        assert finding.verdict is Verdict.REVIEW
        assert finding.key == "uv.lock:cfgrib"

    def test_rioxarray_major_bump_reviews(self) -> None:
        old = self._native_ext_lock("rioxarray", "0.22.0")
        new = self._native_ext_lock("rioxarray", "1.0.0")
        finding = _only(classify_uv_lock_diff(old, new))
        assert finding.verdict is Verdict.REVIEW
        assert finding.key == "uv.lock:rioxarray"

    def test_exactextract_major_bump_reviews(self) -> None:
        old = self._native_ext_lock("exactextract", "0.3.0")
        new = self._native_ext_lock("exactextract", "1.0.0")
        finding = _only(classify_uv_lock_diff(old, new))
        assert finding.verdict is Verdict.REVIEW
        assert finding.key == "uv.lock:exactextract"

    def test_forecastinterface_major_bump_reviews(self) -> None:
        old = self._native_ext_lock("forecastinterface", "0.1.17")
        new = self._native_ext_lock("forecastinterface", "1.0.0")
        finding = _only(classify_uv_lock_diff(old, new))
        assert finding.verdict is Verdict.REVIEW
        assert finding.key == "uv.lock:forecastinterface"

    def test_native_ext_minor_bump_is_silent(self) -> None:
        old = self._native_ext_lock("cfgrib", "0.9.15.1")
        new = self._native_ext_lock("cfgrib", "0.9.16.0")
        assert classify_uv_lock_diff(old, new) == []


class TestClassifyCiWorkflowPostgisServices:
    def test_postgis_major_confined_to_ephemeral_services_reviews_not_blocks(
        self,
    ) -> None:
        old = """\
jobs:
  integration:
    services:
      postgres:
        image: postgis/postgis:16-3.4@sha256:aaa  # postgis/postgis:16-3.4
"""
        new = """\
jobs:
  integration:
    services:
      postgres:
        image: postgis/postgis:17-3.4@sha256:bbb  # postgis/postgis:16-3.4
"""
        finding = _only(classify_ci_workflow_diff(old, new))
        assert finding.verdict is Verdict.REVIEW
        assert finding.key == ".github/workflows/ci.yml:integration:postgres:postgis"
        assert "lockstep" in finding.message

    def test_wheel_only_guard_job_change_reviews(self) -> None:
        old = """\
jobs:
  wheel-only-guard:
    runs-on: ubuntu-latest
    steps:
      - run: uv sync --frozen --no-build --no-install-package forecastinterface
"""
        new = """\
jobs:
  wheel-only-guard:
    runs-on: ubuntu-latest
    steps:
      - run: uv sync --frozen --no-build --no-install-package forecastinterface -v
"""
        finding = _only(classify_ci_workflow_diff(old, new))
        assert finding.verdict is Verdict.REVIEW
        assert finding.key == ".github/workflows/ci.yml:wheel-only-guard"

    def test_unrelated_job_change_is_silent(self) -> None:
        old = "jobs:\n  lint:\n    runs-on: ubuntu-latest\n"
        new = "jobs:\n  lint:\n    runs-on: ubuntu-24.04\n"
        assert classify_ci_workflow_diff(old, new) == []


class TestClassifyPrSkipPass:
    def test_empty_changed_files_is_allow(self) -> None:
        """The invariant §3 depends on: an unrelated-file-only PR must report
        a concrete ALLOW/success, never a missing/Expected status."""
        result = classify_pr({})
        assert result.verdict is Verdict.ALLOW
        assert result.findings == ()
        assert result.overridden == ()


class TestAllowlistOverride:
    def test_allowlisted_key_downgrades_block_to_allow(self) -> None:
        old = """\
services:
  postgres:
    image: postgis/postgis:16-3.4@sha256:aaa  # postgis/postgis:16-3.4
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
        new = """\
services:
  postgres:
    image: postgis/postgis:17-3.4@sha256:bbb  # postgis/postgis:16-3.4
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
        changed = {"docker-compose.yml": (old, new)}

        blocked = classify_pr(changed)
        assert blocked.verdict is Verdict.BLOCK

        overridden = classify_pr(
            changed, allowlist=frozenset({"docker-compose.yml:postgis/postgis"})
        )
        assert overridden.verdict is Verdict.ALLOW
        assert overridden.findings == ()
        assert len(overridden.overridden) == 1
        assert overridden.overridden[0].key == "docker-compose.yml:postgis/postgis"

    def test_load_allowlist_skips_comments_and_blanks(self, tmp_path: Path) -> None:
        allowlist_file = tmp_path / ".dependency-safety-allowlist"
        allowlist_file.write_text(
            "# Known-accepted overrides. Dated comment required.\n"
            "\n"
            "# 2026-07-15: verified safe on staging, see Plan 118 follow-up.\n"
            "docker-compose.yml:postgis/postgis\n"
            "\n"
        )
        assert load_allowlist(allowlist_file) == frozenset(
            {"docker-compose.yml:postgis/postgis"}
        )

    def test_load_allowlist_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert load_allowlist(tmp_path / "does-not-exist") == frozenset()


class TestMainSkipPassEndToEnd:
    """End-to-end proof (real git diff, not a hand-built fixture map): a PR
    touching only an unrelated file must skip-pass with exit 0 — the §3
    'never wedges a required check into Expected/pending' invariant.
    """

    def _init_repo(self, repo: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)

    def _commit(self, repo: Path, message: str) -> str:
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_docs_typo_only_pr_skip_passes(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "pyproject.toml").write_text('requires-python = ">=3.12"\n')
        (repo / "README.md").write_text("hello\n")
        base_sha = self._commit(repo, "base")

        (repo / "README.md").write_text("hello, typo fixed\n")
        self._commit(repo, "docs: fix typo")

        monkeypatch.chdir(repo)
        exit_code = main(["--base-ref", base_sha])
        assert exit_code == 0
        assert "skip-pass" in capsys.readouterr().out

    def test_requires_python_change_blocks_end_to_end(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "pyproject.toml").write_text('requires-python = ">=3.12"\n')
        base_sha = self._commit(repo, "base")

        (repo / "pyproject.toml").write_text('requires-python = ">=3.13"\n')
        self._commit(repo, "bump requires-python")

        monkeypatch.chdir(repo)
        exit_code = main(["--base-ref", base_sha])
        assert exit_code == 1
        assert "::error::" in capsys.readouterr().out
