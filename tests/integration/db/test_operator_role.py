"""Plan 510 T4/T6 — the database-limited operator role, against a real Postgres.

Everything here logs in as the real ``sapphire_operator`` (created NOLOGIN by
``docker/bootstrap-roles.sql`` and given a password only through the overlay
secret), so the guard's ``session_user`` test and the grants are exercised
exactly as in production. Each attempt runs in a transaction that is always
rolled back, so the committed baseline seeded by the owner never changes.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from sqlalchemy.pool import NullPool

from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.dhm_delivery import DELIVERY_ID
from sapphire_flow.types.ids import TenantId
from tests.conftest import make_station_config
from tests.integration.db.dhm_import_support import (
    FIXTURES,
    REPO_ROOT,
    run_qc,
    run_replace,
    run_stations,
)
from tests.integration.db.test_operator_grant_matrix import MATRIX
from tests.integration.db.test_role_bootstrap import (
    role_harness,  # noqa: F401
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness

OPERATOR = "sapphire_operator"
OPERATOR_PW = "operator-pw-one"
_OTHER_DELIVERY = "some-other-delivery"
_T = datetime(2002, 1, 1, tzinfo=UTC)
_Statement = str | tuple[str, dict[str, object]]


@dataclass(frozen=True, kw_only=True, slots=True)
class Outcome:
    ok: bool
    pgcode: str | None
    message: str

    @property
    def refused_by_guard(self) -> bool:
        return (
            not self.ok
            and self.pgcode == "42501"
            and self.message.startswith("operator ")
        )

    @property
    def permission_denied(self) -> bool:
        return (
            not self.ok
            and self.pgcode == "42501"
            and not self.message.startswith("operator ")
        )


class OperatorDb:
    def __init__(self, harness: _RoleBootstrapHarness) -> None:
        self.harness = harness
        self.owner = harness.owner_engine
        self.ids: dict[str, UUID] = {}

    def url(self, password: str = OPERATOR_PW, dbname: str = "sapphire") -> str:
        return self.harness.role_url(OPERATOR, password, dbname)

    def engine(self, password: str = OPERATOR_PW) -> sa.Engine:
        return sa.create_engine(self.url(password), poolclass=NullPool)

    def bootstrap(self, *, operator_password: str | None = OPERATOR_PW) -> None:
        result = self.harness.run_bootstrap(
            "api-pw", "worker-pw", "backup-pw", operator_password=operator_password
        )
        assert result.returncode == 0, result.stderr

    def attempt(
        self, statements: Sequence[_Statement], *, engine: sa.Engine | None = None
    ) -> Outcome:
        own = engine is None
        target = engine or self.engine()
        try:
            with target.connect() as conn:
                txn = conn.begin()
                try:
                    for statement in statements:
                        sql, params = (
                            (statement, {}) if isinstance(statement, str) else statement
                        )
                        conn.execute(sa.text(sql), {**self.ids, **params})
                except sa.exc.DBAPIError as exc:
                    return Outcome(
                        ok=False,
                        pgcode=getattr(exc.orig, "sqlstate", None),
                        message=str(exc.orig),
                    )
                finally:
                    txn.rollback()
            return Outcome(ok=True, pgcode=None, message="")
        finally:
            if own:
                target.dispose()

    def scalar(self, sql: str, **params: object) -> object:
        with self.owner.connect() as conn:
            return conn.scalar(sa.text(sql), params)


@pytest.fixture(scope="module")
def db(role_harness: _RoleBootstrapHarness) -> Iterator[OperatorDb]:  # noqa: F811
    operator_db = OperatorDb(role_harness)
    operator_db.bootstrap()
    with operator_db.owner.begin() as conn:
        tenants = PgTenantStore(conn)
        tenants.ensure_tenant(
            tenant_id=TenantId(uuid4()), code="chwrr", name="CHWRR Nepal"
        )
        tenants.ensure_tenant(
            tenant_id=TenantId(uuid4()), code="other-tenant", name="Other tenant"
        )
        run_stations(conn)
        run_replace(conn)
        swiss = make_station_config(code="2009")
        PgStationStore(conn).store_station(swiss)
        observations = PgObservationStore(conn)
        observations.store_raw_observations([])
        for statement in (
            "INSERT INTO observations (id, station_id, timestamp, parameter, value, "
            "source, delivery_id) VALUES (gen_random_uuid(), :swiss, :t1, "
            "'discharge', 1.0, 'manual_import', NULL)",
            "INSERT INTO observations (id, station_id, timestamp, parameter, value, "
            "source, delivery_id) VALUES (gen_random_uuid(), :swiss, :t2, "
            "'discharge', 1.0, 'manual_import', :d)",
            "INSERT INTO observations (id, station_id, timestamp, parameter, value, "
            "source, delivery_id) VALUES (gen_random_uuid(), :s447, :t3, "
            "'discharge', 1.0, 'manual_import', :other)",
            "INSERT INTO rating_curves (id, station_id, version, valid_from, points, "
            "delivery_id) VALUES (gen_random_uuid(), :swiss, 1, :t1, "
            "'[]'::jsonb, NULL)",
        ):
            ids = {
                row.code: row.id
                for row in conn.execute(sa.text("SELECT id, code FROM stations")).all()
            }
            operator_db.ids = {
                "swiss": ids["2009"],
                "s447": ids["447"],
                "s450": ids["450"],
                "chwrr": conn.scalar(
                    sa.text("SELECT id FROM tenants WHERE code='chwrr'")
                ),
                "swiss_tenant": swiss.tenant_id,
            }
            conn.execute(
                sa.text(statement),
                {
                    **operator_db.ids,
                    "t1": datetime(1990, 1, 1, tzinfo=UTC),
                    "t2": datetime(1990, 1, 2, tzinfo=UTC),
                    "t3": datetime(2001, 1, 1, tzinfo=UTC),
                    "d": DELIVERY_ID,
                    "other": _OTHER_DELIVERY,
                },
            )
    operator_db.ids = {
        **operator_db.ids,
        "d": DELIVERY_ID,
        "other": _OTHER_DELIVERY,
        "t": _T,
    }
    yield operator_db


_INSERT_OBS = (
    "INSERT INTO public.observations (id, station_id, timestamp, parameter, value, "
    "source, delivery_id) VALUES (gen_random_uuid(), :{station}, :t, 'discharge', "
    "7.0, 'manual_import', {delivery})"
)
_INSERT_STATION = (
    "INSERT INTO public.stations (id, code, name, location, station_kind, timezone, "
    "measured_parameters, station_status, network, ownership, gauging_status, "
    "tenant_id) SELECT gen_random_uuid(), 'NEW-1', 'New', "
    "ST_SetSRID(ST_MakePoint(1, 1), 4326), 'river', 'UTC', '{{discharge}}', "
    "'onboarding', 'dhm', 'foreign', 'gauged', :{tenant}"
)

_GUARD_REFUSES: dict[str, list[_Statement]] = {
    "delete-a-swiss-observation": [
        "DELETE FROM public.observations WHERE station_id = :swiss"
    ],
    "delete-every-observation": ["DELETE FROM public.observations"],
    "delete-every-rating-curve": ["DELETE FROM public.rating_curves"],
    "update-a-swiss-observation": [
        "UPDATE public.observations SET value = 0 WHERE station_id = :swiss"
    ],
    "delete-a-swiss-row-tagged-with-the-delivery": [
        "DELETE FROM public.observations WHERE station_id = :swiss AND delivery_id = :d"
    ],
    "delete-another-deliverys-row-at-a-chwrr-station": [
        "DELETE FROM public.observations WHERE delivery_id = :other"
    ],
    "update-another-deliverys-row-at-a-chwrr-station": [
        "UPDATE public.observations SET value = 0 WHERE delivery_id = :other"
    ],
    "insert-a-swiss-observation": [
        _INSERT_OBS.format(station="swiss", delivery="NULL")
    ],
    "insert-a-swiss-observation-tagged-with-the-delivery": [
        _INSERT_OBS.format(station="swiss", delivery=":d")
    ],
    "insert-a-chwrr-observation-of-another-delivery": [
        _INSERT_OBS.format(station="s447", delivery=":other")
    ],
    "insert-an-untagged-chwrr-observation": [
        _INSERT_OBS.format(station="s447", delivery="NULL")
    ],
    "insert-a-swiss-rating-curve": [
        "INSERT INTO public.rating_curves (id, station_id, version, valid_from, "
        "points, delivery_id) VALUES "
        "(gen_random_uuid(), :swiss, 9, :t, '[]'::jsonb, :d)"
    ],
    "upsert-over-a-swiss-observation": [
        "INSERT INTO public.observations (id, station_id, timestamp, parameter, value, "
        "source, delivery_id) VALUES (gen_random_uuid(), :swiss, "
        "'1990-01-01T00:00:00+00', 'discharge', 5.0, 'manual_import', :d) "
        "ON CONFLICT (station_id, timestamp, parameter, source) "
        "DO UPDATE SET value = excluded.value"
    ],
    "upsert-a-chwrr-candidate-over-a-swiss-row-by-primary-key": [
        "INSERT INTO public.observations (id, station_id, timestamp, parameter, "
        "value, source, delivery_id) VALUES (:swiss_obs_id, :s447, :t, "
        "'discharge', 5.0, 'manual_import', :d) "
        "ON CONFLICT (id) DO UPDATE SET value = excluded.value"
    ],
    "upsert-over-another-deliverys-row-at-a-chwrr-station": [
        "INSERT INTO public.observations (id, station_id, timestamp, parameter, "
        "value, source, delivery_id) VALUES (:other_obs_id, :s447, :t, "
        "'discharge', 5.0, 'manual_import', :d) "
        "ON CONFLICT (id) DO UPDATE SET value = excluded.value"
    ],
    "clear-the-delivery-tag": [
        "UPDATE public.observations SET delivery_id = NULL WHERE delivery_id = :d"
    ],
    "move-a-row-to-another-chwrr-station": [
        "UPDATE public.observations SET station_id = :s450 "
        "WHERE station_id = :s447 AND delivery_id = :d"
    ],
    "move-a-row-to-a-swiss-station": [
        "UPDATE public.observations SET station_id = :swiss "
        "WHERE station_id = :s447 AND delivery_id = :d"
    ],
    "create-a-station-in-the-swiss-tenant": [
        _INSERT_STATION.format(tenant="swiss_tenant")
    ],
    "create-a-station-in-another-tenant": [
        _INSERT_STATION.format(tenant="other_tenant")
    ],
    "shadow-the-guards-lookup-tables-with-temp-tables": [
        "CREATE TEMP TABLE stations (id uuid, tenant_id uuid)",
        "CREATE TEMP TABLE tenants (id uuid, code text)",
        "INSERT INTO pg_temp.tenants VALUES ('00000000-0000-0000-0000-0000000000aa', "
        "'chwrr')",
        "INSERT INTO pg_temp.stations SELECT :swiss, "
        "'00000000-0000-0000-0000-0000000000aa'",
        "SET search_path = pg_temp, public",
        _INSERT_OBS.format(station="swiss", delivery=":d"),
    ],
}

_PERMISSION_DENIED: dict[str, list[_Statement]] = {
    "update-a-station": ["UPDATE public.stations SET name = 'x'"],
    "delete-a-station": ["DELETE FROM public.stations WHERE id = :s450"],
    "re-tenant-a-swiss-station": [
        "UPDATE public.stations SET tenant_id = :chwrr WHERE id = :swiss"
    ],
    "truncate-observations": ["TRUNCATE public.observations"],
    "truncate-rating-curves": ["TRUNCATE public.rating_curves"],
    "truncate-stations": ["TRUNCATE public.stations CASCADE"],
    "insert-a-tenant": [
        "INSERT INTO public.tenants (id, code, name) VALUES "
        "(gen_random_uuid(), 'rogue', 'Rogue')"
    ],
    "update-a-tenant": ["UPDATE public.tenants SET name = 'x'"],
    "delete-a-tenant": ["DELETE FROM public.tenants"],
    "update-a-rating-curve": ["UPDATE public.rating_curves SET version = version"],
    "read-the-audit-log": ["SELECT count(*) FROM public.audit_log"],
    "update-the-audit-log": ["UPDATE public.audit_log SET event_type = 'x'"],
    "set-role-to-the-worker": ["SET ROLE sapphire_worker"],
    "set-role-to-the-owner": ["SET ROLE test"],
    "change-session-replication-role": ["SET session_replication_role = replica"],
    "disable-the-guard-triggers": [
        "ALTER TABLE public.observations DISABLE TRIGGER ALL"
    ],
    "create-a-table": ["CREATE TABLE public.rogue (id int)"],
    "read-a-table-it-was-never-given": ["SELECT count(*) FROM public.forecasts"],
}

_ALLOWED: dict[str, list[_Statement]] = {
    "insert-a-delivery-observation": [
        _INSERT_OBS.format(station="s447", delivery=":d")
    ],
    "delete-the-delivery-observations": [
        "DELETE FROM public.observations WHERE delivery_id = :d AND station_id = :s447"
    ],
    "update-a-delivery-observation": [
        "UPDATE public.observations SET value = 2 "
        "WHERE delivery_id = :d AND station_id = :s447"
    ],
    "delete-the-delivery-rating-curves": [
        "DELETE FROM public.observations WHERE delivery_id = :d AND station_id = :s447",
        "DELETE FROM public.rating_curves "
        "WHERE delivery_id = :d AND station_id = :s447",
    ],
    "create-a-station-in-the-delivery-tenant": [_INSERT_STATION.format(tenant="chwrr")],
    "append-an-audit-row-without-reading-it": [
        "INSERT INTO public.audit_log (event_type, actor_type) "
        "VALUES ('delivery_imported', 'system')"
    ],
    "keep-working-with-a-temp-shadow-that-lacks-the-real-rows": [
        "CREATE TEMP TABLE stations (id uuid, tenant_id uuid)",
        "CREATE TEMP TABLE tenants (id uuid, code text)",
        "SET search_path = pg_temp, public",
        _INSERT_OBS.format(station="s447", delivery=":d"),
    ],
}


class TestGuardRefusesEveryWriteOutsideTheDelivery:
    @pytest.mark.parametrize("name", list(_GUARD_REFUSES))
    def test_refused_by_the_guard_not_by_a_missing_grant(
        self, db: OperatorDb, name: str
    ) -> None:
        db.ids = {
            **db.ids,
            "other_tenant": db.scalar(
                "SELECT id FROM tenants WHERE code = 'other-tenant'"
            ),
            "swiss_obs_id": db.scalar(
                "SELECT id FROM observations WHERE station_id = :s "
                "AND delivery_id IS NULL",
                s=db.ids["swiss"],
            ),
            "other_obs_id": db.scalar(
                "SELECT id FROM observations WHERE delivery_id = :o",
                o=db.ids["other"],
            ),
        }
        outcome = db.attempt(_GUARD_REFUSES[name])
        assert outcome.refused_by_guard, outcome

    def test_the_swiss_rows_are_all_still_there(self, db: OperatorDb) -> None:
        assert (
            db.scalar(
                "SELECT count(*) FROM observations WHERE station_id = :s",
                s=db.ids["swiss"],
            )
            == 2
        )


class TestOperatorHasNoPrivilegeBeyondTheMatrix:
    @pytest.mark.parametrize("name", list(_PERMISSION_DENIED))
    def test_denied(self, db: OperatorDb, name: str) -> None:
        outcome = db.attempt(_PERMISSION_DENIED[name])
        assert outcome.permission_denied, outcome

    def test_cannot_connect_to_the_prefect_database(self, db: OperatorDb) -> None:
        engine = sa.create_engine(db.url(dbname="prefect"), poolclass=NullPool)
        try:
            with pytest.raises(sa.exc.OperationalError, match="permission denied"):
                engine.connect()
        finally:
            engine.dispose()


class TestPositiveControls:
    @pytest.mark.parametrize("name", list(_ALLOWED))
    def test_allowed(self, db: OperatorDb, name: str) -> None:
        db.ids = {
            **db.ids,
            "other_tenant": db.scalar(
                "SELECT id FROM tenants WHERE code = 'other-tenant'"
            ),
        }
        outcome = db.attempt(_ALLOWED[name])
        assert outcome.ok, outcome


@contextmanager
def _owner_ran(
    db: OperatorDb, setup: Sequence[str], teardown: Sequence[str]
) -> Iterator[None]:
    with db.owner.begin() as conn:
        for statement in setup:
            conn.execute(sa.text(statement))
    try:
        yield
    finally:
        with db.owner.begin() as conn:
            for statement in teardown:
                conn.execute(sa.text(statement))


class TestGuardDoesNotDependOnGrantsOrFunctionOwnership:
    def test_a_missing_delivery_tenant_refuses_everything(self, db: OperatorDb) -> None:
        with _owner_ran(
            db,
            ["UPDATE tenants SET code = 'chwrr-away' WHERE code = 'chwrr'"],
            ["UPDATE tenants SET code = 'chwrr' WHERE code = 'chwrr-away'"],
        ):
            insert = db.attempt([_INSERT_OBS.format(station="s447", delivery=":d")])
            delete = db.attempt(
                ["DELETE FROM public.observations WHERE delivery_id = :d"]
            )
        assert insert.refused_by_guard, insert
        assert delete.refused_by_guard, delete
        assert db.attempt([_INSERT_OBS.format(station="s447", delivery=":d")]).ok

    def test_a_security_definer_function_cannot_write_around_the_guard(
        self, db: OperatorDb
    ) -> None:
        with _owner_ran(
            db,
            [
                "CREATE FUNCTION public.evil_delete() RETURNS void LANGUAGE sql "
                "SECURITY DEFINER AS $$ DELETE FROM public.observations "
                "WHERE station_id = (SELECT id FROM public.stations "
                "WHERE code = '2009') $$"
            ],
            ["DROP FUNCTION public.evil_delete()"],
        ):
            outcome = db.attempt(["SELECT public.evil_delete()"])
        assert outcome.refused_by_guard, outcome

    @pytest.mark.parametrize("column", ["valid_from", "valid_to"])
    def test_a_curve_update_may_not_change_validity_dates(
        self, db: OperatorDb, column: str
    ) -> None:
        with _owner_ran(
            db,
            [f"GRANT UPDATE ON rating_curves TO {OPERATOR}"],
            [f"REVOKE UPDATE ON rating_curves FROM {OPERATOR}"],
        ):
            moved = db.attempt(
                [
                    f"UPDATE public.rating_curves SET {column} = "
                    "'2010-01-01T00:00:00+00' "
                    "WHERE delivery_id = :d AND station_id = :s447"
                ]
            )
            unchanged = db.attempt(
                [
                    "UPDATE public.rating_curves SET rating_type_label = 'x' "
                    "WHERE delivery_id = :d AND station_id = :s447"
                ]
            )
        assert moved.refused_by_guard, moved
        assert unchanged.ok, unchanged


_TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)


def _signature(conn: sa.Connection, role: str) -> dict[str, list[str]]:
    """Every table and sequence privilege a role holds in schema public."""
    rows = conn.execute(
        sa.text(
            "SELECT c.relname, p.priv FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public' "
            "CROSS JOIN unnest(CAST(:privs AS text[])) AS p(priv) "
            "WHERE c.relkind IN ('r', 'p', 'v', 'm') "
            "AND has_table_privilege(:role, c.oid, p.priv) "
            "UNION ALL "
            "SELECT c.relname, s.priv FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public' "
            "CROSS JOIN (VALUES ('USAGE'), ('SELECT'), ('UPDATE')) AS s(priv) "
            "WHERE c.relkind = 'S' AND has_sequence_privilege(:role, c.oid, s.priv) "
            "ORDER BY 1, 2"
        ),
        {"role": role, "privs": list(_TABLE_PRIVILEGES)},
    ).all()
    signature: dict[str, list[str]] = {}
    for name, privilege in rows:
        signature.setdefault(name, []).append(privilege)
    return signature


def _column_acl_entries(conn: sa.Connection, role: str) -> int:
    return (
        conn.scalar(
            sa.text(
                "SELECT count(*) FROM pg_attribute WHERE attacl IS NOT NULL "
                "AND attacl::text LIKE :pattern"
            ),
            {"pattern": f"%{role}=%"},
        )
        or 0
    )


def _assert_grants_equal_the_matrix(db: OperatorDb) -> None:
    with db.owner.begin() as conn:
        conn.execute(sa.text("CREATE ROLE matrix_probe NOLOGIN"))
        try:
            for grant in MATRIX:
                conn.execute(sa.text(grant.sql.format(role="matrix_probe")))
            assert _signature(conn, OPERATOR) == _signature(conn, "matrix_probe")
            assert _signature(conn, OPERATOR)
        finally:
            conn.execute(sa.text("DROP OWNED BY matrix_probe"))
            conn.execute(sa.text("DROP ROLE matrix_probe"))


def _assert_plain_role(db: OperatorDb) -> None:
    with db.owner.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, "
                "rolreplication, rolbypassrls, rolconnlimit FROM pg_roles "
                "WHERE rolname = :r"
            ),
            {"r": OPERATOR},
        ).one()
        memberships = conn.scalar(
            sa.text(
                "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r "
                "ON r.oid = m.member WHERE r.rolname = :r"
            ),
            {"r": OPERATOR},
        )
        settings = conn.scalar(
            sa.text(
                "SELECT count(*) FROM pg_db_role_setting s JOIN pg_roles r "
                "ON r.oid = s.setrole WHERE r.rolname = :r"
            ),
            {"r": OPERATOR},
        )
        schema = conn.execute(
            sa.text(
                "SELECT has_schema_privilege(:r, 'public', 'USAGE'), "
                "has_schema_privilege(:r, 'public', 'CREATE'), "
                "has_database_privilege(:r, 'sapphire', 'CONNECT'), "
                "has_database_privilege(:r, 'prefect', 'CONNECT')"
            ),
            {"r": OPERATOR},
        ).one()
        assert _column_acl_entries(conn, OPERATOR) == 0
    assert tuple(row)[:6] == (False,) * 6
    assert row.rolconnlimit == -1
    assert (memberships, settings) == (0, 0)
    assert tuple(schema) == (True, False, True, False)


class TestBootstrapConvergence:
    def test_the_real_grants_equal_the_measured_matrix(self, db: OperatorDb) -> None:
        db.bootstrap()
        _assert_grants_equal_the_matrix(db)

    def test_the_role_carries_no_attribute_membership_or_setting(
        self, db: OperatorDb
    ) -> None:
        _assert_plain_role(db)

    def test_a_stale_broad_grant_converges_back_to_the_matrix(
        self, db: OperatorDb
    ) -> None:
        with db.owner.begin() as conn:
            for statement in (
                f"GRANT DELETE, UPDATE ON stations TO {OPERATOR}",
                f"GRANT UPDATE ON tenants TO {OPERATOR}",
                f"GRANT UPDATE (name) ON stations TO {OPERATOR}",
                f"GRANT SELECT ON audit_log TO {OPERATOR}",
                f"GRANT TRUNCATE ON observations TO {OPERATOR}",
                f"GRANT pg_read_all_data TO {OPERATOR}",
                f"ALTER ROLE {OPERATOR} CREATEDB",
                f"ALTER ROLE {OPERATOR} SET session_replication_role = replica",
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"GRANT DELETE ON TABLES TO {OPERATOR}",
            ):
                conn.execute(sa.text(statement))
        db.bootstrap()
        _assert_grants_equal_the_matrix(db)
        _assert_plain_role(db)
        assert (
            db.scalar(
                "SELECT count(*) FROM pg_default_acl WHERE defaclacl::text LIKE :p",
                p=f"%{OPERATOR}=%",
            )
            == 0
        )


class TestBootstrapLoginHandling:
    def _can_log_in(self, db: OperatorDb, password: str = OPERATOR_PW) -> bool:
        engine = db.engine(password)
        try:
            with engine.connect():
                return True
        except sa.exc.OperationalError:
            return False
        finally:
            engine.dispose()

    def test_without_the_overlay_secret_the_role_has_no_login(
        self, db: OperatorDb
    ) -> None:
        db.bootstrap(operator_password=None)
        assert (
            db.scalar("SELECT rolcanlogin FROM pg_roles WHERE rolname = :r", r=OPERATOR)
            is False
        )
        assert not self._can_log_in(db)

    def test_a_secret_that_is_set_but_missing_leaves_the_role_without_login(
        self, db: OperatorDb
    ) -> None:
        result = db.harness.run_bootstrap(
            "api-pw",
            "worker-pw",
            "backup-pw",
            operator_password_file="/tmp/does-not-exist",
        )
        assert result.returncode == 0, result.stderr
        assert "unreadable" in result.stderr
        assert (
            db.scalar("SELECT rolcanlogin FROM pg_roles WHERE rolname = :r", r=OPERATOR)
            is False
        )
        assert not self._can_log_in(db)
        assert not self._can_log_in(db, OPERATOR_PW)

    def test_an_empty_secret_leaves_the_role_without_login(
        self, db: OperatorDb
    ) -> None:
        db.bootstrap(operator_password="")
        assert (
            db.scalar("SELECT rolcanlogin FROM pg_roles WHERE rolname = :r", r=OPERATOR)
            is False
        )
        assert not self._can_log_in(db, "")
        assert not self._can_log_in(db, OPERATOR_PW)

    def test_the_secret_enables_login_and_rotation_replaces_the_password(
        self, db: OperatorDb
    ) -> None:
        db.bootstrap(operator_password="first-pw")
        assert self._can_log_in(db, "first-pw")
        db.bootstrap(operator_password="second-pw")
        assert self._can_log_in(db, "second-pw")
        assert not self._can_log_in(db, "first-pw")
        db.bootstrap()
        assert self._can_log_in(db)

    def test_dropping_the_overlay_stops_new_logins_but_not_an_open_session(
        self, db: OperatorDb
    ) -> None:
        engine = db.engine()
        try:
            with engine.connect() as open_session:
                db.bootstrap(operator_password=None)
                assert open_session.scalar(sa.text("SELECT 1")) == 1
                assert not self._can_log_in(db)
        finally:
            engine.dispose()
            db.bootstrap()

    def test_the_documented_emergency_revoke_ends_open_sessions(
        self, db: OperatorDb
    ) -> None:
        engine = db.engine()
        try:
            with engine.connect() as open_session:
                assert open_session.scalar(sa.text("SELECT 1")) == 1
                with db.owner.begin() as conn:
                    conn.execute(sa.text(f"ALTER ROLE {OPERATOR} NOLOGIN"))
                    conn.execute(
                        sa.text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE usename = :r"
                        ),
                        {"r": OPERATOR},
                    )
                with pytest.raises(sa.exc.DBAPIError):
                    open_session.scalar(sa.text("SELECT 1"))
        finally:
            engine.dispose()
        verification = db.scalar(
            "SELECT (SELECT rolcanlogin FROM pg_roles WHERE rolname = :r) "
            "OR EXISTS (SELECT 1 FROM pg_stat_activity WHERE usename = :r)",
            r=OPERATOR,
        )
        assert verification is False
        assert not self._can_log_in(db)
        db.bootstrap()
        assert self._can_log_in(db)


def _trigger_definition(db: OperatorDb, name: str) -> str:
    return str(
        db.scalar(
            "SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE tgname = :n", n=name
        )
    )


def _all_table_privileges(db: OperatorDb) -> dict[str, list[str]]:
    """Privileges beyond PostGIS's own PUBLIC-readable catalog relations."""
    public_postgis = {"geography_columns", "geometry_columns", "spatial_ref_sys"}
    with db.owner.connect() as conn:
        return {
            table: privileges
            for table, privileges in _signature(conn, OPERATOR).items()
            if table not in public_postgis
        }


def _dml_privileges(db: OperatorDb) -> list[tuple[str, str]]:
    with db.owner.connect() as conn:
        held = _signature(conn, OPERATOR)
    return sorted(
        (table, privilege)
        for table, privileges in held.items()
        for privilege in privileges
        if privilege in {"INSERT", "UPDATE", "DELETE", "TRUNCATE"}
        and table != "audit_log"
    )


class TestBootstrapConvergesToTheSafeStateWhenTheGuardIsBroken:
    _TRIGGER = "trg_observations_operator_guard_delete"

    def test_a_missing_guard_trigger_grants_no_dml_and_does_not_abort(
        self, db: OperatorDb
    ) -> None:
        definition = _trigger_definition(db, self._TRIGGER)
        assert _dml_privileges(db)
        with db.owner.begin() as conn:
            conn.execute(sa.text(f"DROP TRIGGER {self._TRIGGER} ON observations"))
        try:
            result = db.harness.run_bootstrap(
                "api-pw", "worker-pw", "backup-pw", operator_password=OPERATOR_PW
            )
            assert result.returncode == 0, result.stderr
            assert "guard trigger" in result.stderr
            assert _dml_privileges(db) == []
            assert _all_table_privileges(db) == {}
            assert (
                db.scalar(
                    "SELECT count(*) FROM pg_trigger WHERE tgname = :n",
                    n=self._TRIGGER,
                )
                == 0
            )
            assert db.attempt(
                [_INSERT_OBS.format(station="s447", delivery=":d")]
            ).permission_denied
        finally:
            with db.owner.begin() as conn:
                conn.execute(sa.text(definition))
            db.bootstrap()
        assert _dml_privileges(db)

    @pytest.mark.parametrize(
        "trigger",
        [
            "trg_observations_operator_guard_insert",
            "trg_rating_curves_operator_guard_update",
            "trg_stations_operator_guard_insert",
            "trg_stations_operator_guard_truncate",
        ],
    )
    def test_a_disabled_guard_trigger_revokes_what_the_operator_held(
        self, db: OperatorDb, trigger: str
    ) -> None:
        table = str(
            db.scalar(
                "SELECT tgrelid::regclass::text FROM pg_trigger WHERE tgname = :n",
                n=trigger,
            )
        )
        assert _dml_privileges(db)
        with db.owner.begin() as conn:
            conn.execute(sa.text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
        try:
            assert (
                db.scalar(
                    "SELECT tgenabled FROM pg_trigger WHERE tgname = :n", n=trigger
                )
                == "D"
            )
            db.bootstrap()
            assert _dml_privileges(db) == []
            assert _all_table_privileges(db) == {}
        finally:
            with db.owner.begin() as conn:
                conn.execute(sa.text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))
            db.bootstrap()
        assert _dml_privileges(db)

    def test_a_guard_trigger_without_its_when_clause_counts_as_broken(
        self, db: OperatorDb
    ) -> None:
        trigger = "trg_observations_operator_guard_insert"
        definition = _trigger_definition(db, trigger)
        assert "WHEN" in definition
        with db.owner.begin() as conn:
            conn.execute(sa.text(f"DROP TRIGGER {trigger} ON observations"))
            conn.execute(
                sa.text(
                    f"CREATE TRIGGER {trigger} BEFORE INSERT ON observations "
                    "FOR EACH ROW EXECUTE FUNCTION "
                    "public.operator_guard_delivery_row()"
                )
            )
        try:
            db.bootstrap()
            assert _all_table_privileges(db) == {}
        finally:
            with db.owner.begin() as conn:
                conn.execute(sa.text(f"DROP TRIGGER {trigger} ON observations"))
                conn.execute(sa.text(definition))
            db.bootstrap()
        assert _dml_privileges(db)

    def test_a_guard_trigger_enabled_always_still_counts_as_guarded(
        self, db: OperatorDb
    ) -> None:
        with db.owner.begin() as conn:
            conn.execute(
                sa.text(
                    "ALTER TABLE observations ENABLE ALWAYS TRIGGER "
                    "trg_observations_operator_guard_delete"
                )
            )
        try:
            db.bootstrap()
            assert _dml_privileges(db)
        finally:
            with db.owner.begin() as conn:
                conn.execute(
                    sa.text(
                        "ALTER TABLE observations ENABLE TRIGGER "
                        "trg_observations_operator_guard_delete"
                    )
                )

    def test_the_guard_is_expected_to_be_exactly_the_migrations_trigger_set(
        self, db: OperatorDb
    ) -> None:
        assert (
            db.scalar(
                "SELECT count(*) FROM pg_trigger WHERE tgname LIKE '%operator_guard%'"
            )
            == 10
        )


@contextmanager
def _operator_transaction(db: OperatorDb) -> Iterator[sa.Connection]:
    engine = db.engine()
    try:
        with engine.connect() as conn:
            txn = conn.begin()
            try:
                yield conn
            finally:
                txn.rollback()
    finally:
        engine.dispose()


class TestImportCommandsAgainstTheExistingOwnerWrittenState:
    """The Mac-mini's real state: rows the owner wrote earlier with the delivery
    tag, a random tenant id and existing station rows."""

    def test_stations_replace_and_qc_run_as_the_operator(self, db: OperatorDb) -> None:
        with _operator_transaction(db) as conn:
            run_stations(conn)
            run_replace(conn)
            run_qc(conn)
            after = conn.execute(
                sa.text(
                    "SELECT count(*) FILTER (WHERE station_id = :swiss), "
                    "count(*) FILTER (WHERE delivery_id = :other), "
                    "count(*) FILTER (WHERE delivery_id = :d "
                    "AND qc_status <> 'raw') FROM observations"
                ),
                {**db.ids},
            ).one()
        assert after[0] == 2
        assert after[1] == 1
        assert after[2] == 18

    def test_the_first_replace_deletes_and_reinserts_the_owner_written_rows(
        self, db: OperatorDb
    ) -> None:
        ids_before = {
            row[0]
            for row in db.owner.connect().execute(
                sa.text("SELECT id FROM observations WHERE delivery_id = :d"),
                {"d": DELIVERY_ID},
            )
        }
        with _operator_transaction(db) as conn:
            run_replace(conn)
            ids_after = {
                row[0]
                for row in conn.execute(
                    sa.text(
                        "SELECT id FROM observations WHERE delivery_id = :d "
                        "AND station_id <> :swiss"
                    ),
                    {"d": DELIVERY_ID, "swiss": db.ids["swiss"]},
                )
            }
        assert ids_after
        assert not ids_after & ids_before

    def test_a_committed_stations_run_appends_an_audit_row_the_operator_cannot_read(
        self, db: OperatorDb
    ) -> None:
        before = db.scalar("SELECT count(*) FROM audit_log")
        engine = db.engine()
        try:
            with engine.begin() as conn:
                run_stations(conn)
        finally:
            engine.dispose()
        assert db.scalar("SELECT count(*) FROM audit_log") == before + 1
        assert db.attempt(["SELECT count(*) FROM public.audit_log"]).permission_denied


class TestTenantLockIsSharedByEveryRole:
    def _second_contender(
        self, first: sa.Connection, second: sa.Connection, tenant_id: UUID
    ) -> None:
        PgTenantStore(first).lock_tenant(TenantId(tenant_id))
        second.execute(sa.text("SET LOCAL lock_timeout = '150ms'"))
        with pytest.raises(sa.exc.OperationalError, match="lock timeout"):
            PgTenantStore(second).lock_tenant(TenantId(tenant_id))

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("operator", "operator"),
            ("operator", "owner"),
            ("owner", "operator"),
            ("owner", "owner"),
        ],
    )
    def test_the_second_caller_waits_whichever_roles_are_involved(
        self, db: OperatorDb, first: str, second: str
    ) -> None:
        engines = {"operator": db.engine(), "owner": db.owner}
        with engines[first].connect() as a, engines[second].connect() as b:
            ta, tb = a.begin(), b.begin()
            try:
                self._second_contender(a, b, db.ids["chwrr"])
            finally:
                tb.rollback()
                ta.rollback()
        engines["operator"].dispose()

    def test_locking_one_tenant_does_not_block_another(self, db: OperatorDb) -> None:
        other = db.scalar("SELECT id FROM tenants WHERE code = 'other-tenant'")
        with _operator_transaction(db) as a, db.owner.connect() as b:
            tb = b.begin()
            try:
                PgTenantStore(a).lock_tenant(TenantId(db.ids["chwrr"]))
                b.execute(sa.text("SET LOCAL lock_timeout = '150ms'"))
                PgTenantStore(b).lock_tenant(TenantId(other))  # type: ignore[arg-type]
            finally:
                tb.rollback()

    @pytest.mark.parametrize(
        ("holder", "waiter"), [("operator", "owner"), ("owner", "operator")]
    )
    def test_replace_and_qc_exclude_each_other_across_roles(
        self, db: OperatorDb, holder: str, waiter: str
    ) -> None:
        engines = {"operator": db.engine(), "owner": db.owner}
        with engines[holder].connect() as a, engines[waiter].connect() as b:
            ta, tb = a.begin(), b.begin()
            try:
                run_replace(a)
                b.execute(sa.text("SET LOCAL lock_timeout = '150ms'"))
                with pytest.raises(sa.exc.OperationalError, match="lock timeout"):
                    run_qc(b)
            finally:
                tb.rollback()
                ta.rollback()
        engines["operator"].dispose()


class TestNoUnexpectedFunctionWritesTheGuardedTables:
    """A static scan of function source text across every non-system schema. It
    is NOT a dynamic-SQL detector: a function that builds its statement with
    EXECUTE from strings is invisible to it."""

    _NON_SYSTEM = (
        "pronamespace NOT IN ('pg_catalog'::regnamespace, "
        "'information_schema'::regnamespace) "
        "AND pronamespace::regnamespace::text NOT LIKE 'pg_toast%'"
    )

    def test_no_function_writes_the_guarded_tables(self, db: OperatorDb) -> None:
        writers = db.scalar(
            "SELECT string_agg(proname, ', ') FROM pg_proc "
            f"WHERE {self._NON_SYSTEM} "
            "AND prosrc ~* '(insert into|update|delete from|truncate)\\s+"
            "(only\\s+)?([a-z_]+\\.)?(observations|rating_curves|stations)\\y'"
        )
        assert writers is None

    def test_the_security_definer_functions_are_the_known_publication_locks(
        self, db: OperatorDb
    ) -> None:
        names = db.scalar(
            "SELECT string_agg(proname, ', ' ORDER BY proname) FROM pg_proc "
            f"WHERE {self._NON_SYSTEM} AND prosecdef"
        )
        assert names == "lock_publication_candidate, lock_publication_grants"


class TestCopyIsGuarded:
    def _copy(self, db: OperatorDb, station: str) -> Outcome:
        engine = db.engine()
        raw = engine.raw_connection()
        try:
            cursor = raw.cursor()
            try:
                with cursor.copy(  # type: ignore[attr-defined]
                    "COPY public.observations (id, station_id, timestamp, "
                    "parameter, value, source, delivery_id) FROM STDIN"
                ) as copy:
                    copy.write_row(
                        (
                            uuid4(),
                            db.ids[station],
                            _T,
                            "discharge",
                            3.0,
                            "manual_import",
                            DELIVERY_ID,
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - classify any driver error
                return Outcome(
                    ok=False,
                    pgcode=getattr(exc, "sqlstate", None),
                    message=str(exc),
                )
            finally:
                raw.rollback()
            return Outcome(ok=True, pgcode=None, message="")
        finally:
            raw.close()
            engine.dispose()

    def test_a_delivery_tagged_chwrr_row_is_accepted(self, db: OperatorDb) -> None:
        assert self._copy(db, "s447").ok

    def test_a_swiss_station_row_is_refused_by_the_guard(self, db: OperatorDb) -> None:
        outcome = self._copy(db, "swiss")
        assert outcome.refused_by_guard, outcome


class TestTruncateTriggerRefusesEvenWithTheGrant:
    @pytest.mark.parametrize("table", ["observations", "rating_curves", "stations"])
    def test_the_guard_itself_refuses_truncate(
        self, db: OperatorDb, table: str
    ) -> None:
        with _owner_ran(
            db,
            [f"GRANT TRUNCATE ON ALL TABLES IN SCHEMA public TO {OPERATOR}"],
            [f"REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM {OPERATOR}"],
        ):
            outcome = db.attempt([f"TRUNCATE public.{table} CASCADE"])
        assert outcome.refused_by_guard, outcome
        assert "may not TRUNCATE" in outcome.message


class TestOperatorTempTableDoesNotBlockTheBootstrap:
    """An operator session that created a temp table and stays open must neither
    abort `init` nor keep its privileges when the bootstrap converges."""

    def _open_session_with_temp_table(
        self, db: OperatorDb
    ) -> tuple[sa.Engine, sa.Connection]:
        engine = db.engine()
        conn = engine.connect()
        conn.execute(sa.text("CREATE TEMP TABLE scratch (id int)"))
        conn.commit()
        return engine, conn

    def test_a_healthy_guard_still_bootstraps(self, db: OperatorDb) -> None:
        engine, conn = self._open_session_with_temp_table(db)
        try:
            db.bootstrap()
            assert _dml_privileges(db)
            assert conn.scalar(sa.text("SELECT 1")) == 1
        finally:
            conn.close()
            engine.dispose()

    def test_a_broken_guard_revokes_the_open_sessions_privileges(
        self, db: OperatorDb
    ) -> None:
        engine, conn = self._open_session_with_temp_table(db)
        db.ids = {**db.ids, "t": _T}
        try:
            with _owner_ran(
                db,
                [
                    "ALTER TABLE observations DISABLE TRIGGER "
                    "trg_observations_operator_guard_insert"
                ],
                [
                    "ALTER TABLE observations ENABLE TRIGGER "
                    "trg_observations_operator_guard_insert"
                ],
            ):
                db.bootstrap()
                assert _all_table_privileges(db) == {}
                with pytest.raises(sa.exc.ProgrammingError, match="permission denied"):
                    conn.execute(
                        sa.text(_INSERT_OBS.format(station="swiss", delivery="NULL")),
                        db.ids,
                    )
                conn.rollback()
        finally:
            conn.close()
            engine.dispose()
            db.bootstrap()
        assert _dml_privileges(db)


def _stub_bin(directory: Path) -> Path:
    directory.mkdir()
    (directory / "chown").write_text("#!/bin/sh\nexit 0\n")
    (directory / "gosu").write_text('#!/bin/sh\nshift\nexec "$@"\n')
    for stub in ("chown", "gosu"):
        (directory / stub).chmod(0o755)
    return directory


def _operator_database_url(db: OperatorDb, tmp_path: Path) -> str:
    """The URL the `operator` service would build: its compose environment run
    through the REAL docker/entrypoint.sh (secret file spliced into the
    template), with the compose hostname pointed at the test container."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.operator.yml").read_text())
    env = compose["services"]["operator"]["environment"]
    secret = tmp_path / "operator_secret"
    secret.write_text(OPERATOR_PW)
    stubs = _stub_bin(tmp_path / "stubs")
    result = subprocess.run(
        [
            str(REPO_ROOT / "docker/entrypoint.sh"),
            "sh",
            "-c",
            "printf '%s' \"$DATABASE_URL\"",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
        cwd=tmp_path,
        env={
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "DATABASE_URL_TEMPLATE": env["DATABASE_URL_TEMPLATE"],
            "DB_PASSWORD_SECRET": str(secret),
        },
    )
    host = db.harness.role_url(OPERATOR, OPERATOR_PW).split("@", 1)[1]
    assert "@postgres:5432/sapphire" in result.stdout
    return result.stdout.replace("postgres:5432/sapphire", host)


def _write_delivery(directory: Path) -> Path:
    directory.mkdir()
    daily = (FIXTURES / "synthetic_daily_flow.txt").read_text()
    rating = (FIXTURES / "synthetic_rating_tables.txt").read_text()
    for code in ("447", "450", "604.5", "647", "670", "684"):
        (directory / f"DFL_{code}.txt").write_text(daily.replace("999", code))
        (directory / f"RT_{code}.txt").write_text(rating.replace("999", code))
    return directory


def _wipe_chwrr_rows(db: OperatorDb) -> None:
    with db.owner.begin() as conn:
        for statement in (
            "DELETE FROM observations WHERE station_id IN (SELECT id FROM stations "
            "WHERE tenant_id = :chwrr)",
            "DELETE FROM rating_curves WHERE station_id IN (SELECT id FROM stations "
            "WHERE tenant_id = :chwrr)",
            "DELETE FROM stations WHERE tenant_id = :chwrr",
        ):
            conn.execute(sa.text(statement), {"chwrr": db.ids["chwrr"]})


def _restore_baseline_after_end_to_end(db: OperatorDb) -> None:
    with db.owner.begin() as conn:
        ids = {
            row.code: row.id
            for row in conn.execute(sa.text("SELECT id, code FROM stations")).all()
        }
        db.ids = {**db.ids, "s447": ids["447"], "s450": ids["450"]}
        conn.execute(
            sa.text(
                "INSERT INTO observations (id, station_id, timestamp, parameter, "
                "value, source, delivery_id) VALUES (gen_random_uuid(), :s447, "
                "'2001-01-01T00:00:00+00', 'discharge', 1.0, 'manual_import', :other)"
            ),
            db.ids,
        )


class TestEndToEndUnderTheRealOperatorLogin:
    """The three commands through their CLI, on the URL the `operator` service
    builds, against a tenant with no stations or delivery yet."""

    @pytest.fixture
    def env(
        self, db: OperatorDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[Path]:
        db.bootstrap()
        delivery = _write_delivery(tmp_path / "restricted-delivery")
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(REPO_ROOT / "config.toml"))
        monkeypatch.setenv(
            "SAPPHIRE_CONFIG_OVERLAY",
            str(REPO_ROOT / "config/overlays/chwrr-import.toml"),
        )
        monkeypatch.setenv("DATABASE_URL", _operator_database_url(db, tmp_path))
        _wipe_chwrr_rows(db)
        try:
            yield delivery
        finally:
            _wipe_chwrr_rows(db)
            with db.owner.begin() as conn:
                run_stations(conn)
                run_replace(conn)
            _restore_baseline_after_end_to_end(db)

    def _counts(self, db: OperatorDb) -> tuple[object, ...]:
        with db.owner.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT (SELECT count(*) FROM stations WHERE tenant_id = :chwrr), "
                    "(SELECT count(*) FROM rating_curves WHERE delivery_id = :d), "
                    "(SELECT count(*) FROM observations WHERE delivery_id = :d "
                    "AND station_id IN (SELECT id FROM stations "
                    "WHERE tenant_id = :chwrr)), "
                    "(SELECT count(*) FROM observations WHERE delivery_id = :d "
                    "AND qc_status <> 'raw' AND station_id IN (SELECT id FROM "
                    "stations WHERE tenant_id = :chwrr)), "
                    "(SELECT count(*) FROM audit_log WHERE event_type = "
                    "'delivery_imported')"
                ),
                db.ids,
            ).one()
        return tuple(row)

    def test_stations_replace_and_qc_all_succeed_and_each_leaves_an_audit_row(
        self, db: OperatorDb, env: Path
    ) -> None:
        from sapphire_flow.cli.import_dhm_delivery import main

        audit_before = self._counts(db)[4]
        assert main(["stations", "--tenant", "chwrr", "--dry-run"]) == 0
        assert self._counts(db)[:1] == (0,)
        assert main(["stations", "--tenant", "chwrr"]) == 0
        replace = ["replace", "--tenant", "chwrr", "--input-dir", str(env)]
        assert main([*replace, "--dry-run"]) == 0
        assert self._counts(db)[1:3] == (0, 0)
        assert main(replace) == 0
        assert main(["qc", "--tenant", "chwrr"]) == 0
        counts = self._counts(db)
        assert counts[:4] == (6, 12, 18, 18)
        assert counts[4] == audit_before + 3

    def test_the_worker_cannot_replace_but_still_runs_stations_and_qc(
        self,
        db: OperatorDb,
        env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from sapphire_flow.cli.import_dhm_delivery import main

        assert main(["stations", "--tenant", "chwrr"]) == 0
        replace = ["replace", "--tenant", "chwrr", "--input-dir", str(env)]
        assert main(replace) == 0
        before = self._counts(db)
        monkeypatch.setenv(
            "DATABASE_URL", db.harness.role_url("sapphire_worker", "worker-pw")
        )
        with pytest.raises(sa.exc.ProgrammingError, match="permission denied"):
            main(replace)
        assert self._counts(db) == before
        assert main(["stations", "--tenant", "chwrr"]) == 0
        assert main(["qc", "--tenant", "chwrr"]) == 0


def _alembic(direction: str, revision: str) -> None:
    from alembic.config import Config

    from alembic import command

    getattr(command, direction)(Config("alembic.ini"), revision)


def _guard_object_counts(db: OperatorDb) -> tuple[object, object]:
    return (
        db.scalar(
            "SELECT count(*) FROM pg_trigger WHERE tgname LIKE '%operator_guard%'"
        ),
        db.scalar("SELECT count(*) FROM pg_proc WHERE proname LIKE 'operator_guard%'"),
    )


class TestGuardMigrationDowngrade:
    """Last in the module: it removes and restores the guard."""

    def test_downgrade_with_an_active_operator_revokes_dml_then_drops_the_guard(
        self, db: OperatorDb
    ) -> None:
        engine = db.engine()
        try:
            with engine.connect() as active:
                assert active.scalar(sa.text("SELECT 1")) == 1
                assert _dml_privileges(db)
                _alembic("downgrade", "0066")
                assert _dml_privileges(db) == []
                assert _guard_object_counts(db) == (0, 0)
                assert active.scalar(sa.text("SELECT 1")) == 1
                blocked = db.attempt(
                    [_INSERT_OBS.format(station="swiss", delivery="NULL")],
                    engine=engine,
                )
                assert blocked.permission_denied, blocked
        finally:
            engine.dispose()
            _alembic("upgrade", "head")
            db.bootstrap()
        assert _guard_object_counts(db) == (10, 3)
        assert _dml_privileges(db)

    def test_downgrade_on_a_database_without_the_operator_role_does_not_error(
        self, db: OperatorDb
    ) -> None:
        with db.owner.begin() as conn:
            conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE usename = :r"
                ),
                {"r": OPERATOR},
            )
            conn.execute(sa.text(f"DROP OWNED BY {OPERATOR}"))
            conn.execute(sa.text(f"DROP ROLE {OPERATOR}"))
        try:
            _alembic("downgrade", "0066")
            assert _guard_object_counts(db) == (0, 0)
        finally:
            _alembic("upgrade", "head")
            db.bootstrap()
        assert _guard_object_counts(db) == (10, 3)
        assert _dml_privileges(db)
