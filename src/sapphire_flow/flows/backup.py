from __future__ import annotations

import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import structlog
from prefect import flow, task

log = structlog.get_logger(__name__)


def _to_libpq_url(url: str) -> str:
    """Strip SQLAlchemy driver suffix (e.g. +asyncpg, +psycopg) for pg_dump."""
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url)


@task(name="dump-database", log_prints=False)
def dump_database_task(backup_dir: str) -> str:
    backup_path = Path(backup_dir)
    backup_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"sapphire_{timestamp}.dump"
    dump_file = backup_path / filename

    database_url = _to_libpq_url(os.environ["DATABASE_URL"])
    parsed = urlparse(database_url)
    cmd = [
        "pg_dump",
        "--format=custom",
        f"--file={dump_file}",
        f"--host={parsed.hostname}",
        f"--port={parsed.port or 5432}",
        f"--username={unquote(parsed.username or '')}",
        f"--dbname={parsed.path.lstrip('/')}",
    ]
    env = {**os.environ, "PGPASSWORD": unquote(parsed.password or "")}

    start = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    end = time.perf_counter()

    if result.returncode != 0:
        msg = f"pg_dump failed (exit {result.returncode}): {result.stderr.strip()}"
        raise RuntimeError(msg)

    dump_file.chmod(0o600)

    size_mb = dump_file.stat().st_size / (1024 * 1024)
    log.info(
        "backup.completed",
        file=str(dump_file),
        size_mb=round(size_mb, 1),
        duration_ms=round((end - start) * 1000, 1),
    )
    return str(dump_file)


@task(name="cleanup-old-backups", log_prints=False)
def cleanup_old_backups_task(backup_dir: str, keep_count: int) -> int:
    backup_path = Path(backup_dir)
    dumps = sorted(backup_path.glob("sapphire_*.dump"), key=lambda p: p.stat().st_mtime)

    removed = 0
    while len(dumps) > keep_count:
        old = dumps.pop(0)
        old.unlink()
        removed += 1
        log.info("backup.removed_old", file=str(old))

    return removed


@flow(name="backup-database", log_prints=False)
def backup_database_flow(
    backup_dir: str = "/data/backups",
    keep_count: int = 7,
) -> str:
    path = dump_database_task(backup_dir)
    cleanup_old_backups_task(backup_dir, keep_count)
    return path
