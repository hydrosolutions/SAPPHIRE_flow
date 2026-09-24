"""Unit-suite shard definitions for the CI matrix (Plan 319).

`.github/workflows/ci.yml`'s `unit` job runs one matrix leg per shard here, so
this module is the single source of truth for the split: the workflow asks it
for each leg's pytest arguments rather than repeating any path.

The last shard is **computed** — "whatever the named groups are not", expressed
as `tests/unit` minus an `--ignore` per claimed path. It must never become a
list of directories. A list drops whatever it forgets, in silence: an earlier
draft of Plan 319 enumerated the subdirectories and thereby dropped the 44
tests that live in files directly under `tests/unit/`, and the suite would have
gone green having run less. `tests/unit/tools/test_unit_shards.py` proves the
shards partition the suite exactly, by collected node id.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

UNIT_ROOT = "tests/unit"
CATCH_ALL_SHARD_ID = "rest"

NamedGroups = tuple[tuple[str, tuple[str, ...]], ...]

NAMED_GROUPS: NamedGroups = (
    ("scripts", ("tests/unit/scripts",)),
    ("services", ("tests/unit/services",)),
    ("adapters-flows", ("tests/unit/adapters", "tests/unit/flows")),
)


@dataclass(frozen=True, kw_only=True, slots=True)
class Shard:
    id: str
    paths: tuple[str, ...]
    ignores: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.paths:
            raise ValueError(f"shard {self.id!r} selects no paths")

    def pytest_args(self) -> tuple[str, ...]:
        return (*self.paths, *(f"--ignore={path}" for path in self.ignores))


def build_shards(named_groups: NamedGroups = NAMED_GROUPS) -> tuple[Shard, ...]:
    """The named groups, plus one catch-all DERIVED from them.

    Never return a listed fourth group: the catch-all is what makes a new
    subdirectory — or a test file directly in `tests/unit/` — land somewhere
    instead of nowhere.
    """
    named = tuple(Shard(id=shard_id, paths=paths) for shard_id, paths in named_groups)
    claimed = tuple(path for shard in named for path in shard.paths)
    return (*named, Shard(id=CATCH_ALL_SHARD_ID, paths=(UNIT_ROOT,), ignores=claimed))


SHARDS = build_shards()
SHARD_IDS = tuple(shard.id for shard in SHARDS)


@dataclass(frozen=True, kw_only=True, slots=True)
class PartitionReport:
    missing: frozenset[str]
    unexpected: frozenset[str]
    duplicated: frozenset[str]

    @property
    def is_exhaustive_partition(self) -> bool:
        return not (self.missing or self.unexpected or self.duplicated)

    def describe(self) -> str:
        if self.is_exhaustive_partition:
            return "shards partition the suite exactly"
        return "; ".join(
            f"{label} ({len(nodes)}): {', '.join(sorted(nodes)[:5])}"
            for label, nodes in (
                ("in NO shard", self.missing),
                ("in TWO OR MORE shards", self.duplicated),
                ("collected by a shard but not by the whole suite", self.unexpected),
            )
            if nodes
        )


def check_partition(
    *, shard_nodes: Mapping[str, frozenset[str]], all_nodes: frozenset[str]
) -> PartitionReport:
    counts = Counter(node for nodes in shard_nodes.values() for node in nodes)
    union = frozenset(counts)
    return PartitionReport(
        missing=frozenset(all_nodes - union),
        unexpected=frozenset(union - all_nodes),
        duplicated=frozenset(node for node, count in counts.items() if count > 1),
    )


def collect_node_ids(
    pytest_args: Sequence[str], *, repo_root: Path, python: str | None = None
) -> frozenset[str]:
    completed = subprocess.run(
        [
            python or sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            *pytest_args,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"collection failed for {list(pytest_args)}:\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    return frozenset(
        line
        for line in completed.stdout.splitlines()
        if line.startswith(f"{UNIT_ROOT}/") and "::" in line
    )


def shard_by_id(shard_id: str, shards: Sequence[Shard] = SHARDS) -> Shard:
    for shard in shards:
        if shard.id == shard_id:
            return shard
    known = ", ".join(shard.id for shard in shards)
    raise SystemExit(f"unknown shard {shard_id!r}; known shards: {known}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pytest-args", metavar="SHARD_ID")
    group.add_argument("--list-ids", action="store_true")
    args = parser.parse_args(argv)

    if args.list_ids:
        print("\n".join(SHARD_IDS))
        return 0
    print(" ".join(shard_by_id(args.pytest_args).pytest_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
