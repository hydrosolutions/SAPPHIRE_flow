"""Nightly database backup flow (Plan 162 Phase A).

T2 — the backup identity's credential reaches ``pg_dump`` as a small
ALLOWLIST of ``PG*`` environment variables built from scratch, never by
mutating/copying the parent process's ``os.environ``. No URL is ever
constructed or parsed in this module (that was the pre-Plan-162 design and
the source of the 2026-08-13 outage's first defect).

T3 — publication is atomic: a dump is written to a temp file whose name
cannot match the published glob, validated (``pg_restore --list`` must show
a real, schema-qualified ``access_tokens`` TABLE DATA entry — direct
evidence the T1 privilege fix works), then linked into place. A file only
ever carries the published name if it already validated. The whole
sequence — sweep, prune, dump, validate, publish — runs under a filesystem
advisory lock so a manual invocation, a duplicate deployment, or a second
scheduler can never race the same directory.
"""

from __future__ import annotations

import fcntl
import os
import re
import secrets
import shutil
import stat
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import structlog
from prefect import flow, runtime, task
from prefect.cache_policies import NO_CACHE

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# T2 — allowlisted child environment, no URL anywhere in this path.
# ---------------------------------------------------------------------------

# The only process-environment values ever forwarded to the pg_dump/
# pg_restore child. Deliberately excludes PATH-adjacent conveniences beyond
# what's needed to exec the binary and get deterministic (C-locale) output —
# and, critically, excludes every libpq-recognized override (PGSERVICE,
# PGOPTIONS, PGPASSFILE, PGSSLMODE, ...) that might be set on the parent
# process. Those must never reach the child implicitly.
_PASSTHROUGH_ENV_VARS: tuple[str, ...] = ("PATH",)


def _read_required_env(name: str) -> str:
    """Missing OR empty is a loud failure — never a default."""
    value = os.environ.get(name)
    if not value:
        msg = f"{name} is required and must not be empty"
        raise RuntimeError(msg)
    return value


def _read_backup_password(password_file_path: str) -> str:
    """Read the password file with the SAME BYTE-FOR-BYTE convention
    `bootstrap-roles.sh` uses for the sibling API/worker secrets: shell
    `$(cat file)` reads raw bytes and strips only trailing `\\n`
    character(s) via command substitution — never interior or leading
    whitespace, and it never touches a `\\r` anywhere (bash command
    substitution only strips newlines, not carriage returns).

    `Path.read_text()` (an earlier revision of this function) applies
    Python's UNIVERSAL NEWLINE translation before this function ever sees
    the content, silently rewriting every `\\r\\n` or lone `\\r` in the file
    to `\\n`. A password ending in `\\r\\n` (a file saved with Windows line
    endings) would then lose its trailing `\\r` here but NOT in
    `bootstrap-roles.sh`'s `$(cat file)`, which strips only the `\\n` and
    leaves the `\\r` — the role's password and pg_dump's `PGPASSWORD` would
    silently diverge. Reading raw bytes and decoding (no newline
    translation at all) then stripping only trailing `\\n` matches the
    shell exactly, including for a bare `\\r` embedded anywhere in the
    password."""
    path = Path(password_file_path)
    try:
        raw = path.read_bytes().decode("utf-8")
    except OSError as exc:
        msg = (
            f"cannot read SAPPHIRE_BACKUP_DB_PASSWORD_FILE={password_file_path}: {exc}"
        )
        raise RuntimeError(msg) from exc
    password = raw.rstrip("\n")
    if not password:
        msg = f"SAPPHIRE_BACKUP_DB_PASSWORD_FILE={password_file_path} is empty"
        raise RuntimeError(msg)
    return password


def build_pg_child_env() -> dict[str, str]:
    """Build the pg_dump/pg_restore child's environment from an explicit
    ALLOWLIST — never by copying or mutating ``os.environ`` (Plan 162 T2).

    Every required ``SAPPHIRE_BACKUP_PG*`` value, and the password file it
    names, must be present and non-empty or this raises. Only ``PGHOST``,
    ``PGPORT``, ``PGUSER``, ``PGDATABASE``, ``PGPASSWORD`` and the
    deliberately-allowed passthrough values (``PATH``) ever appear in the
    returned mapping — no ``PGSERVICE``, ``PGOPTIONS``, ``PGPASSFILE``,
    ``PGSSLMODE``, or any other inherited ``PG*`` variable can reach libpq.
    """
    host = _read_required_env("SAPPHIRE_BACKUP_PGHOST")
    port = _read_required_env("SAPPHIRE_BACKUP_PGPORT")
    user = _read_required_env("SAPPHIRE_BACKUP_PGUSER")
    database = _read_required_env("SAPPHIRE_BACKUP_PGDATABASE")
    password_file = _read_required_env("SAPPHIRE_BACKUP_DB_PASSWORD_FILE")
    password = _read_backup_password(password_file)

    env: dict[str, str] = {
        "PGHOST": host,
        "PGPORT": port,
        "PGUSER": user,
        "PGDATABASE": database,
        "PGPASSWORD": password,
        # Deterministic, locale-independent pg_dump/pg_restore output.
        "LC_ALL": "C",
    }
    for name in _PASSTHROUGH_ENV_VARS:
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


# ---------------------------------------------------------------------------
# T3 — atomic, validated publication under a filesystem advisory lock.
# ---------------------------------------------------------------------------

FINAL_NAME_GLOB = "sapphire_*.dump"
_FINAL_NAME_RE = re.compile(r"^sapphire_\d{8}_\d{6}_[0-9a-f]{8}\.dump$")
TEMP_NAME_GLOB = "sapphire_*.dump.tmp"
LOCK_FILENAME = ".backup.lock"

DUMP_TIMEOUT_S = 1800.0  # 30 min ceiling for a nightly pg_dump
VALIDATE_TIMEOUT_S = 120.0
LOCK_ACQUIRE_TIMEOUT_S = 30.0
STALE_TEMP_AGE_S = 3600.0  # a temp older than this is a crashed prior run
MIN_FREE_BYTES_FLOOR = 200 * 1024 * 1024  # 200 MiB absolute floor
HEADROOM_MULTIPLIER = 2  # require >= 2x the last valid artifact's size free

# Anchored (line-start), schema-qualified TOC-entry match: `TABLE DATA` (not
# bare `TABLE`, not `ACL`/`COMMENT`), schema `public` (not another schema),
# table `access_tokens` followed by whitespace (not `access_tokens_old` or
# `access_token_stations`) — this is the direct evidence the T1 privilege
# fix actually works, not merely that pg_dump exited 0.
_ACCESS_TOKENS_TOC_RE = re.compile(
    r"^\d+;\s+\d+\s+\d+\s+TABLE DATA\s+public\s+access_tokens\s", re.MULTILINE
)


class BackupConfigError(RuntimeError):
    """A `backup_database_flow`/`dump_database_task` argument is set to a
    value that can never converge to a stable state, and is rejected loudly
    at the start of the run rather than silently producing surprising
    behaviour."""


class BackupLockTimeoutError(RuntimeError):
    """The filesystem advisory lock could not be acquired in time — another
    backup run (manual invocation, a duplicate deployment, a second
    scheduler) currently holds it."""


class BackupValidationError(RuntimeError):
    """A freshly-produced dump failed TOC validation and was never
    published."""


class BackupHeadroomError(RuntimeError):
    """Insufficient free space to safely attempt a new dump."""


@dataclass(frozen=True, kw_only=True, slots=True)
class PublishedBackup:
    path: Path
    size_bytes: int


@contextmanager
def _backup_lock(backup_dir: Path, *, timeout_s: float) -> Iterator[None]:
    """A filesystem advisory lock (``flock``) scoped to ``backup_dir``.

    A Prefect concurrency limit does not cover a manual ``prefect deployment
    run`` invocation, a duplicate deployment, or a second scheduler pointed
    at the same directory — this is defense-in-depth underneath that, not a
    replacement for it.
    """
    lock_path = backup_dir / LOCK_FILENAME
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    msg = (
                        f"could not acquire backup lock at {lock_path} within "
                        f"{timeout_s}s — another backup run holds it"
                    )
                    raise BackupLockTimeoutError(msg) from None
                time.sleep(0.1)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _sweep_stale_temps(
    backup_dir: Path, *, older_than_s: float, now_ts: float
) -> list[Path]:
    """Remove leftover dump temps from a crashed prior run, before this run
    consumes any space of its own. Never touches a file matching the
    published glob."""
    swept: list[Path] = []
    for entry in backup_dir.glob(TEMP_NAME_GLOB):
        try:
            entry_stat = entry.lstat()
        except OSError:
            continue
        if not stat.S_ISREG(entry_stat.st_mode):
            continue
        if now_ts - entry_stat.st_mtime < older_than_s:
            continue
        entry.unlink(missing_ok=True)
        swept.append(entry)
        log.info("backup.stale_temp_swept", file=str(entry))
    return swept


def _valid_published_artifacts(backup_dir: Path) -> list[Path]:
    """Only a name-matching REGULAR file with size > 0, checked via
    ``lstat()`` + ``stat.S_ISREG`` (never ``Path.is_file()``, which follows
    symlinks), counts as a valid published artifact — mirrors the exact
    predicate `newest_backup_mtime` uses in `ops/watchdog.py`. Retention
    (pre-prune and headroom) must apply the same evidence bar the monitor
    does: a newer zero-byte file or a symlink must never be preferred over
    the last genuine dump, and must never itself be treated as "the
    artifact to keep"."""
    entries: list[tuple[Path, float]] = []
    for p in backup_dir.glob(FINAL_NAME_GLOB):
        if not _FINAL_NAME_RE.match(p.name):
            continue
        try:
            entry_stat = p.lstat()
        except OSError:
            continue
        if not stat.S_ISREG(entry_stat.st_mode):
            continue
        if entry_stat.st_size <= 0:
            continue
        entries.append((p, entry_stat.st_mtime))
    return [p for p, _ in sorted(entries, key=lambda item: item[1])]


def _prune_retention(backup_dir: Path, *, keep_count: int) -> list[Path]:
    """Prune BEFORE consuming space for a new dump, always preserving at
    least one valid artifact — local retention must never reach zero."""
    dumps = _valid_published_artifacts(backup_dir)
    keep = max(keep_count, 1)
    removed: list[Path] = []
    while len(dumps) > keep:
        old = dumps.pop(0)
        old.unlink(missing_ok=True)
        removed.append(old)
        log.info("backup.retention_pruned", file=str(old))
    return removed


class _DiskUsage(Protocol):
    # Read-only properties, not plain attributes: `shutil.disk_usage`
    # returns a NamedTuple, whose fields are read-only — a mutable-attribute
    # Protocol does not structurally match it.
    @property
    def total(self) -> int: ...
    @property
    def used(self) -> int: ...
    @property
    def free(self) -> int: ...


def _check_headroom(
    backup_dir: Path,
    *,
    disk_usage: Callable[[str], _DiskUsage] = shutil.disk_usage,
) -> None:
    """Fail BEFORE the dump starts, not after a partial file has consumed
    the space a valid retained artifact needed."""
    usage = disk_usage(str(backup_dir))
    existing = _valid_published_artifacts(backup_dir)
    latest_size = existing[-1].stat().st_size if existing else 0
    required = max(MIN_FREE_BYTES_FLOOR, latest_size * HEADROOM_MULTIPLIER)
    if usage.free < required:
        msg = (
            f"insufficient headroom on {backup_dir}: {usage.free} bytes free, "
            f"need >= {required}"
        )
        raise BackupHeadroomError(msg)


def _dump_to_temp(*, temp_path: Path, env: dict[str, str], timeout_s: float) -> None:
    """Runs pg_dump with its output directed at ``temp_path`` — a name that
    can never match the published glob, so a partial/failed dump is never
    mistaken for evidence of a good backup."""
    fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)

    cmd = ["pg_dump", "--format=custom", f"--file={temp_path}"]
    try:
        # subprocess.run reaps the child (waits on it) even on the
        # TimeoutExpired path before returning/raising — required so
        # cleanup below never races a still-running pg_dump.
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            cmd, env=env, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"pg_dump timed out after {timeout_s}s"
        raise RuntimeError(msg) from exc

    if result.returncode != 0:
        msg = f"pg_dump failed (exit {result.returncode}): {result.stderr.strip()}"
        raise RuntimeError(msg)

    os.chmod(temp_path, 0o600)


def _validate_dump(temp_path: Path, *, timeout_s: float) -> None:
    """``pg_restore --list`` must show an anchored, schema-qualified
    ``TABLE DATA public access_tokens`` entry — direct evidence T1's
    privilege fix worked, not merely that pg_dump exited 0."""
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["pg_restore", "--list", str(temp_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"pg_restore --list timed out after {timeout_s}s"
        raise BackupValidationError(msg) from exc

    if result.returncode != 0:
        msg = (
            f"pg_restore --list failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
        raise BackupValidationError(msg)

    if not _ACCESS_TOKENS_TOC_RE.search(result.stdout):
        msg = (
            "pg_restore --list did not report a schema-qualified "
            "'TABLE DATA public access_tokens' entry — the dump is not "
            "trusted as complete"
        )
        raise BackupValidationError(msg)


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(
    *,
    backup_dir: Path,
    temp_path: Path,
    final_path: Path,
) -> PublishedBackup:
    """The commit point is AFTER the directory fsync, per Plan 162 T3's
    ordering: fsync the file -> ``os.link`` -> unlink the temp -> fsync the
    directory -> THAT is the commit point. ``final_path`` becomes externally
    observable at ``os.link`` and already satisfies the monitor's (T4)
    freshness predicate, but durability is not established until the
    directory fsync below completes: a crash between ``os.link`` and a
    successful directory fsync can leave the new directory entry
    unrecoverable, so an ``OSError`` from ``_fsync_dir`` MUST propagate and
    fail the run — reporting a SUCCESSFUL backup whose existence may not
    survive a crash is precisely the false-green class this plan exists to
    eliminate.

    The temp unlink in between stays best-effort: unlinking the temp file
    cannot affect whether a durable, valid artifact exists at
    ``final_path`` — a leftover temp is harmless, `_sweep_stale_temps`
    reclaims it later, and its ``.tmp`` suffix means it can never match the
    published glob — so a failure there is logged and swallowed rather than
    raised. The two neighbouring calls are deliberately treated
    differently: unlink failure cannot affect whether a durable, valid
    artifact exists; dir-fsync failure means durability was never
    established, so success must not be claimed.

    ``size_bytes`` is read from ``temp_path`` BEFORE the link — the hard
    link creates a second directory entry for the SAME inode, so the byte
    count cannot change between temp and final. Stat-ing ``final_path``
    after the commit point (the prior shape) put a filesystem call after
    the logical commit point, where a failure could fail an already-
    published run for no reason."""
    size = temp_path.stat().st_size
    _fsync_file(temp_path)
    os.link(temp_path, final_path)  # atomic; raises FileExistsError on clobber
    # final_path now exists and is externally observable, but durability is
    # not yet proven — only the directory fsync below establishes that.
    try:
        temp_path.unlink()
    except OSError as exc:
        log.warning("backup.temp_unlink_after_publish_failed", error=str(exc))
    _fsync_dir(backup_dir)
    # --- commit point: the directory fsync above proves final_path's
    # directory entry is durable.
    return PublishedBackup(path=final_path, size_bytes=size)


def _resolve_dump_db_task_run_name() -> str:
    scheduled = getattr(runtime.task_run, "scheduled_start_time", None)
    if scheduled is None:
        return "dump-db"
    try:
        return f"dump-db-{scheduled:%Y-%m-%dT%H%M}"
    except (TypeError, ValueError):
        return "dump-db"


def _resolve_backup_flow_run_name() -> str:
    scheduled = getattr(runtime.flow_run, "scheduled_start_time", None)
    if scheduled is None:
        return "backup"
    try:
        return f"backup-{scheduled:%Y-%m-%dT%H%M}"
    except (TypeError, ValueError):
        return "backup"


@task(
    name="dump-database",
    log_prints=False,
    task_run_name=_resolve_dump_db_task_run_name,
    cache_policy=NO_CACHE,
)
def dump_database_task(
    backup_dir: str,
    *,
    keep_count: int = 7,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> str:
    """Sweep stale temps -> pre-prune retention -> headroom check -> dump to
    a non-matching temp -> validate -> publish atomically. Everything up to
    and including `_publish`'s directory fsync happens under a single
    filesystem advisory lock (Plan 162 T3).

    `keep_count < 2` is rejected loudly (`BackupConfigError`), not accepted
    and silently mis-honoured. The pre-prune step below reserves THIS run's
    slot by pruning to `keep_count - 1` BEFORE dumping, so a successful run
    converges the steady-state count to exactly `keep_count` — but pruning
    must also never drop the last remaining valid artifact to zero (a
    still-failing new dump must never leave zero backups on disk). For
    `keep_count == 1` those two invariants are mutually exclusive within a
    SINGLE run's pre-prune: reserving a slot needs 0 kept, the floor needs
    >= 1 kept, so the floor wins and the run holds 2 artifacts instead of
    1 — and the NEXT run repeats the same conflict, so the count never
    converges to 1, it oscillates between 1 and 2 forever. `keep_count <=
    0` has the identical problem. Rather than silently honour a parameter
    the implementation cannot actually deliver, `keep_count` must be >= 2."""
    if keep_count < 2:
        msg = (
            f"keep_count={keep_count} can never converge: pre-prune reserving "
            "this run's slot (keep_count - 1) and the floor of >= 1 retained "
            "artifact during a risky new dump are mutually exclusive below "
            "keep_count=2, so the published count would oscillate between 1 "
            "and 2 forever instead of converging to keep_count. Use "
            "keep_count >= 2, or call cleanup_old_backups_task directly for "
            "ad-hoc/manual pruning to a lower count."
        )
        raise BackupConfigError(msg)
    backup_path = Path(backup_dir)
    backup_path.mkdir(parents=True, exist_ok=True)

    env = build_pg_child_env()

    now = clock()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    suffix = secrets.token_hex(4)
    final_path = backup_path / f"sapphire_{timestamp}_{suffix}.dump"
    temp_path = backup_path / f"sapphire_{timestamp}_{suffix}.dump.tmp"

    with _backup_lock(backup_path, timeout_s=LOCK_ACQUIRE_TIMEOUT_S):
        _sweep_stale_temps(
            backup_path, older_than_s=STALE_TEMP_AGE_S, now_ts=time.time()
        )
        # Reserve THIS run's slot before it exists: prune to keep_count - 1
        # so a successful publish below lands the retained count exactly at
        # keep_count, without a second post-commit prune. Never below 1 —
        # local retention must never reach zero even when keep_count is 0
        # or 1, so a still-failing new dump never leaves zero artifacts.
        _prune_retention(backup_path, keep_count=max(keep_count - 1, 1))
        _check_headroom(backup_path)

        start = time.perf_counter()
        try:
            _dump_to_temp(temp_path=temp_path, env=env, timeout_s=DUMP_TIMEOUT_S)
            _validate_dump(temp_path, timeout_s=VALIDATE_TIMEOUT_S)
            published = _publish(
                backup_dir=backup_path, temp_path=temp_path, final_path=final_path
            )
        except BaseException:
            # A partial/invalid temp must never be mistaken for evidence of
            # a good backup — best-effort cleanup; the raise below is what
            # fails the run regardless of whether this cleanup succeeds.
            temp_path.unlink(missing_ok=True)
            raise
        end = time.perf_counter()

    log.info(
        "backup.completed",
        file=str(published.path),
        size_mb=round(published.size_bytes / (1024 * 1024), 1),
        duration_ms=round((end - start) * 1000, 1),
    )
    return str(published.path)


@task(
    name="cleanup-old-backups",
    log_prints=False,
    task_run_name="cleanup-old-backups",
    cache_policy=NO_CACHE,
)
def cleanup_old_backups_task(backup_dir: str, keep_count: int) -> int:
    """Best-effort retention sweep. NOT called from `backup_database_flow`'s
    success path (Plan 162 T3 review fix): `dump_database_task` already
    reserves this run's retention slot BEFORE dumping (`keep_count - 1`
    pre-prune), so a successful publish already leaves exactly `keep_count`
    artifacts and no post-commit prune is needed. A second, AWAITED
    Prefect task after the commit point could still fail the flow on a
    task/orchestration error even though `except OSError` alone catches a
    plain filesystem failure — removing it from the success path removes
    that whole failure class rather than trying to catch every kind of
    failure it could raise. Kept as a standalone task for manual/ad-hoc
    retention cleanup (e.g. after changing `keep_count`), where a failure
    is expected to surface normally.
    """
    backup_path = Path(backup_dir)
    removed = 0
    try:
        removed = len(_prune_retention(backup_path, keep_count=keep_count))
    except OSError as exc:
        log.warning("backup.post_run_retention_failed", error=str(exc))
    return removed


@flow(
    name="backup-database",
    log_prints=False,
    flow_run_name=_resolve_backup_flow_run_name,
)
def backup_database_flow(
    backup_dir: str = "/data/backups",
    keep_count: int = 7,
) -> str:
    """`dump_database_task` alone: it pre-prunes (reserving this run's
    slot), dumps, validates and atomically publishes. No second, awaited
    post-commit task runs here — see `cleanup_old_backups_task`'s
    docstring for why (Plan 162 T3 review fix)."""
    return dump_database_task(backup_dir, keep_count=keep_count)
