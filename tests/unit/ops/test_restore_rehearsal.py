"""Ops tests for Plan 162 T5 — scripts/restore-rehearsal.sh.

Shell-script tests (not Python unit tests): we shell out to exercise the
actual script; docker is faked by setting DOCKER_CMD to an absolute path
of a stub, the same convention tests/unit/ops/test_launchd_prune_docker.py
and test_recap_probe_wrapper.py use. The fake docker dispatches on
argv/query-substring so a single stub can play "healthy Postgres
container" across `run`/`logs`/`exec`/`cp`/`rm -f`.

Required cases (docs/plans/162-robust-database-backup.md T5):
  (a) success exits 0 and reports the assertions made.
  (b) a pg_restore failure exits non-zero AND still tears down.
  (c) a restore that succeeds but yields zero rows in access_tokens FAILS
      naming that table.
  (d) teardown happens when an assertion fails mid-way.
  (e) a sequence with last_value == MAX(id) and is_called = false FAILS.

Locked 2026-08-18 against the four real-artifact restore failures found by
running #180's merged script against a live mac-mini dump (see the plan's
"VERIFIED RESTORE PROCEDURE" section):
  (f) the restore targets a FRESH database via `createdb`, never the image's
      pre-populated default `postgres` (PostGIS pre-initialises `tiger` there
      -> `ERROR: schema "tiger" already exists`).
  (g) the pg_restore invocation carries `--no-owner --no-acl` (pg_dump does
      not back up cluster roles, so a fresh cluster otherwise dies on
      `ERROR: role "sapphire" does not exist`).
  (h) when pg_restore fails, its STDERR TEXT (not just a generic message)
      appears in the script's own failure output.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import textwrap
from pathlib import Path

_SCRIPT = (
    Path(__file__).parent.parent.parent.parent / "scripts" / "restore-rehearsal.sh"
)


def _write_fake_docker(
    bin_dir: Path,
    args_log: Path,
    *,
    pg_restore_exit: int = 0,
    pg_restore_stderr: str = "",
    access_tokens_count: str = "3",
    access_tokens_max_id: str = "5",
    seq_last_value: str = "5",
    seq_is_called: str = "t",
) -> Path:
    """A stateful fake `docker` that recognises the exact subcommands
    restore-rehearsal.sh issues and answers each on its merits, logging
    every invocation (one line per call) so teardown can be asserted on."""
    quoted_pg_restore_stderr = shlex.quote(pg_restore_stderr)
    script = textwrap.dedent(
        f"""\
        #!/bin/bash
        printf '%s\\n' "$*" >> "{args_log}"
        case "$1" in
          run)
            echo "fake-container-id"
            exit 0
            ;;
          logs)
            echo "PostgreSQL init process complete; ready for start up."
            exit 0
            ;;
          cp)
            exit 0
            ;;
          rm)
            exit 0
            ;;
          exec)
            case "$*" in
              *pg_isready*)
                exit 0
                ;;
              *createdb*)
                exit 0
                ;;
              *pg_restore*)
                if [[ -n {quoted_pg_restore_stderr} ]]; then
                  echo {quoted_pg_restore_stderr} >&2
                fi
                exit {pg_restore_exit}
                ;;
              *"SELECT 1"*)
                echo "1"
                exit 0
                ;;
              *"count(*) FROM access_tokens"*)
                echo "{access_tokens_count}"
                exit 0
                ;;
              *"version_num FROM alembic_version"*)
                echo "abcd1234ef56"
                exit 0
                ;;
              *"access_tokens_id_seq"*)
                echo "{seq_last_value}|{seq_is_called}"
                exit 0
                ;;
              *"coalesce(max(id), 0) FROM access_tokens"*)
                echo "{access_tokens_max_id}"
                exit 0
                ;;
              *)
                echo "fake docker: unrecognised exec: $*" >&2
                exit 1
                ;;
            esac
            ;;
          *)
            echo "fake docker: unrecognised command: $*" >&2
            exit 1
            ;;
        esac
        """
    )
    fake = bin_dir / "docker"
    fake.write_text(script)
    fake.chmod(0o755)
    return fake


def _exec_calls(args_log: Path, substring: str) -> list[str]:
    if not args_log.exists():
        return []
    return [
        line
        for line in args_log.read_text().splitlines()
        if line.startswith("exec ") and substring in line
    ]


def _run_script(
    tmp_path: Path, docker_cmd: Path, dump_path: Path
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "DOCKER_CMD": str(docker_cmd),
        "RESTORE_CONTAINER_NAME": "test-restore-rehearsal",
        "RESTORE_WAIT_ATTEMPTS": "2",
        "RESTORE_WAIT_INTERVAL": "0",
    }
    return subprocess.run(
        ["bash", str(_SCRIPT), str(dump_path)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )


def _dump_file(tmp_path: Path) -> Path:
    dump = tmp_path / "fake.dump"
    dump.write_bytes(b"not-a-real-dump")
    return dump


def _rm_calls(args_log: Path) -> list[str]:
    if not args_log.exists():
        return []
    return [
        line for line in args_log.read_text().splitlines() if line.startswith("rm ")
    ]


class TestScriptExists:
    def test_script_file_exists(self) -> None:
        assert _SCRIPT.exists(), f"expected {_SCRIPT} to exist"


class TestHappyPath:
    def test_success_exits_zero_and_reports_assertions(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        fake = _write_fake_docker(bin_dir, args_log)
        result = _run_script(tmp_path, fake, _dump_file(tmp_path))

        assert result.returncode == 0, result.stderr
        assert "PASS" in result.stdout
        assert "access_tokens" in result.stdout
        assert len(_rm_calls(args_log)) == 1, "container was not torn down exactly once"


class TestPgRestoreFailure:
    def test_pg_restore_failure_exits_nonzero_and_tears_down(
        self, tmp_path: Path
    ) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        fake = _write_fake_docker(bin_dir, args_log, pg_restore_exit=1)
        result = _run_script(tmp_path, fake, _dump_file(tmp_path))

        assert result.returncode != 0
        assert "PASS" not in result.stdout
        assert "pg_restore" in result.stderr.lower()
        assert len(_rm_calls(args_log)) == 1, (
            "docker rm -f must still run after a pg_restore failure"
        )


class TestEmptyAccessTokens:
    def test_zero_rows_in_access_tokens_fails_naming_the_table(
        self, tmp_path: Path
    ) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        fake = _write_fake_docker(bin_dir, args_log, access_tokens_count="0")
        result = _run_script(tmp_path, fake, _dump_file(tmp_path))

        assert result.returncode != 0
        assert "PASS" not in result.stdout
        assert "access_tokens" in result.stderr
        assert "zero rows" in result.stderr
        assert len(_rm_calls(args_log)) == 1, (
            "teardown must still happen when a content assertion fails mid-way"
        )


class TestSequenceCollisionRisk:
    def test_last_value_equals_max_id_with_is_called_false_fails(
        self, tmp_path: Path
    ) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        fake = _write_fake_docker(
            bin_dir,
            args_log,
            access_tokens_max_id="5",
            seq_last_value="5",
            seq_is_called="f",
        )
        result = _run_script(tmp_path, fake, _dump_file(tmp_path))

        assert result.returncode != 0
        assert "PASS" not in result.stdout
        assert "is_called" in result.stderr
        assert "access_tokens_id_seq" in result.stderr
        assert len(_rm_calls(args_log)) == 1, (
            "teardown must still happen when the sequence check fails"
        )

    def test_last_value_below_max_id_with_is_called_false_passes(
        self, tmp_path: Path
    ) -> None:
        """is_called=false is only a collision risk when last_value has
        already caught up to MAX(id) -- a freshly-created, never-advanced
        sequence (last_value < max_id would be a different, pre-existing
        data problem outside this script's scope) is not this bug."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        fake = _write_fake_docker(
            bin_dir,
            args_log,
            access_tokens_max_id="5",
            seq_last_value="1",
            seq_is_called="f",
        )
        result = _run_script(tmp_path, fake, _dump_file(tmp_path))

        assert result.returncode == 0, result.stderr


class TestRestoresIntoFreshDatabase:
    def test_restore_targets_a_fresh_database_not_postgres(
        self, tmp_path: Path
    ) -> None:
        """FIX 1: the PostGIS image pre-initialises tiger/tiger_data/topology
        in its default `postgres` database, so a restore into it dies on
        `ERROR: schema "tiger" already exists` against a real dump. The
        script must `createdb` a fresh database and restore into THAT, never
        `-d postgres`."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        fake = _write_fake_docker(bin_dir, args_log)
        result = _run_script(tmp_path, fake, _dump_file(tmp_path))

        assert result.returncode == 0, result.stderr
        assert _exec_calls(args_log, "createdb"), (
            "createdb was never invoked — restore did not create a fresh database"
        )
        pg_restore_calls = _exec_calls(args_log, "pg_restore")
        assert pg_restore_calls, "pg_restore was never invoked"
        assert " -d postgres " not in f" {pg_restore_calls[-1]} ", (
            "pg_restore targeted the image's pre-populated default "
            f"'postgres' database instead of a fresh one: {pg_restore_calls[-1]}"
        )


class TestPgRestoreNoOwnerNoAcl:
    def test_pg_restore_argv_includes_no_owner_and_no_acl(self, tmp_path: Path) -> None:
        """FIX 2: pg_dump does not back up cluster roles, but the dump is
        full of OWNER TO / ACL statements referencing them. Without
        --no-owner --no-acl a fresh cluster dies on
        `ERROR: role "sapphire" does not exist`."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        fake = _write_fake_docker(bin_dir, args_log)
        result = _run_script(tmp_path, fake, _dump_file(tmp_path))

        assert result.returncode == 0, result.stderr
        pg_restore_calls = _exec_calls(args_log, "pg_restore")
        assert pg_restore_calls, "pg_restore was never invoked"
        pg_restore_argv = pg_restore_calls[-1]
        assert "--no-owner" in pg_restore_argv, pg_restore_argv
        assert "--no-acl" in pg_restore_argv, pg_restore_argv


class TestPgRestoreStderrSurfaced:
    def test_pg_restore_stderr_text_appears_in_script_output(
        self, tmp_path: Path
    ) -> None:
        """FIX 3: the merged script discards pg_restore's stderr
        (`>/dev/null 2>&1`), so the operator only ever sees the generic
        "FAIL: pg_restore failed" — the actual diagnostic (e.g. which schema
        already exists, which role is missing) is invisible. It must be
        captured and included in the script's own failure output."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        distinctive_error = (
            'pg_restore: error: could not execute query: ERROR:  schema "tiger" '
            "already exists"
        )
        fake = _write_fake_docker(
            bin_dir,
            args_log,
            pg_restore_exit=1,
            pg_restore_stderr=distinctive_error,
        )
        result = _run_script(tmp_path, fake, _dump_file(tmp_path))

        assert result.returncode != 0
        assert distinctive_error in result.stderr, result.stderr


class TestMissingDump:
    def test_missing_dump_path_fails_without_invoking_docker(
        self, tmp_path: Path
    ) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        fake = _write_fake_docker(bin_dir, args_log)
        result = _run_script(tmp_path, fake, tmp_path / "does-not-exist.dump")

        assert result.returncode != 0
        assert not args_log.exists(), "docker was invoked despite a missing dump file"
