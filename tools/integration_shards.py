"""Integration-suite shard definitions for the CI matrix.

The CI workflow runs one matrix leg per shard here, so this module is the
single source of truth for integration pytest paths. The ``rest`` shard is a
computed catch-all: ``tests/integration`` minus the curated heavy paths.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

INTEGRATION_ROOT = "tests/integration"
LIVE_ROOT = "tests/integration/live"
CATCH_ALL_SHARD_ID = "rest"
COMMON_SELECTED_ARGS = (f"--ignore={LIVE_ROOT}", "-m", "not slow")
ALL_NON_LIVE_ARGS = ("--override-ini", "addopts=", f"--ignore={LIVE_ROOT}")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./-]+$")

NamedGroups = tuple[tuple[str, tuple[str, ...]], ...]

NAMED_GROUPS: NamedGroups = (
    (
        "heavy",
        (
            "tests/integration/db/test_migration_0041_0044_tenant_model.py",
            "tests/integration/db/test_migration_input_quality.py",
            "tests/integration/db/test_operator_role.py",
            "tests/integration/db/test_role_bootstrap.py",
            "tests/integration/flows/test_train_models_warm_start_pg.py",
            "tests/integration/ops/test_protected_evidence_restore.py",
        ),
    ),
)


class Collector(Protocol):
    def __call__(
        self, pytest_args: Sequence[str], *, repo_root: Path, python: str | None = None
    ) -> tuple[str, ...]: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class Shard:
    id: str
    paths: tuple[str, ...]
    ignores: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.paths:
            raise ValueError(f"shard {self.id!r} selects no paths")
        for value in (*self.paths, *self.ignores):
            _validate_transport_safe_path(value)

    def pytest_args(self) -> tuple[str, ...]:
        return (*self.paths, *(f"--ignore={path}" for path in self.ignores))


def _validate_transport_safe_path(path: str) -> None:
    if not _SAFE_PATH.fullmatch(path):
        raise ValueError(f"path {path!r} is not safe for shell argv transport")


def _validate_curated_file_path(path: str, *, repo_root: Path) -> None:
    _validate_transport_safe_path(path)
    if not path.startswith(f"{INTEGRATION_ROOT}/"):
        raise ValueError(f"curated path {path!r} is outside {INTEGRATION_ROOT}")
    if path.startswith(f"{LIVE_ROOT}/"):
        raise ValueError(f"curated path {path!r} points at live integration tests")
    full_path = repo_root / path
    if not full_path.is_file():
        raise ValueError(f"curated path {path!r} is not an existing file")


def build_shards(named_groups: NamedGroups = NAMED_GROUPS) -> tuple[Shard, ...]:
    named = tuple(Shard(id=shard_id, paths=paths) for shard_id, paths in named_groups)
    claimed = tuple(path for shard in named for path in shard.paths)
    return (
        *named,
        Shard(id=CATCH_ALL_SHARD_ID, paths=(INTEGRATION_ROOT,), ignores=claimed),
    )


SHARDS = build_shards()
SHARD_IDS = tuple(shard.id for shard in SHARDS)


@dataclass(frozen=True, kw_only=True, slots=True)
class PartitionReport:
    missing: frozenset[str]
    unexpected: frozenset[str]
    duplicated: frozenset[str]
    empty_shards: frozenset[str]

    @property
    def is_exhaustive_partition(self) -> bool:
        return not (
            self.missing or self.unexpected or self.duplicated or self.empty_shards
        )

    def describe(self) -> str:
        if self.is_exhaustive_partition:
            return "shards partition the suite exactly"
        return "; ".join(
            f"{label} ({len(values)}): {', '.join(sorted(values)[:5])}"
            for label, values in (
                ("in NO shard", self.missing),
                ("in TWO OR MORE shards", self.duplicated),
                ("collected by a shard but not by the whole suite", self.unexpected),
                ("empty shards", self.empty_shards),
            )
            if values
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class CountPartitionReport:
    missing: frozenset[str]
    unexpected: frozenset[str]
    duplicated_in_whole: frozenset[str]
    duplicated_in_shards: frozenset[str]
    count_mismatched: frozenset[str]
    empty_shards: frozenset[str]

    @property
    def is_exactly_once_partition(self) -> bool:
        return not (
            self.missing
            or self.unexpected
            or self.duplicated_in_whole
            or self.duplicated_in_shards
            or self.count_mismatched
            or self.empty_shards
        )

    def describe(self) -> str:
        if self.is_exactly_once_partition:
            return "shards partition the suite exactly once"
        return "; ".join(
            f"{label} ({len(values)}): {', '.join(sorted(values)[:5])}"
            for label, values in (
                ("in NO shard", self.missing),
                ("collected by shards but not by the whole suite", self.unexpected),
                ("duplicated in whole-suite collection", self.duplicated_in_whole),
                ("duplicated inside a shard", self.duplicated_in_shards),
                ("different selected-node multiplicity", self.count_mismatched),
                ("empty shards", self.empty_shards),
            )
            if values
        )


def check_partition(
    *, shard_items: Mapping[str, frozenset[str]], all_items: frozenset[str]
) -> PartitionReport:
    counts = Counter(item for items in shard_items.values() for item in items)
    union = frozenset(counts)
    return PartitionReport(
        missing=frozenset(all_items - union),
        unexpected=frozenset(union - all_items),
        duplicated=frozenset(item for item, count in counts.items() if count > 1),
        empty_shards=frozenset(
            shard_id for shard_id, items in shard_items.items() if not items
        ),
    )


def check_count_partition(
    *, shard_items: Mapping[str, tuple[str, ...]], all_items: tuple[str, ...]
) -> CountPartitionReport:
    whole_counts = Counter(all_items)
    shard_counts_by_id = {
        shard_id: Counter(items) for shard_id, items in shard_items.items()
    }
    merged_counts = Counter(
        item
        for shard_counts in shard_counts_by_id.values()
        for item in shard_counts.elements()
    )
    whole_items = set(whole_counts)
    merged_items = set(merged_counts)
    duplicated_in_shards = {
        item
        for shard_counts in shard_counts_by_id.values()
        for item, count in shard_counts.items()
        if count > 1
    }
    count_mismatched = {
        item
        for item in whole_items | merged_items
        if whole_counts[item] != merged_counts[item]
    }
    return CountPartitionReport(
        missing=frozenset(whole_items - merged_items),
        unexpected=frozenset(merged_items - whole_items),
        duplicated_in_whole=frozenset(
            item for item, count in whole_counts.items() if count > 1
        ),
        duplicated_in_shards=frozenset(duplicated_in_shards),
        count_mismatched=frozenset(count_mismatched),
        empty_shards=frozenset(
            shard_id for shard_id, items in shard_items.items() if not items
        ),
    )


def collect_pytest(
    pytest_args: Sequence[str], *, repo_root: Path, python: str | None = None
) -> tuple[str, ...]:
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
    return tuple(
        line
        for line in completed.stdout.splitlines()
        if line.startswith(f"{INTEGRATION_ROOT}/") and "::" in line
    )


def _files(node_ids: Iterable[str]) -> frozenset[str]:
    return frozenset(node_id.split("::", maxsplit=1)[0] for node_id in node_ids)


def collect_node_ids(
    pytest_args: Sequence[str],
    *,
    repo_root: Path,
    python: str | None = None,
    collector: Collector = collect_pytest,
) -> frozenset[str]:
    return frozenset(
        collect_node_sequence(
            pytest_args, repo_root=repo_root, python=python, collector=collector
        )
    )


def collect_node_sequence(
    pytest_args: Sequence[str],
    *,
    repo_root: Path,
    python: str | None = None,
    collector: Collector = collect_pytest,
) -> tuple[str, ...]:
    return collector(pytest_args, repo_root=repo_root, python=python)


def collect_files(
    pytest_args: Sequence[str],
    *,
    repo_root: Path,
    python: str | None = None,
    collector: Collector = collect_pytest,
) -> frozenset[str]:
    return _files(collector(pytest_args, repo_root=repo_root, python=python))


def selected_args(shard: Shard) -> tuple[str, ...]:
    return (*shard.pytest_args(), *COMMON_SELECTED_ARGS)


def all_non_live_args(shard: Shard) -> tuple[str, ...]:
    return (*ALL_NON_LIVE_ARGS, *shard.pytest_args())


def shard_by_id(shard_id: str, shards: Sequence[Shard] = SHARDS) -> Shard:
    for shard in shards:
        if shard.id == shard_id:
            return shard
    known = ", ".join(shard.id for shard in shards)
    raise SystemExit(f"unknown shard {shard_id!r}; known shards: {known}")


def _validate_curated_membership(
    *,
    repo_root: Path,
    selected_shards: Mapping[str, tuple[str, ...]],
    all_non_live_shard_files: Mapping[str, frozenset[str]],
    named_groups: NamedGroups | None = None,
) -> None:
    for shard_id, paths in named_groups or NAMED_GROUPS:
        selected_files = _files(selected_shards[shard_id])
        all_files = all_non_live_shard_files[shard_id]
        for path in paths:
            try:
                _validate_curated_file_path(path, repo_root=repo_root)
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
            if path not in all_files:
                raise SystemExit(
                    f"curated path {path!r} was not discovered by "
                    "all-non-live collection"
                )
            if path not in selected_files:
                raise SystemExit(
                    f"curated path {path!r} has no selected non-slow tests"
                )


def check_native_partition(
    *,
    repo_root: Path,
    python: str | None = None,
    collector: Collector = collect_pytest,
) -> str:
    selected_all = collect_node_sequence(
        (INTEGRATION_ROOT, *COMMON_SELECTED_ARGS),
        repo_root=repo_root,
        python=python,
        collector=collector,
    )
    selected_shards = {
        shard.id: collect_node_sequence(
            selected_args(shard),
            repo_root=repo_root,
            python=python,
            collector=collector,
        )
        for shard in SHARDS
    }
    selected_report = check_count_partition(
        shard_items=selected_shards, all_items=selected_all
    )
    if not selected_report.is_exactly_once_partition:
        raise SystemExit(
            f"selected-node partition failed: {selected_report.describe()}"
        )

    all_non_live_files = collect_files(
        (*ALL_NON_LIVE_ARGS, INTEGRATION_ROOT),
        repo_root=repo_root,
        python=python,
        collector=collector,
    )
    shard_files = {
        shard.id: collect_files(
            all_non_live_args(shard),
            repo_root=repo_root,
            python=python,
            collector=collector,
        )
        for shard in SHARDS
    }
    file_report = check_partition(shard_items=shard_files, all_items=all_non_live_files)
    if not file_report.is_exhaustive_partition:
        raise SystemExit(
            f"all-non-live file partition failed: {file_report.describe()}"
        )
    _validate_curated_membership(
        repo_root=repo_root,
        selected_shards=selected_shards,
        all_non_live_shard_files=shard_files,
    )

    return (
        "integration shards partition selected nodes and all non-live files: "
        f"{len(selected_all)} selected nodes, {len(all_non_live_files)} non-live files"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pytest-args", metavar="SHARD_ID")
    group.add_argument("--list-ids", action="store_true")
    group.add_argument("--check-partition", action="store_true")
    args = parser.parse_args(argv)

    if args.list_ids:
        print("\n".join(SHARD_IDS))
        return 0
    if args.check_partition:
        print(check_native_partition(repo_root=Path.cwd()))
        return 0
    print(" ".join(shard_by_id(args.pytest_args).pytest_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
