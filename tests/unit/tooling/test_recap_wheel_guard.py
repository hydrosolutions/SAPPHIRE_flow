"""Plan 082 Task 2H: CI wheel-guard exception + Docker builder clone auth.

Prose assertions on the real ``ci.yml``/``Dockerfile`` text — a single
selector each, not a bespoke YAML/Dockerfile parser (Task 2H scope).
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _ci_yml_text() -> str:
    return (_REPO_ROOT / ".github/workflows/ci.yml").read_text()


def _dockerfile_text() -> str:
    return (_REPO_ROOT / "Dockerfile").read_text()


class TestWheelGuardException:
    def test_step_one_no_build_guard_excludes_recap_dg_client(self) -> None:
        text = _ci_yml_text()
        assert "--no-install-package recap-dg-client" in text

    def test_step_two_reinstalls_recap_dg_client_after_the_guard(self) -> None:
        text = _ci_yml_text()
        assert "--reinstall-package recap-dg-client" in text

    def test_forecastinterface_exception_still_present(self) -> None:
        # Negative-regression control: the existing FI exception must not be
        # dropped/overwritten by the new one.
        text = _ci_yml_text()
        assert "--no-install-package forecastinterface" in text
        assert "--reinstall-package forecastinterface" in text

    def test_docker_builder_stage_declares_a_clone_auth_step(self) -> None:
        text = _dockerfile_text()
        assert "recap-dg-client" in text
        assert "--mount=type=secret" in text

    def test_docker_builder_auth_step_precedes_uv_sync(self) -> None:
        text = _dockerfile_text()
        auth_idx = text.index("--mount=type=secret")
        sync_idx = text.index("uv sync --frozen --no-dev")
        assert auth_idx < sync_idx
