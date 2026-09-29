"""Plan 510 T1 — the diagnostic grant matrix for the DHM import commands.

The three import commands (``stations``, ``replace``, ``qc``) are run under a
scratch role that starts with NO grants, against a real Postgres. ``MATRIX`` is
the measured set of privileges the commands need; the tests prove it is both
sufficient (all three commands pass) and minimal (removing any single grant
makes the command that needs it fail at a named table with a permission error).

The final operator grants are set by ``docker/bootstrap-roles.sql`` (T4) and
compared with this matrix in ``test_operator_role.py``; this module is the
single place the matrix is written down.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

from sapphire_flow.adapters.dhm_files import parse_daily_flow, parse_rating_tables
from sapphire_flow.cli.import_dhm_delivery import (
    load_station_metadata,
    register_stations,
    replace_delivery,
    run_delivery_qc,
)
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import TenantId

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURES = _REPO_ROOT / "tests/fixtures/dhm"
_CONFIG = _REPO_ROOT / "config.toml"
_NOW = ensure_utc(datetime(2026, 9, 28, tzinfo=UTC))
_CHWRR = DeploymentIdentityConfig(
    writable_tenants=frozenset({"chwrr"}), global_admin=False
)
SCRATCH = "scratch_operator"


class Command(Enum):
    STATIONS = "stations"
    REPLACE = "replace"
    QC = "qc"


_ALL = frozenset(Command)
_REPLACE_QC = frozenset({Command.REPLACE, Command.QC})
_ONLY_REPLACE = frozenset({Command.REPLACE})
_ONLY_STATIONS = frozenset({Command.STATIONS})


@dataclass(frozen=True, kw_only=True, slots=True)
class Grant:
    sql: str
    commands: frozenset[Command]
    denied_on: str


MATRIX: tuple[Grant, ...] = (
    Grant(
        sql="GRANT SELECT ON tenants TO {role}",
        commands=_ALL,
        denied_on="table tenants",
    ),
    Grant(
        sql="GRANT UPDATE (name) ON tenants TO {role}",
        commands=_REPLACE_QC,
        denied_on="table tenants",
    ),
    Grant(
        sql="GRANT SELECT ON stations TO {role}",
        commands=_ALL,
        denied_on="table stations",
    ),
    Grant(
        sql="GRANT INSERT ON stations TO {role}",
        commands=_ONLY_STATIONS,
        denied_on="table stations",
    ),
    Grant(
        sql="GRANT SELECT ON rating_curves TO {role}",
        commands=_ONLY_REPLACE,
        denied_on="table rating_curves",
    ),
    Grant(
        sql="GRANT INSERT ON rating_curves TO {role}",
        commands=_ONLY_REPLACE,
        denied_on="table rating_curves",
    ),
    Grant(
        sql="GRANT DELETE ON rating_curves TO {role}",
        commands=_ONLY_REPLACE,
        denied_on="table rating_curves",
    ),
    Grant(
        sql="GRANT SELECT ON observations TO {role}",
        commands=_REPLACE_QC,
        denied_on="table observations",
    ),
    Grant(
        sql="GRANT INSERT ON observations TO {role}",
        commands=_ONLY_REPLACE,
        denied_on="table observations",
    ),
    Grant(
        sql="GRANT UPDATE ON observations TO {role}",
        commands=_REPLACE_QC,
        denied_on="table observations",
    ),
    Grant(
        sql="GRANT DELETE ON observations TO {role}",
        commands=_ONLY_REPLACE,
        denied_on="table observations",
    ),
)


def _label(value: Command | Grant) -> str:
    return value.value if isinstance(value, Command) else value.sql.split(" TO")[0]


def _stores(
    conn: sa.Connection,
) -> tuple[
    PgTenantStore,
    PgStationStore,
    PgRatingCurveStore,
    PgObservationStore,
    PgAuditLogStore,
]:
    return (
        PgTenantStore(conn),
        PgStationStore(conn),
        PgRatingCurveStore(conn),
        PgObservationStore(conn),
        PgAuditLogStore(conn),
    )


def _files() -> dict[str, tuple[object, object]]:
    metadata = load_station_metadata(_FIXTURES / "stations.toml")
    daily = parse_daily_flow((_FIXTURES / "synthetic_daily_flow.txt").read_text())
    rating = parse_rating_tables(
        (_FIXTURES / "synthetic_rating_tables.txt").read_text()
    )
    return {
        spec.code: (
            replace(daily, station_code=spec.code),
            replace(rating, station_code=spec.code),
        )
        for spec in metadata.stations
    }


def _run_stations(conn: sa.Connection) -> None:
    tenants, stations, _, _, _ = _stores(conn)
    register_stations(
        tenants,
        stations,
        _CHWRR,
        load_station_metadata(_FIXTURES / "stations.toml"),
        now=_NOW,
    )


def _run_replace(conn: sa.Connection) -> None:
    tenants, stations, curves, observations, _ = _stores(conn)
    replace_delivery(
        tenants,
        stations,
        curves,
        observations,
        _CHWRR,
        _files(),  # type: ignore[arg-type]
        now=_NOW,
    )


def _run_qc(conn: sa.Connection) -> None:
    tenants, stations, _, observations, _ = _stores(conn)
    run_delivery_qc(tenants, stations, observations, _CHWRR, _CONFIG, now=_NOW)


_RUNNERS: dict[Command, Callable[[sa.Connection], None]] = {
    Command.STATIONS: _run_stations,
    Command.REPLACE: _run_replace,
    Command.QC: _run_qc,
}

_WIPE_CHWRR_ROWS = (
    "DELETE FROM observations WHERE station_id IN "
    "(SELECT s.id FROM stations s JOIN tenants t ON t.id = s.tenant_id "
    "WHERE t.code = 'chwrr')",
    "DELETE FROM rating_curves WHERE station_id IN "
    "(SELECT s.id FROM stations s JOIN tenants t ON t.id = s.tenant_id "
    "WHERE t.code = 'chwrr')",
    "DELETE FROM stations WHERE tenant_id IN "
    "(SELECT id FROM tenants WHERE code = 'chwrr')",
)


@pytest.fixture(scope="module")
def owner() -> Iterator[sa.Engine]:
    import os

    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire",
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        prior = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        engine = sa.create_engine(url)
        try:
            from alembic.config import Config

            from alembic import command

            command.upgrade(Config("alembic.ini"), "head")
            with engine.begin() as conn:
                conn.execute(sa.text(f"CREATE ROLE {SCRATCH} NOLOGIN"))
                conn.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {SCRATCH}"))
                tenants = PgTenantStore(conn)
                tenants.ensure_tenant(
                    tenant_id=TenantId(uuid4()), code="chwrr", name="CHWRR Nepal"
                )
                _run_stations(conn)
                _run_replace(conn)
            yield engine
        finally:
            engine.dispose()
            if prior is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prior


def _apply(conn: sa.Connection, grants: tuple[Grant, ...]) -> None:
    conn.execute(sa.text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {SCRATCH}"))
    conn.execute(
        sa.text(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {SCRATCH}")
    )
    for grant in grants:
        conn.execute(sa.text(grant.sql.format(role=SCRATCH)))


def _run_as_scratch(
    owner: sa.Engine, grants: tuple[Grant, ...], command: Command
) -> None:
    with owner.connect() as conn:
        txn = conn.begin()
        try:
            _apply(conn, grants)
            if command is Command.STATIONS:
                for statement in _WIPE_CHWRR_ROWS:
                    conn.execute(sa.text(statement))
            conn.execute(sa.text(f"SET LOCAL ROLE {SCRATCH}"))
            _RUNNERS[command](conn)
        finally:
            txn.rollback()


def _denied(
    owner: sa.Engine, grants: tuple[Grant, ...], command: Command
) -> str | None:
    try:
        _run_as_scratch(owner, grants, command)
    except sa.exc.ProgrammingError as exc:
        if isinstance(exc.orig, psycopg.errors.InsufficientPrivilege):
            return str(exc.orig)
        raise
    return None


class TestEmptyRoleFailsAtANamedStatement:
    @pytest.mark.parametrize("command", list(Command), ids=lambda c: c.value)
    def test_each_command_fails_at_its_first_missing_privilege(
        self, owner: sa.Engine, command: Command
    ) -> None:
        message = _denied(owner, (), command)
        assert message is not None
        assert "permission denied for table tenants" in message


class TestMatrixIsSufficient:
    @pytest.mark.parametrize("command", list(Command), ids=lambda c: c.value)
    def test_command_succeeds_with_exactly_the_matrix(
        self, owner: sa.Engine, command: Command
    ) -> None:
        assert _denied(owner, MATRIX, command) is None


class TestMatrixIsMinimal:
    @pytest.mark.parametrize(
        ("command", "grant"),
        [(c, g) for g in MATRIX for c in sorted(g.commands, key=lambda c: c.value)],
        ids=_label,
    )
    def test_removing_a_grant_fails_at_the_named_table(
        self, owner: sa.Engine, command: Command, grant: Grant
    ) -> None:
        message = _denied(owner, tuple(g for g in MATRIX if g is not grant), command)
        assert message is not None, f"{grant.sql} is not needed by {command.value}"
        assert grant.denied_on in message

    def test_every_grant_is_needed_by_at_least_one_command(self) -> None:
        assert all(grant.commands for grant in MATRIX)


class TestTenantLockPrivilege:
    """FOR UPDATE and FOR SHARE both need UPDATE on at least one column of
    ``tenants`` (PostgreSQL row-locking privilege); an advisory lock needs no
    table privilege at all."""

    _LOCKS = {
        "for_update": "SELECT id FROM tenants WHERE code = 'chwrr' FOR UPDATE",
        "for_share": "SELECT id FROM tenants WHERE code = 'chwrr' FOR SHARE",
    }

    def _as_scratch(self, owner: sa.Engine, grants: str, statement: str) -> str | None:
        with owner.connect() as conn:
            txn = conn.begin()
            try:
                conn.execute(
                    sa.text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {SCRATCH}")
                )
                for grant in grants.split(";"):
                    if grant:
                        conn.execute(sa.text(grant.format(role=SCRATCH)))
                conn.execute(sa.text(f"SET LOCAL ROLE {SCRATCH}"))
                conn.execute(sa.text(statement))
            except sa.exc.ProgrammingError as exc:
                if isinstance(exc.orig, psycopg.errors.InsufficientPrivilege):
                    return str(exc.orig)
                raise
            finally:
                txn.rollback()
        return None

    @pytest.mark.parametrize("lock", ["for_update", "for_share"])
    def test_select_alone_cannot_lock_a_tenant_row(
        self, owner: sa.Engine, lock: str
    ) -> None:
        message = self._as_scratch(
            owner, "GRANT SELECT ON tenants TO {role}", self._LOCKS[lock]
        )
        assert message is not None
        assert "permission denied for table tenants" in message

    @pytest.mark.parametrize("lock", ["for_update", "for_share"])
    def test_update_on_one_column_is_enough_to_lock_a_tenant_row(
        self, owner: sa.Engine, lock: str
    ) -> None:
        grants = (
            "GRANT SELECT ON tenants TO {role};GRANT UPDATE (name) ON tenants TO {role}"
        )
        assert self._as_scratch(owner, grants, self._LOCKS[lock]) is None

    def test_an_advisory_lock_needs_no_table_privilege(self, owner: sa.Engine) -> None:
        assert self._as_scratch(owner, "", "SELECT pg_advisory_xact_lock(1, 2)") is None


class TestImportWritesNoObservationVersions:
    def test_replace_and_qc_leave_observation_versions_untouched(
        self, owner: sa.Engine
    ) -> None:
        count = "SELECT count(*) FROM observation_versions"
        with owner.connect() as conn:
            txn = conn.begin()
            try:
                before = conn.scalar(sa.text(count))
                _run_replace(conn)
                _run_qc(conn)
                assert conn.scalar(sa.text(count)) == before
            finally:
                txn.rollback()
