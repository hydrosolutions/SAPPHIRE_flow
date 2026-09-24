"""Plan 319 — the CI unit-suite shards must partition the suite exactly.

The hazard this file exists for: **a test in no shard runs nowhere, and nothing
fails.** The suite goes green having run less. A directory-level check cannot
see that hazard twice over — it is blind to the test files sitting directly in
``tests/unit/`` (no subdirectory), and directory coverage plus a total count
still cannot distinguish "each node ran once" from "one node ran twice and
another never ran".

So the proof here is **by collected node id**: the union of the four shards'
``--collect-only`` node ids must equal the unsharded collection as sets, with no
node in two shards.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from tools.unit_shards import (
    CATCH_ALL_SHARD_ID,
    NAMED_GROUPS,
    SHARD_IDS,
    SHARDS,
    Shard,
    build_shards,
    check_partition,
    collect_node_ids,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW_PATH = _REPO_ROOT / ".github/workflows/ci.yml"
_UNIT_JOB = "unit"

# Named because these are what an earlier draft of Plan 319 silently dropped:
# they live directly in tests/unit/, in no subdirectory, so a split enumerating
# subdirectories forgets them without a word.
_ROOT_LEVEL_FILES = (
    "tests/unit/test_check.py",
    "tests/unit/test_compose_schedule_default.py",
    "tests/unit/test_config.py",
    "tests/unit/test_conftest.py",
    "tests/unit/test_hash_verification_coverage.py",
    "tests/unit/test_logging_override.py",
    "tests/unit/test_prefect_home_isolation.py",
    "tests/unit/test_structlog_cache_isolation.py",
)


@pytest.fixture(scope="module")
def unsharded_nodes() -> frozenset[str]:
    return collect_node_ids(("tests/unit",), repo_root=_REPO_ROOT)


@pytest.fixture(scope="module")
def shard_nodes() -> dict[str, frozenset[str]]:
    return {
        shard.id: collect_node_ids(shard.pytest_args(), repo_root=_REPO_ROOT)
        for shard in SHARDS
    }


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return yaml.safe_load(_WORKFLOW_PATH.read_text())


def _unit_job(workflow: dict[str, Any]) -> dict[str, Any]:
    return workflow["jobs"][_UNIT_JOB]


def _run_scripts(job: Mapping[str, Any]) -> list[str]:
    return [step["run"] for step in job["steps"] if "run" in step]


class TestShardDefinitions:
    def test_the_catch_all_shard_is_computed_not_listed(self) -> None:
        """The fourth shard is "whatever the first three are not".

        Asserted against a *different* group set than the real one, so a
        hand-written list of directories cannot satisfy it.
        """
        shards = build_shards((("only", ("tests/unit/store",)),))

        assert [shard.id for shard in shards] == ["only", CATCH_ALL_SHARD_ID]
        assert shards[-1].paths == ("tests/unit",)
        assert shards[-1].ignores == ("tests/unit/store",)

    def test_the_catch_all_ignores_every_named_group_path(self) -> None:
        claimed = tuple(path for _, paths in NAMED_GROUPS for path in paths)

        assert SHARDS[-1].id == CATCH_ALL_SHARD_ID
        assert SHARDS[-1].ignores == claimed

    def test_pytest_args_scope_a_shard_to_its_paths(self) -> None:
        shard = Shard(id="x", paths=("tests/unit",), ignores=("tests/unit/db",))

        assert shard.pytest_args() == ("tests/unit", "--ignore=tests/unit/db")

    def test_a_shard_selecting_nothing_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="selects no paths"):
            Shard(id="empty", paths=())


class TestCheckPartition:
    def test_an_exhaustive_partition_is_accepted(self) -> None:
        report = check_partition(
            shard_nodes={"a": frozenset({"n1"}), "b": frozenset({"n2"})},
            all_nodes=frozenset({"n1", "n2"}),
        )

        assert report.is_exhaustive_partition
        assert report.describe() == "shards partition the suite exactly"

    def test_a_node_in_no_shard_is_reported_missing(self) -> None:
        report = check_partition(
            shard_nodes={"a": frozenset({"n1"})},
            all_nodes=frozenset({"n1", "n2"}),
        )

        assert not report.is_exhaustive_partition
        assert report.missing == frozenset({"n2"})
        assert "n2" in report.describe()

    def test_a_node_in_two_shards_is_reported_duplicated(self) -> None:
        report = check_partition(
            shard_nodes={"a": frozenset({"n1"}), "b": frozenset({"n1"})},
            all_nodes=frozenset({"n1"}),
        )

        assert not report.is_exhaustive_partition
        assert report.duplicated == frozenset({"n1"})

    def test_a_node_no_unsharded_run_collects_is_reported_unexpected(self) -> None:
        report = check_partition(
            shard_nodes={"a": frozenset({"n1", "ghost"})},
            all_nodes=frozenset({"n1"}),
        )

        assert not report.is_exhaustive_partition
        assert report.unexpected == frozenset({"ghost"})


class TestShardsPartitionTheUnitSuite:
    def test_the_union_equals_the_unsharded_collection(
        self, shard_nodes: dict[str, frozenset[str]], unsharded_nodes: frozenset[str]
    ) -> None:
        report = check_partition(shard_nodes=shard_nodes, all_nodes=unsharded_nodes)

        assert report.is_exhaustive_partition, report.describe()

    def test_no_node_runs_in_two_shards(
        self, shard_nodes: dict[str, frozenset[str]]
    ) -> None:
        report = check_partition(
            shard_nodes=shard_nodes,
            all_nodes=frozenset().union(*shard_nodes.values()),
        )

        assert report.duplicated == frozenset()

    def test_every_root_level_test_file_runs_in_exactly_one_shard(
        self, shard_nodes: dict[str, frozenset[str]]
    ) -> None:
        for path in _ROOT_LEVEL_FILES:
            owners = [
                shard_id
                for shard_id, nodes in shard_nodes.items()
                if any(node.startswith(f"{path}::") for node in nodes)
            ]

            assert owners == [CATCH_ALL_SHARD_ID], f"{path} ran in {owners}"

    def test_dropping_the_catch_all_shard_loses_the_root_level_tests(
        self, shard_nodes: dict[str, frozenset[str]], unsharded_nodes: frozenset[str]
    ) -> None:
        """The defect that motivated this plan, reproduced against real ids.

        An enumerate-the-subdirectories split is exactly this: the named groups
        and no catch-all. The check must refuse it, and name what it dropped.
        """
        listed_only = {
            shard_id: nodes
            for shard_id, nodes in shard_nodes.items()
            if shard_id != CATCH_ALL_SHARD_ID
        }

        report = check_partition(shard_nodes=listed_only, all_nodes=unsharded_nodes)

        assert not report.is_exhaustive_partition
        assert any(
            node.startswith("tests/unit/test_config.py::") for node in report.missing
        )

    def test_removing_a_named_group_keeps_the_split_exhaustive(self) -> None:
        """Because the catch-all is computed, not listed.

        This is the property that makes the split safe as the tree changes: a
        group that disappears from the definitions — or a subdirectory nobody
        adds one for — is absorbed, not dropped.
        """
        shards = build_shards(NAMED_GROUPS[1:])
        nodes = {
            shard.id: collect_node_ids(shard.pytest_args(), repo_root=_REPO_ROOT)
            for shard in shards
        }

        report = check_partition(
            shard_nodes=nodes,
            all_nodes=collect_node_ids(("tests/unit",), repo_root=_REPO_ROOT),
        )

        assert report.is_exhaustive_partition, report.describe()


class TestCiWorkflowMatrix:
    def test_the_matrix_lists_exactly_the_defined_shards(
        self, workflow: dict[str, Any]
    ) -> None:
        matrix = _unit_job(workflow)["strategy"]["matrix"]["shard"]

        assert tuple(matrix) == SHARD_IDS

    def test_one_shard_failing_does_not_cancel_the_others(
        self, workflow: dict[str, Any]
    ) -> None:
        assert _unit_job(workflow)["strategy"]["fail-fast"] is False

    def test_each_shard_keeps_n_auto_and_derives_its_scope_from_this_module(
        self, workflow: dict[str, Any]
    ) -> None:
        shard_step = next(
            script
            for script in _run_scripts(_unit_job(workflow))
            if "-n auto" in script
        )

        assert "tools/unit_shards.py --pytest-args" in shard_step
        assert "tests/unit/scripts" not in shard_step

    def test_each_shard_reports_its_own_wall_clock(
        self, workflow: dict[str, Any]
    ) -> None:
        shard_step = next(
            script
            for script in _run_scripts(_unit_job(workflow))
            if "-n auto" in script
        )

        assert "wall-clock" in shard_step

    def test_the_plan_201_canary_runs_once_sequentially_and_unsharded(
        self, workflow: dict[str, Any]
    ) -> None:
        job = _unit_job(workflow)
        canary_steps = [
            step
            for step in job["steps"]
            if "Plan 201 regression" in step.get("name", "")
        ]

        assert len(canary_steps) == 1
        (canary,) = canary_steps
        # Pinned to one matrix leg, so the matrix cannot multiply it.
        assert canary["if"] == f"matrix.shard == '{SHARD_IDS[0]}'"
        assert "-n auto" not in canary["run"]
        assert "unit_shards" not in canary["run"]
        assert (
            "tests/unit/services/skill/test_combined_skill.py"
            "::TestCoverageLogging::test_coverage_log_message PASSED"
        ) in canary["run"]

    def test_no_coverage_threshold_is_introduced(
        self, workflow: dict[str, Any]
    ) -> None:
        """Plan 319 D2 closed on stitching, not gating; none exists today.

        Read from the parsed `run:` scripts with their shell comments stripped:
        a comment SAYING there is no threshold must not read as one.
        """
        commands = "\n".join(
            line
            for job in workflow["jobs"].values()
            for script in _run_scripts(job)
            for line in script.splitlines()
            if not line.lstrip().startswith("#")
        )

        assert "--cov-fail-under" not in commands
        assert "fail_under" not in (_REPO_ROOT / "pyproject.toml").read_text()


class TestCoverageIsStitchedBackIntoOneNumber:
    def test_each_shard_writes_its_own_coverage_data_file(
        self, workflow: dict[str, Any]
    ) -> None:
        job = _unit_job(workflow)
        shard_step = next(
            step for step in job["steps"] if "-n auto" in step.get("run", "")
        )

        assert "${{ matrix.shard }}" in shard_step["env"]["COVERAGE_FILE"]
        # No per-shard report: four partial numbers is exactly what D2 rejected.
        assert "--cov-report=" in shard_step["run"]
        assert "term-missing" not in shard_step["run"]

    def test_a_follow_on_job_combines_them_and_prints_one_number(
        self, workflow: dict[str, Any]
    ) -> None:
        job = workflow["jobs"]["unit-coverage"]

        assert job["needs"] == _UNIT_JOB
        combine = "\n".join(_run_scripts(job))
        assert "coverage combine" in combine
        assert re.search(r"coverage report\b", combine)
