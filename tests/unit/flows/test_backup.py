"""Plan 162 Phase A — T2 (allowlisted credential, no URL) + T3 (atomic,
validated publication under a filesystem advisory lock)."""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

from sapphire_flow.flows import backup as backup_mod
from sapphire_flow.flows.backup import (
    FINAL_NAME_GLOB,
    BackupConfigError,
    BackupHeadroomError,
    BackupLockTimeoutError,
    BackupValidationError,
    backup_database_flow,
    build_pg_child_env,
    cleanup_old_backups_task,
    dump_database_task,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_TOC_WITH_ACCESS_TOKENS = """;
; Archive created at 2026-08-15 00:00:00 UTC
;     dbname: sapphire
;     TOC Entries: 4
;     Format: CUSTOM
;
217; 1259 24610 TABLE public access_tokens sapphire_backup
3421; 0 24610 TABLE DATA public access_tokens sapphire_backup
3422; 0 24612 TABLE DATA public access_token_stations sapphire_backup
3500; 0 0 ACL public access_tokens sapphire_backup
"""

# Every line here is a DECOY: another schema, TABLE without DATA, an ACL
# entry, a similarly-named table, and the real access_tokens row is a
# schema definition (no DATA) only. No anchored `TABLE DATA public
# access_tokens` line exists anywhere.
_TOC_WITHOUT_ACCESS_TOKENS_DATA = """;
; Archive created at 2026-08-15 00:00:00 UTC
;
217; 1259 24610 TABLE public access_tokens sapphire_backup
3421; 0 24610 TABLE DATA other_schema access_tokens sapphire_backup
3422; 0 24612 TABLE DATA public access_token_stations sapphire_backup
3423; 0 24613 TABLE DATA public access_tokens_old sapphire_backup
3500; 0 0 ACL public access_tokens sapphire_backup
3501; 0 0 COMMENT public access_tokens sapphire_backup
"""


def _set_backup_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    password_file = tmp_path / "backup_password"
    password_file.write_text("backup-secret-pw\n")
    monkeypatch.setenv("SAPPHIRE_BACKUP_PGHOST", "postgres")
    monkeypatch.setenv("SAPPHIRE_BACKUP_PGPORT", "5432")
    monkeypatch.setenv("SAPPHIRE_BACKUP_PGUSER", "sapphire_backup")
    monkeypatch.setenv("SAPPHIRE_BACKUP_PGDATABASE", "sapphire")
    monkeypatch.setenv("SAPPHIRE_BACKUP_DB_PASSWORD_FILE", str(password_file))


@contextmanager
def _patch_subprocess_run(
    run_impl: Callable[..., subprocess.CompletedProcess[str]] | MagicMock,
) -> Iterator[Callable[..., subprocess.CompletedProcess[str]] | MagicMock]:
    """Replace ONLY `backup.py`'s `subprocess` name binding with a fake
    exposing `.run`/`.TimeoutExpired` — NOT the real, process-wide
    `subprocess` module. `sapphire_flow.flows.backup.subprocess` IS the real
    module object, so `patch.object(backup_mod.subprocess, "run", ...)`
    mutates the shared module for the whole process, including Prefect's own
    internal `subprocess.run` calls (a cold-start ephemeral-server import
    chain shells out to `file` via `platform.architecture()`) — this scopes
    the fake to backup.py alone."""
    fake_module = SimpleNamespace(
        run=run_impl, TimeoutExpired=subprocess.TimeoutExpired
    )
    with patch.object(backup_mod, "subprocess", fake_module):
        yield run_impl


def _dispatch_fake_subprocess_run(
    *, toc_output: str = _TOC_WITH_ACCESS_TOKENS
) -> MagicMock:
    """A fake `subprocess.run` that behaves like a real pg_dump (writes the
    `--file=` target) and pg_restore --list (returns `toc_output`)."""

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "pg_dump":
            for arg in cmd:
                if arg.startswith("--file="):
                    Path(arg.split("=", 1)[1]).write_bytes(b"fake dump bytes")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
        if cmd[0] == "pg_restore":
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout=toc_output, stderr=""
            )
        raise AssertionError(f"unexpected command: {cmd}")

    mock = MagicMock(side_effect=_fake_run)
    return mock


# ---------------------------------------------------------------------------
# T2 — build_pg_child_env: allowlisted credential, no URL
# ---------------------------------------------------------------------------


class TestBuildPgChildEnv:
    def test_builds_expected_pg_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_backup_env(monkeypatch, tmp_path)
        env = build_pg_child_env()
        assert env["PGHOST"] == "postgres"
        assert env["PGPORT"] == "5432"
        assert env["PGUSER"] == "sapphire_backup"
        assert env["PGDATABASE"] == "sapphire"
        assert env["PGPASSWORD"] == "backup-secret-pw"

    @pytest.mark.parametrize(
        "name",
        [
            "SAPPHIRE_BACKUP_PGHOST",
            "SAPPHIRE_BACKUP_PGPORT",
            "SAPPHIRE_BACKUP_PGUSER",
            "SAPPHIRE_BACKUP_PGDATABASE",
            "SAPPHIRE_BACKUP_DB_PASSWORD_FILE",
        ],
    )
    def test_missing_required_var_raises_loudly(
        self, name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_backup_env(monkeypatch, tmp_path)
        monkeypatch.delenv(name, raising=False)
        with pytest.raises(RuntimeError, match=name):
            build_pg_child_env()

    @pytest.mark.parametrize(
        "name",
        [
            "SAPPHIRE_BACKUP_PGHOST",
            "SAPPHIRE_BACKUP_PGPORT",
            "SAPPHIRE_BACKUP_PGUSER",
            "SAPPHIRE_BACKUP_PGDATABASE",
        ],
    )
    def test_empty_required_var_raises_loudly_not_silently_defaulted(
        self, name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_backup_env(monkeypatch, tmp_path)
        monkeypatch.setenv(name, "")
        with pytest.raises(RuntimeError, match=name):
            build_pg_child_env()

    def test_missing_password_file_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_backup_env(monkeypatch, tmp_path)
        monkeypatch.setenv(
            "SAPPHIRE_BACKUP_DB_PASSWORD_FILE", str(tmp_path / "does-not-exist")
        )
        with pytest.raises(RuntimeError, match="cannot read"):
            build_pg_child_env()

    def test_empty_password_file_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_backup_env(monkeypatch, tmp_path)
        empty = tmp_path / "empty_pw"
        empty.write_text("")
        monkeypatch.setenv("SAPPHIRE_BACKUP_DB_PASSWORD_FILE", str(empty))
        with pytest.raises(RuntimeError, match="empty"):
            build_pg_child_env()

    def test_unrelated_pg_env_vars_are_absent_from_child(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan 162 T2 red-first criterion: seed unrelated PG* variables in
        the parent process and assert they never reach the child — the
        ALLOWLIST, not a copy of os.environ, decides the child's env."""
        _set_backup_env(monkeypatch, tmp_path)
        monkeypatch.setenv("PGSERVICE", "some-other-service")
        monkeypatch.setenv("PGOPTIONS", "-c statement_timeout=1")
        monkeypatch.setenv("PGPASSFILE", "/tmp/.pgpass")
        monkeypatch.setenv("PGSSLMODE", "disable")
        monkeypatch.setenv("PGDATABASE", "some_other_db")  # a plain libpq var too

        env = build_pg_child_env()

        assert "PGSERVICE" not in env
        assert "PGOPTIONS" not in env
        assert "PGPASSFILE" not in env
        assert "PGSSLMODE" not in env
        # The allowlist builds PGDATABASE from SAPPHIRE_BACKUP_PGDATABASE,
        # not from whatever PGDATABASE happens to be set to in the parent.
        assert env["PGDATABASE"] == "sapphire"

    def test_backup_identity_used_even_with_conflicting_worker_credential(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_backup_env(monkeypatch, tmp_path)
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://sapphire_worker:worker-pw@postgres:5432/sapphire",
        )
        env = build_pg_child_env()
        assert env["PGUSER"] == "sapphire_backup"
        assert env["PGPASSWORD"] == "backup-secret-pw"
        assert "DATABASE_URL" not in env

    def test_path_passed_through_when_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_backup_env(monkeypatch, tmp_path)
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        env = build_pg_child_env()
        assert env["PATH"] == "/usr/bin:/bin"

    def test_no_url_is_ever_constructed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No URL-shaped value anywhere in the child env — T2's hard rule."""
        _set_backup_env(monkeypatch, tmp_path)
        env = build_pg_child_env()
        assert not any("://" in v for v in env.values())

    def test_password_file_whitespace_is_preserved_except_trailing_newline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan 162 T2 review fix, red-first: the shell wrapper
        (`bootstrap-roles.sh`) reads the sibling API/worker password files
        with `$(cat file)`, which strips only trailing newline
        character(s) — never interior or leading whitespace. A `.strip()`
        on the Python side disagreed with that convention and would turn a
        password with deliberate leading/trailing spaces into a credential
        mismatch. RED against `.strip()`: this password's leading/trailing
        spaces would be silently dropped, so the assertion below would see
        `"secret-pw-with-space"` instead of `"  secret-pw-with-space  "`."""
        _set_backup_env(monkeypatch, tmp_path)
        password_file = tmp_path / "backup_password"
        password_file.write_text("  secret-pw-with-space  \n")
        monkeypatch.setenv("SAPPHIRE_BACKUP_DB_PASSWORD_FILE", str(password_file))

        env = build_pg_child_env()

        assert env["PGPASSWORD"] == "  secret-pw-with-space  "

    def test_password_file_crlf_matches_shell_dollar_paren_cat(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan 162 T1-fixer-round MINOR, red-first: `Path.read_text()`
        applies universal-newline translation before `rstrip("\\n")`,
        silently rewriting a CR/CRLF ANYWHERE in the file to `\\n` —
        disagreeing with `bootstrap-roles.sh`'s shell `$(cat file)`, which
        strips ONLY trailing `\\n` characters and never touches `\\r`
        (verified against real bash: `$(cat file)` on bytes
        `b"secret-pw\\r\\n"` yields `"secret-pw\\r"`, trailing CR intact).
        Byte-write a password ending in CRLF and assert the trailing `\\r`
        survives, proving the two readers agree on the actual role
        password. RED against `.read_text()` + `rstrip("\\n")`: universal
        newlines collapses the trailing `\\r\\n` to `\\n` before `rstrip`
        ever runs, so the trailing `\\r` would be silently dropped and this
        assertion would see `"secret-pw"` instead of `"secret-pw\\r"`."""
        _set_backup_env(monkeypatch, tmp_path)
        password_file = tmp_path / "backup_password"
        password_file.write_bytes(b"secret-pw\r\n")
        monkeypatch.setenv("SAPPHIRE_BACKUP_DB_PASSWORD_FILE", str(password_file))

        env = build_pg_child_env()

        assert env["PGPASSWORD"] == "secret-pw\r"

    def test_password_file_embedded_cr_is_preserved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Companion to the CRLF case: a lone `\\r` embedded in the middle
        of the password (not a line ending at all, from the shell's point
        of view) must survive untouched — `$(cat file)` never treats `\\r`
        as a newline anywhere, only `\\n`. RED against universal-newline
        translation, which rewrites a lone `\\r` to `\\n` regardless of
        position."""
        _set_backup_env(monkeypatch, tmp_path)
        password_file = tmp_path / "backup_password"
        password_file.write_bytes(b"sec\rret-pw\n")
        monkeypatch.setenv("SAPPHIRE_BACKUP_DB_PASSWORD_FILE", str(password_file))

        env = build_pg_child_env()

        assert env["PGPASSWORD"] == "sec\rret-pw"


# ---------------------------------------------------------------------------
# T3 — the anchored, schema-qualified access_tokens TOC regex
# ---------------------------------------------------------------------------


class TestAccessTokensTocRegex:
    def test_matches_real_table_data_entry(self) -> None:
        assert backup_mod._ACCESS_TOKENS_TOC_RE.search(_TOC_WITH_ACCESS_TOKENS)

    def test_does_not_match_decoys_only(self) -> None:
        assert not backup_mod._ACCESS_TOKENS_TOC_RE.search(
            _TOC_WITHOUT_ACCESS_TOKENS_DATA
        )

    def test_rejects_another_schema(self) -> None:
        line = "3421; 0 24610 TABLE DATA other_schema access_tokens sapphire_backup\n"
        assert not backup_mod._ACCESS_TOKENS_TOC_RE.search(line)

    def test_rejects_table_without_data(self) -> None:
        line = "217; 1259 24610 TABLE public access_tokens sapphire_backup\n"
        assert not backup_mod._ACCESS_TOKENS_TOC_RE.search(line)

    def test_rejects_acl_entry(self) -> None:
        line = "3500; 0 0 ACL public access_tokens sapphire_backup\n"
        assert not backup_mod._ACCESS_TOKENS_TOC_RE.search(line)

    def test_rejects_comment_entry(self) -> None:
        line = "3501; 0 0 COMMENT public access_tokens sapphire_backup\n"
        assert not backup_mod._ACCESS_TOKENS_TOC_RE.search(line)

    def test_rejects_similarly_named_table(self) -> None:
        line = "3423; 0 24613 TABLE DATA public access_tokens_old sapphire_backup\n"
        assert not backup_mod._ACCESS_TOKENS_TOC_RE.search(line)

    def test_rejects_access_token_stations(self) -> None:
        line = "3422; 0 24612 TABLE DATA public access_token_stations sapphire_backup\n"
        assert not backup_mod._ACCESS_TOKENS_TOC_RE.search(line)

    def test_rejects_header_comment_lines(self) -> None:
        line = "; Archive created at 2026-08-15 00:00:00 UTC\n"
        assert not backup_mod._ACCESS_TOKENS_TOC_RE.search(line)


class TestValidateDump:
    def test_passes_with_anchored_entry(self, tmp_path: Path) -> None:
        fake_run = MagicMock(
            return_value=subprocess.CompletedProcess(
                [], returncode=0, stdout=_TOC_WITH_ACCESS_TOKENS, stderr=""
            )
        )
        with _patch_subprocess_run(fake_run):
            backup_mod._validate_dump(tmp_path / "x.tmp", timeout_s=5.0)

    def test_raises_when_only_decoys_present(self, tmp_path: Path) -> None:
        fake_run = MagicMock(
            return_value=subprocess.CompletedProcess(
                [],
                returncode=0,
                stdout=_TOC_WITHOUT_ACCESS_TOKENS_DATA,
                stderr="",
            )
        )
        with (
            _patch_subprocess_run(fake_run),
            pytest.raises(BackupValidationError, match="access_tokens"),
        ):
            backup_mod._validate_dump(tmp_path / "x.tmp", timeout_s=5.0)

    def test_raises_when_pg_restore_nonzero(self, tmp_path: Path) -> None:
        fake_run = MagicMock(
            return_value=subprocess.CompletedProcess(
                [], returncode=1, stdout="", stderr="corrupt archive"
            )
        )
        with (
            _patch_subprocess_run(fake_run),
            pytest.raises(BackupValidationError, match="corrupt archive"),
        ):
            backup_mod._validate_dump(tmp_path / "x.tmp", timeout_s=5.0)

    def test_raises_on_timeout(self, tmp_path: Path) -> None:
        fake_run = MagicMock(
            side_effect=subprocess.TimeoutExpired(cmd=["pg_restore"], timeout=5.0)
        )
        with (
            _patch_subprocess_run(fake_run),
            pytest.raises(BackupValidationError, match="timed out"),
        ):
            backup_mod._validate_dump(tmp_path / "x.tmp", timeout_s=5.0)


# ---------------------------------------------------------------------------
# T3 — filesystem advisory lock
# ---------------------------------------------------------------------------


class TestBackupLock:
    def test_acquires_and_releases(self, tmp_path: Path) -> None:
        with backup_mod._backup_lock(tmp_path, timeout_s=1.0):
            pass
        # A second acquisition after release must succeed immediately.
        with backup_mod._backup_lock(tmp_path, timeout_s=1.0):
            pass

    def test_lock_contention_raises_after_timeout(self, tmp_path: Path) -> None:
        """Plan 162 T3 red-first criterion: lock contention from a second
        process. A second `os.open` on the SAME lock file is a distinct
        open file description, so its `flock()` genuinely contends with
        the first — this reproduces cross-process contention without
        needing an actual second OS process."""
        lock_path = tmp_path / backup_mod.LOCK_FILENAME
        holder_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(holder_fd, fcntl.LOCK_EX)
        try:
            start = time.monotonic()
            with (
                pytest.raises(BackupLockTimeoutError),
                backup_mod._backup_lock(tmp_path, timeout_s=0.3),
            ):
                pass
            assert time.monotonic() - start >= 0.3
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)

    def test_lock_released_on_exception_inside_block(self, tmp_path: Path) -> None:
        with (
            pytest.raises(ValueError, match="boom"),
            backup_mod._backup_lock(tmp_path, timeout_s=1.0),
        ):
            raise ValueError("boom")
        # The lock must have been released — a fresh acquisition succeeds.
        with backup_mod._backup_lock(tmp_path, timeout_s=0.3):
            pass


# A genuinely separate OS process (via `subprocess.Popen`, not a second file
# descriptor in THIS process) that opens and `flock()`s the backup lock file,
# signals readiness by writing `ready_path`, and holds the lock until
# `release_path` appears. `TestBackupLock`'s and `TestDumpDatabaseTask`'s
# earlier "second process" tests use two file descriptors in the SAME
# process — a distinct open-file-description, so `flock()` genuinely
# contends, but it does not exercise real inter-process contention.
_LOCK_HOLDER_SCRIPT = """
import fcntl, os, sys, time
lock_path, ready_path, release_path = sys.argv[1], sys.argv[2], sys.argv[3]
fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
with open(ready_path, "w") as f:
    f.write("ready")
deadline = time.monotonic() + 10.0
while not os.path.exists(release_path) and time.monotonic() < deadline:
    time.sleep(0.02)
fcntl.flock(fd, fcntl.LOCK_UN)
os.close(fd)
"""


@contextmanager
def _lock_held_by_real_child_process(lock_dir: Path, tmp_path: Path) -> Iterator[None]:
    """Starts the real child process above, blocks until it confirms the
    lock is held, yields, then signals release and reaps the child."""
    lock_path = lock_dir / backup_mod.LOCK_FILENAME
    ready = tmp_path / "child_lock_ready"
    release = tmp_path / "child_lock_release"
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        [
            sys.executable,
            "-c",
            _LOCK_HOLDER_SCRIPT,
            str(lock_path),
            str(ready),
            str(release),
        ]
    )
    try:
        deadline = time.monotonic() + 5.0
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "child process failed to acquire the backup lock"
        yield
    finally:
        release.write_text("release")
        try:
            proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=5.0)


class TestBackupLockCrossProcess:
    """Plan 162 T3 review fix: the same contention/timeout properties as
    `TestBackupLock`, but against an ACTUAL second OS process, and proving
    sweep/prune/headroom mutations stay behind the lock (not merely that
    the lock call itself raises)."""

    def test_backup_lock_raises_when_a_real_process_holds_it(
        self, tmp_path: Path
    ) -> None:
        with _lock_held_by_real_child_process(tmp_path, tmp_path):
            start = time.monotonic()
            with (
                pytest.raises(BackupLockTimeoutError),
                backup_mod._backup_lock(tmp_path, timeout_s=0.3),
            ):
                pass
            assert time.monotonic() - start >= 0.3

    def test_second_real_process_blocks_dump_database_task_without_mutating_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Red-first: a fake `subprocess.run` (pg_dump) must never be
        invoked, and pre-existing sweep/prune targets must survive
        untouched, while a REAL second process holds the lock."""
        _set_backup_env(monkeypatch, tmp_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(parents=True)

        stale_temp = backup_dir / "sapphire_20260101_000000_deadbeef.dump.tmp"
        stale_temp.write_bytes(b"x")
        os.utime(stale_temp, (1000, 1000))
        old_artifact = backup_dir / "sapphire_20260101_000000_11111111.dump"
        old_artifact.write_bytes(b"old")
        os.utime(old_artifact, (1000, 1000))

        monkeypatch.setattr(backup_mod, "LOCK_ACQUIRE_TIMEOUT_S", 0.3)

        with _lock_held_by_real_child_process(backup_dir, tmp_path):
            start = time.monotonic()
            with (
                _patch_subprocess_run(MagicMock()) as mock_run,
                pytest.raises(BackupLockTimeoutError),
            ):
                dump_database_task.fn(str(backup_dir))
            assert time.monotonic() - start >= 0.3
            mock_run.assert_not_called()

        # Contention happened BEFORE sweep/prune/headroom ran under the
        # lock — nothing was mutated.
        assert stale_temp.exists()
        assert old_artifact.exists()


# ---------------------------------------------------------------------------
# T3 — stale-temp sweep, pre-prune retention, headroom check
# ---------------------------------------------------------------------------


class TestSweepStaleTemps:
    def test_removes_temps_older_than_threshold(self, tmp_path: Path) -> None:
        stale = tmp_path / "sapphire_20260101_000000_deadbeef.dump.tmp"
        stale.write_bytes(b"x")
        os.utime(stale, (1000, 1000))
        swept = backup_mod._sweep_stale_temps(
            tmp_path, older_than_s=3600, now_ts=1000 + 7200
        )
        assert swept == [stale]
        assert not stale.exists()

    def test_keeps_fresh_temps(self, tmp_path: Path) -> None:
        fresh = tmp_path / "sapphire_20260101_000000_cafebabe.dump.tmp"
        fresh.write_bytes(b"x")
        os.utime(fresh, (1000, 1000))
        swept = backup_mod._sweep_stale_temps(
            tmp_path, older_than_s=3600, now_ts=1000 + 60
        )
        assert swept == []
        assert fresh.exists()

    def test_ignores_published_artifacts(self, tmp_path: Path) -> None:
        final = tmp_path / "sapphire_20260101_000000_00000000.dump"
        final.write_bytes(b"x")
        os.utime(final, (1000, 1000))
        backup_mod._sweep_stale_temps(tmp_path, older_than_s=1, now_ts=1000 + 7200)
        assert final.exists()


class TestPruneRetention:
    def _make(self, tmp_path: Path, name: str, mtime: float) -> Path:
        p = tmp_path / name
        p.write_bytes(b"x")
        os.utime(p, (mtime, mtime))
        return p

    def test_removes_oldest_beyond_keep_count(self, tmp_path: Path) -> None:
        old = self._make(tmp_path, "sapphire_20260101_000000_11111111.dump", 1000)
        mid = self._make(tmp_path, "sapphire_20260102_000000_22222222.dump", 2000)
        new = self._make(tmp_path, "sapphire_20260103_000000_33333333.dump", 3000)
        removed = backup_mod._prune_retention(tmp_path, keep_count=2)
        assert removed == [old]
        remaining = {p.name for p in tmp_path.glob(FINAL_NAME_GLOB)}
        assert remaining == {mid.name, new.name}

    def test_never_prunes_below_one_even_with_keep_count_zero(
        self, tmp_path: Path
    ) -> None:
        old = self._make(tmp_path, "sapphire_20260101_000000_11111111.dump", 1000)
        new = self._make(tmp_path, "sapphire_20260102_000000_22222222.dump", 2000)
        removed = backup_mod._prune_retention(tmp_path, keep_count=0)
        assert removed == [old]
        remaining = list(tmp_path.glob(FINAL_NAME_GLOB))
        assert len(remaining) == 1
        assert remaining[0] == new

    def test_ignores_non_matching_names(self, tmp_path: Path) -> None:
        (tmp_path / "sapphire_bad_name.dump").write_bytes(b"x")
        (tmp_path / "sapphire_20260101_000000_11111111.dump.tmp").write_bytes(b"x")
        removed = backup_mod._prune_retention(tmp_path, keep_count=0)
        assert removed == []


class TestValidPublishedArtifactsRejectsDecoys:
    """Plan 162 T3 review fix: `_valid_published_artifacts` must apply the
    SAME lstat()+S_ISREG+size>0 predicate the watchdog uses — a name-matching
    but zero-byte file, a symlink, or a directory must never be treated as
    "the artifact to keep" (or as a target retention can safely delete a
    genuine dump in favour of)."""

    def test_ignores_zero_byte_file(self, tmp_path: Path) -> None:
        real = tmp_path / "sapphire_20260101_000000_11111111.dump"
        real.write_bytes(b"data")
        os.utime(real, (1000, 1000))
        zero = tmp_path / "sapphire_20260102_000000_22222222.dump"
        zero.write_bytes(b"")
        os.utime(zero, (2000, 2000))  # newer mtime than the real dump
        assert backup_mod._valid_published_artifacts(tmp_path) == [real]

    def test_ignores_symlink(self, tmp_path: Path) -> None:
        real = tmp_path / "sapphire_20260101_000000_11111111.dump"
        real.write_bytes(b"data")
        os.utime(real, (1000, 1000))
        target = tmp_path / "not_a_dump.bin"
        target.write_bytes(b"data")
        link = tmp_path / "sapphire_20260102_000000_22222222.dump"
        link.symlink_to(target)
        assert backup_mod._valid_published_artifacts(tmp_path) == [real]

    def test_ignores_directory(self, tmp_path: Path) -> None:
        real = tmp_path / "sapphire_20260101_000000_11111111.dump"
        real.write_bytes(b"data")
        os.utime(real, (1000, 1000))
        decoy_dir = tmp_path / "sapphire_20260102_000000_22222222.dump"
        decoy_dir.mkdir()
        assert backup_mod._valid_published_artifacts(tmp_path) == [real]

    def test_prune_retention_never_deletes_the_last_real_dump_for_a_decoy(
        self, tmp_path: Path
    ) -> None:
        """Plan 162 T3 review fix, red-first: a newer zero-byte decoy must
        not be preferred over a real, older dump during retention. RED
        against the pre-fix `_valid_published_artifacts` (plain `.glob` +
        regex match, no lstat/S_ISREG/size check): it counts the decoy as a
        second valid artifact, sorts it as NEWEST by mtime, and prunes the
        genuine (older) dump instead — `real` would no longer exist and the
        assertion below would fail."""
        real = tmp_path / "sapphire_20260101_000000_11111111.dump"
        real.write_bytes(b"data")
        os.utime(real, (1000, 1000))
        zero = tmp_path / "sapphire_20260102_000000_22222222.dump"
        zero.write_bytes(b"")
        os.utime(zero, (2000, 2000))

        removed = backup_mod._prune_retention(tmp_path, keep_count=0)

        assert removed == []
        assert real.exists()


class TestCheckHeadroom:
    def test_raises_when_free_below_floor(self, tmp_path: Path) -> None:
        def _fake_usage(_path: str) -> object:
            class _U:
                total = 1_000_000_000
                used = 999_000_000
                free = 1_000  # far below the floor

            return _U()

        with pytest.raises(BackupHeadroomError):
            backup_mod._check_headroom(tmp_path, disk_usage=_fake_usage)

    def test_passes_when_free_above_floor(self, tmp_path: Path) -> None:
        def _fake_usage(_path: str) -> object:
            class _U:
                total = 10_000_000_000
                used = 1_000_000_000
                free = 9_000_000_000

            return _U()

        backup_mod._check_headroom(tmp_path, disk_usage=_fake_usage)

    def test_requires_multiple_of_latest_artifact_size(self, tmp_path: Path) -> None:
        big = tmp_path / "sapphire_20260101_000000_11111111.dump"
        big.write_bytes(b"x" * 400_000_000)

        def _fake_usage(_path: str) -> object:
            class _U:
                total = 1_000_000_000
                used = 900_000_000
                # Enough for the 200MiB floor but NOT for 2x the 400MB
                # existing artifact.
                free = 300_000_000

            return _U()

        with pytest.raises(BackupHeadroomError):
            backup_mod._check_headroom(tmp_path, disk_usage=_fake_usage)


# ---------------------------------------------------------------------------
# T3 — atomic publish (fsync -> link -> unlink temp -> fsync dir)
# ---------------------------------------------------------------------------


class TestPublish:
    def test_links_temp_to_final_and_removes_temp(self, tmp_path: Path) -> None:
        temp = tmp_path / "sapphire_x.dump.tmp"
        temp.write_bytes(b"dump content")
        final = tmp_path / "sapphire_20260101_000000_deadbeef.dump"

        result = backup_mod._publish(
            backup_dir=tmp_path, temp_path=temp, final_path=final
        )

        assert result.path == final
        assert final.read_bytes() == b"dump content"
        assert not temp.exists()

    def test_never_clobbers_an_existing_final_name(self, tmp_path: Path) -> None:
        temp = tmp_path / "sapphire_x.dump.tmp"
        temp.write_bytes(b"new content")
        final = tmp_path / "sapphire_20260101_000000_deadbeef.dump"
        final.write_bytes(b"pre-existing content")

        with pytest.raises(FileExistsError):
            backup_mod._publish(backup_dir=tmp_path, temp_path=temp, final_path=final)

        assert final.read_bytes() == b"pre-existing content"

    def test_fsync_failure_prevents_publish(self, tmp_path: Path) -> None:
        """Plan 162 T3 red-first criterion: an fsync failure must prevent
        publication — the final name must never appear."""
        temp = tmp_path / "sapphire_x.dump.tmp"
        temp.write_bytes(b"dump content")
        final = tmp_path / "sapphire_20260101_000000_deadbeef.dump"

        with (
            patch.object(backup_mod.os, "fsync", side_effect=OSError("disk error")),
            pytest.raises(OSError, match="disk error"),
        ):
            backup_mod._publish(backup_dir=tmp_path, temp_path=temp, final_path=final)

        assert not final.exists()
        assert temp.exists()  # never linked, never unlinked

    def test_size_is_never_read_from_final_path_after_the_commit_point(
        self, tmp_path: Path
    ) -> None:
        """Plan 162 T3 review fix, red-first: the commit point (fsync-file
        -> link -> unlink-temp -> fsync-dir) must be TERMINAL — nothing
        after it may still be able to fail an already-published run.
        `size_bytes` must be read from `temp_path` before any of that, not
        from `final_path` afterward. RED against the pre-fix `_publish`
        (`size = final_path.stat().st_size` placed after `_fsync_dir`):
        patching `Path.stat` to raise for `final_path` makes that call
        raise, and this test's `_publish(...)` invocation would raise
        instead of returning."""
        temp = tmp_path / "sapphire_x.dump.tmp"
        temp.write_bytes(b"dump content")
        final = tmp_path / "sapphire_20260101_000000_deadbeef.dump"

        orig_stat = Path.stat

        def _stat(self: Path, *args: object, **kwargs: object) -> object:
            if self == final:
                raise OSError("must not stat the final path after commit")
            return orig_stat(self, *args, **kwargs)

        with patch.object(Path, "stat", _stat):
            result = backup_mod._publish(
                backup_dir=tmp_path, temp_path=temp, final_path=final
            )

        assert result.size_bytes == len(b"dump content")
        assert result.path == final

    def test_temp_unlink_failure_after_publish_does_not_fail(
        self, tmp_path: Path
    ) -> None:
        """Plan 162 T1-fixer-round MINOR, red-first: an OSError from
        `temp_path.unlink()` AFTER `os.link` has already succeeded must not
        propagate out of `_publish` — the artifact is already published and
        externally observable (a regular file under its final name) at that
        point, and a leftover temp is harmless (`_sweep_stale_temps`
        reclaims it later). RED against the pre-fix `_publish` (a hard,
        non-best-effort `temp_path.unlink()`): the injected OSError would
        propagate out of `_publish`, and this test's call would raise
        instead of returning."""
        temp = tmp_path / "sapphire_x.dump.tmp"
        temp.write_bytes(b"dump content")
        final = tmp_path / "sapphire_20260101_000000_deadbeef.dump"

        orig_unlink = Path.unlink

        def _unlink(self: Path, *args: object, **kwargs: object) -> None:
            if self == temp:
                raise OSError("transient unlink failure")
            return orig_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", _unlink):
            result = backup_mod._publish(
                backup_dir=tmp_path, temp_path=temp, final_path=final
            )

        assert result.path == final
        assert final.is_file()
        assert final.stat().st_size == len(b"dump content")
        # Best-effort unlink failed — the temp is still there, which is
        # fine; it never matches the published glob.
        assert temp.exists()

    def test_dir_fsync_failure_fails_the_run_because_durability_is_unproven(
        self, tmp_path: Path
    ) -> None:
        """Plan 162 T3 ordering: fsync-the-file -> os.link -> unlink-temp ->
        fsync-the-directory -> THAT is the commit point. The directory
        fsync is what makes the new directory entry DURABLE — unlike the
        temp unlink (which is genuinely cosmetic and stays best-effort), a
        failed directory fsync means durability was never established.
        Swallowing it would report a SUCCESSFUL backup whose existence may
        not survive a crash, which is exactly the false-green class this
        plan exists to eliminate: between a false green and a false red, a
        backup system must choose false red. This behaviour has now
        flipped twice (T1-fixer made it best-effort in error; this test
        locks it back to hard) — do NOT "helpfully" swallow it again.

        RED against 8a18b53, where `_fsync_dir`'s OSError is caught and
        logged as `backup.dir_fsync_after_publish_failed`: the injected
        OSError on the SECOND `os.fsync` invocation (the first is the
        pre-link temp-file fsync, which must still succeed) would be
        swallowed there, and this test's `pytest.raises` would fail to
        see any exception."""
        temp = tmp_path / "sapphire_x.dump.tmp"
        temp.write_bytes(b"dump content")
        final = tmp_path / "sapphire_20260101_000000_deadbeef.dump"

        orig_fsync = os.fsync
        call_count = 0

        def _fsync(fd: int) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:  # 1st = temp-file fsync (pre-link, must pass)
                raise OSError("transient directory fsync failure")
            orig_fsync(fd)

        with (
            patch.object(backup_mod.os, "fsync", _fsync),
            pytest.raises(OSError, match="transient directory fsync failure"),
        ):
            backup_mod._publish(backup_dir=tmp_path, temp_path=temp, final_path=final)

        # os.link already ran: final_path exists even though the run must
        # be reported as failed (durability was never proven).
        assert final.is_file()
        assert not temp.exists()  # unlink itself still succeeded


# ---------------------------------------------------------------------------
# T3 — dump_database_task end to end
# ---------------------------------------------------------------------------


class TestDumpDatabaseTask:
    def test_happy_path_publishes_a_single_validated_artifact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_backup_env(monkeypatch, tmp_path)
        backup_dir = tmp_path / "backups"

        mock_run = MagicMock(side_effect=_dispatch_fake_subprocess_run().side_effect)
        with _patch_subprocess_run(mock_run):
            result = dump_database_task.fn(str(backup_dir))

        result_path = Path(result)
        assert result_path.exists()
        assert result_path.name.startswith("sapphire_")
        assert result_path.name.endswith(".dump")
        mode = stat.S_IMODE(result_path.stat().st_mode)
        assert mode == 0o600
        # No leftover temp files.
        assert list(backup_dir.glob("*.dump.tmp")) == []
        # Env passed to pg_dump used the allowlisted backup identity.
        dump_call = next(
            c for c in mock_run.call_args_list if c.args[0][0] == "pg_dump"
        )
        assert dump_call.kwargs["env"]["PGUSER"] == "sapphire_backup"

    def test_temp_unlink_failure_after_publish_does_not_fail_the_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan 162 T1-fixer-round MINOR, red-first, end-to-end: an OSError
        from the temp-file unlink AFTER `os.link` has already published the
        artifact must not fail `dump_database_task` as a whole. RED against
        the pre-fix `_publish` (hard `temp_path.unlink()`): the OSError
        propagates out of `_publish`, is caught by this task's
        `except BaseException` cleanup handler, and re-raised — failing the
        Prefect run — even though a fully valid, fsynced backup already
        exists at `final_path`. This test asserts both that the task
        returns successfully AND that the published artifact exists and is
        valid (regular file, matching name, size > 0) — not just "no
        exception"."""
        _set_backup_env(monkeypatch, tmp_path)
        backup_dir = tmp_path / "backups"

        orig_unlink = Path.unlink

        def _unlink(self: Path, *args: object, **kwargs: object) -> None:
            if self.name.endswith(".dump.tmp"):
                raise OSError("transient unlink failure")
            return orig_unlink(self, *args, **kwargs)

        fake = _dispatch_fake_subprocess_run()
        with (
            _patch_subprocess_run(fake.side_effect),
            patch.object(Path, "unlink", _unlink),
        ):
            result = dump_database_task.fn(str(backup_dir))

        result_path = Path(result)
        assert result_path.name.startswith("sapphire_")
        assert result_path.name.endswith(".dump")
        st = result_path.lstat()
        assert stat.S_ISREG(st.st_mode)
        assert st.st_size > 0

    def test_partial_content_not_published_on_dump_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan 162 T3 red-first criterion: the fake subprocess WRITES
        PARTIAL CONTENT before failing — a fake that writes nothing passes
        against today's (pre-T3) code trivially and proves nothing."""
        _set_backup_env(monkeypatch, tmp_path)
        backup_dir = tmp_path / "backups"

        def _fake_run(
            cmd: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            if cmd[0] == "pg_dump":
                for arg in cmd:
                    if arg.startswith("--file="):
                        # Partial write, THEN fail — simulates disk-full or
                        # a killed mid-dump process.
                        Path(arg.split("=", 1)[1]).write_bytes(b"partial-bytes")
                return subprocess.CompletedProcess(
                    cmd, returncode=1, stdout="", stderr="disk full"
                )
            raise AssertionError(f"unexpected command: {cmd}")

        with (
            _patch_subprocess_run(_fake_run),
            pytest.raises(RuntimeError, match="disk full"),
        ):
            dump_database_task.fn(str(backup_dir))

        # No file matching the published glob was ever created — an
        # artifact exists only if it validated.
        assert list(backup_dir.glob(FINAL_NAME_GLOB)) == []
        # The partial temp must not survive either.
        assert list(backup_dir.glob("*.dump.tmp")) == []

    def test_validation_failure_does_not_publish(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_backup_env(monkeypatch, tmp_path)
        backup_dir = tmp_path / "backups"

        fake = _dispatch_fake_subprocess_run(toc_output=_TOC_WITHOUT_ACCESS_TOKENS_DATA)
        with (
            _patch_subprocess_run(fake.side_effect),
            pytest.raises(BackupValidationError),
        ):
            dump_database_task.fn(str(backup_dir))

        assert list(backup_dir.glob(FINAL_NAME_GLOB)) == []

    def test_dump_timeout_raises_runtime_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan 162 T3 red-first criterion: a timed-out child must fail the
        run cleanly (and, via `subprocess.run`'s own contract, is reaped
        before this function's cleanup runs)."""
        _set_backup_env(monkeypatch, tmp_path)
        backup_dir = tmp_path / "backups"

        def _fake_run(
            cmd: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            if cmd[0] == "pg_dump":
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))
            raise AssertionError(f"unexpected command: {cmd}")

        with (
            _patch_subprocess_run(_fake_run),
            pytest.raises(RuntimeError, match="timed out"),
        ):
            dump_database_task.fn(str(backup_dir))

        assert list(backup_dir.glob(FINAL_NAME_GLOB)) == []
        assert list(backup_dir.glob("*.dump.tmp")) == []

    def test_lock_contention_from_a_second_process_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan 162 T3 red-first criterion: lock contention from a second
        process (simulated the same way as `TestBackupLock`, at the
        `dump_database_task` level)."""
        _set_backup_env(monkeypatch, tmp_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(parents=True)
        monkeypatch.setattr(backup_mod, "LOCK_ACQUIRE_TIMEOUT_S", 0.3)

        lock_path = backup_dir / backup_mod.LOCK_FILENAME
        holder_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(holder_fd, fcntl.LOCK_EX)
        try:
            with (
                _patch_subprocess_run(MagicMock()) as mock_run,
                pytest.raises(BackupLockTimeoutError),
            ):
                dump_database_task.fn(str(backup_dir))
            mock_run.assert_not_called()
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)

    def test_two_runs_publish_two_distinct_artifacts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_backup_env(monkeypatch, tmp_path)
        backup_dir = tmp_path / "backups"
        fake = _dispatch_fake_subprocess_run()

        with _patch_subprocess_run(fake.side_effect):
            first = dump_database_task.fn(str(backup_dir))
            second = dump_database_task.fn(str(backup_dir))

        assert first != second
        assert len(list(backup_dir.glob(FINAL_NAME_GLOB))) == 2

    def test_pre_prune_reserves_a_slot_for_the_new_artifact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan 162 T3 review fix, red-first: pre-prune must reserve THIS
        run's slot (prune to keep_count - 1) so a successful publish lands
        the steady-state count at exactly keep_count WITHOUT a second,
        post-commit prune. RED against the pre-fix pre-prune
        (`keep_count=keep_count` before the dump): 3 pre-existing + a
        successful new dump leaves 4 artifacts when called directly (no
        flow-level cleanup runs here), so the assertion below would see 4."""
        _set_backup_env(monkeypatch, tmp_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(parents=True)
        for i in range(3):
            f = backup_dir / f"sapphire_20260101_000000_{i:08x}.dump"
            f.write_bytes(b"old")
            os.utime(f, (100 + i, 100 + i))

        fake = _dispatch_fake_subprocess_run()
        with _patch_subprocess_run(fake.side_effect):
            dump_database_task.fn(str(backup_dir), keep_count=3)

        remaining = list(backup_dir.glob(FINAL_NAME_GLOB))
        assert len(remaining) == 3

    def test_keep_count_one_is_a_loud_config_error_not_a_silent_oscillation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan 162 T1-fixer-round MINOR: `keep_count=1` cannot reserve a
        slot AND preserve the floor of one existing artifact in the same
        pre-dump prune — the floor wins, so the run holds 2 artifacts, and
        the NEXT run's pre-prune hits the identical conflict, so the count
        NEVER converges to 1 — it oscillates between 1 and 2 on every run,
        forever. (An earlier revision of this test asserted "the next run
        converges it back to 1", which is WRONG: every run prunes to 1 THEN
        publishes another, landing back at 2.) Rather than silently deliver
        a count the parameter never actually reaches, `keep_count < 2` is
        rejected loudly before any dump is attempted — no subprocess call,
        no new artifact, no pruning. RED against silent acceptance: with no
        validation, this call would succeed and leave 2 artifacts instead
        of raising."""
        _set_backup_env(monkeypatch, tmp_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(parents=True)
        old = backup_dir / "sapphire_20260101_000000_11111111.dump"
        old.write_bytes(b"old")
        os.utime(old, (100, 100))

        with (
            _patch_subprocess_run(MagicMock()) as mock_run,
            pytest.raises(BackupConfigError, match="keep_count=1"),
        ):
            dump_database_task.fn(str(backup_dir), keep_count=1)

        mock_run.assert_not_called()
        remaining = list(backup_dir.glob(FINAL_NAME_GLOB))
        assert remaining == [old]

    def test_keep_count_zero_is_also_a_loud_config_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_backup_env(monkeypatch, tmp_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(parents=True)

        with (
            _patch_subprocess_run(MagicMock()) as mock_run,
            pytest.raises(BackupConfigError, match="keep_count=0"),
        ):
            dump_database_task.fn(str(backup_dir), keep_count=0)

        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# cleanup_old_backups_task — best-effort, post-commit-point retention
# ---------------------------------------------------------------------------


class TestCleanupOldBackupsTask:
    def _create_dumps(self, tmp_path: Path, count: int) -> list[Path]:
        files = []
        for i in range(count):
            f = tmp_path / f"sapphire_20260101_000000_{i:08x}.dump"
            f.write_bytes(b"fake")
            os.utime(f, (1000 + i, 1000 + i))
            files.append(f)
        return files

    def test_removes_oldest_when_over_limit(self, tmp_path: Path) -> None:
        files = self._create_dumps(tmp_path, 5)
        removed = cleanup_old_backups_task.fn(str(tmp_path), keep_count=3)
        assert removed == 2
        remaining = sorted(tmp_path.glob(FINAL_NAME_GLOB))
        assert len(remaining) == 3
        assert files[0] not in remaining
        assert files[1] not in remaining

    def test_noop_at_limit(self, tmp_path: Path) -> None:
        self._create_dumps(tmp_path, 3)
        removed = cleanup_old_backups_task.fn(str(tmp_path), keep_count=3)
        assert removed == 0

    def test_keep_zero_still_preserves_at_least_one(self, tmp_path: Path) -> None:
        # Plan 162 T3: local retention must NEVER reach zero.
        self._create_dumps(tmp_path, 3)
        removed = cleanup_old_backups_task.fn(str(tmp_path), keep_count=0)
        assert removed == 2
        assert len(list(tmp_path.glob(FINAL_NAME_GLOB))) == 1

    def test_ignores_non_dump_files(self, tmp_path: Path) -> None:
        self._create_dumps(tmp_path, 5)
        (tmp_path / "other.txt").write_text("keep me")
        cleanup_old_backups_task.fn(str(tmp_path), keep_count=2)
        assert (tmp_path / "other.txt").exists()

    def test_failure_is_swallowed_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan 162 T3: retention/cleanup failing after a valid artifact
        exists must NOT fail the run — this is the exact bug named in the
        plan (`flows/backup.py:115-121` prior to this fix)."""
        self._create_dumps(tmp_path, 5)

        def _raise(*_args: object, **_kwargs: object) -> list[Path]:
            raise OSError("disk yanked mid-prune")

        monkeypatch.setattr(backup_mod, "_prune_retention", _raise)
        removed = cleanup_old_backups_task.fn(str(tmp_path), keep_count=2)
        assert removed == 0


# ---------------------------------------------------------------------------
# backup_database_flow — integration of tasks
# ---------------------------------------------------------------------------


class TestBackupDatabaseFlow:
    def test_dump_then_cleanup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_backup_env(monkeypatch, tmp_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(parents=True)

        # Pre-create 8 old published artifacts.
        for i in range(8):
            f = backup_dir / f"sapphire_20260101_000000_{i:08x}.dump"
            f.write_bytes(b"old")
            os.utime(f, (100 + i, 100 + i))

        fake = _dispatch_fake_subprocess_run()
        with _patch_subprocess_run(fake.side_effect):
            result = backup_database_flow.fn(backup_dir=str(backup_dir), keep_count=7)

        assert "sapphire_" in result
        # 8 old artifacts; dump_database_task's pre-prune reserves this
        # run's slot (prunes to keep_count - 1 = 6) BEFORE the new dump, so
        # the successful publish lands the count at exactly keep_count = 7
        # with no second, post-commit prune task in the flow.
        remaining = list(backup_dir.glob(FINAL_NAME_GLOB))
        assert len(remaining) == 7

    def test_cleanup_task_never_runs_on_the_success_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan 162 T1-fixer-round MAJOR: the sibling
        `test_dump_then_cleanup` above proves nothing about the post-commit
        regression it names in its comment — both the current flow (no
        second task) AND the pre-fix `84fb9af` flow (an AWAITED
        `cleanup_old_backups_task(...)` call after `dump_database_task`)
        land at the SAME final count of 7 when that task succeeds, so it
        cannot tell the two apart. The actual bug: a second, awaited
        post-commit task can still fail an ALREADY-SUCCESSFUL run if it
        raises anything `cleanup_old_backups_task`'s own `except OSError`
        doesn't catch (`flows/backup.py:115-121` at 84fb9af) — "everything
        after the commit point is best-effort and MUST NOT change a
        successful result" (Plan 162 T3). Force
        `cleanup_old_backups_task` to raise if it is EVER called and assert
        the flow still returns the already-committed artifact successfully.

        RED against 84fb9af: `backup_database_flow` there calls
        `cleanup_old_backups_task(backup_dir, keep_count)` unconditionally
        right after `dump_database_task`, so this raising fake propagates
        out of the flow and the call below raises instead of returning —
        proven by stashing this fixer round's diff and running this exact
        test against that commit (see the implementer's soundness proof)."""
        _set_backup_env(monkeypatch, tmp_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(parents=True)

        def _must_not_be_called(*_args: object, **_kwargs: object) -> int:
            raise AssertionError(
                "cleanup_old_backups_task must never run on the success path"
            )

        monkeypatch.setattr(backup_mod, "cleanup_old_backups_task", _must_not_be_called)

        fake = _dispatch_fake_subprocess_run()
        with _patch_subprocess_run(fake.side_effect):
            result = backup_database_flow.fn(backup_dir=str(backup_dir), keep_count=7)

        assert "sapphire_" in result
        assert list(backup_dir.glob(FINAL_NAME_GLOB)) != []
