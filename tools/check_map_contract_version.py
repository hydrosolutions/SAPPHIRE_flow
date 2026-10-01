#!/usr/bin/env python3
"""Plan 402 T4 D14 — CI gate: a pull request that changes
docs/spec/api-v1-map.openapi.json must bump its `info.version` (compared as
an integer (major, minor) tuple; `"1.9"` -> `"1.10"` counts as greater).
Whether the bump SHOULD be minor (additive) or major (anything else) is a
review judgement, not machine-checked (owner, 2026-09-26) — this script only
checks that SOME greater version was set.

Rules:
  * the file absent at the merge-base -> pass (new file)
  * the file byte-identical to the merge-base copy -> pass (unchanged)
  * the file changed with a version > the merge-base's -> pass
  * the file changed with a version <= the merge-base's -> FAIL
  * the file present at the merge-base and absent at HEAD -> FAIL (a PR
    deleting the committed contract is not a version bump — review finding,
    2026-09-28)

Direct pushes to `main` are not checked — code (and so this generated file)
only reaches `main` through a pull request (hold-at-PR, AGENTS.md § Version
Bumping).

Usage::

    uv run python tools/check_map_contract_version.py --base-ref <base-sha>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_FILE = "docs/spec/api-v1-map.openapi.json"


def _git_show(ref: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"], capture_output=True, text=True, check=False
    )
    return result.stdout if result.returncode == 0 else None


def _merge_base(base_ref: str) -> str:
    result = subprocess.run(
        ["git", "merge-base", "HEAD", base_ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"`git merge-base HEAD {base_ref}` failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


def _version_tuple(raw: str) -> tuple[int, int]:
    major_str, _, minor_str = raw.partition(".")
    return (int(major_str), int(minor_str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", required=True, help="PR base SHA to diff against")
    args = parser.parse_args(argv)

    merge_base = _merge_base(args.base_ref)
    base_text = _git_show(merge_base, _FILE)

    head_path = Path(_FILE)
    if not head_path.is_file():
        if base_text is None:
            print(f"{_FILE}: absent at HEAD and at the merge-base — nothing to check.")
            return 0
        print(
            f"{_FILE}: present at the merge-base ({merge_base}) but absent at "
            "HEAD — a PR must not delete the committed map contract."
        )
        return 1
    head_text = head_path.read_text()

    if base_text is None:
        print(f"{_FILE}: absent at the merge-base ({merge_base}) — new file, pass.")
        return 0
    if base_text == head_text:
        print(f"{_FILE}: unchanged since the merge-base ({merge_base}) — pass.")
        return 0

    base_version_raw = json.loads(base_text)["info"]["version"]
    head_version_raw = json.loads(head_text)["info"]["version"]
    base_version = _version_tuple(base_version_raw)
    head_version = _version_tuple(head_version_raw)

    if head_version > base_version:
        print(
            f"{_FILE}: changed with a greater version "
            f"({base_version_raw} -> {head_version_raw}) — pass."
        )
        return 0

    print(
        f"{_FILE}: changed WITHOUT a greater version "
        f"({base_version_raw} -> {head_version_raw}) — bump info.version (D14), "
        "regenerate with `uv run python tools/generate_map_contract.py` after "
        "editing MAP_CONTRACT_VERSION in src/sapphire_flow/api/map_contract.py."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
