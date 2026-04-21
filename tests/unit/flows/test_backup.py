from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sapphire_flow.flows.backup import (
    _to_libpq_url,
    backup_database_flow,
    cleanup_old_backups_task,
    dump_database_task,
)

# ---------------------------------------------------------------------------
# _to_libpq_url — pure function
# ---------------------------------------------------------------------------


class TestToLibpqUrl:
    def test_strips_asyncpg_driver(self) -> None:
        url = "postgresql+asyncpg://user:pass@host:5432/db"
        assert _to_libpq_url(url) == "postgresql://user:pass@host:5432/db"

    def test_strips_psycopg_driver(self) -> None:
        url = "postgresql+psycopg://user:pass@host/db"
        assert _to_libpq_url(url) == "postgresql://user:pass@host/db"

    def test_plain_postgresql_unchanged(self) -> None:
        url = "postgresql://user:pass@host:5432/db"
        assert _to_libpq_url(url) == url

    def test_preserves_query_params(self) -> None:
        url = "postgresql+asyncpg://u:p@h:5432/db?sslmode=require"
        assert _to_libpq_url(url) == "postgresql://u:p@h:5432/db?sslmode=require"

    def test_strips_psycopg2_driver(self) -> None:
        url = "postgresql+psycopg2://user:pass@host/db"
        assert _to_libpq_url(url) == "postgresql://user:pass@host/db"


# ---------------------------------------------------------------------------
# cleanup_old_backups_task
# ---------------------------------------------------------------------------


class TestCleanupOldBackups:
    def _create_dumps(self, tmp_path: Path, count: int) -> list[Path]:
        """Create `count` dump files with staggered mtimes."""
        files = []
        for i in range(count):
            f = tmp_path / f"sapphire_20260101_{i:06d}.dump"
            f.write_bytes(b"fake")
            # Stagger mtimes so sort order is deterministic
            os.utime(f, (1000 + i, 1000 + i))
            files.append(f)
        return files

    def test_removes_oldest_when_over_limit(self, tmp_path: Path) -> None:
        files = self._create_dumps(tmp_path, 5)
        removed = cleanup_old_backups_task.fn(str(tmp_path), keep_count=3)
        assert removed == 2
        remaining = sorted(tmp_path.glob("sapphire_*.dump"))
        assert len(remaining) == 3
        # Oldest two should be gone
        assert files[0] not in remaining
        assert files[1] not in remaining

    def test_noop_at_limit(self, tmp_path: Path) -> None:
        self._create_dumps(tmp_path, 3)
        removed = cleanup_old_backups_task.fn(str(tmp_path), keep_count=3)
        assert removed == 0
        assert len(list(tmp_path.glob("sapphire_*.dump"))) == 3

    def test_noop_under_limit(self, tmp_path: Path) -> None:
        self._create_dumps(tmp_path, 1)
        removed = cleanup_old_backups_task.fn(str(tmp_path), keep_count=7)
        assert removed == 0

    def test_empty_directory(self, tmp_path: Path) -> None:
        removed = cleanup_old_backups_task.fn(str(tmp_path), keep_count=7)
        assert removed == 0

    def test_ignores_non_dump_files(self, tmp_path: Path) -> None:
        self._create_dumps(tmp_path, 5)
        (tmp_path / "other.txt").write_text("keep me")
        cleanup_old_backups_task.fn(str(tmp_path), keep_count=2)
        assert (tmp_path / "other.txt").exists()
        assert len(list(tmp_path.glob("sapphire_*.dump"))) == 2

    def test_keep_zero_removes_all(self, tmp_path: Path) -> None:
        self._create_dumps(tmp_path, 3)
        removed = cleanup_old_backups_task.fn(str(tmp_path), keep_count=0)
        assert removed == 3
        assert len(list(tmp_path.glob("sapphire_*.dump"))) == 0


# ---------------------------------------------------------------------------
# dump_database_task
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# URL → pg_dump args parsing (no subprocess, validates arg construction)
# ---------------------------------------------------------------------------


class TestPgDumpArgConstruction:
    """Validate that _to_libpq_url + urlparse produces correct pg_dump args.

    This catches the class of bugs that subprocess mocking hides: special
    characters in passwords, unusual port configs, etc.
    """

    @pytest.mark.parametrize(
        "url, expected_host, expected_port, expected_user, expected_db, expected_pw",
        [
            (
                "postgresql+psycopg://user:s3cret@db:5432/sapphire",
                "db",
                "5432",
                "user",
                "sapphire",
                "s3cret",
            ),
            (
                "postgresql+asyncpg://admin:p%40ss%23word@host:5433/mydb",
                "host",
                "5433",
                "admin",
                "mydb",
                "p@ss#word",
            ),
            (
                "postgresql://user@host/db",
                "host",
                "5432",
                "user",
                "db",
                "",
            ),
            (
                "postgresql+psycopg://u:pass%3Dwith%3Dequals@h:5432/d",
                "h",
                "5432",
                "u",
                "d",
                "pass=with=equals",
            ),
        ],
        ids=["basic", "special-chars-in-password", "no-password", "equals-in-password"],
    )
    def test_url_to_pgdump_args(
        self,
        url: str,
        expected_host: str,
        expected_port: str,
        expected_user: str,
        expected_db: str,
        expected_pw: str,
    ) -> None:
        from urllib.parse import unquote, urlparse

        parsed = urlparse(_to_libpq_url(url))
        assert parsed.hostname == expected_host
        assert str(parsed.port or 5432) == expected_port
        assert unquote(parsed.username or "") == expected_user
        assert parsed.path.lstrip("/") == expected_db
        assert unquote(parsed.password or "") == expected_pw


class TestDumpDatabaseTask:
    DB_URL = "postgresql+psycopg://user:s3cret@db.host:5433/sapphire"

    @patch("sapphire_flow.flows.backup.subprocess.run")
    def test_happy_path(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", self.DB_URL)

        def fake_run(
            cmd: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            # Extract --file= arg to create the dump file
            for arg in cmd:
                if arg.startswith("--file="):
                    Path(arg.split("=", 1)[1]).write_bytes(b"fake dump data")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_run
        result = dump_database_task.fn(str(tmp_path))

        # File created with correct name pattern
        assert result.startswith(str(tmp_path))
        assert "sapphire_" in result
        assert result.endswith(".dump")

        # pg_dump called with correct args
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "pg_dump"
        assert "--format=custom" in cmd
        assert "--host=db.host" in cmd
        assert "--port=5433" in cmd
        assert "--username=user" in cmd
        assert "--dbname=sapphire" in cmd
        # Password not in CLI args
        assert all("s3cret" not in arg for arg in cmd)

        # Password passed via PGPASSWORD env var
        env = call_args[1]["env"]
        assert env["PGPASSWORD"] == "s3cret"

    @patch("sapphire_flow.flows.backup.subprocess.run")
    def test_file_permissions_0600(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", self.DB_URL)

        def fake_run(
            cmd: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            for arg in cmd:
                if arg.startswith("--file="):
                    Path(arg.split("=", 1)[1]).write_bytes(b"dump")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_run
        result = dump_database_task.fn(str(tmp_path))

        file_stat = os.stat(result)
        mode = stat.S_IMODE(file_stat.st_mode)
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

    @patch("sapphire_flow.flows.backup.subprocess.run")
    def test_failure_raises_runtime_error(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", self.DB_URL)
        mock_run.return_value = subprocess.CompletedProcess(
            [], returncode=1, stdout="", stderr="connection refused"
        )

        with pytest.raises(
            RuntimeError, match="pg_dump failed.*exit 1.*connection refused"
        ):
            dump_database_task.fn(str(tmp_path))

    def test_missing_database_url_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(KeyError, match="DATABASE_URL"):
            dump_database_task.fn(str(tmp_path))

    @patch("sapphire_flow.flows.backup.subprocess.run")
    def test_default_port_when_omitted(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")

        def fake_run(
            cmd: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            for arg in cmd:
                if arg.startswith("--file="):
                    Path(arg.split("=", 1)[1]).write_bytes(b"dump")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_run
        dump_database_task.fn(str(tmp_path))

        cmd = mock_run.call_args[0][0]
        assert "--port=5432" in cmd

    @patch("sapphire_flow.flows.backup.subprocess.run")
    def test_no_password_in_url(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://user@host/db")

        def fake_run(
            cmd: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            for arg in cmd:
                if arg.startswith("--file="):
                    Path(arg.split("=", 1)[1]).write_bytes(b"dump")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_run
        dump_database_task.fn(str(tmp_path))

        env = mock_run.call_args[1]["env"]
        assert env["PGPASSWORD"] == ""

    @patch("sapphire_flow.flows.backup.subprocess.run")
    def test_creates_backup_dir_if_missing(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", self.DB_URL)
        nested = tmp_path / "deep" / "path"
        assert not nested.exists()

        def fake_run(
            cmd: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            for arg in cmd:
                if arg.startswith("--file="):
                    Path(arg.split("=", 1)[1]).write_bytes(b"dump")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_run
        dump_database_task.fn(str(nested))
        assert nested.exists()


# ---------------------------------------------------------------------------
# backup_database_flow — integration of tasks
# ---------------------------------------------------------------------------


class TestBackupDatabaseFlow:
    @patch("sapphire_flow.flows.backup.subprocess.run")
    def test_dump_then_cleanup(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")

        # Pre-create 8 old dumps
        for i in range(8):
            f = tmp_path / f"sapphire_old_{i:04d}.dump"
            f.write_bytes(b"old")
            os.utime(f, (100 + i, 100 + i))

        def fake_run(
            cmd: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            for arg in cmd:
                if arg.startswith("--file="):
                    Path(arg.split("=", 1)[1]).write_bytes(b"new dump")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_run

        result = backup_database_flow.fn(backup_dir=str(tmp_path), keep_count=7)

        # Flow returns the new dump path
        assert "sapphire_" in result
        # 8 old + 1 new = 9, keep 7 → 2 removed, 7 remain
        remaining = list(tmp_path.glob("sapphire_*.dump"))
        assert len(remaining) == 7
