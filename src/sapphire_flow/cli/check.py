"""Developer-side local gate helper invoked via `uv run check`."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import structlog

from sapphire_flow.logging import configure_cli_logging

log = structlog.get_logger(__name__)

_ALLOWED_FOCUSED_ROOTS = (Path("tests/unit"), Path("tests/fakes"))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the local lint-only gate, or lint plus explicitly named "
            "focused pytest paths."
        )
    )
    parser.add_argument(
        "pytest_paths",
        nargs="*",
        help=(
            "Optional focused pytest files/directories under tests/unit or tests/fakes."
        ),
    )
    return parser.parse_args(argv)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_focused_paths(paths: list[str], repo_root: Path) -> list[str]:
    validated: list[str] = []
    allowed_roots = tuple(
        (repo_root / root).resolve() for root in _ALLOWED_FOCUSED_ROOTS
    )
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(
                f"focused pytest path must be a relative in-repository path: {raw_path}"
            )
        resolved = (repo_root / path).resolve()
        if not resolved.exists():
            raise ValueError(f"focused pytest path does not exist: {raw_path}")
        if not any(_is_relative_to(resolved, root) for root in allowed_roots):
            allowed = ", ".join(str(root) for root in _ALLOWED_FOCUSED_ROOTS)
            raise ValueError(
                f"focused pytest path must be under one of: {allowed}; got {raw_path}"
            )
        validated.append(raw_path)
    return validated


def _run_steps(steps: list[list[str]]) -> int:
    for cmd in steps:
        log.info("local_check_step", command=cmd)
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            log.error(
                "local_check_step_failed", command=cmd, returncode=result.returncode
            )
            return result.returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run CI-matching local checks and return the first nonzero exit code."""
    configure_cli_logging()
    cli_args = sys.argv[1:] if argv is None else argv
    if any(arg.startswith("-") for arg in cli_args):
        log.error(
            "local_check_invalid_scope",
            error="focused pytest paths must not be CLI flags",
        )
        return 2
    args = _parse_args(cli_args)
    try:
        focused_paths = _validate_focused_paths(args.pytest_paths, Path.cwd())
    except ValueError as exc:
        log.error("local_check_invalid_scope", error=str(exc))
        return 2

    label = "lint-only" if not focused_paths else "focused-not-full"
    log.info("local_check_start", scope=label, pytest_paths=focused_paths)
    steps: list[list[str]] = [
        ["uv", "run", "ruff", "format", "--check", "src/", "tests/"],
        ["uv", "run", "ruff", "check", "src/", "tests/"],
    ]
    if focused_paths:
        steps.append(["uv", "run", "pytest", *focused_paths])

    exit_code = _run_steps(steps)
    log.info("local_check_finished", scope=label, exit_code=exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
