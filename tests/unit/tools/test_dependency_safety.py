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

import pytest

from tools.dependency_safety import (
    WATCHED_FILES,
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
        assert finding.key == "docker-compose.yml:pgdata:postgis/postgis:16-3.4->17-3.4"
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
        assert finding.key == (
            "docker-compose.yml:prefect_data:prefecthq/prefect:3-python3.11->4-python3.11"
        )
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

    def test_non_major_tag_bump_on_stateful_image_is_silent(self) -> None:
        """A real (non-major, non-digest-only) tag bump on a stateful image
        stays silent: the leading major-version digit is unchanged."""
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
    image: postgis/postgis:16-3.5@sha256:aaa  # postgis/postgis:16-3.4
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
        assert finding.key == "Dockerfile:python-base-image:3.14.6-slim->3.15.0-slim"
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
        assert finding.key == "Dockerfile:python->debian-base-image"


class TestClassifyPyproject:
    def test_requires_python_change_blocks(self) -> None:
        old = 'requires-python = ">=3.12"\n'
        new = 'requires-python = ">=3.13"\n'
        finding = _only(classify_pyproject_diff(old, new))
        assert finding.verdict is Verdict.BLOCK
        assert finding.key == "pyproject.toml:requires-python:>=3.12->>=3.13"
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
        """The override key is EXACT (file + volume + repo + old->new tag),
        not class-wide — see `TestAllowlistOverrideIsExactNotClassWide` for
        the bypass this format closes (an override minted for 16->17 must
        not silently clear 17->18 too)."""
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
        expected_key = "docker-compose.yml:pgdata:postgis/postgis:16-3.4->17-3.4"
        assert _only(list(blocked.findings)).key == expected_key

        overridden = classify_pr(changed, allowlist=frozenset({expected_key}))
        assert overridden.verdict is Verdict.ALLOW
        assert overridden.findings == ()
        assert len(overridden.overridden) == 1
        assert overridden.overridden[0].key == expected_key

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


# ---------------------------------------------------------------------------
# Adversarial-review hardening (2026-07-15): every class below reproduces a
# concrete BLOCK-returns-ALLOW bypass found in review. Each test is proven
# sound by running it against the pre-hardening classifier first (it fails —
# the finding list is empty / verdict is ALLOW / exit code is 0) and only
# then against the fixed classifier (it passes).
# ---------------------------------------------------------------------------


class TestClassifyDockerfileMultiStageAndTagShapeBypasses:
    def test_only_final_stage_bump_blocks(self) -> None:
        """Bypass: only `old_images[0]`/`new_images[0]` (the FIRST `FROM`)
        was ever compared. A PR bumping only the final (runtime) stage while
        the builder stage stays pinned returned [] (ALLOW) pre-fix, because
        the first FROM was identical on both sides.
        """
        old = (
            "FROM python:3.14.6-slim@sha256:aaa AS builder\n"
            "RUN true\n"
            "FROM python:3.14.6-slim@sha256:aaa\n"
        )
        new = (
            "FROM python:3.14.6-slim@sha256:aaa AS builder\n"
            "RUN true\n"
            "FROM python:3.15.0-slim@sha256:bbb\n"
        )
        finding = _only(classify_dockerfile_diff(old, new))
        assert finding.verdict is Verdict.BLOCK
        assert "3.14.6-slim" in finding.message
        assert "3.15.0-slim" in finding.message

    def test_two_component_cpython_tag_minor_bump_blocks(self) -> None:
        """Bypass: `parse_cpython_tag` required a full `X.Y.Z` tag and
        returned None for the valid two-component Docker Hub tag form
        `X.Y` (e.g. `python:3.14-slim`), so `3.14-slim -> 3.15-slim` fell
        through the `old_parts is None` early-return silently.
        """
        old = "FROM python:3.14-slim@sha256:aaa AS builder\n"
        new = "FROM python:3.15-slim@sha256:bbb AS builder\n"
        finding = _only(classify_dockerfile_diff(old, new))
        assert finding.verdict is Verdict.BLOCK
        assert finding.key.startswith("Dockerfile:python-base-image")

    def test_platform_flag_is_not_mistaken_for_the_image(self) -> None:
        """Bypass: `FROM --platform=... <image>` captured `--platform=...`
        itself as "the image" (first whitespace-delimited token after
        `FROM`). When the platform flag is identical old vs new but the
        REAL image tag changed, `old_image == new_image` (both equal to the
        platform-flag string) short-circuited to [] before the real image
        was ever parsed.
        """
        old = "FROM --platform=linux/amd64 python:3.14.6-slim@sha256:aaa AS builder\n"
        new = "FROM --platform=linux/amd64 python:3.15.0-slim@sha256:bbb AS builder\n"
        finding = _only(classify_dockerfile_diff(old, new))
        assert finding.verdict is Verdict.BLOCK


class TestClassifyDockerComposeVolumeKeyedBypasses:
    def test_service_renamed_reusing_volume_major_bump_still_blocks(self) -> None:
        """Bypass: the classifier keyed on the service NAME
        (`new_services.items()` / `old_services.get(name)`). Renaming
        `postgres` -> `db` while reusing the same `pgdata` named volume and
        bumping the major made `old_services.get("db")` return nothing, so
        `old_image` was treated as absent and the whole service was
        skipped -- a manual major bump slipped through as long as it came
        with a rename.
        """
        old = """\
services:
  postgres:
    image: postgis/postgis:16-3.4@sha256:aaa  # postgis/postgis:16-3.4
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
        new = """\
services:
  db:
    image: postgis/postgis:17-3.4@sha256:bbb  # postgis/postgis:16-3.4
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
        finding = _only(classify_docker_compose_diff(old, new))
        assert finding.verdict is Verdict.BLOCK
        assert finding.key == "docker-compose.yml:pgdata:postgis/postgis:16-3.4->17-3.4"

    def test_brand_new_volume_mounted_image_is_flagged_not_silent(self) -> None:
        """Bypass: adding a wholly NEW volume-mounted stateful service was
        silent -- `old_services.get(name)` found nothing for the new
        service name, `old_image` was None, and the loop skipped it exactly
        like the "not a real change" case. Post-fix: a brand-new
        volume-backed image is at least REVIEW (no prior version exists to
        diff a major against, so it cannot be BLOCK).
        """
        old = "services:\n  api:\n    image: sapphire-flow:1.0.0\n"
        new = """\
services:
  api:
    image: sapphire-flow:1.0.0
  redis:
    image: redis:7-alpine@sha256:ccc
    volumes:
      - redis_data:/data
"""
        finding = _only(classify_docker_compose_diff(old, new))
        assert finding.verdict is not Verdict.ALLOW

    def test_digest_only_change_on_stateful_volume_is_never_silent(self) -> None:
        """Bypass: the classifier parsed the version from the `image:`
        field AFTER dropping the `@sha256:...` digest
        (`_parse_image_field`), then compared only the resulting tags. A
        human can leave the tag `postgis/postgis:16-3.4` unchanged while
        re-pinning to a completely different digest -- old_tag == new_tag
        short-circuited to a silent ALLOW even though the actual image
        content pulled could be any major version.
        """
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
    image: postgis/postgis:16-3.4@sha256:zzz  # postgis/postgis:16-3.4
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
        finding = _only(classify_docker_compose_diff(old, new))
        assert finding.verdict is not Verdict.ALLOW
        assert "aaa" in finding.message
        assert "zzz" in finding.message


class TestAllowlistOverrideIsExactNotClassWide:
    _OLD_16 = """\
services:
  postgres:
    image: postgis/postgis:16-3.4@sha256:aaa  # postgis/postgis:16-3.4
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
    _NEW_17 = """\
services:
  postgres:
    image: postgis/postgis:17-3.4@sha256:bbb  # postgis/postgis:16-3.4
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
    _NEW_18 = """\
services:
  postgres:
    image: postgis/postgis:18-3.4@sha256:ccc  # postgis/postgis:16-3.4
    volumes:
      - pgdata:/var/lib/postgresql/data
"""

    def test_override_does_not_clear_a_different_major_transition(self) -> None:
        """Bypass: pre-hardening, the override key was class-wide --
        `docker-compose.yml:postgis/postgis` -- with no old->new tag
        transition encoded. An entry minted (and code-reviewed) to clear
        the 16->17 bump would ALSO silently clear every future major on
        that image, e.g. 17->18, 18->19, forever. Proof: an allowlist
        containing exactly that class-wide string must NOT clear a 17->18
        bump post-fix -- the exact (tag-scoped) key format no longer
        matches that string at all.
        """
        class_wide_key = "docker-compose.yml:postgis/postgis"  # pre-hardening shape

        result = classify_pr(
            {"docker-compose.yml": (self._NEW_17, self._NEW_18)},
            allowlist=frozenset({class_wide_key}),
        )
        assert result.verdict is Verdict.BLOCK
        assert result.overridden == ()
        finding = _only(list(result.findings))
        assert finding.key != class_wide_key
        assert finding.key == "docker-compose.yml:pgdata:postgis/postgis:17-3.4->18-3.4"

    def test_exact_key_still_clears_its_own_bump(self) -> None:
        """Converse: an override minted for the CURRENT finding's exact key
        still works -- this is not a regression to "nothing can ever be
        overridden", just "the override must name the exact bump".
        """
        blocked = classify_pr({"docker-compose.yml": (self._OLD_16, self._NEW_17)})
        finding = _only(list(blocked.findings))

        overridden = classify_pr(
            {"docker-compose.yml": (self._OLD_16, self._NEW_17)},
            allowlist=frozenset({finding.key}),
        )
        assert overridden.verdict is Verdict.ALLOW
        assert overridden.findings == ()
        assert len(overridden.overridden) == 1


class TestSelfPolicyFileChangeIsNeverSilent:
    """Bypass: the gate excluded its OWN policy files from WATCHED_FILES /
    `_CLASSIFIERS` entirely. A PR could weaken `tools/dependency_safety.py`,
    its workflow, or `.github/dependabot.yml` while making a dangerous bump
    elsewhere, and the neutered classifier would judge itself -- silently,
    since none of these paths had a registered classifier.
    """

    @pytest.mark.parametrize(
        "path",
        [
            "tools/dependency_safety.py",
            ".github/workflows/dependency-safety.yml",
            ".github/dependabot.yml",
            ".dependency-safety-allowlist",
        ],
    )
    def test_self_policy_file_change_is_flagged(self, path: str) -> None:
        result = classify_pr({path: ("old content\n", "new content\n")})
        assert result.verdict is not Verdict.ALLOW
        assert result.findings != ()

    def test_watched_files_includes_gate_policy_files(self) -> None:
        assert "tools/dependency_safety.py" in WATCHED_FILES
        assert ".github/workflows/dependency-safety.yml" in WATCHED_FILES
        assert ".github/dependabot.yml" in WATCHED_FILES
        assert ".dependency-safety-allowlist" in WATCHED_FILES


class TestGatherChangedWatchedFilesFailsClosed:
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

    def test_main_fails_closed_when_base_content_unreadable_for_modified_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Bypass: `_git_show` failures were swallowed and substituted with
        `""` (empty old text) for ANY failure reason, not just a genuine
        add. A transient `git show` failure on a MODIFIED watched file made
        every classifier see an empty "old" -- indistinguishable from "this
        file was just added" -- which for docker-compose.yml means
        `old_image` is missing and the whole finding is skipped. Proof:
        monkeypatch `_git_show` to always fail while a REAL dangerous major
        bump sits in the working tree; pre-fix `main()` returns 0 (pass)
        despite the failure. Post-fix it must fail closed (non-zero).
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "docker-compose.yml").write_text(
            "services:\n"
            "  postgres:\n"
            "    image: postgis/postgis:16-3.4@sha256:aaa\n"
            "    volumes:\n"
            "      - pgdata:/var/lib/postgresql/data\n"
        )
        base_sha = self._commit(repo, "base")

        (repo / "docker-compose.yml").write_text(
            "services:\n"
            "  postgres:\n"
            "    image: postgis/postgis:17-3.4@sha256:bbb\n"
            "    volumes:\n"
            "      - pgdata:/var/lib/postgresql/data\n"
        )
        self._commit(repo, "bump postgis major")

        monkeypatch.chdir(repo)
        monkeypatch.setattr("tools.dependency_safety._git_show", lambda ref, path: None)

        exit_code = main(["--base-ref", base_sha])
        assert exit_code == 1
        assert "base content" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# Fail-closed convergence (2026-07-15): re-review found the classifier still
# default-ALLOWed on four concrete unparseable/ambiguous shapes instead of
# failing closed. Each test below reproduces the dangerous change and asserts
# BLOCK/REVIEW, never ALLOW/[] — the opposite of the invariant this section's
# docstring block asserts for the earlier (already-hardened) bypasses.
# ---------------------------------------------------------------------------


class TestDockerfileAddedStageNeverSilent:
    """Bypass: `zip(old_images, new_images, strict=False)` truncates to the
    SHORTER list. Old two-stage build + a NEW final `FROM python:3.15...`
    stage in the new Dockerfile made `new_images` longer than `old_images`;
    the third (added) `FROM` had no positional pair and was silently
    dropped, returning `[]` for a brand-new, unvetted base image.
    """

    def test_added_final_python_stage_blocks(self) -> None:
        old = (
            "FROM python:3.14.6-slim@sha256:aaa AS builder\n"
            "RUN true\n"
            "FROM python:3.14.6-slim@sha256:aaa\n"
        )
        new = (
            "FROM python:3.14.6-slim@sha256:aaa AS builder\n"
            "RUN true\n"
            "FROM python:3.14.6-slim@sha256:aaa\n"
            "FROM python:3.15-slim@sha256:ccc\n"
        )
        finding = _only(classify_dockerfile_diff(old, new))
        assert finding.verdict is Verdict.BLOCK
        assert "3.15-slim" in finding.message

    def test_added_non_python_stage_reviews(self) -> None:
        old = "FROM python:3.14.6-slim@sha256:aaa AS builder\n"
        new = (
            "FROM python:3.14.6-slim@sha256:aaa AS builder\n"
            "FROM alpine:3.20@sha256:ddd\n"
        )
        finding = _only(classify_dockerfile_diff(old, new))
        assert finding.verdict is Verdict.REVIEW
        assert "alpine" in finding.message


class TestDockerfilePythonTagUnparseableOrAliased:
    """Bypass: `parse_cpython_tag` returning `None` for either side made
    `_classify_one_from_change` `return []` — silent — instead of failing
    closed. Three concrete shapes, all previously silent."""

    def test_latest_alias_blocks(self) -> None:
        old = "FROM python:3.14.6-slim@sha256:aaa AS builder\n"
        new = "FROM python:latest@sha256:bbb AS builder\n"
        finding = _only(classify_dockerfile_diff(old, new))
        assert finding.verdict is Verdict.BLOCK
        assert "latest" in finding.message

    def test_arg_interpolated_tag_change_blocks(self) -> None:
        old = "FROM python:3.14.6-slim@sha256:aaa AS builder\n"
        new = "FROM python:${PYTHON_VERSION}-slim@sha256:bbb AS builder\n"
        finding = _only(classify_dockerfile_diff(old, new))
        assert finding.verdict is Verdict.BLOCK
        assert "PYTHON_VERSION" in finding.message

    def test_registry_prefixed_python_minor_bump_still_blocks(self) -> None:
        """`docker.io/library/python` must still be recognised as the
        Python base (repo canonicalization) so the CPython minor-axis rule
        applies — pre-fix, `old_repo == "python"` was a literal string
        check that never matched a registry-prefixed repo, so this fell
        through to the generic `_leading_int` axis, which sees `3` == `3`
        on both sides (14 vs 15 never inspected) and returns `[]`.
        """
        old = "FROM docker.io/library/python:3.14-slim@sha256:aaa AS builder\n"
        new = "FROM docker.io/library/python:3.15-slim@sha256:bbb AS builder\n"
        finding = _only(classify_dockerfile_diff(old, new))
        assert finding.verdict is Verdict.BLOCK
        assert "3.14-slim" in finding.message
        assert "3.15-slim" in finding.message


class TestDockerComposeMultiServiceSharedVolumeNotOverwritten:
    """Bypass: `_volume_image_map` stored ONE `_VolumeImage` per volume name
    in a plain dict — a later service iterated in `services.items()` order
    overwrote an earlier one's entry for the same volume. Adding a new
    `db: postgis/postgis:17-3.4` service on `pgdata` alongside the existing,
    UNCHANGED `postgres: postgis/postgis:16-3.4` service hid the dangerous
    addition entirely (the unchanged `postgres` entry was the last one
    written into the map in the old-services pass, so nothing looked new)."""

    def test_added_service_on_existing_volume_is_flagged_not_hidden(self) -> None:
        """`db` is declared BEFORE `postgres` deliberately: a plain
        service-keyed dict with `services.items()` insertion-order iteration
        writes `db` into the volume map first, then `postgres` (unchanged,
        same tag as the base ref) overwrites it LAST — so the final map
        value for `pgdata` is the harmless unchanged entry and the
        dangerous `db` addition is invisible. The list-based map must not
        depend on which service happens to be declared last.
        """
        old = """\
services:
  postgres:
    image: postgis/postgis:16-3.4@sha256:aaa
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
        new = """\
services:
  db:
    image: postgis/postgis:17-3.4@sha256:bbb
    volumes:
      - pgdata:/var/lib/postgresql/data
  postgres:
    image: postgis/postgis:16-3.4@sha256:aaa
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
        finding = _only(classify_docker_compose_diff(old, new))
        assert finding.verdict is not Verdict.ALLOW
        assert "db" in finding.message
        assert "pgdata" in finding.message

    def test_unchanged_service_alone_stays_silent(self) -> None:
        """Converse/soundness: the SAME unchanged `postgres` service, with
        no added entrant, must stay silent — proves the finding above comes
        from the new `db` entrant, not from re-flagging `postgres`."""
        old = """\
services:
  postgres:
    image: postgis/postgis:16-3.4@sha256:aaa
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
        assert classify_docker_compose_diff(old, old) == []


class TestDockerComposeMutableStatefulTagNeverSilent:
    """Bypass: `_leading_int("latest")` is `None`, and the old code did
    `if old_major is None or new_major is None or new_major <= old_major:
    continue` — an unparseable major on either side of a CHANGED tag on a
    stateful volume silently passed instead of failing closed."""

    def test_move_to_latest_on_stateful_volume_blocks(self) -> None:
        old = """\
services:
  postgres:
    image: postgis/postgis:16-3.4@sha256:aaa
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
        new = """\
services:
  postgres:
    image: postgis/postgis:latest@sha256:bbb
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
        finding = _only(classify_docker_compose_diff(old, new))
        assert finding.verdict is Verdict.BLOCK
        assert "latest" in finding.message

    def test_move_off_latest_on_stateful_volume_also_blocks(self) -> None:
        """'regardless of direction' — a MOVING tag is a footgun whether it
        starts or ends on the mutable alias."""
        old = """\
services:
  postgres:
    image: postgis/postgis:latest@sha256:aaa
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
        new = """\
services:
  postgres:
    image: postgis/postgis:16-3.4@sha256:bbb
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
        finding = _only(classify_docker_compose_diff(old, new))
        assert finding.verdict is Verdict.BLOCK


class TestNoOverBlockingRegressionGuard:
    """Fixed-code sanity: none of the fail-closed hardening above should
    make a legitimate patch-only bump BLOCK/REVIEW, and the original #78
    stale-comment shape must classify exactly as before."""

    def test_python_patch_bump_still_allows(self) -> None:
        old = "FROM python:3.14.6-slim@sha256:aaa AS builder\n"
        new = "FROM python:3.14.7-slim@sha256:bbb AS builder\n"
        assert classify_dockerfile_diff(old, new) == []

    def test_pr78_shape_still_blocks_with_unchanged_key(self) -> None:
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
        finding = _only(classify_docker_compose_diff(old, new))
        assert finding.verdict is Verdict.BLOCK
        assert finding.key == "docker-compose.yml:pgdata:postgis/postgis:16-3.4->17-3.4"

    def test_registry_prefixed_python_patch_bump_still_allows(self) -> None:
        old = "FROM docker.io/library/python:3.14.6-slim@sha256:aaa AS builder\n"
        new = "FROM docker.io/library/python:3.14.7-slim@sha256:bbb AS builder\n"
        assert classify_dockerfile_diff(old, new) == []
