"""Integration shard contracts for the CI workflow."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from tools import integration_shards
from tools.integration_shards import (
    ALL_NON_LIVE_ARGS,
    CATCH_ALL_SHARD_ID,
    COMMON_SELECTED_ARGS,
    INTEGRATION_ROOT,
    LIVE_ROOT,
    NAMED_GROUPS,
    SHARD_IDS,
    SHARDS,
    Shard,
    all_non_live_args,
    build_shards,
    check_count_partition,
    check_native_partition,
    check_partition,
    collect_files,
    collect_node_ids,
    selected_args,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW_PATH = _REPO_ROOT / ".github/workflows/ci.yml"
_SHARD_JOB = "integration-shard"
_AGGREGATE_JOB = "integration"
_HEAVY_FILES = tuple(path for _, paths in NAMED_GROUPS for path in paths)
_HEAVY_NODES = tuple(f"{path}::test_selected" for path in _HEAVY_FILES)
_REST_NODE = "tests/integration/api/test_access_token_auth.py::test_auth"
_SLOW_ONLY_NODE = "tests/integration/test_e2e_pipeline.py::test_slow"

_SELECTED_FIXTURE = {
    (INTEGRATION_ROOT, *COMMON_SELECTED_ARGS): (*_HEAVY_NODES, _REST_NODE),
    selected_args(SHARDS[0]): _HEAVY_NODES,
    selected_args(SHARDS[1]): (_REST_NODE,),
    (*ALL_NON_LIVE_ARGS, INTEGRATION_ROOT): (
        *_HEAVY_NODES,
        _REST_NODE,
        _SLOW_ONLY_NODE,
    ),
    all_non_live_args(SHARDS[0]): _HEAVY_NODES,
    all_non_live_args(SHARDS[1]): (_REST_NODE, _SLOW_ONLY_NODE),
}


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(_WORKFLOW_PATH.read_text())


def _run_scripts(job: Mapping[str, Any]) -> list[str]:
    return [step["run"] for step in job["steps"] if "run" in step]


def _pytest_filter_args(script: str) -> tuple[str, ...]:
    run_line = next(
        line.strip() for line in script.splitlines() if "uv run pytest" in line
    )
    args = shlex.split(run_line)
    assert args[:3] == ["uv", "run", "pytest"]
    pytest_args = args[3:]
    pytest_args = [arg for arg in pytest_args if arg != "${PYTEST_ARGS}"]
    filtered: list[str] = []
    skip_next = False
    for arg in pytest_args:
        if skip_next:
            skip_next = False
            continue
        if arg == "-v":
            continue
        if arg == "--junitxml":
            skip_next = True
            continue
        if arg.startswith("--junitxml="):
            continue
        filtered.append(arg)
    return tuple(filtered)


def _fake_collector(
    pytest_args: Sequence[str], *, repo_root: Path, python: str | None = None
) -> tuple[str, ...]:
    del repo_root, python
    return _SELECTED_FIXTURE[tuple(pytest_args)]


class TestIntegrationShardDefinitions:
    def test_the_catch_all_shard_is_computed_not_listed(self) -> None:
        shards = build_shards(
            (("heavy", ("tests/integration/db/test_operator_role.py",)),)
        )

        assert [shard.id for shard in shards] == ["heavy", CATCH_ALL_SHARD_ID]
        assert shards[-1].paths == (INTEGRATION_ROOT,)
        assert shards[-1].ignores == ("tests/integration/db/test_operator_role.py",)

    def test_the_catch_all_ignores_every_named_heavy_path(self) -> None:
        claimed = tuple(path for _, paths in NAMED_GROUPS for path in paths)

        assert SHARDS[-1].id == CATCH_ALL_SHARD_ID
        assert SHARDS[-1].ignores == claimed

    def test_pytest_args_scope_a_shard_to_its_paths(self) -> None:
        shard = Shard(
            id="x",
            paths=(INTEGRATION_ROOT,),
            ignores=("tests/integration/db/test_operator_role.py",),
        )

        assert shard.pytest_args() == (
            INTEGRATION_ROOT,
            "--ignore=tests/integration/db/test_operator_role.py",
        )

    def test_a_shard_selecting_nothing_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="selects no paths"):
            Shard(id="empty", paths=())

    def test_all_non_live_collection_clears_repo_addopts(self) -> None:
        assert ALL_NON_LIVE_ARGS[:2] == ("--override-ini", "addopts=")
        assert f"--ignore={LIVE_ROOT}" in ALL_NON_LIVE_ARGS

    def test_curated_paths_are_safe_for_shell_argv_transport(self) -> None:
        with pytest.raises(ValueError, match="not safe"):
            Shard(id="unsafe", paths=("tests/integration/bad path.py",))


class TestCheckPartition:
    def test_an_exhaustive_partition_is_accepted(self) -> None:
        report = check_partition(
            shard_items={"a": frozenset({"n1"}), "b": frozenset({"n2"})},
            all_items=frozenset({"n1", "n2"}),
        )

        assert report.is_exhaustive_partition
        assert report.describe() == "shards partition the suite exactly"

    def test_missing_items_are_reported(self) -> None:
        report = check_partition(
            shard_items={"a": frozenset({"n1"})},
            all_items=frozenset({"n1", "n2"}),
        )

        assert not report.is_exhaustive_partition
        assert report.missing == frozenset({"n2"})

    def test_duplicated_items_are_reported(self) -> None:
        report = check_partition(
            shard_items={"a": frozenset({"n1"}), "b": frozenset({"n1"})},
            all_items=frozenset({"n1"}),
        )

        assert not report.is_exhaustive_partition
        assert report.duplicated == frozenset({"n1"})

    def test_unexpected_items_are_reported(self) -> None:
        report = check_partition(
            shard_items={"a": frozenset({"n1", "ghost"})},
            all_items=frozenset({"n1"}),
        )

        assert not report.is_exhaustive_partition
        assert report.unexpected == frozenset({"ghost"})

    def test_empty_shards_are_reported(self) -> None:
        report = check_partition(
            shard_items={"a": frozenset(), "b": frozenset({"n1"})},
            all_items=frozenset({"n1"}),
        )

        assert not report.is_exhaustive_partition
        assert report.empty_shards == frozenset({"a"})


class TestCheckCountPartition:
    def test_exactly_once_partition_is_accepted(self) -> None:
        report = check_count_partition(
            shard_items={"a": ("n1",), "b": ("n2",)}, all_items=("n1", "n2")
        )

        assert report.is_exactly_once_partition

    def test_duplicate_in_one_shard_is_rejected(self) -> None:
        report = check_count_partition(
            shard_items={"a": ("n1", "n1"), "b": ("n2",)},
            all_items=("n1", "n2"),
        )

        assert not report.is_exactly_once_partition
        assert report.duplicated_in_shards == frozenset({"n1"})
        assert report.count_mismatched == frozenset({"n1"})

    def test_duplicate_in_whole_collection_is_rejected(self) -> None:
        report = check_count_partition(
            shard_items={"a": ("n1",), "b": ("n2",)},
            all_items=("n1", "n1", "n2"),
        )

        assert not report.is_exactly_once_partition
        assert report.duplicated_in_whole == frozenset({"n1"})
        assert report.count_mismatched == frozenset({"n1"})


class TestInjectedPartitionProof:
    def test_selected_nodes_and_all_non_live_files_partition(self) -> None:
        message = check_native_partition(
            repo_root=_REPO_ROOT, collector=_fake_collector
        )

        assert message == (
            "integration shards partition selected nodes and all non-live files: "
            "7 selected nodes, 8 non-live files"
        )

    def test_duplicate_node_inside_a_shard_fails_through_public_proof(self) -> None:
        def collector(
            pytest_args: Sequence[str], *, repo_root: Path, python: str | None = None
        ) -> tuple[str, ...]:
            nodes = _fake_collector(pytest_args, repo_root=repo_root, python=python)
            if tuple(pytest_args) == selected_args(SHARDS[0]):
                return (*nodes, nodes[0])
            return nodes

        with pytest.raises(SystemExit, match="duplicated inside a shard"):
            check_native_partition(repo_root=_REPO_ROOT, collector=collector)

    def test_duplicate_node_in_whole_collection_fails_through_public_proof(
        self,
    ) -> None:
        def collector(
            pytest_args: Sequence[str], *, repo_root: Path, python: str | None = None
        ) -> tuple[str, ...]:
            nodes = _fake_collector(pytest_args, repo_root=repo_root, python=python)
            if tuple(pytest_args) == (INTEGRATION_ROOT, *COMMON_SELECTED_ARGS):
                return (*nodes, nodes[0])
            return nodes

        with pytest.raises(SystemExit, match="duplicated in whole-suite collection"):
            check_native_partition(repo_root=_REPO_ROOT, collector=collector)

    def test_collector_failure_propagates_through_public_proof(self) -> None:
        def collector(
            pytest_args: Sequence[str], *, repo_root: Path, python: str | None = None
        ) -> tuple[str, ...]:
            del pytest_args, repo_root, python
            raise RuntimeError("collection failed")

        with pytest.raises(RuntimeError, match="collection failed"):
            check_native_partition(repo_root=_REPO_ROOT, collector=collector)

    def test_omitting_the_catch_all_loses_current_rest_tests(self) -> None:
        selected_nodes = collect_node_ids(
            (INTEGRATION_ROOT, *COMMON_SELECTED_ARGS),
            repo_root=_REPO_ROOT,
            collector=_fake_collector,
        )
        listed_only = {
            SHARDS[0].id: collect_node_ids(
                selected_args(SHARDS[0]),
                repo_root=_REPO_ROOT,
                collector=_fake_collector,
            )
        }

        report = check_partition(shard_items=listed_only, all_items=selected_nodes)

        assert not report.is_exhaustive_partition
        assert report.missing == frozenset({_REST_NODE})

    def test_all_non_live_files_include_slow_only_catch_all_file(self) -> None:
        files = collect_files(
            all_non_live_args(SHARDS[1]),
            repo_root=_REPO_ROOT,
            collector=_fake_collector,
        )

        assert "tests/integration/test_e2e_pipeline.py" in files

    def test_all_mark_collections_use_addopts_clear(self) -> None:
        seen_args: list[tuple[str, ...]] = []

        def collector(
            pytest_args: Sequence[str], *, repo_root: Path, python: str | None = None
        ) -> tuple[str, ...]:
            seen_args.append(tuple(pytest_args))
            return _fake_collector(pytest_args, repo_root=repo_root, python=python)

        check_native_partition(repo_root=_REPO_ROOT, collector=collector)

        all_mark_calls = [
            args for args in seen_args if args[:2] == ALL_NON_LIVE_ARGS[:2]
        ]
        assert len(all_mark_calls) == 3


class TestCuratedPathValidation:
    def test_every_real_curated_file_is_represented_in_selected_membership(
        self,
    ) -> None:
        selected = _fake_collector(selected_args(SHARDS[0]), repo_root=_REPO_ROOT)
        selected_files = {node.split("::", maxsplit=1)[0] for node in selected}

        assert selected_files == set(_HEAVY_FILES)

    def test_missing_curated_file_fails_through_public_proof(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        path = "tests/integration/db/test_missing.py"
        shards = build_shards((("heavy", (path,)),))
        monkeypatch.setattr(integration_shards, "SHARDS", shards)
        monkeypatch.setattr(integration_shards, "NAMED_GROUPS", (("heavy", (path,)),))

        with pytest.raises(SystemExit, match="not an existing file"):
            check_native_partition(
                repo_root=tmp_path, collector=_single_path_collector(path)
            )

    def test_live_curated_file_fails_through_public_proof(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        path = "tests/integration/live/test_live.py"
        (tmp_path / path).parent.mkdir(parents=True)
        (tmp_path / path).write_text("def test_live():\n    assert True\n")
        shards = build_shards((("heavy", (path,)),))
        monkeypatch.setattr(integration_shards, "SHARDS", shards)
        monkeypatch.setattr(integration_shards, "NAMED_GROUPS", (("heavy", (path,)),))

        with pytest.raises(SystemExit, match="live integration"):
            check_native_partition(
                repo_root=tmp_path, collector=_single_path_collector(path)
            )

    def test_empty_curated_file_fails_through_public_proof(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        selected = "tests/integration/db/test_selected.py"
        empty = "tests/integration/db/test_empty.py"
        for path in (selected, empty):
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).write_text("def test_placeholder():\n    assert True\n")
        shards = build_shards((("heavy", (selected, empty)),))
        monkeypatch.setattr(integration_shards, "SHARDS", shards)
        monkeypatch.setattr(
            integration_shards, "NAMED_GROUPS", (("heavy", (selected, empty)),)
        )

        with pytest.raises(SystemExit, match="not discovered"):
            check_native_partition(
                repo_root=tmp_path,
                collector=_curated_stale_collector(selected=selected, stale=empty),
            )

    def test_slow_only_curated_file_fails_through_public_proof(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        selected = "tests/integration/db/test_selected.py"
        slow_only = "tests/integration/db/test_slow_only.py"
        for path in (selected, slow_only):
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).write_text("def test_placeholder():\n    assert True\n")
        shards = build_shards((("heavy", (selected, slow_only)),))
        monkeypatch.setattr(integration_shards, "SHARDS", shards)
        monkeypatch.setattr(
            integration_shards, "NAMED_GROUPS", (("heavy", (selected, slow_only)),)
        )

        with pytest.raises(SystemExit, match="no selected non-slow tests"):
            check_native_partition(
                repo_root=tmp_path,
                collector=_curated_stale_collector(
                    selected=selected, stale=slow_only, stale_in_all_mark=True
                ),
            )


def _single_path_collector(path: str):
    node = f"{path}::test_selected"
    rest_node = "tests/integration/api/test_rest.py::test_rest"

    def collector(
        pytest_args: Sequence[str], *, repo_root: Path, python: str | None = None
    ) -> tuple[str, ...]:
        del repo_root, python
        args = tuple(pytest_args)
        if args == (INTEGRATION_ROOT, *COMMON_SELECTED_ARGS):
            return (node, rest_node)
        if args == selected_args(integration_shards.SHARDS[0]):
            return (node,)
        if args == selected_args(integration_shards.SHARDS[1]):
            return (rest_node,)
        if args == (*ALL_NON_LIVE_ARGS, INTEGRATION_ROOT):
            return (node, rest_node)
        if args == all_non_live_args(integration_shards.SHARDS[0]):
            return (node,)
        if args == all_non_live_args(integration_shards.SHARDS[1]):
            return (rest_node,)
        raise AssertionError(args)

    return collector


def _curated_stale_collector(
    *, selected: str, stale: str, stale_in_all_mark: bool = False
):
    selected_node = f"{selected}::test_selected"
    stale_node = f"{stale}::test_slow"
    rest_node = "tests/integration/api/test_rest.py::test_rest"

    def collector(
        pytest_args: Sequence[str], *, repo_root: Path, python: str | None = None
    ) -> tuple[str, ...]:
        del repo_root, python
        args = tuple(pytest_args)
        if args == (INTEGRATION_ROOT, *COMMON_SELECTED_ARGS):
            return (selected_node, rest_node)
        if args == selected_args(integration_shards.SHARDS[0]):
            return (selected_node,)
        if args == selected_args(integration_shards.SHARDS[1]):
            return (rest_node,)
        if args == (*ALL_NON_LIVE_ARGS, INTEGRATION_ROOT):
            return (selected_node, rest_node) + (
                (stale_node,) if stale_in_all_mark else ()
            )
        if args == all_non_live_args(integration_shards.SHARDS[0]):
            return (selected_node,) + ((stale_node,) if stale_in_all_mark else ())
        if args == all_non_live_args(integration_shards.SHARDS[1]):
            return (rest_node,)
        raise AssertionError(args)

    return collector


class TestCiWorkflowIntegrationMatrix:
    def test_the_matrix_lists_exactly_the_defined_shards(self) -> None:
        matrix = _workflow()["jobs"][_SHARD_JOB]["strategy"]["matrix"]["shard"]

        assert tuple(matrix) == SHARD_IDS

    def test_one_shard_failing_does_not_cancel_the_other(self) -> None:
        assert _workflow()["jobs"][_SHARD_JOB]["strategy"]["fail-fast"] is False

    def test_shards_derive_their_scope_from_this_module(self) -> None:
        run_scripts = "\n".join(_run_scripts(_workflow()["jobs"][_SHARD_JOB]))

        assert "tools/integration_shards.py --pytest-args" in run_scripts
        assert "tests/integration/db/test_operator_role.py" not in run_scripts

    def test_workflow_execution_filters_match_the_proof_filters(self) -> None:
        run_script = next(
            script
            for script in _run_scripts(_workflow()["jobs"][_SHARD_JOB])
            if "uv run pytest" in script
        )

        assert _pytest_filter_args(run_script) == COMMON_SELECTED_ARGS

    def test_partition_proof_runs_in_one_already_set_up_integration_leg(self) -> None:
        proof_steps = [
            step
            for step in _workflow()["jobs"][_SHARD_JOB]["steps"]
            if step.get("name") == "Prove integration shard partition"
        ]

        assert len(proof_steps) == 1
        (proof_step,) = proof_steps
        assert proof_step["if"] == "matrix.shard == 'heavy'"
        assert "tools/integration_shards.py --check-partition" in proof_step["run"]

    def test_each_shard_uploads_a_unique_bounded_junit_artifact(self) -> None:
        steps = _workflow()["jobs"][_SHARD_JOB]["steps"]
        run_step = next(
            step for step in steps if step.get("name") == "Run integration tests"
        )
        upload = next(
            step
            for step in steps
            if step.get("name") == "Upload integration JUnit durations"
        )

        assert "--junitxml" in run_step["run"]
        assert "junit-integration-${{ matrix.shard }}.xml" in run_step["run"]
        assert upload["if"] == "${{ !cancelled() }}"
        assert upload["continue-on-error"] is True
        assert upload["timeout-minutes"] == 1
        assert upload["with"]["name"] == "integration-junit-${{ matrix.shard }}"
        assert upload["with"]["if-no-files-found"] == "warn"

    def test_aggregate_preserves_the_required_integration_check_context(self) -> None:
        job = _workflow()["jobs"][_AGGREGATE_JOB]

        assert "name" not in job
        assert job["needs"] == [_SHARD_JOB]
        assert job["if"] == "${{ always() }}"

    @pytest.mark.parametrize(
        ("result", "expected"),
        [
            ("success", 0),
            ("failure", 1),
            ("cancelled", 1),
            ("skipped", 1),
            ("", 1),
            ("timed_out", 1),
        ],
    )
    def test_aggregate_yaml_guard_executes_without_checkout_or_uv(
        self, tmp_path: Path, result: str, expected: int
    ) -> None:
        aggregate = _workflow()["jobs"][_AGGREGATE_JOB]
        script = "\n".join(_run_scripts(aggregate))
        executable = script.replace("${{ needs.integration-shard.result }}", result)

        completed = integration_shards.subprocess.run(
            ["bash", "-euo", "pipefail", "-c", executable],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == expected
        assert "uv run" not in script
        assert "actions/checkout" not in script
        if expected == 0:
            assert "integration shards passed" in completed.stdout
        else:
            assert "::error::integration shards finished" in completed.stdout

    def test_unknown_cli_shard_id_fails(self) -> None:
        with pytest.raises(SystemExit, match="unknown shard"):
            integration_shards.main(["--pytest-args", "unknown"])
