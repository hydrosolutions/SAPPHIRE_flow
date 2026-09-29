"""Reject restricted DHM delivery filenames from commits."""

from __future__ import annotations

import sys
from pathlib import PurePosixPath

_ALLOWED = frozenset(
    {
        "docs/requirements/DFL_Dummy Station A.txt",
        "docs/requirements/RT_Dummy Station A.txt",
    }
)


def rejected_paths(paths: list[str]) -> list[str]:
    rejected: list[str] = []
    for raw_path in paths:
        path = PurePosixPath(raw_path.removeprefix("./"))
        name = path.name
        if name in {"", ".", ".."}:
            continue
        restricted = (
            name.startswith("DFL_") or name.startswith("RT_")
        ) and name.endswith(".txt")
        if restricted and path.as_posix() not in _ALLOWED:
            rejected.append(path.as_posix())
    return rejected


def main(paths: list[str] | None = None) -> int:
    rejected = rejected_paths(sys.argv[1:] if paths is None else paths)
    for path in rejected:
        print(
            f"restricted DHM delivery filename cannot be committed: {path}",
            file=sys.stderr,
        )
    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
