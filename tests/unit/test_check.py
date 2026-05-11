"""Smoke tests for the `uv run check` local gate helper."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from sapphire_flow.cli import check as check_module

if TYPE_CHECKING:
    from collections.abc import Sequence


class _StubResult:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def test_main_returns_zero_when_all_steps_succeed(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_kw: _StubResult(0),
    )
    assert check_module.main() == 0


def test_main_returns_first_nonzero_exit_code(monkeypatch) -> None:
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
    calls: list[Sequence[str]] = []

    def _fake_run(cmd: Sequence[str], *_a: object, **_kw: object) -> _StubResult:
        calls.append(cmd)
        return _StubResult(2)  # always fail

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert check_module.main() == 2
    # Only first step ran; failure stopped the loop.
    assert len(calls) == 1
