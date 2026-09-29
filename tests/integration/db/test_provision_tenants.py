"""Plan 513 T2/T3: the declared-tenant step against a real Postgres."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa

from sapphire_flow.cli import provision_tenants
from sapphire_flow.cli.import_dhm_delivery import (
    DELIVERY_TENANT_CODE,
    DELIVERY_TENANT_NAME,
)
from sapphire_flow.cli.provision_tenants import ensure_declared_tenants
from sapphire_flow.db.metadata import tenants
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.tenant import (
    DEFAULT_TENANT_CODE,
    DEFAULT_TENANT_ID,
    DEFAULT_TENANT_NAME,
    DeclaredTenant,
    Tenant,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _new_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def committed_cleanup(db_engine: sa.Engine) -> Iterator[list[str]]:
    """Tests that COMMIT (rollback tests, concurrency) delete their rows after."""
    codes: list[str] = []
    yield codes
    with db_engine.begin() as conn:
        conn.execute(sa.delete(tenants).where(tenants.c.code.in_(codes)))


class TestSeededNameIsOneSource:
    def test_default_tenant_name_matches_migration_0041(
        self, db_connection: sa.Connection
    ) -> None:
        seeded = PgTenantStore(db_connection).fetch_tenant_by_code(DEFAULT_TENANT_CODE)
        assert seeded is not None
        assert seeded.name == DEFAULT_TENANT_NAME


class TestEnsureDeclaredTenants:
    def test_fresh_database_gains_declared_tenant(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgTenantStore(db_connection)
        ensure_declared_tenants(
            store,
            [DeclaredTenant(code="chwrr-fresh", name="CHWRR Nepal")],
            new_id=_new_id,
        )
        created = store.fetch_tenant_by_code("chwrr-fresh")
        assert created is not None
        assert created.name == "CHWRR Nepal"

    def test_existing_tenant_created_by_the_old_command_keeps_its_id(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgTenantStore(db_connection)
        old_id = TenantId(uuid.uuid4())
        store.store_tenant(
            Tenant(
                id=old_id,
                code=DELIVERY_TENANT_CODE,
                name=DELIVERY_TENANT_NAME,
                created_at=ensure_utc(datetime(2026, 9, 29, tzinfo=UTC)),
            )
        )
        result = ensure_declared_tenants(
            store,
            [DeclaredTenant(code=DELIVERY_TENANT_CODE, name=DELIVERY_TENANT_NAME)],
            new_id=_new_id,
        )
        assert result[0].id == old_id

    def test_existing_tenant_holding_stations_is_kept(
        self, db_connection: sa.Connection
    ) -> None:
        # The seeded tenant already owns rows in real deployments; declaring it
        # again must neither replace nor duplicate it.
        store = PgTenantStore(db_connection)
        result = ensure_declared_tenants(
            store,
            [DeclaredTenant(code=DEFAULT_TENANT_CODE, name=DEFAULT_TENANT_NAME)],
            new_id=_new_id,
        )
        assert result[0].id == DEFAULT_TENANT_ID
        assert len(store.fetch_all_tenants()) >= 1

    def test_rerun_creates_nothing_new(self, db_connection: sa.Connection) -> None:
        store = PgTenantStore(db_connection)
        declared = [DeclaredTenant(code="rerun-t", name="Rerun")]
        first = ensure_declared_tenants(store, declared, new_id=_new_id)
        second = ensure_declared_tenants(store, declared, new_id=_new_id)
        assert first[0].id == second[0].id
        count = db_connection.execute(
            sa.select(sa.func.count()).where(tenants.c.code == "rerun-t")
        ).scalar_one()
        assert count == 1

    def test_name_conflict_raises_and_keeps_the_existing_row(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgTenantStore(db_connection)
        with pytest.raises(ConfigurationError, match=DEFAULT_TENANT_CODE):
            ensure_declared_tenants(
                store,
                [DeclaredTenant(code=DEFAULT_TENANT_CODE, name="Renamed")],
                new_id=_new_id,
            )
        kept = store.fetch_tenant_by_code(DEFAULT_TENANT_CODE)
        assert kept is not None
        assert kept.name == DEFAULT_TENANT_NAME


class TestBatchIsAtomic:
    def test_second_entry_conflict_rolls_back_the_first(
        self, db_engine: sa.Engine, committed_cleanup: list[str]
    ) -> None:
        committed_cleanup.append("aaa-atomic")
        declared = [
            DeclaredTenant(code="aaa-atomic", name="First"),
            DeclaredTenant(code=DEFAULT_TENANT_CODE, name="Conflicting"),
        ]
        with pytest.raises(ConfigurationError), db_engine.begin() as conn:
            ensure_declared_tenants(PgTenantStore(conn), declared, new_id=_new_id)
        with db_engine.connect() as conn:
            assert PgTenantStore(conn).fetch_tenant_by_code("aaa-atomic") is None


class TestConcurrentRuns:
    def test_overlapping_batches_in_opposite_orders_do_not_deadlock(
        self, db_engine: sa.Engine, committed_cleanup: list[str]
    ) -> None:
        committed_cleanup.extend(["conc-a", "conc-b"])
        a = DeclaredTenant(code="conc-a", name="A")
        b = DeclaredTenant(code="conc-b", name="B")
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def run(batch: list[DeclaredTenant]) -> None:
            try:
                barrier.wait(timeout=10)
                with db_engine.begin() as conn:
                    ensure_declared_tenants(PgTenantStore(conn), batch, new_id=_new_id)
            except Exception as exc:  # surfaced by the assert below
                errors.append(exc)

        threads = [
            threading.Thread(target=run, args=([a, b],)),
            threading.Thread(target=run, args=([b, a],)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert errors == []
        with db_engine.connect() as conn:
            rows = conn.execute(
                sa.select(tenants.c.code).where(
                    tenants.c.code.in_(["conc-a", "conc-b"])
                )
            ).all()
        assert len(rows) == 2

    def test_ensure_tenant_skips_a_row_committed_by_a_concurrent_winner(
        self, db_engine: sa.Engine, committed_cleanup: list[str]
    ) -> None:
        committed_cleanup.append("conc-c")
        winner_id = uuid.uuid4()
        with db_engine.begin() as conn:
            PgTenantStore(conn).ensure_tenant(
                tenant_id=TenantId(winner_id), code="conc-c", name="C"
            )
        with db_engine.begin() as conn:
            loser = PgTenantStore(conn).ensure_tenant(
                tenant_id=TenantId(uuid.uuid4()), code="conc-c", name="C"
            )
        assert loser.id == winner_id


class TestRunThroughTheRealConfigPath:
    def _config(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "config.toml"
        path.write_text(body)
        return path

    def test_runtime_only_placeholder_does_not_break_the_step(
        self,
        db_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        committed_cleanup: list[str],
    ) -> None:
        committed_cleanup.append("viaconfig")
        monkeypatch.delenv("SAPPHIRE_FOO", raising=False)
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        config = self._config(
            tmp_path,
            '[adapters.x]\ntoken = "${SAPPHIRE_FOO}"\n'
            '[tenants.viaconfig]\nname = "Via config"\n',
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))
        monkeypatch.setenv(
            "DATABASE_URL", db_engine.url.render_as_string(hide_password=False)
        )
        assert provision_tenants.main([]) == 0
        with db_engine.connect() as conn:
            created = PgTenantStore(conn).fetch_tenant_by_code("viaconfig")
        assert created is not None
        assert created.name == "Via config"

    def test_no_tenants_table_succeeds_and_changes_nothing(
        self,
        db_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv(
            "SAPPHIRE_CONFIG", str(self._config(tmp_path, "[logging]\nlevel='INFO'\n"))
        )
        monkeypatch.setenv(
            "DATABASE_URL", db_engine.url.render_as_string(hide_password=False)
        )
        with db_engine.connect() as conn:
            before = len(PgTenantStore(conn).fetch_all_tenants())
        assert provision_tenants.main([]) == 0
        with db_engine.connect() as conn:
            assert len(PgTenantStore(conn).fetch_all_tenants()) == before
