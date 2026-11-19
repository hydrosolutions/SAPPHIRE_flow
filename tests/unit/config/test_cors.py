from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from sapphire_flow.config.cors import parse_exact_origin
from sapphire_flow.exceptions import ConfigurationError


@pytest.mark.parametrize(
    "origin",
    ["https://example.invalid", "http://localhost:8000", "http://127.0.0.1:8000"],
)
def test_exact_origin_preserves_runtime_behavior(origin: str) -> None:
    assert parse_exact_origin(origin) == origin


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "http://example.invalid",
        "https://user:secret-canary@example.invalid",
        "https://example.invalid/path",
    ],
)
def test_invalid_origin_uses_fixed_error(origin: str) -> None:
    with pytest.raises(ConfigurationError, match="one exact HTTPS origin") as error:
        parse_exact_origin(origin)
    assert "secret-canary" not in str(error.value)


def test_pure_origin_import_does_not_initialize_api(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from sapphire_flow.config.cors import parse_exact_origin; "
            "assert 'sapphire_flow.api' not in sys.modules; "
            "assert parse_exact_origin('https://example.invalid')",
        ],
        cwd=tmp_path,
        env={"PYTHONPATH": os.pathsep.join(sys.path), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
