"""Plan 147 Slice D — least-privilege DB role bootstrap, LOCKED acceptance
tests.

Exercises the REAL ``docker/bootstrap-roles.sql`` (via ``docker/bootstrap-
roles.sh``'s own psql invocation, run inside the throwaway Postgres
container so the actual shipped SQL — not a Python reimplementation — is
under test) against a fresh Alembic-migrated schema. Covers the Slice D
"Verify" contract: least-privilege denial (no DROP/CREATE, no cross-DB
CONNECT), per-table grant scoping (not blanket UPDATE/DELETE), append-only
defense-in-depth on ``audit_log`` for BOTH roles, idempotent re-run, and
password rotation.

`psql` is not assumed to be on the host running pytest — the throwaway
container (postgis/postgis, which ships psql) is used as the exec target via
``docker exec``, mirroring how the real `init` service invokes the same
script inside the same kind of Postgres-adjacent environment.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

from sapphire_flow.store.station_store import PgStationStore
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from collections.abc import Iterator

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BOOTSTRAP_SQL = _REPO_ROOT / "docker" / "bootstrap-roles.sql"
_BOOTSTRAP_SH = _REPO_ROOT / "docker" / "bootstrap-roles.sh"


class _RoleBootstrapHarness:
    """Owns the throwaway container + convenience helpers for the tests."""

    def __init__(self, postgres: PostgresContainer, owner_engine: sa.Engine) -> None:
        assert _BOOTSTRAP_SQL.is_file(), (
            "docker/bootstrap-roles.sql must exist (Plan 147 Slice D)"
        )
        self._postgres = postgres
        self.owner_engine = owner_engine
        self._container_id = postgres.get_wrapped_container().id
        dest = f"{self._container_id}:/tmp/bootstrap-roles.sql"
        subprocess.run(["docker", "cp", str(_BOOTSTRAP_SQL), dest], check=True)

    def run_bootstrap(
        self, api_password: str, worker_password: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "docker",
                "exec",
                "-e",
                "PGPASSWORD=test",
                self._container_id,
                "psql",
                "-U",
                "test",
                "-d",
                "sapphire",
                "-v",
                "ON_ERROR_STOP=1",
                "-v",
                f"api_password={api_password}",
                "-v",
                f"worker_password={worker_password}",
                "-f",
                "/tmp/bootstrap-roles.sql",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def role_url(self, role: str, password: str, dbname: str = "sapphire") -> str:
        host = self._postgres.get_container_host_ip()
        port = self._postgres.get_exposed_port(5432)
        return f"postgresql+psycopg://{role}:{password}@{host}:{port}/{dbname}"

    def denied(self, url: str, sql: str) -> bool:
        """True if ``sql`` (run in its own fresh connection) raises."""
        engine = sa.create_engine(url)
        try:
            with engine.connect() as conn, conn.begin():
                conn.execute(sa.text(sql))
            return False
        except sa.exc.SQLAlchemyError:
            return True
        finally:
            engine.dispose()


@pytest.fixture(scope="module")
def role_harness() -> Iterator[_RoleBootstrapHarness]:
    """A migrated `sapphire`-named DB (matching the real deployment's
    POSTGRES_DB) plus a real `prefect` sibling database (matching
    docker/init-db.sh), owned by the `test` bootstrap superuser standing in
    for the real `${DB_USER:-sapphire}` owner role."""
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

            cfg = Config("alembic.ini")
            command.upgrade(cfg, "head")

            with engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                conn.execute(sa.text("CREATE DATABASE prefect"))

            yield _RoleBootstrapHarness(postgres, engine)
        finally:
            engine.dispose()
            if prior is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prior


@pytest.fixture
def bootstrapped(role_harness: _RoleBootstrapHarness) -> _RoleBootstrapHarness:
    """(Re-)runs the bootstrap with FIXED passwords before every test that
    needs the resulting roles/grants — function-scoped (not module-scoped)
    so the idempotency/rotation tests, which deliberately re-bootstrap the
    shared module-scoped container with DIFFERENT passwords, can never leave
    "-initial" stale for a test that runs after them."""
    result = role_harness.run_bootstrap("api-pw-initial", "worker-pw-initial")
    assert result.returncode == 0, result.stderr
    return role_harness


class TestBootstrapScriptExists:
    def test_sql_file_exists(self) -> None:
        assert _BOOTSTRAP_SQL.is_file(), (
            "docker/bootstrap-roles.sql must exist (Plan 147 Slice D)"
        )

    def test_shell_wrapper_exists_and_is_executable(self) -> None:
        assert _BOOTSTRAP_SH.is_file(), (
            "docker/bootstrap-roles.sh must exist (Plan 147 Slice D)"
        )
        assert os.access(_BOOTSTRAP_SH, os.X_OK), (
            "docker/bootstrap-roles.sh must be executable"
        )


class TestBootstrapCreatesBothRolesNonSuperuser:
    def test_roles_exist_and_carry_no_elevated_privilege(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        with bootstrapped.owner_engine.connect() as conn:
            rows = {
                row.rolname: row
                for row in conn.execute(
                    sa.text(
                        "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, "
                        "rolcreaterole FROM pg_roles "
                        "WHERE rolname IN ('sapphire_api', 'sapphire_worker')"
                    )
                ).fetchall()
            }
        assert set(rows) == {"sapphire_api", "sapphire_worker"}
        for row in rows.values():
            assert row.rolcanlogin is True
            assert row.rolsuper is False
            assert row.rolcreatedb is False
            assert row.rolcreaterole is False


class TestAppRolesCannotDropOrCreate:
    def test_sapphire_api_cannot_create_table(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_api", "api-pw-initial")
        assert bootstrapped.denied(url, "CREATE TABLE evil_api (id int)")

    def test_sapphire_worker_cannot_create_table(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
        assert bootstrapped.denied(url, "CREATE TABLE evil_worker (id int)")

    def test_sapphire_api_cannot_drop_a_table(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_api", "api-pw-initial")
        assert bootstrapped.denied(url, "DROP TABLE stations")

    def test_sapphire_worker_cannot_drop_a_table(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
        assert bootstrapped.denied(url, "DROP TABLE stations")


class TestAppRolesCannotReadTheOtherDatabase:
    def test_sapphire_api_cannot_connect_to_prefect_db(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_api", "api-pw-initial", dbname="prefect")
        assert bootstrapped.denied(url, "SELECT 1")

    def test_sapphire_worker_cannot_connect_to_prefect_db(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url(
            "sapphire_worker", "worker-pw-initial", dbname="prefect"
        )
        assert bootstrapped.denied(url, "SELECT 1")


class TestAuditLogAppendOnlyHoldsForBothAppRoles:
    """Defense-in-depth atop Slice B's role-independent trigger (migration
    0046) — neither role is even GRANTed UPDATE/DELETE on audit_log."""

    def test_sapphire_api_cannot_update_audit_log(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_api", "api-pw-initial")
        assert bootstrapped.denied(url, "UPDATE audit_log SET event_type = event_type")

    def test_sapphire_api_cannot_delete_audit_log(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_api", "api-pw-initial")
        assert bootstrapped.denied(url, "DELETE FROM audit_log")

    def test_sapphire_worker_cannot_update_audit_log(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
        assert bootstrapped.denied(url, "UPDATE audit_log SET event_type = event_type")

    def test_sapphire_worker_cannot_delete_audit_log(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
        assert bootstrapped.denied(url, "DELETE FROM audit_log")

    def test_sapphire_api_can_insert_audit_log(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_api", "api-pw-initial")
        assert not bootstrapped.denied(
            url,
            "INSERT INTO audit_log (event_type, actor_type, created_at) "
            "VALUES ('api_key_created', 'system', now())",
        )

    def test_sapphire_worker_can_insert_audit_log(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
        assert not bootstrapped.denied(
            url,
            "INSERT INTO audit_log (event_type, actor_type, created_at) "
            "VALUES ('station_onboarded', 'system', now())",
        )


class TestPerTableGrantsAreNotBlanket:
    """The core F3(b) invariant: SELECT is broad, but write access is scoped
    per table — a role must not silently gain UPDATE/DELETE everywhere."""

    def test_sapphire_api_cannot_update_stations(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        # sapphire_api is GET-only at the HTTP layer (G4) — it has no
        # business writing domain tables like `stations` at all.
        url = bootstrapped.role_url("sapphire_api", "api-pw-initial")
        assert bootstrapped.denied(url, "UPDATE stations SET name = name")

    def test_sapphire_api_can_select_stations(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_api", "api-pw-initial")
        assert not bootstrapped.denied(url, "SELECT count(*) FROM stations")

    def test_sapphire_api_can_update_access_tokens(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_api", "api-pw-initial")
        # No rows to update, but the GRANT itself must not raise
        # InsufficientPrivilege — a real UPDATE with a false predicate still
        # requires the privilege check to pass.
        assert not bootstrapped.denied(
            url, "UPDATE access_tokens SET last_used_at = now() WHERE false"
        )

    def test_sapphire_worker_can_update_stations(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
        assert not bootstrapped.denied(url, "UPDATE stations SET name = name")

    def test_sapphire_worker_cannot_update_access_tokens(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        # access_tokens lifecycle is sapphire_api's job (CLI via the api
        # service), not the worker's.
        url = bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
        assert bootstrapped.denied(
            url, "UPDATE access_tokens SET last_used_at = now() WHERE false"
        )

    def test_sapphire_worker_cannot_insert_access_tokens(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
        assert bootstrapped.denied(
            url,
            "INSERT INTO access_tokens (id, token_hash, key_prefix, name, "
            "role, expires_at, created_at, pepper_version) VALUES "
            "(gen_random_uuid(), 'x', 'y', 'z', 'consumer', now(), now(), 1)",
        )


class TestBootstrapIsIdempotent:
    def test_rerun_with_same_passwords_succeeds_and_role_set_is_unchanged(
        self, role_harness: _RoleBootstrapHarness
    ) -> None:
        r1 = role_harness.run_bootstrap("idempotent-pw-1", "idempotent-pw-2")
        assert r1.returncode == 0, r1.stderr

        with role_harness.owner_engine.connect() as conn:
            before = (
                conn.execute(
                    sa.text(
                        "SELECT rolname FROM pg_roles "
                        "WHERE rolname IN ('sapphire_api', 'sapphire_worker') "
                        "ORDER BY rolname"
                    )
                )
                .scalars()
                .all()
            )

        r2 = role_harness.run_bootstrap("idempotent-pw-1", "idempotent-pw-2")
        assert r2.returncode == 0, r2.stderr

        with role_harness.owner_engine.connect() as conn:
            after = (
                conn.execute(
                    sa.text(
                        "SELECT rolname FROM pg_roles "
                        "WHERE rolname IN ('sapphire_api', 'sapphire_worker') "
                        "ORDER BY rolname"
                    )
                )
                .scalars()
                .all()
            )

        assert before == after == ["sapphire_api", "sapphire_worker"]


class TestPasswordRotation:
    def test_rotating_a_role_password_invalidates_the_old_one(
        self, role_harness: _RoleBootstrapHarness
    ) -> None:
        r1 = role_harness.run_bootstrap("rotate-old-pw", "worker-unrelated-pw")
        assert r1.returncode == 0, r1.stderr

        old_url = role_harness.role_url("sapphire_api", "rotate-old-pw")
        assert not role_harness.denied(old_url, "SELECT 1")

        r2 = role_harness.run_bootstrap("rotate-new-pw", "worker-unrelated-pw")
        assert r2.returncode == 0, r2.stderr

        assert role_harness.denied(old_url, "SELECT 1"), (
            "the pre-rotation password must no longer authenticate"
        )
        new_url = role_harness.role_url("sapphire_api", "rotate-new-pw")
        assert not role_harness.denied(new_url, "SELECT 1"), (
            "the post-rotation password must authenticate"
        )


class TestApplicationStoresWorkUnderScopedRoles:
    """Not just raw SQL — the actual store layer the flows/API use, running
    through a `sapphire_worker`-authenticated write and a
    `sapphire_api`-authenticated read of the SAME row."""

    def test_worker_writes_station_api_reads_it_back(
        self, role_harness: _RoleBootstrapHarness
    ) -> None:
        # Other test classes in this module rotate role passwords via the
        # shared, module-scoped `role_harness` fixture (idempotency/rotation
        # tests) — re-bootstrap with KNOWN passwords right before use rather
        # than depending on `bootstrapped`'s "-initial" values, which may be
        # stale by the time this test runs.
        result = role_harness.run_bootstrap("smoke-api-pw", "smoke-worker-pw")
        assert result.returncode == 0, result.stderr

        station = make_station_config(code="ROLE-BOOTSTRAP-TEST")

        worker_engine = sa.create_engine(
            role_harness.role_url("sapphire_worker", "smoke-worker-pw")
        )
        try:
            with worker_engine.begin() as conn:
                PgStationStore(conn).store_station(station)
        finally:
            worker_engine.dispose()

        api_engine = sa.create_engine(
            role_harness.role_url("sapphire_api", "smoke-api-pw")
        )
        try:
            with api_engine.connect() as conn:
                fetched = PgStationStore(conn).fetch_station(station.id)
        finally:
            api_engine.dispose()

        assert fetched is not None
        assert fetched.code == "ROLE-BOOTSTRAP-TEST"


class TestPreExistingOverprivilegedRoleConvergesToLeastPriv:
    """The in-place-upgrade convergence contract (Plan 147 Slice D): a role
    that ALREADY exists with escalated attributes + broad grants — left behind
    by an earlier, over-broad deploy on the SAME volume — MUST be normalized to
    least privilege by a bootstrap re-run, not merely have its password reset.

    RED against the password-only bootstrap: the pre-existing SUPERUSER role
    keeps SUPERUSER + UPDATE/DELETE on audit_log, so the assertions below fail.
    GREEN after the convergence fix (normalize attrs + revoke-before-regrant):
    the role converges to the identical least-privilege state a fresh volume
    would produce.
    """

    def test_overprivileged_pre_existing_api_role_is_demoted_and_stripped(
        self, role_harness: _RoleBootstrapHarness
    ) -> None:
        # Arrange: forge a deliberately OVERPRIVILEGED pre-existing sapphire_api
        # role (superuser, CREATEDB/CREATEROLE, UPDATE/DELETE on audit_log),
        # standing in for one an earlier over-broad deploy left on the volume.
        with role_harness.owner_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            conn.execute(
                sa.text(
                    "DO $$ BEGIN "
                    "IF EXISTS (SELECT 1 FROM pg_roles "
                    "WHERE rolname = 'sapphire_api') THEN "
                    "  EXECUTE 'DROP OWNED BY sapphire_api CASCADE'; "
                    "  EXECUTE 'DROP ROLE sapphire_api'; "
                    "END IF; END $$"
                )
            )
            conn.execute(
                sa.text(
                    "CREATE ROLE sapphire_api SUPERUSER CREATEDB CREATEROLE "
                    "LOGIN PASSWORD 'overprivileged-pw'"
                )
            )
            conn.execute(sa.text("GRANT UPDATE, DELETE ON audit_log TO sapphire_api"))

        # Act: run the SHIPPED bootstrap against the pre-existing role.
        result = role_harness.run_bootstrap("api-pw-converged", "worker-pw-conv")
        assert result.returncode == 0, result.stderr

        # Assert: attributes are demoted to least privilege.
        with role_harness.owner_engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole "
                    "FROM pg_roles WHERE rolname = 'sapphire_api'"
                )
            ).one()
        assert row.rolsuper is False
        assert row.rolcreatedb is False
        assert row.rolcreaterole is False

        # Assert: the converged role can no longer CREATE/DROP objects nor
        # UPDATE/DELETE audit_log (the stale grants were revoked).
        url = role_harness.role_url("sapphire_api", "api-pw-converged")
        assert role_harness.denied(url, "CREATE TABLE evil_converge (id int)")
        assert role_harness.denied(url, "DROP TABLE stations")
        assert role_harness.denied(url, "UPDATE audit_log SET event_type = event_type")
        assert role_harness.denied(url, "DELETE FROM audit_log")


class TestMigrationUnderScopedRoleFails:
    def test_sapphire_worker_cannot_run_a_schema_migration(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        # A schema-altering DDL statement — proxy for "a migration run under
        # the scoped role" — must be rejected (no CREATE on schema public).
        url = bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
        assert bootstrapped.denied(
            url, "ALTER TABLE stations ADD COLUMN evil_column int"
        )

    def test_sapphire_api_cannot_run_a_schema_migration(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_api", "api-pw-initial")
        assert bootstrapped.denied(
            url, "ALTER TABLE stations ADD COLUMN evil_column int"
        )
