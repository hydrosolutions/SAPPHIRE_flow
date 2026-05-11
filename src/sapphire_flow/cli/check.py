"""Developer-side local gate helper invoked via `uv run check`."""

from __future__ import annotations

import subprocess


def main() -> int:
    """Run the ruff steps the CI `lint` job runs; return first nonzero exit code."""
    steps: list[list[str]] = [
        ["uv", "run", "ruff", "format", "--check", "src/", "tests/"],
        ["uv", "run", "ruff", "check", "src/", "tests/"],
    ]
    for cmd in steps:
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
