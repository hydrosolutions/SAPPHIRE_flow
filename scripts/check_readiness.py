#!/usr/bin/env python3
"""Accept implementation only for an exact YAML frontmatter status of READY."""

from __future__ import annotations

import sys
from pathlib import Path


def parse_frontmatter(text: str) -> dict[str, str]:
    """Parse unindented scalar fields from leading YAML frontmatter."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}

    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}

    values: dict[str, str] = {}
    for line in lines[1:end]:
        if not line or line[0].isspace() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        if key in values:
            values[key] = "__INVALID_DUPLICATE__"
        else:
            values[key] = value.strip()
    return values


def check_readiness(path: Path) -> tuple[bool, str, str]:
    if not path.is_file():
        return False, f"File not found: {path}", "N/A"

    frontmatter = parse_frontmatter(path.read_text(encoding="utf-8"))
    status = frontmatter.get("status")
    if status == "READY":
        return True, "YAML frontmatter status is READY", status
    if status is None:
        return False, "No 'status' field in YAML frontmatter", "NONE"
    return False, f"YAML frontmatter status is {status!r}; expected 'READY'", status


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: check_readiness.py <path-to-plan>", file=sys.stderr)
        raise SystemExit(64)

    path = Path(sys.argv[1]).resolve()
    is_ready, reason, status = check_readiness(path)
    print(f"Readiness check: {path}")
    print(f"  Frontmatter status: {status}")
    print(f"  Verdict: {'READY' if is_ready else 'NOT READY'}")
    if not is_ready:
        print(f"Error: {reason}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
