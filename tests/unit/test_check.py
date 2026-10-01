"""Smoke tests for the `uv run check` local gate helper."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from sapphire_flow.cli import check as check_module

if TYPE_CHECKING:
    from collections.abc import Sequence


class _StubResult:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def _without_cli_args(monkeypatch) -> None:
    monkeypatch.setattr(check_module.sys, "argv", ["check"])


def test_main_returns_zero_when_all_steps_succeed(monkeypatch) -> None:
    _without_cli_args(monkeypatch)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_kw: _StubResult(0),
    )
    assert check_module.main() == 0


def test_main_returns_first_nonzero_exit_code(monkeypatch) -> None:
    _without_cli_args(monkeypatch)
    calls: list[Sequence[str]] = []

    def _fake_run(cmd: Sequence[str], *_a: object, **_kw: object) -> _StubResult:
        calls.append(cmd)
        # First call: ruff format --check succeeds (0).
        # Second call: ruff check fails (1).
        return _StubResult(0) if len(calls) == 1 else _StubResult(1)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert check_module.main() == 1
    # Sanity: second step was reached (not short-circuited).
    assert len(calls) == 2


def test_main_short_circuits_on_first_failure(monkeypatch) -> None:
    _without_cli_args(monkeypatch)
    calls: list[Sequence[str]] = []

    def _fake_run(cmd: Sequence[str], *_a: object, **_kw: object) -> _StubResult:
        calls.append(cmd)
        return _StubResult(2)  # always fail

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert check_module.main() == 2
    # Only first step ran; failure stopped the loop.
    assert len(calls) == 1


def test_main_runs_focused_pytest_paths_after_ruff(monkeypatch) -> None:
    calls: list[Sequence[str]] = []

    def _fake_run(cmd: Sequence[str], *_a: object, **_kw: object) -> _StubResult:
        calls.append(cmd)
        return _StubResult(0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    assert check_module.main(["tests/unit/test_check.py"]) == 0
    assert calls == [
        ["uv", "run", "ruff", "format", "--check", "src/", "tests/"],
        ["uv", "run", "ruff", "check", "src/", "tests/"],
        ["uv", "run", "pytest", "tests/unit/test_check.py"],
    ]


def test_main_without_explicit_argv_uses_sys_argv_for_console_entry(
    monkeypatch,
) -> None:
    calls: list[Sequence[str]] = []

    def _fake_run(cmd: Sequence[str], *_a: object, **_kw: object) -> _StubResult:
        calls.append(cmd)
        return _StubResult(0)

    monkeypatch.setattr(check_module.sys, "argv", ["check", "tests/unit/test_check.py"])
    monkeypatch.setattr(subprocess, "run", _fake_run)

    assert check_module.main() == 0
    assert calls[-1] == ["uv", "run", "pytest", "tests/unit/test_check.py"]


def test_main_rejects_paths_outside_focused_unit_scope(monkeypatch, tmp_path) -> None:
    calls: list[Sequence[str]] = []
    outside = Path("src/sapphire_flow/cli/check.py")

    def _fake_run(cmd: Sequence[str], *_a: object, **_kw: object) -> _StubResult:
        calls.append(cmd)
        return _StubResult(0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    assert check_module.main([str(outside)]) == 2
    assert calls == []


def test_main_rejects_traversal_paths_before_subprocess(monkeypatch) -> None:
    calls: list[Sequence[str]] = []

    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **_kw: calls.append(cmd) or _StubResult(0)
    )

    assert check_module.main(["tests/unit/../conftest.py"]) == 2
    assert calls == []


def test_main_rejects_flag_like_pytest_arguments_before_subprocess(monkeypatch) -> None:
    calls: list[Sequence[str]] = []

    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **_kw: calls.append(cmd) or _StubResult(0)
    )

    assert check_module.main(["-k", "test_main"]) == 2
    assert calls == []


def test_main_rejects_symlink_escape_from_allowed_roots(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[Sequence[str]] = []
    allowed = tmp_path / "tests" / "unit"
    allowed.mkdir(parents=True)
    outside = tmp_path / "outside_test.py"
    outside.write_text("def test_outside():\n    assert True\n")
    escaped = allowed / "escaped_test.py"
    escaped.symlink_to(outside)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **_kw: calls.append(cmd) or _StubResult(0)
    )

    assert check_module.main(["tests/unit/escaped_test.py"]) == 2
    assert calls == []


def test_main_propagates_focused_pytest_failure(monkeypatch) -> None:
    calls: list[Sequence[str]] = []

    def _fake_run(cmd: Sequence[str], *_a: object, **_kw: object) -> _StubResult:
        calls.append(cmd)
        return _StubResult(7) if cmd[:3] == ["uv", "run", "pytest"] else _StubResult(0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    assert check_module.main(["tests/unit/test_check.py"]) == 7
    assert len(calls) == 3


def test_main_logs_lint_only_and_focused_labels(monkeypatch) -> None:
    scopes: list[str] = []

    class _Log:
        def info(self, event: str, **kwargs: object) -> None:
            if event == "local_check_start":
                scope = kwargs["scope"]
                assert isinstance(scope, str)
                scopes.append(scope)

        def error(self, *_a: object, **_kw: object) -> None:
            return None

    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: _StubResult(0))
    monkeypatch.setattr(check_module, "log", _Log())
    monkeypatch.setattr(check_module, "configure_cli_logging", lambda: None)

    assert check_module.main([]) == 0
    assert check_module.main(["tests/unit/test_check.py"]) == 0
    assert scopes == ["lint-only", "focused-not-full"]
