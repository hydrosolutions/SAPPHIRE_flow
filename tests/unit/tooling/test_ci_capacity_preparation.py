from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
JOB = "aquacast-release-image-proof"
STEP = "Prepare hosted runner capacity"
ORIGINAL_ROOTS = ("/usr/local/lib/android/sdk", "/usr/share/dotnet")
RootKind = Literal["dir", "absent", "symlink", "dangling", "file"]


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text())


def _step() -> dict[str, Any]:
    return next(
        step for step in _workflow()["jobs"][JOB]["steps"] if step.get("name") == STEP
    )


@dataclass(frozen=True, kw_only=True)
class RunResult:
    completed: subprocess.CompletedProcess[str]
    roots: tuple[Path, Path]
    rm_log: Path
    realpath_log: Path


class TestCapacityWorkflowContract:
    def test_is_bounded_hosted_and_between_identity_and_buildx(self) -> None:
        job = _workflow()["jobs"][JOB]
        steps = job["steps"]
        step = _step()
        assert job["runs-on"] == "ubuntu-latest"
        assert job["timeout-minutes"] == 35
        assert step["if"] == "env.AQUACAST_TOKEN_PRESENT == 'true'"
        assert step["timeout-minutes"] == 2
        assert (
            steps.index(step)
            == next(
                i
                for i, item in enumerate(steps)
                if item.get("name") == "Assert checkout matches GitHub SHA"
            )
            + 1
        )
        assert steps.index(step) < next(
            i
            for i, item in enumerate(steps)
            if str(item.get("uses", "")).startswith("docker/setup-buildx-action@")
        )
        assert "Build" not in step["name"]
        assert "env" not in step
        assert "continue-on-error" not in step
        assert sum(item.get("timeout-minutes", 0) for item in steps) == 34
        assert job["timeout-minutes"] >= 34

    def test_script_is_exactly_allowlisted(self) -> None:
        script = _step()["run"]
        assert all(root in script for root in ORIGINAL_ROOTS)
        assert "set -euo pipefail" in script
        assert "set -x" not in script
        assert script.count("sudo ") == 2
        assert [
            line.strip()
            for line in script.splitlines()
            if line.strip().startswith("sudo ")
        ] == [f"sudo rm -rf --one-file-system -- {root}" for root in ORIGINAL_ROOTS]
        assert "df -B1 --output=avail /" in script
        assert "/proc/self/mountinfo" in script
        assert "du " not in script
        for forbidden in ("/opt/hostedtoolcache", "/var/lib/docker", "${SDK", "eval "):
            assert forbidden not in script


class TestCapacityScriptBehavior:
    def _run(
        self,
        tmp_path: Path,
        *,
        df_outputs: tuple[str, ...] = (" Avail \n100\n", " Avail \n20000000000\n"),
        root_kinds: tuple[RootKind, RootKind] = ("dir", "dir"),
        mount_lines: str = "1 2 0:1 / / rw - ext4 /dev/x rw\n",
        mountinfo_directory: bool = False,
        canonical_mismatch: bool = False,
        realpath_failure: bool = False,
        rm_failure: bool = False,
        df_failure_at: int | None = None,
        context: dict[str, str] | None = None,
    ) -> RunResult:
        tmp_path.mkdir(parents=True, exist_ok=True)
        test_roots = (tmp_path / "android", tmp_path / "dotnet")
        for kind, path in zip(root_kinds, test_roots, strict=True):
            path.parent.mkdir(parents=True, exist_ok=True)
            if kind == "dir":
                path.mkdir()
            elif kind == "symlink":
                target = path.with_name(f"{path.name}-target")
                target.mkdir()
                path.symlink_to(target, target_is_directory=True)
            elif kind == "dangling":
                path.symlink_to(
                    path.with_name("missing-target"), target_is_directory=True
                )
            elif kind == "file":
                path.write_text("not a directory")

        mountinfo = tmp_path / "mountinfo"
        if mountinfo_directory:
            mountinfo.mkdir()
        else:
            mountinfo.write_text(mount_lines)

        script = _step()["run"]
        for original, replacement in zip(ORIGINAL_ROOTS, test_roots, strict=True):
            script = script.replace(original, str(replacement.absolute()))
        script = script.replace("/proc/self/mountinfo", str(mountinfo))
        assert not any(root in script for root in ORIGINAL_ROOTS)
        script_path = tmp_path / "capacity.sh"
        script_path.write_text(script)

        bin_dir = tmp_path / "bin"
        outputs = tmp_path / "df-outputs"
        bin_dir.mkdir()
        outputs.mkdir()
        for index, output in enumerate(df_outputs):
            (outputs / str(index)).write_text(output)
        state = tmp_path / "df-state"
        state.write_text("0")
        (bin_dir / "df").write_text(
            "#!/bin/sh\n"
            'index=$(cat "$DF_STATE")\n'
            "next=$((index + 1))\n"
            'printf \'%s\\n\' "$next" > "$DF_STATE"\n'
            'if [ "${DF_FAILURE_AT:-}" = "$index" ]; then exit 8; fi\n'
            'file="$DF_OUTPUT_DIR/$index"\n'
            'if [ ! -f "$file" ]; then '
            "echo 'fake df sequence exhausted' >&2; exit 88; fi\n"
            'cat "$file"\n'
        )
        realpath_log = tmp_path / "realpath.log"
        (bin_dir / "realpath").write_text(
            "#!/bin/sh\n"
            'printf \'%s\\n\' "$*" >> "$REALPATH_LOG"\n'
            'if [ "${REALPATH_FAILURE:-0}" = 1 ]; then exit 7; fi\n'
            "last=''; for arg in \"$@\"; do last=$arg; done\n"
            'if [ "${CANONICAL_MISMATCH:-0}" = 1 ]; then '
            "printf '%s-mismatch\\n' \"$last\"; "
            "else printf '%s\\n' \"$last\"; fi\n"
        )
        (bin_dir / "sudo").write_text(
            '#!/bin/sh\n[ "$1" = rm ] || exit 95\nexec "$@"\n'
        )
        rm_log = tmp_path / "rm.log"
        allowed = ":".join(str(path.absolute()) for path in test_roots)
        (bin_dir / "rm").write_text(
            "#!/bin/sh\n"
            'printf \'%s\\n\' "$*" >> "$RM_LOG"\n'
            '[ "$#" -eq 4 ] && [ "$1" = -rf ] && '
            '[ "$2" = --one-file-system ] && [ "$3" = -- ] || exit 96\n'
            "last=$4\n"
            'case :$ALLOWED_ROOTS: in *:"$last":*) ;; '
            "*) echo unsafe >&2; exit 97;; esac\n"
            'if [ "${RM_FAILURE:-0}" = 1 ]; then exit 9; fi\n'
            '/bin/rm -rf -- "$last"\n'
        )
        for command in ("df", "realpath", "sudo", "rm"):
            (bin_dir / command).chmod(0o755)
        env = {
            "PATH": f"{bin_dir}:/bin:/usr/bin",
            "ALLOWED_ROOTS": allowed,
            "DF_OUTPUT_DIR": str(outputs),
            "DF_STATE": str(state),
            "RM_LOG": str(rm_log),
            "REALPATH_LOG": str(realpath_log),
            "CANONICAL_MISMATCH": "1" if canonical_mismatch else "0",
            "REALPATH_FAILURE": "1" if realpath_failure else "0",
            "RM_FAILURE": "1" if rm_failure else "0",
            "GITHUB_ACTIONS": "true",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "GITHUB_JOB": JOB,
        }
        if df_failure_at is not None:
            env["DF_FAILURE_AT"] = str(df_failure_at)
        if context is not None:
            for key in ("GITHUB_ACTIONS", "RUNNER_ENVIRONMENT", "GITHUB_JOB"):
                env.pop(key, None)
            env.update(context)
        completed = subprocess.run(
            ["bash", str(script_path)],
            env=env,
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=False,
        )
        return RunResult(
            completed=completed,
            roots=test_roots,
            rm_log=rm_log,
            realpath_log=realpath_log,
        )

    @staticmethod
    def _assert_rejected(result: RunResult, message: str) -> None:
        assert result.completed.returncode != 0
        assert message in result.completed.stdout + result.completed.stderr
        assert all(path.exists() or path.is_symlink() for path in result.roots)
        assert not result.rm_log.exists()

    def test_noop_at_minimum_does_not_inspect_unusable_roots(
        self, tmp_path: Path
    ) -> None:
        result = self._run(
            tmp_path,
            df_outputs=(" Avail \n20000000000\n", " Avail \n20000000000\n"),
            root_kinds=("dangling", "file"),
            mountinfo_directory=True,
            realpath_failure=True,
        )
        assert result.completed.returncode == 0, (
            result.completed.stderr + result.completed.stdout
        )
        assert not result.rm_log.exists()
        assert not result.realpath_log.exists()

    def test_removes_both_roots_with_exact_argv(self, tmp_path: Path) -> None:
        result = self._run(tmp_path)
        assert result.completed.returncode == 0, (
            result.completed.stderr + result.completed.stdout
        )
        assert not any(path.exists() for path in result.roots)
        assert result.rm_log.read_text().splitlines() == [
            f"-rf --one-file-system -- {result.roots[0]}",
            f"-rf --one-file-system -- {result.roots[1]}",
        ]

    @pytest.mark.parametrize(
        ("output", "message"),
        [
            ("", "must contain exactly two lines"),
            (" Wrong \n100\n", "unexpected root filesystem capacity header"),
            (" Avail \nnot-a-number\n", "one non-negative decimal integer"),
            (" Avail \n1\n2\n", "must contain exactly two lines"),
            (" Avail \n100\n\n", "must contain exactly two lines"),
            (" Avail \n-1\n", "one non-negative decimal integer"),
            (" Avail \n9223372036854775808\n", "supported numeric range"),
            (" Avail \n999999999999999999999999999999\n", "supported numeric range"),
        ],
    )
    def test_malformed_df_fails_before_removal(
        self, tmp_path: Path, output: str, message: str
    ) -> None:
        result = self._run(tmp_path, df_outputs=(output, " Avail \n20000000000\n"))
        self._assert_rejected(result, message)

    @pytest.mark.parametrize(
        "context",
        [
            {"RUNNER_ENVIRONMENT": "github-hosted", "GITHUB_JOB": JOB},
            {
                "GITHUB_ACTIONS": "false",
                "RUNNER_ENVIRONMENT": "github-hosted",
                "GITHUB_JOB": JOB,
            },
            {
                "GITHUB_ACTIONS": "true",
                "RUNNER_ENVIRONMENT": "self-hosted",
                "GITHUB_JOB": JOB,
            },
            {
                "GITHUB_ACTIONS": "true",
                "RUNNER_ENVIRONMENT": "github-hosted",
                "GITHUB_JOB": "other",
            },
        ],
    )
    def test_rejects_each_runtime_guard_mismatch(
        self, tmp_path: Path, context: dict[str, str]
    ) -> None:
        result = self._run(tmp_path, context=context)
        self._assert_rejected(result, "refused outside its exact CI job")

    @pytest.mark.parametrize("root_index", [0, 1])
    @pytest.mark.parametrize("kind", ["symlink", "dangling"])
    def test_rejects_symlink_roots(
        self, tmp_path: Path, root_index: int, kind: RootKind
    ) -> None:
        kinds: list[RootKind] = ["dir", "dir"]
        kinds[root_index] = kind
        result = self._run(tmp_path, root_kinds=(kinds[0], kinds[1]))
        self._assert_rejected(result, "allowlisted SDK root is a symlink")

    @pytest.mark.parametrize("root_index", [0, 1])
    def test_rejects_non_directory_roots(self, tmp_path: Path, root_index: int) -> None:
        kinds: list[RootKind] = ["dir", "dir"]
        kinds[root_index] = "file"
        result = self._run(tmp_path, root_kinds=(kinds[0], kinds[1]))
        self._assert_rejected(result, "allowlisted SDK root is not a directory")

    @pytest.mark.parametrize(
        ("failure", "message"),
        [
            ("mismatch", "allowlisted SDK root is not canonical"),
            ("command", "cannot resolve allowlisted SDK root"),
        ],
    )
    def test_rejects_canonical_path_failures(
        self, tmp_path: Path, failure: Literal["mismatch", "command"], message: str
    ) -> None:
        result = self._run(
            tmp_path,
            canonical_mismatch=failure == "mismatch",
            realpath_failure=failure == "command",
        )
        self._assert_rejected(result, message)

    @pytest.mark.parametrize("root_index", [0, 1])
    @pytest.mark.parametrize("device", ["0:1", "8:7"])
    @pytest.mark.parametrize("nested", [False, True])
    def test_rejects_exact_and_nested_mounts_on_both_roots(
        self, tmp_path: Path, root_index: int, device: str, nested: bool
    ) -> None:
        root = (tmp_path / ("android" if root_index == 0 else "dotnet")).absolute()
        mount = f"{root}/nested" if nested else str(root)
        result = self._run(
            tmp_path,
            mount_lines=f"1 2 {device} / {mount} rw - ext4 /dev/x rw\n",
        )
        self._assert_rejected(result, "mount exists at or below allowlisted SDK root")

    @pytest.mark.parametrize(
        ("mount_lines", "directory", "message"),
        [
            ("", False, "Linux mount table is empty"),
            ("1 2 0:1 / / rw\n", False, "malformed Linux mount table"),
            (
                "1 2 0:1 / / rw option extra ext4 /dev/x rw\n",
                False,
                "malformed Linux mount table",
            ),
            (
                "1 2 0:1 / / rw - ext4 /dev/x rw - extra x y\n",
                False,
                "malformed Linux mount table",
            ),
            ("", True, "cannot read Linux mount table"),
        ],
    )
    def test_rejects_empty_malformed_and_unreadable_mountinfo(
        self, tmp_path: Path, mount_lines: str, directory: bool, message: str
    ) -> None:
        result = self._run(
            tmp_path, mount_lines=mount_lines, mountinfo_directory=directory
        )
        self._assert_rejected(result, message)

    def test_mount_sibling_does_not_match_allowlisted_root(
        self, tmp_path: Path
    ) -> None:
        sibling = (tmp_path / "dotnet2").absolute()
        result = self._run(
            tmp_path,
            mount_lines=f"1 2 0:1 / {sibling} rw - ext4 /dev/x rw\n",
        )
        assert result.completed.returncode == 0, (
            result.completed.stderr + result.completed.stdout
        )

    def test_absent_roots_are_safe_and_telemetry_is_bounded(
        self, tmp_path: Path
    ) -> None:
        result = self._run(tmp_path, root_kinds=("absent", "absent"))
        assert result.completed.returncode == 0, (
            result.completed.stderr + result.completed.stdout
        )
        assert (
            "available_bytes=100 minimum_bytes=20000000000" in result.completed.stdout
        )
        assert (
            "available_bytes=20000000000 minimum_bytes=20000000000"
            in result.completed.stdout
        )
        assert not result.rm_log.exists()

    @pytest.mark.parametrize("failure_at", [0, 1])
    def test_df_command_error_fails_closed(
        self, tmp_path: Path, failure_at: int
    ) -> None:
        result = self._run(tmp_path, df_failure_at=failure_at)
        if failure_at == 0:
            self._assert_rejected(result, "root filesystem capacity query failed")
        else:
            assert result.completed.returncode != 0
            assert "root filesystem capacity query failed" in (
                result.completed.stdout + result.completed.stderr
            )

    def test_removal_failure_preserves_both_roots(self, tmp_path: Path) -> None:
        result = self._run(tmp_path, rm_failure=True)
        assert result.completed.returncode != 0
        assert all(path.exists() for path in result.roots)
        assert result.rm_log.read_text().splitlines() == [
            f"-rf --one-file-system -- {result.roots[0]}"
        ]

    def test_post_cleanup_shortfall_fails(self, tmp_path: Path) -> None:
        result = self._run(
            tmp_path,
            df_outputs=(" Avail \n100\n", " Avail \n19999999999\n"),
        )
        assert result.completed.returncode != 0
        assert "requires at least 20000000000" in result.completed.stdout

    @pytest.mark.parametrize("absent_index", [0, 1])
    def test_one_absent_root_removes_only_the_other(
        self, tmp_path: Path, absent_index: int
    ) -> None:
        kinds: tuple[RootKind, RootKind] = (
            ("absent", "dir") if absent_index == 0 else ("dir", "absent")
        )
        result = self._run(tmp_path, root_kinds=kinds)
        assert result.completed.returncode == 0, result.completed.stderr
        assert result.rm_log.read_text().splitlines() == [
            f"-rf --one-file-system -- {result.roots[1 - absent_index]}"
        ]

    def test_df_without_terminal_newline_is_readable(self, tmp_path: Path) -> None:
        result = self._run(
            tmp_path, df_outputs=(" Avail \n100", " Avail \n20000000000")
        )
        assert result.completed.returncode == 0, result.completed.stderr

    def test_fake_df_fails_when_sequence_is_exhausted(self, tmp_path: Path) -> None:
        result = self._run(tmp_path, df_outputs=(" Avail \n100\n",))
        assert result.completed.returncode != 0
        assert "fake df sequence exhausted" in result.completed.stderr
