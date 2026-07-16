"""Plan 082 Task 1A: register + discriminate the ``live_recap`` marker.

No default-CI network calls are exercised here — this proves the *collection*
mechanics (marker registration + ``-m`` discrimination) using a synthetic
probe test file, never the real live Gateway.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_pyproject() -> dict[str, Any]:
    with (_REPO_ROOT / "pyproject.toml").open("rb") as f:
        return cast("dict[str, Any]", tomllib.load(f))


def _default_marker_expr() -> str:
    """The ``-m`` expression baked into ``[tool.pytest.ini_options].addopts``."""
    addopts = cast("str", _load_pyproject()["tool"]["pytest"]["ini_options"]["addopts"])
    # addopts = "-m '<expr>'" — extract the quoted expression.
    marker = "-m '"
    start = addopts.index(marker) + len(marker)
    end = addopts.index("'", start)
    return addopts[start:end]


def _write_probe(tmp_path: Path, *, markers: list[str]) -> Path:
    marks = "\n".join(f"@pytest.mark.{m}" for m in markers)
    test_file = tmp_path / "test_probe.py"
    test_file.write_text(
        f"import pytest\n\n\n{marks}\ndef test_probe() -> None:\n    assert True\n"
    )
    return test_file


def _collect(test_file: Path, marker_expr: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(test_file),
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "-m",
            marker_expr,
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


_NO_TESTS_COLLECTED = 5


class TestLiveRecapMarker:
    def test_live_recap_marker_registered_in_pyproject(self) -> None:
        markers = cast(
            "list[str]",
            _load_pyproject()["tool"]["pytest"]["ini_options"]["markers"],
        )
        names = {m.split(":", 1)[0].strip() for m in markers}
        assert "live_recap" in names

    def test_default_expression_collects_zero_live_recap_tests(
        self, tmp_path: Path
    ) -> None:
        probe = _write_probe(tmp_path, markers=["live", "live_recap"])
        result = _collect(probe, _default_marker_expr())
        assert result.returncode == _NO_TESTS_COLLECTED, result.stdout

    def test_live_and_live_recap_expression_collects_the_probe(
        self, tmp_path: Path
    ) -> None:
        probe = _write_probe(tmp_path, markers=["live", "live_recap"])
        result = _collect(probe, "live and live_recap")
        assert result.returncode == 0, result.stdout
        assert "1 test" in result.stdout or "test_probe" in result.stdout

    def test_bare_live_expression_still_excluded_by_default(
        self, tmp_path: Path
    ) -> None:
        """A plain ``live``-only test (no ``live_recap``) stays excluded under
        the project default — the ``live_recap`` addition did not regress the
        pre-existing ``not live`` catch-all."""
        probe = _write_probe(tmp_path, markers=["live"])
        result = _collect(probe, _default_marker_expr())
        assert result.returncode == _NO_TESTS_COLLECTED, result.stdout

    def test_not_live_lindas_alone_does_not_gate_live_recap(
        self, tmp_path: Path
    ) -> None:
        """``live_recap`` exclusion must come from the ``not live`` clause in
        the real addopts default, NOT be assumed covered by an unrelated
        ``not live_lindas`` filter. Proof: filtering by ``not live_lindas``
        ALONE (i.e. without ``not live``) still ADMITS a ``live``+``live_recap``
        probe — discrimination is specific to ``live_recap``/``live``, not a
        side effect of the ``live_lindas`` marker.
        """
        probe = _write_probe(tmp_path, markers=["live", "live_recap"])
        result = _collect(probe, "not live_lindas")
        assert result.returncode == 0, result.stdout
        assert "1 test" in result.stdout or "test_probe" in result.stdout
