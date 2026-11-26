from __future__ import annotations

import dataclasses
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.cli import ingest_pause as cli
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import TenantId
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sapphire_flow.types.auth import AuditEntry

NOW = ensure_utc(datetime(2026, 11, 26, tzinfo=UTC))
ARGV = [
    "--tenant", "sapphire", "--network", "research", "--code", "00001",
    "--operator", "bea", "--reason", "gauge destroyed",
]  # fmt: skip


class _FailingAudit:
    def append_entry(self, entry: AuditEntry) -> None:
        raise RuntimeError("audit insert failed")


class _Engine:
    """Hands the test's rolled-back connection to main() in a savepoint."""

    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    @contextmanager
    def begin(self) -> Iterator[sa.Connection]:
        with self._conn.begin_nested():
            yield self._conn


def _seed(conn: sa.Connection, *, tenant_id: TenantId | None = None) -> None:
    station = make_station_config(code="00001", network="research")
    if tenant_id is not None:
        station = dataclasses.replace(station, tenant_id=tenant_id)
    PgStationStore(conn).store_station(station)


def _paused(conn: sa.Connection) -> bool:
    station = PgStationStore(conn).fetch_station_by_code("00001", "research")
    assert station is not None
    return station.ingest_paused


def _audit_rows(conn: sa.Connection) -> list[dict[str, object]]:
    rows = conn.execute(
        sa.text(
            "SELECT detail FROM audit_log "
            "WHERE event_type = 'station_ingest_pause_changed'"
        )
    ).all()
    return [r[0] for r in rows]


def _main(
    conn: sa.Connection,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *extra: str,
) -> int:
    config = tmp_path / "config.toml"
    config.write_text('[deployment]\nwritable_tenants = ["sapphire"]\n')
    monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))
    monkeypatch.setattr(cli, "create_engine_from_env", lambda: _Engine(conn))
    return cli.main([*ARGV, *extra])


class TestIngestPausePg:
    def test_apply_updates_flag_and_audit_together(
        self,
        db_connection: sa.Connection,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _seed(db_connection)
        assert _main(db_connection, monkeypatch, tmp_path, "--pause") == 0
        assert not _paused(db_connection)
        assert _audit_rows(db_connection) == []
        assert _main(db_connection, monkeypatch, tmp_path, "--pause", "--apply") == 0
        assert _paused(db_connection)
        (detail,) = _audit_rows(db_connection)
        assert detail["to"] is True
        assert detail["from"] is False
        assert detail["operator"] == "bea"
        assert _main(db_connection, monkeypatch, tmp_path, "--resume", "--apply") == 0
        assert not _paused(db_connection)
        assert len(_audit_rows(db_connection)) == 2

    def test_audit_failure_rolls_the_flag_back(
        self, db_connection: sa.Connection
    ) -> None:
        _seed(db_connection)
        args = cli._build_parser().parse_args([*ARGV, "--pause", "--apply"])  # pyright: ignore[reportPrivateUsage]
        identity = DeploymentIdentityConfig(
            writable_tenants=frozenset({"sapphire"}), global_admin=False
        )
        with pytest.raises(RuntimeError, match="audit insert failed"):  # noqa: SIM117
            with db_connection.begin_nested():
                cli.run_ingest_pause(
                    args,
                    identity=identity,
                    tenants=PgTenantStore(db_connection),
                    stations=PgStationStore(db_connection),
                    audit=_FailingAudit(),  # type: ignore[arg-type]
                    clock=lambda: NOW,
                )
        assert not _paused(db_connection)

    def test_tenant_mismatch_refuses_and_persists_one_rejection_row(
        self,
        db_connection: sa.Connection,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        other = TenantId(uuid4())
        db_connection.execute(
            sa.text(
                "INSERT INTO tenants (id, code, name, created_at) "
                "VALUES (:i, 'other', 'Other', now())"
            ),
            {"i": other},
        )
        _seed(db_connection, tenant_id=other)
        assert _main(db_connection, monkeypatch, tmp_path, "--pause", "--apply") == 3
        assert not _paused(db_connection)
        (detail,) = _audit_rows(db_connection)
        assert detail["outcome"] == "rejected_tenant_mismatch"
