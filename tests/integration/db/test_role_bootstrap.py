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
    """Owns the throwaway container + convenience helpers for the tests.

    `run_bootstrap` executes the REAL `docker/bootstrap-roles.sh` wrapper
    (not a hand-rolled `psql` invocation that bypasses it) — a broken
    secret-file read, a missing `-v backup_password=` wire-up, or any other
    defect in the shipped shell script would otherwise stay green forever.
    Password "secrets" are written INSIDE the container via `docker exec`
    stdin (never touching the host filesystem), mirroring how a real
    Compose file-backed secret is mounted read-only into the container.
    """

    _OWNER_DATABASE_URL = "postgresql+psycopg://test:test@localhost:5432/sapphire"

    def __init__(self, postgres: PostgresContainer, owner_engine: sa.Engine) -> None:
        assert _BOOTSTRAP_SQL.is_file(), (
            "docker/bootstrap-roles.sql must exist (Plan 147 Slice D)"
        )
        assert _BOOTSTRAP_SH.is_file(), (
            "docker/bootstrap-roles.sh must exist (Plan 147 Slice D)"
        )
        self._postgres = postgres
        self.owner_engine = owner_engine
        self._container_id = postgres.get_wrapped_container().id
        subprocess.run(
            [
                "docker",
                "cp",
                str(_BOOTSTRAP_SQL),
                f"{self._container_id}:/tmp/bootstrap-roles.sql",
            ],
            check=True,
        )
        subprocess.run(
            [
                "docker",
                "cp",
                str(_BOOTSTRAP_SH),
                f"{self._container_id}:/tmp/bootstrap-roles.sh",
            ],
            check=True,
        )
        subprocess.run(
            [
                "docker",
                "exec",
                self._container_id,
                "chmod",
                "+x",
                "/tmp/bootstrap-roles.sh",
            ],
            check=True,
        )

    def _write_secret_file_in_container(self, path: str, content: str) -> None:
        subprocess.run(
            ["docker", "exec", "-i", self._container_id, "sh", "-c", f"cat > {path}"],
            input=content,
            text=True,
            check=True,
            timeout=10,
        )

    def run_bootstrap(
        self,
        api_password: str,
        worker_password: str,
        backup_password: str = "backup-pw-default",
    ) -> subprocess.CompletedProcess[str]:
        self._write_secret_file_in_container("/tmp/api_password", api_password)
        self._write_secret_file_in_container("/tmp/worker_password", worker_password)
        self._write_secret_file_in_container("/tmp/backup_password", backup_password)
        return subprocess.run(
            [
                "docker",
                "exec",
                "-e",
                f"DATABASE_URL={self._OWNER_DATABASE_URL}",
                "-e",
                "SAPPHIRE_API_DB_PASSWORD_FILE=/tmp/api_password",
                "-e",
                "SAPPHIRE_WORKER_DB_PASSWORD_FILE=/tmp/worker_password",
                "-e",
                "SAPPHIRE_BACKUP_DB_PASSWORD_FILE=/tmp/backup_password",
                self._container_id,
                "sh",
                "/tmp/bootstrap-roles.sh",
            ],
            capture_output=True,
            text=True,
            timeout=40,
        )

    def dump_as_backup_role(self, password: str) -> subprocess.CompletedProcess[str]:
        """Runs the REAL `pg_dump` binary (shipped in the postgis/postgis
        image, same family docker-compose.yml pins) INSIDE the container as
        `sapphire_backup` with the given password. Plan 162 T1 review fix:
        proves the rotated credential + privilege combination actually
        produces a valid dump — not merely that a bare `SELECT` succeeds —
        i.e. that "the next backup succeeds" after rotation, mirroring the
        real `SAPPHIRE_BACKUP_PG*` -> `pg_dump` path in `flows/backup.py`.
        """
        return subprocess.run(
            [
                "docker",
                "exec",
                "-e",
                f"PGPASSWORD={password}",
                self._container_id,
                "pg_dump",
                "-h",
                "localhost",
                "-U",
                "sapphire_backup",
                "-d",
                "sapphire",
                "--format=custom",
                "--file=/tmp/rotation-check.dump",
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
    result = role_harness.run_bootstrap(
        "api-pw-initial", "worker-pw-initial", "backup-pw-initial"
    )
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


class TestSapphireApiCanDeleteAccessTokenStations:
    """Plan 215 T7 (round 1 blocker): `revoke-station` and the mode-switch
    cleanup both run as `sapphire_api` (the role the CLI connects as inside
    the `api` container, `cli/access_tokens.py:6-14`) and both DELETE from
    `access_token_stations`. The pre-Plan-215 bootstrap grants that role
    INSERT only, so both would fail with `InsufficientPrivilege` in
    production while every test using the owner role stayed green — this is
    the widening `docker/bootstrap-roles.sql` gains, and `sapphire_worker`
    must stay denied on the same table (re-asserted below, not just
    assumed)."""

    def test_sapphire_api_can_delete_access_token_stations(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_api", "api-pw-initial")
        # No rows to delete, but the GRANT itself must not raise
        # InsufficientPrivilege.
        assert not bootstrapped.denied(
            url, "DELETE FROM access_token_stations WHERE false"
        )

    def test_sapphire_worker_still_cannot_delete_access_token_stations(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
        assert bootstrapped.denied(url, "DELETE FROM access_token_stations WHERE false")

    def test_sapphire_worker_still_cannot_select_access_token_stations(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        # Re-assertion (not new coverage on its own): T7 widens sapphire_api
        # by exactly one verb on one table — it must not touch the
        # sapphire_worker SELECT revoke from TestWorkerCannotReadAuthTables
        # below.
        url = bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
        assert bootstrapped.denied(url, "SELECT * FROM access_token_stations")


class TestWorkerCannotReadAuthTables:
    """A live docker-compose deploy rehearsal caught a least-privilege
    over-grant static review missed: the blanket `GRANT SELECT ON ALL
    TABLES ...` handed sapphire_worker (a Prefect worker running flows) read
    access to the auth tables (access_tokens/access_token_stations) it has
    no business reading. sapphire_api legitimately needs access_tokens for
    auth and keeps it.

    RED against the blanket-grant-only bootstrap (no REVOKE): sapphire_worker
    CAN select from access_tokens/access_token_stations, so the `denied(...)`
    assertions below fail. GREEN after the fix (explicit REVOKE SELECT ...
    FROM sapphire_worker appended after the blanket GRANT and the per-table
    grants): sapphire_worker is denied, sapphire_api and the worker's own
    domain-table access are unaffected.
    """

    def test_sapphire_worker_cannot_select_access_tokens(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
        assert bootstrapped.denied(url, "SELECT * FROM access_tokens")

    def test_sapphire_worker_cannot_select_access_token_stations(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
        assert bootstrapped.denied(url, "SELECT * FROM access_token_stations")

    def test_sapphire_api_can_select_access_tokens(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        # sapphire_api's auth path legitimately reads access_tokens — the
        # fix must not touch sapphire_api's grants.
        url = bootstrapped.role_url("sapphire_api", "api-pw-initial")
        assert not bootstrapped.denied(url, "SELECT * FROM access_tokens")

    def test_sapphire_worker_can_still_select_stations(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        # The revoke is scoped to the two auth tables only — the worker's
        # broad SELECT elsewhere (its own domain tables) is unaffected.
        url = bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
        assert not bootstrapped.denied(url, "SELECT count(*) FROM stations")

    def test_sapphire_worker_can_still_write_stations(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
        assert not bootstrapped.denied(url, "UPDATE stations SET name = name")


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

    def test_rotating_the_backup_role_password_invalidates_the_old_one(
        self, role_harness: _RoleBootstrapHarness
    ) -> None:
        """Plan 162 Phase A infrastructure: 'edit the secret -> re-run init
        -> effective next run, no restart' — the same idempotent
        ALTER-ROLE-PASSWORD mechanism api/worker already use, exercised here
        for `sapphire_backup` specifically.

        Plan 162 T1 review fix: goes past a direct-SQL login check to prove
        the NEW password can actually run a REAL `pg_dump` — "the next
        backup succeeds" — not merely that some `SELECT` authenticates."""
        r1 = role_harness.run_bootstrap(
            "api-unrelated-pw", "worker-unrelated-pw", "rotate-backup-old-pw"
        )
        assert r1.returncode == 0, r1.stderr

        old_url = role_harness.role_url("sapphire_backup", "rotate-backup-old-pw")
        assert not role_harness.denied(old_url, "SELECT * FROM access_tokens")

        r2 = role_harness.run_bootstrap(
            "api-unrelated-pw", "worker-unrelated-pw", "rotate-backup-new-pw"
        )
        assert r2.returncode == 0, r2.stderr

        assert role_harness.denied(old_url, "SELECT 1"), (
            "the pre-rotation backup password must no longer authenticate"
        )
        new_url = role_harness.role_url("sapphire_backup", "rotate-backup-new-pw")
        assert not role_harness.denied(new_url, "SELECT * FROM access_tokens"), (
            "the post-rotation backup password must authenticate and still "
            "read access_tokens"
        )

        dump_result = role_harness.dump_as_backup_role("rotate-backup-new-pw")
        assert dump_result.returncode == 0, dump_result.stderr


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

    def test_worker_can_write_and_read_back_artifact_provenance(
        self,
        role_harness: _RoleBootstrapHarness,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Plan 157 T3: the exact failure mode the plan warned about — a
        superuser test staying green while the scoped `sapphire_worker` role
        is denied the NEW `model_artifact_provenance` INSERT grant in
        production. Runs the REAL `import_external_artifact` service (Plan
        157 T3 fixer round — not raw store calls: register a model,
        deserialize-validate, store the artifact, record its provenance, AND
        promote it — through a real `AuditedWriter` transaction)
        authenticated as `sapphire_worker`, then reads the provenance row
        back as `sapphire_api`. A missing grant on ANY step — including
        `promote_artifact`'s status transitions or its `audit_log` INSERT —
        fails this test, not just raw INSERT/store_artifact."""
        from datetime import UTC, datetime
        from uuid import uuid4

        from sapphire_flow.services.model_import import import_external_artifact
        from sapphire_flow.store.audited_writer import AuditedWriter
        from sapphire_flow.store.model_artifact_provenance import (
            PgArtifactProvenanceStore,
        )
        from sapphire_flow.types.datetime import ensure_utc
        from sapphire_flow.types.enums import ArtifactScope, ModelArtifactStatus
        from sapphire_flow.types.ids import ModelId, StationId

        class _RoleBootstrapTestModel:
            """Structurally satisfies `import_external_artifact`'s model
            boundary — a discoverable, deserializable synthetic FI-style
            model, standing in for a real aquacast-shim entry point."""

            config_hash = "abc123"
            artifact_scope = ArtifactScope.STATION
            display_name = "Role bootstrap test model"
            description = "role bootstrap test double"
            data_requirements = None

            def deserialize_artifact(self, raw: bytes) -> object:
                return {"weights": raw}

            def train(self, *args: object, **kwargs: object) -> object:
                raise AssertionError(
                    "train() must never be called from the import path"
                )

        result = role_harness.run_bootstrap("prov-api-pw", "prov-worker-pw")
        assert result.returncode == 0, result.stderr

        model_id = ModelId(f"prov-role-test-{uuid4().hex[:8]}")
        station = make_station_config(
            station_id=StationId(uuid4()), code=f"PROV-ROLE-{uuid4().hex[:8]}"
        )
        now = ensure_utc(datetime.now(UTC))
        monkeypatch.setattr(
            "sapphire_flow.config.paths.resolve_artifact_dir", lambda: tmp_path
        )

        worker_engine = sa.create_engine(
            role_harness.role_url("sapphire_worker", "prov-worker-pw")
        )
        try:
            with worker_engine.begin() as conn:
                PgStationStore(conn).store_station(station)

            writer = AuditedWriter(begin=worker_engine.begin)
            with worker_engine.connect() as station_conn:
                artifact_id = import_external_artifact(
                    model=_RoleBootstrapTestModel(),  # type: ignore[arg-type]
                    model_id=model_id,
                    artifact_bytes=b"role-test-checkpoint-bytes",
                    trained_at=now,
                    training_period_start=now,
                    training_period_end=now,
                    expected_config_hash="abc123",
                    clock=lambda: now,
                    station_id=station.id,
                    station_store=PgStationStore(station_conn),
                    source_repository="hydrosolutions/sapphire-aquacast",
                    source_commit="deadbeef",
                    imported_by="operator@hydrosolutions.ch",
                    audited_writer=writer,
                )

            with worker_engine.connect() as conn:
                status = conn.execute(
                    sa.text("SELECT status FROM model_artifacts WHERE id = :id"),
                    {"id": str(artifact_id)},
                ).scalar_one()
            assert status == ModelArtifactStatus.ACTIVE.value
        finally:
            worker_engine.dispose()

        api_engine = sa.create_engine(
            role_harness.role_url("sapphire_api", "prov-api-pw")
        )
        try:
            with api_engine.connect() as conn:
                fetched = PgArtifactProvenanceStore(conn).fetch(artifact_id)
        finally:
            api_engine.dispose()

        assert fetched is not None
        assert fetched.source_commit == "deadbeef"
        assert fetched.imported_by == "operator@hydrosolutions.ch"

    def test_worker_cannot_update_artifact_provenance(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        # model_artifact_provenance is a row a caller "imports" once — the
        # grant matrix gives sapphire_worker INSERT only, mirroring
        # audit_log's append-only convention.
        url = bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
        assert bootstrapped.denied(
            url, "UPDATE model_artifact_provenance SET notes = 'x'"
        )


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


class TestBackupRoleCanReadEverything:
    """Plan 162 T1 acceptance criterion (LOCKED red-first): a REAL LOGIN as
    `sapphire_backup` performing `SELECT` on `access_tokens` — a
    `pg_auth_members` membership row proves nothing; `pg_read_all_data`
    membership without `INHERIT` (or without `pg_dump`-compatible semantics)
    would still leave the role DENIED at connect time. This is the direct
    evidence the privilege half of the 2026-08-13 outage is fixed: the
    pre-Plan-162 backup path ran as `sapphire_worker`, which is explicitly
    denied SELECT on `access_tokens`/`access_token_stations`
    (`TestWorkerCannotReadAuthTables` above) — a whole-database `pg_dump`
    under that role fails outright on `LOCK TABLE access_tokens`.
    """

    def test_can_select_access_tokens(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_backup", "backup-pw-initial")
        assert not bootstrapped.denied(url, "SELECT * FROM access_tokens")

    def test_can_select_access_token_stations(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_backup", "backup-pw-initial")
        assert not bootstrapped.denied(url, "SELECT * FROM access_token_stations")

    def test_can_select_a_domain_table(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_backup", "backup-pw-initial")
        assert not bootstrapped.denied(url, "SELECT count(*) FROM stations")

    def test_can_select_audit_log(self, bootstrapped: _RoleBootstrapHarness) -> None:
        url = bootstrapped.role_url("sapphire_backup", "backup-pw-initial")
        assert not bootstrapped.denied(url, "SELECT count(*) FROM audit_log")


class TestBackupRoleIsReadOnlyAndCannotEscalate:
    """`pg_read_all_data` grants SELECT only — defense-in-depth checks that
    the backup identity cannot write, create, drop, or reach the other
    database, mirroring the equivalent api/worker tests above."""

    def test_cannot_insert_into_a_domain_table(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_backup", "backup-pw-initial")
        assert bootstrapped.denied(
            url,
            "INSERT INTO audit_log (event_type, actor_type, created_at) "
            "VALUES ('api_key_created', 'system', now())",
        )

    def test_cannot_update_a_domain_table(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url("sapphire_backup", "backup-pw-initial")
        assert bootstrapped.denied(url, "UPDATE stations SET name = name")

    def test_cannot_create_table(self, bootstrapped: _RoleBootstrapHarness) -> None:
        url = bootstrapped.role_url("sapphire_backup", "backup-pw-initial")
        assert bootstrapped.denied(url, "CREATE TABLE evil_backup (id int)")

    def test_cannot_drop_table(self, bootstrapped: _RoleBootstrapHarness) -> None:
        url = bootstrapped.role_url("sapphire_backup", "backup-pw-initial")
        assert bootstrapped.denied(url, "DROP TABLE stations")

    def test_cannot_connect_to_prefect_db(
        self, bootstrapped: _RoleBootstrapHarness
    ) -> None:
        url = bootstrapped.role_url(
            "sapphire_backup", "backup-pw-initial", dbname="prefect"
        )
        assert bootstrapped.denied(url, "SELECT 1")


class TestPreExistingBackupRoleOwningObjectFailsBootstrap:
    """Plan 162 T1 acceptance criterion (LOCKED red-first): direct ACL
    REVOKE cannot strip owner-intrinsic privileges, so the bootstrap must
    fail loudly — not silently proceed — when `sapphire_backup` already
    owns a relation. RED against a bootstrap without this preflight: the
    forged pre-existing role owns `evil_owned_table` and the bootstrap
    would exit 0, leaving that ownership (and the ability to `DROP`/`ALTER`
    it) intact underneath the intended read-only role.
    """

    def test_bootstrap_fails_when_backup_role_owns_a_table(
        self, role_harness: _RoleBootstrapHarness
    ) -> None:
        with role_harness.owner_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            conn.execute(
                sa.text(
                    "DO $$ BEGIN "
                    "IF EXISTS (SELECT 1 FROM pg_roles "
                    "WHERE rolname = 'sapphire_backup') THEN "
                    "  EXECUTE 'DROP OWNED BY sapphire_backup CASCADE'; "
                    "  EXECUTE 'DROP ROLE sapphire_backup'; "
                    "END IF; END $$"
                )
            )
            conn.execute(
                sa.text("CREATE ROLE sapphire_backup LOGIN PASSWORD 'pre-existing-pw'")
            )
            # Create as the owner/superuser, then hand ownership to
            # sapphire_backup directly — this must work regardless of
            # whether sapphire_backup itself holds CREATE on schema public
            # (it does not, by default, on PG15+).
            conn.execute(sa.text("CREATE TABLE evil_owned_table (id int)"))
            conn.execute(
                sa.text("ALTER TABLE evil_owned_table OWNER TO sapphire_backup")
            )

        try:
            result = role_harness.run_bootstrap(
                "api-pw-preexist", "worker-pw-preexist", "backup-pw-preexist"
            )
            assert result.returncode != 0, (
                "bootstrap must fail loudly when sapphire_backup already "
                "owns a relation, not proceed silently"
            )
            assert "sapphire_backup owns" in result.stderr
        finally:
            # Clean up so later tests in this module see a role bootstrap
            # can converge to normally again.
            with role_harness.owner_engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                conn.execute(
                    sa.text(
                        "DO $$ BEGIN "
                        "IF EXISTS (SELECT 1 FROM pg_roles "
                        "WHERE rolname = 'sapphire_backup') THEN "
                        "  EXECUTE 'DROP OWNED BY sapphire_backup CASCADE'; "
                        "  EXECUTE 'DROP ROLE sapphire_backup'; "
                        "END IF; END $$"
                    )
                )

    def test_bootstrap_fails_when_backup_role_owns_a_function(
        self, role_harness: _RoleBootstrapHarness
    ) -> None:
        """Plan 162 T1 review fix (blocker): ownership detection covering
        ONLY `pg_class` relations and schemas misses non-relation objects —
        functions, standalone types/domains, databases, objects in another
        database. This locks the function case specifically: a
        `pg_class`-only preflight would let this bootstrap proceed and
        leave `sapphire_backup` owning (hence able to `DROP`/`ALTER`) a
        function underneath the intended read-only role."""
        with role_harness.owner_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            conn.execute(
                sa.text(
                    "DO $$ BEGIN "
                    "IF EXISTS (SELECT 1 FROM pg_roles "
                    "WHERE rolname = 'sapphire_backup') THEN "
                    "  EXECUTE 'DROP OWNED BY sapphire_backup CASCADE'; "
                    "  EXECUTE 'DROP ROLE sapphire_backup'; "
                    "END IF; END $$"
                )
            )
            conn.execute(
                sa.text("CREATE ROLE sapphire_backup LOGIN PASSWORD 'pre-existing-pw'")
            )
            conn.execute(
                sa.text(
                    "CREATE FUNCTION test_evil_owned_fn() RETURNS int "
                    "LANGUAGE sql AS $$ SELECT 1 $$"
                )
            )
            conn.execute(
                sa.text("ALTER FUNCTION test_evil_owned_fn() OWNER TO sapphire_backup")
            )

        try:
            result = role_harness.run_bootstrap(
                "api-pw-preexist-fn", "worker-pw-preexist-fn", "backup-pw-preexist-fn"
            )
            assert result.returncode != 0, (
                "bootstrap must fail loudly when sapphire_backup already "
                "owns a NON-RELATION object (a function), not proceed "
                "silently — a pg_class-only ownership check misses this"
            )
            assert "sapphire_backup owns" in result.stderr
        finally:
            with role_harness.owner_engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                conn.execute(sa.text("DROP FUNCTION IF EXISTS test_evil_owned_fn()"))
                conn.execute(
                    sa.text(
                        "DO $$ BEGIN "
                        "IF EXISTS (SELECT 1 FROM pg_roles "
                        "WHERE rolname = 'sapphire_backup') THEN "
                        "  EXECUTE 'DROP OWNED BY sapphire_backup CASCADE'; "
                        "  EXECUTE 'DROP ROLE sapphire_backup'; "
                        "END IF; END $$"
                    )
                )


class TestPreExistingOverprivilegedBackupRoleConvergesToLeastPriv:
    """Plan 162 T1 review fix (blocker): a pre-existing `sapphire_backup`
    left over from an earlier deploy might be `NOLOGIN` (unusable for
    `pg_dump`), might carry an expired `VALID UNTIL`, and might carry
    direct INSERT/UPDATE/DELETE/EXECUTE grants that `GRANT pg_read_all_data`
    alone never revokes (it only ADDS broad SELECT). A convergence block
    that only strips SUPERUSER/CREATEDB/CREATEROLE and revokes stale ROLE
    MEMBERSHIPS leaves such a role write-capable, unusable, or both — even
    though bootstrap exits 0.

    Acceptance is a REAL LOGIN as the converged role (not a
    `pg_auth_members`/`rolcanlogin` catalog check) performing `SELECT` on
    `access_tokens` — the same bar `TestBackupRoleCanReadEverything` uses —
    PLUS proof the stale direct write/execute grants are gone.

    RED against the pre-fix convergence block (attribute normalization
    without `LOGIN`/`CONNECTION LIMIT`/`VALID UNTIL`, and no direct-ACL
    revoke step): the real login fails outright (still `NOLOGIN` /
    expired), so the first assertion below fails before the write/execute
    assertions are even reached.
    """

    def test_pre_existing_backup_role_converges_to_usable_read_only(
        self, role_harness: _RoleBootstrapHarness
    ) -> None:
        with role_harness.owner_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            conn.execute(
                sa.text(
                    "DO $$ BEGIN "
                    "IF EXISTS (SELECT 1 FROM pg_roles "
                    "WHERE rolname = 'sapphire_backup') THEN "
                    "  EXECUTE 'DROP OWNED BY sapphire_backup CASCADE'; "
                    "  EXECUTE 'DROP ROLE sapphire_backup'; "
                    "END IF; END $$"
                )
            )
            # Forge a deliberately BROKEN pre-existing role: NOLOGIN, an
            # expired VALID UNTIL, AND direct write/execute grants — none
            # of which `GRANT pg_read_all_data` alone would fix or strip.
            conn.execute(
                sa.text(
                    "CREATE ROLE sapphire_backup NOLOGIN "
                    "VALID UNTIL '2000-01-01T00:00:00+00' "
                    "PASSWORD 'pre-existing-broken-pw'"
                )
            )
            conn.execute(
                sa.text("GRANT INSERT, UPDATE, DELETE ON stations TO sapphire_backup")
            )
            conn.execute(
                sa.text(
                    "CREATE OR REPLACE FUNCTION test_evil_backup_fn() "
                    "RETURNS int LANGUAGE sql AS $$ SELECT 1 $$"
                )
            )
            conn.execute(
                sa.text("REVOKE EXECUTE ON FUNCTION test_evil_backup_fn() FROM PUBLIC")
            )
            conn.execute(
                sa.text(
                    "GRANT EXECUTE ON FUNCTION test_evil_backup_fn() TO sapphire_backup"
                )
            )

        try:
            result = role_harness.run_bootstrap(
                "api-pw-backup-conv", "worker-pw-backup-conv", "backup-pw-converged"
            )
            assert result.returncode == 0, result.stderr

            # A REAL login (not a catalog check) as the converged role,
            # performing SELECT on access_tokens — proves LOGIN,
            # CONNECTION LIMIT, and VALID UNTIL all converged, not merely
            # the password.
            url = role_harness.role_url("sapphire_backup", "backup-pw-converged")
            assert not role_harness.denied(url, "SELECT * FROM access_tokens"), (
                "the converged role must be able to log in and read — "
                "still NOLOGIN or still expired would fail here"
            )

            # The stale DIRECT grants must be gone — pg_read_all_data only
            # adds SELECT, it never removes an unrelated write/execute
            # grant a prior deploy left behind.
            #
            # A raw hand-written `INSERT INTO stations (...) VALUES (...)`
            # with a hardcoded column list is brittle against schema churn
            # (`stations` has picked up MORE NOT NULL columns over time --
            # `network`, `tenant_id` -- since the table was first created)
            # AND, worse, silently VACUOUS the moment it drifts out of
            # sync: an incomplete/malformed row fails on the DATA before
            # Postgres ever reaches the PRIVILEGE check, so `denied()`
            # would return True regardless of whether INSERT was actually
            # revoked, proving nothing. Using the SAME `make_station_config`
            # + `PgStationStore` application path that
            # `test_worker_writes_station_api_reads_it_back` above already
            # proves SUCCEEDS for a role that DOES hold INSERT is
            # self-healing against future migrations AND directly
            # comparable -- the row shape can never drift from what "a
            # valid station" actually means, because it's the exact same
            # call production code makes.
            backup_engine = sa.create_engine(url)
            try:
                with (
                    pytest.raises(sa.exc.SQLAlchemyError),
                    backup_engine.begin() as conn,
                ):
                    PgStationStore(conn).store_station(
                        make_station_config(code="evil-backup-insert")
                    )
            finally:
                backup_engine.dispose()

            assert role_harness.denied(url, "UPDATE stations SET name = name")
            assert role_harness.denied(url, "DELETE FROM stations")
            assert role_harness.denied(url, "SELECT test_evil_backup_fn()")
        finally:
            with role_harness.owner_engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                conn.execute(sa.text("DROP FUNCTION IF EXISTS test_evil_backup_fn()"))
                conn.execute(
                    sa.text(
                        "DO $$ BEGIN "
                        "IF EXISTS (SELECT 1 FROM pg_roles "
                        "WHERE rolname = 'sapphire_backup') THEN "
                        "  EXECUTE 'DROP OWNED BY sapphire_backup CASCADE'; "
                        "  EXECUTE 'DROP ROLE sapphire_backup'; "
                        "END IF; END $$"
                    )
                )


def _drop_sapphire_backup_role(conn: sa.Connection) -> None:
    conn.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM pg_roles "
            "WHERE rolname = 'sapphire_backup') THEN "
            "  EXECUTE 'DROP OWNED BY sapphire_backup CASCADE'; "
            "  EXECUTE 'DROP ROLE sapphire_backup'; "
            "END IF; END $$"
        )
    )


def _capture_sapphire_backup_signature(conn: sa.Connection) -> dict[str, object]:
    """A normalized snapshot of EVERYTHING `sapphire_backup` holds: role
    attributes, role memberships (with options), direct table/column/
    routine grants (`information_schema`), namespace (schema) ACL entries,
    database ACL entries, and default-privilege entries naming it as
    grantee -- via `aclexplode`, the same mechanism `DROP OWNED BY` itself
    walks (`pg_shdepend` deptype 'a'). Two roles with an identical
    signature hold IDENTICAL privileges, full stop -- this is the
    "converges to exactly a freshly-created role" proof, not a hand-picked
    subset of checks."""
    attrs = conn.execute(
        sa.text(
            "SELECT rolsuper, rolinherit, rolcreaterole, rolcreatedb, "
            "rolcanlogin, rolreplication, rolbypassrls, rolconnlimit, "
            # `::text`, not the raw `timestamptz` -- `VALID UNTIL 'infinity'`
            # stores the special value `infinity`, which psycopg cannot
            # decode into a Python `datetime` (out of `datetime`'s range) and
            # raises `DataError` on fetch. The signature only needs the two
            # roles to AGREE, so the textual form is exactly as discriminating.
            "rolvaliduntil::text "
            "FROM pg_roles WHERE rolname = 'sapphire_backup'"
        )
    ).one()
    memberships = conn.execute(
        sa.text(
            "SELECT granted.rolname, am.admin_option, am.inherit_option, "
            "am.set_option "
            "FROM pg_auth_members am "
            "JOIN pg_roles granted ON granted.oid = am.roleid "
            "JOIN pg_roles member ON member.oid = am.member "
            "WHERE member.rolname = 'sapphire_backup' "
            "ORDER BY granted.rolname"
        )
    ).all()
    table_grants = conn.execute(
        sa.text(
            "SELECT table_schema, table_name, privilege_type "
            "FROM information_schema.role_table_grants "
            "WHERE grantee = 'sapphire_backup' ORDER BY 1, 2, 3"
        )
    ).all()
    column_grants = conn.execute(
        sa.text(
            "SELECT table_schema, table_name, column_name, privilege_type "
            "FROM information_schema.role_column_grants "
            "WHERE grantee = 'sapphire_backup' ORDER BY 1, 2, 3, 4"
        )
    ).all()
    routine_grants = conn.execute(
        sa.text(
            "SELECT specific_schema, routine_name, privilege_type "
            "FROM information_schema.role_routine_grants "
            "WHERE grantee = 'sapphire_backup' ORDER BY 1, 2, 3"
        )
    ).all()
    namespace_acl = conn.execute(
        sa.text(
            "SELECT n.nspname, a.privilege_type "
            "FROM pg_namespace n "
            "CROSS JOIN LATERAL aclexplode(n.nspacl) a "
            "JOIN pg_roles r ON r.oid = a.grantee "
            "WHERE n.nspacl IS NOT NULL AND r.rolname = 'sapphire_backup' "
            "ORDER BY 1, 2"
        )
    ).all()
    database_acl = conn.execute(
        sa.text(
            "SELECT d.datname, a.privilege_type "
            "FROM pg_database d "
            "CROSS JOIN LATERAL aclexplode(d.datacl) a "
            "JOIN pg_roles r ON r.oid = a.grantee "
            "WHERE d.datacl IS NOT NULL AND r.rolname = 'sapphire_backup' "
            "ORDER BY 1, 2"
        )
    ).all()
    default_acl = conn.execute(
        sa.text(
            "SELECT n.nspname, da.defaclobjtype, a.privilege_type "
            "FROM pg_default_acl da "
            "LEFT JOIN pg_namespace n ON n.oid = da.defaclnamespace "
            "CROSS JOIN LATERAL aclexplode(da.defaclacl) a "
            "JOIN pg_roles r ON r.oid = a.grantee "
            "WHERE r.rolname = 'sapphire_backup' "
            "ORDER BY 1, 2, 3"
        )
    ).all()
    return {
        "attrs": tuple(attrs),
        "memberships": [tuple(row) for row in memberships],
        "table_grants": [tuple(row) for row in table_grants],
        "column_grants": [tuple(row) for row in column_grants],
        "routine_grants": [tuple(row) for row in routine_grants],
        "namespace_acl": [tuple(row) for row in namespace_acl],
        "database_acl": [tuple(row) for row in database_acl],
        "default_acl": [tuple(row) for row in default_acl],
    }


class TestPreExistingBackupRoleFullConvergence:
    """Plan 162 T1-fixer-round BLOCKER: `sapphire_backup` convergence was
    still incomplete for a PRE-EXISTING role beyond what `c8e724d` already
    fixed (`ALL ROUTINES` instead of `ALL FUNCTIONS`; `pg_read_all_data`
    re-granted `WITH ADMIN FALSE, INHERIT TRUE, SET FALSE`). Three more
    gaps a per-object-kind `REVOKE` enumeration can never close:
    COLUMN-level grants (a table-level `REVOKE ALL` does not touch
    `pg_attribute.attacl`), ACLs on objects OUTSIDE schema `public` (the
    enumeration was scoped `IN SCHEMA public`), and `ALTER DEFAULT
    PRIVILEGES` entries (a template applied at future `CREATE` time, not
    an ACL on anything that exists yet).

    Forges a pre-existing role holding ALL FOUR escapes at once --
    `pg_read_all_data WITH ADMIN TRUE`, a direct `EXECUTE ON PROCEDURE`, a
    column-level `UPDATE` grant, a grant on an object in a non-public
    schema, and a matching `ALTER DEFAULT PRIVILEGES` entry -- then
    asserts every one is individually gone after bootstrap AND that the
    converged role's FULL privilege signature equals a genuinely
    freshly-created role's (captured from the SAME script's CREATE
    branch, not a hand-derived expectation of what it "should" be).

    RED against `c8e724d`'s enumeration (`REVOKE ALL PRIVILEGES ON ALL
    TABLES/SEQUENCES/ROUTINES IN SCHEMA public`, no `DROP OWNED BY`, no
    default-privileges handling): the column-level UPDATE grant, the
    non-public-schema INSERT grant, and the `ALTER DEFAULT PRIVILEGES`
    entry all survive that enumeration untouched, so three of the four
    "gone" assertions below fail, and the signature-equality assertion
    fails too (the forged role's signature carries three extra entries
    the fresh role's does not).
    """

    def test_all_four_escapes_converge_to_exactly_a_fresh_role(
        self, role_harness: _RoleBootstrapHarness
    ) -> None:
        with role_harness.owner_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            _drop_sapphire_backup_role(conn)

        # --- Baseline: a GENUINELY fresh sapphire_backup, via the SAME
        # script's CREATE branch -- the reference signature everything
        # else in this test is compared against.
        baseline_result = role_harness.run_bootstrap(
            "api-pw-full-conv-baseline",
            "worker-pw-full-conv-baseline",
            "backup-pw-full-conv-baseline",
        )
        assert baseline_result.returncode == 0, baseline_result.stderr
        with role_harness.owner_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            fresh_signature = _capture_sapphire_backup_signature(conn)
            _drop_sapphire_backup_role(conn)

        try:
            with role_harness.owner_engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                # Forge a pre-existing role holding all four escapes.
                conn.execute(
                    sa.text(
                        "CREATE ROLE sapphire_backup LOGIN "
                        "PASSWORD 'pre-existing-full-conv-pw'"
                    )
                )
                # (1) pg_read_all_data membership WITH ADMIN TRUE -- lets
                # sapphire_backup itself grant cluster-wide read to others.
                conn.execute(
                    sa.text(
                        "GRANT pg_read_all_data TO sapphire_backup WITH ADMIN OPTION"
                    )
                )
                # (2) A direct EXECUTE ON PROCEDURE grant.
                conn.execute(
                    sa.text(
                        "CREATE OR REPLACE PROCEDURE test_full_conv_proc() "
                        "LANGUAGE plpgsql AS $$ BEGIN PERFORM 1; END $$"
                    )
                )
                conn.execute(
                    sa.text(
                        "REVOKE EXECUTE ON PROCEDURE test_full_conv_proc() FROM PUBLIC"
                    )
                )
                conn.execute(
                    sa.text(
                        "GRANT EXECUTE ON PROCEDURE test_full_conv_proc() "
                        "TO sapphire_backup"
                    )
                )
                # (3) A COLUMN-level UPDATE grant -- distinct from the
                # table-level grant `ALL TABLES` already catches.
                conn.execute(
                    sa.text("GRANT UPDATE (name) ON TABLE stations TO sapphire_backup")
                )
                # (4) A grant on an object in a NON-public schema. INSERT,
                # not SELECT -- pg_read_all_data already confers SELECT
                # everywhere by design (that grant must SURVIVE), so only
                # a privilege it does NOT confer proves this specific gap.
                conn.execute(sa.text("CREATE SCHEMA evil_schema"))
                conn.execute(sa.text("CREATE TABLE evil_schema.evil_table (id int)"))
                conn.execute(
                    sa.text("GRANT INSERT ON evil_schema.evil_table TO sapphire_backup")
                )
                # (5) A matching ALTER DEFAULT PRIVILEGES entry: the NEXT
                # table the owner creates in `public` would auto-grant
                # INSERT to sapphire_backup, forever, unless this is reset.
                conn.execute(
                    sa.text(
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                        "GRANT INSERT ON TABLES TO sapphire_backup"
                    )
                )

            result = role_harness.run_bootstrap(
                "api-pw-full-conv", "worker-pw-full-conv", "backup-pw-full-conv"
            )
            assert result.returncode == 0, result.stderr

            url = role_harness.role_url("sapphire_backup", "backup-pw-full-conv")

            # (1) ADMIN option gone.
            with role_harness.owner_engine.connect() as conn:
                admin_option = conn.execute(
                    sa.text(
                        "SELECT am.admin_option FROM pg_auth_members am "
                        "JOIN pg_roles granted ON granted.oid = am.roleid "
                        "JOIN pg_roles member ON member.oid = am.member "
                        "WHERE member.rolname = 'sapphire_backup' "
                        "AND granted.rolname = 'pg_read_all_data'"
                    )
                ).scalar_one()
            assert admin_option is False

            # (2) EXECUTE ON PROCEDURE gone.
            assert role_harness.denied(url, "CALL test_full_conv_proc()")

            # (3) Column-level UPDATE gone (checked directly, not via a
            # bare `UPDATE stations SET name = name`, which would also
            # fail identically on a REMAINING table-level grant and so
            # would not isolate the column-level case).
            with role_harness.owner_engine.connect() as conn:
                has_column_update = conn.execute(
                    sa.text(
                        "SELECT has_column_privilege("
                        "'sapphire_backup', 'stations', 'name', 'UPDATE')"
                    )
                ).scalar_one()
            assert has_column_update is False

            # (4) Non-public-schema INSERT gone (SELECT must SURVIVE --
            # that's pg_read_all_data working as intended, not a leak).
            with role_harness.owner_engine.connect() as conn:
                has_evil_insert = conn.execute(
                    sa.text(
                        "SELECT has_table_privilege("
                        "'sapphire_backup', 'evil_schema.evil_table', 'INSERT')"
                    )
                ).scalar_one()
                has_evil_select = conn.execute(
                    sa.text(
                        "SELECT has_table_privilege("
                        "'sapphire_backup', 'evil_schema.evil_table', 'SELECT')"
                    )
                ).scalar_one()
            assert has_evil_insert is False
            assert has_evil_select is True

            # (5) The default-privileges entry gone: a table created AFTER
            # bootstrap must NOT auto-grant INSERT to sapphire_backup.
            with role_harness.owner_engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                conn.execute(
                    sa.text("DROP TABLE IF EXISTS convergence_default_priv_probe")
                )
                conn.execute(
                    sa.text("CREATE TABLE convergence_default_priv_probe (id int)")
                )
                has_probe_insert = conn.execute(
                    sa.text(
                        "SELECT has_table_privilege("
                        "'sapphire_backup', 'convergence_default_priv_probe', "
                        "'INSERT')"
                    )
                ).scalar_one()
                remaining_default_acl_rows = conn.execute(
                    sa.text(
                        "SELECT count(*) FROM pg_default_acl da "
                        "CROSS JOIN LATERAL aclexplode(da.defaclacl) a "
                        "JOIN pg_roles r ON r.oid = a.grantee "
                        "WHERE r.rolname = 'sapphire_backup'"
                    )
                ).scalar_one()
            assert has_probe_insert is False
            assert remaining_default_acl_rows == 0

            # The converged role's FULL signature equals the genuinely
            # fresh role's -- not merely "the four escapes we thought of
            # are gone", but nothing else differs either.
            with role_harness.owner_engine.connect() as conn:
                converged_signature = _capture_sapphire_backup_signature(conn)
            assert converged_signature == fresh_signature
        finally:
            with role_harness.owner_engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                conn.execute(
                    sa.text("DROP TABLE IF EXISTS convergence_default_priv_probe")
                )
                conn.execute(
                    sa.text(
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                        "REVOKE INSERT ON TABLES FROM sapphire_backup"
                    )
                )
                conn.execute(sa.text("DROP SCHEMA IF EXISTS evil_schema CASCADE"))
                conn.execute(sa.text("DROP PROCEDURE IF EXISTS test_full_conv_proc()"))
                _drop_sapphire_backup_role(conn)
