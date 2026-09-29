"""Plan 513 T2/T3: the declared-tenant step against a real Postgres."""

from __future__ import annotations

import contextlib
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from structlog.testing import capture_logs

from sapphire_flow.cli import provision_tenants
from sapphire_flow.cli.import_dhm_delivery import (
    DELIVERY_TENANT_CODE,
    DELIVERY_TENANT_NAME,
)
from sapphire_flow.cli.provision_tenants import ensure_declared_tenants
from sapphire_flow.db.metadata import tenants
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.tenant import (
    DEFAULT_TENANT_CODE,
    DEFAULT_TENANT_NAME,
    DeclaredTenant,
    Tenant,
)
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def keep_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() reconfigures structlog, which would replace capture_logs()."""
    monkeypatch.setattr(provision_tenants, "configure_cli_logging", lambda: None)


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

    def test_existing_tenant_with_a_station_keeps_id_name_and_station(
        self, db_connection: sa.Connection
    ) -> None:
        tenant_store = PgTenantStore(db_connection)
        station_store = PgStationStore(db_connection)
        owner_id = TenantId(uuid.uuid4())
        tenant_store.store_tenant(
            Tenant(
                id=owner_id,
                code="owns-station",
                name="Owns Station",
                created_at=ensure_utc(datetime(2026, 9, 1, tzinfo=UTC)),
            )
        )
        station = make_station_config(
            code="OWN-513", network="test513", tenant_id=owner_id
        )
        station_store.store_station(station)

        result = ensure_declared_tenants(
            tenant_store,
            [DeclaredTenant(code="owns-station", name="Owns Station")],
            new_id=_new_id,
        )

        assert (result[0].id, result[0].name) == (owner_id, "Owns Station")
        kept = station_store.fetch_station(station.id)
        assert kept is not None
        assert kept.tenant_id == owner_id
        assert (kept.code, kept.name, kept.network) == (
            station.code,
            station.name,
            station.network,
        )

    def test_injected_id_factory_is_used_for_a_new_tenant(
        self, db_connection: sa.Connection
    ) -> None:
        wanted = uuid.UUID("00000000-0000-0000-0000-0000000005a3")
        store = PgTenantStore(db_connection)
        result = ensure_declared_tenants(
            store,
            [DeclaredTenant(code="factory-t", name="Factory")],
            new_id=lambda: wanted,
        )
        assert result[0].id == wanted

    def test_existing_row_keeps_its_created_at(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgTenantStore(db_connection)
        created = ensure_utc(datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
        store.store_tenant(
            Tenant(
                id=TenantId(uuid.uuid4()),
                code="kept-ts",
                name="Kept",
                created_at=created,
            )
        )
        result = ensure_declared_tenants(
            store, [DeclaredTenant(code="kept-ts", name="Kept")], new_id=_new_id
        )
        assert result[0].created_at == created

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


class _BarrierStore:
    """After the FIRST insert of its batch, waits at a barrier — so both threads
    hold their first-row lock before either attempts a second. A timed-out wait
    is tolerated: it means the other side is blocked on this thread's row."""

    def __init__(
        self, inner: PgTenantStore, barrier: threading.Barrier, timeout: float
    ) -> None:
        self._inner = inner
        self._barrier = barrier
        self._timeout = timeout
        self._first = True

    def ensure_tenant(self, *, tenant_id: TenantId, code: str, name: str) -> Tenant:
        tenant = self._inner.ensure_tenant(tenant_id=tenant_id, code=code, name=name)
        if self._first:
            self._first = False
            with contextlib.suppress(threading.BrokenBarrierError):
                self._barrier.wait(timeout=self._timeout)
        return tenant


def _bounded(conn: sa.Connection) -> None:
    conn.execute(sa.text("SET LOCAL lock_timeout = '20s'"))
    conn.execute(sa.text("SET LOCAL statement_timeout = '30s'"))


def _run_opposite_orders(
    db_engine: sa.Engine,
    *,
    apply: object,
    barrier_timeout: float,
) -> tuple[list[BaseException], list[threading.Thread]]:
    a = DeclaredTenant(code="conc-a", name="A")
    b = DeclaredTenant(code="conc-b", name="B")
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def run(batch: list[DeclaredTenant]) -> None:
        try:
            with db_engine.begin() as conn:
                _bounded(conn)
                store = _BarrierStore(PgTenantStore(conn), barrier, barrier_timeout)
                apply(store, batch)  # type: ignore[operator]
        except Exception as exc:  # surfaced by the caller's asserts
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=([a, b],)),
        threading.Thread(target=run, args=([b, a],)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return errors, threads


def _apply_in_given_order(store: _BarrierStore, batch: list[DeclaredTenant]) -> None:
    """The hazard: what an unsorted batch loop would do."""
    for tenant in batch:
        store.ensure_tenant(
            tenant_id=TenantId(uuid.uuid4()), code=tenant.code, name=tenant.name
        )


def _apply_production(store: _BarrierStore, batch: list[DeclaredTenant]) -> None:
    ensure_declared_tenants(store, batch, new_id=_new_id)  # type: ignore[arg-type]


def _wait_until_blocked_on_a_lock(db_engine: sa.Engine, pid: int) -> None:
    deadline = time.monotonic() + 10
    with db_engine.connect() as watcher:
        while time.monotonic() < deadline:
            waiting = watcher.execute(
                sa.text(
                    "SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"
                ),
                {"pid": pid},
            ).scalar_one_or_none()
            if waiting == "Lock":
                return
            time.sleep(0.02)
    raise AssertionError("second run never blocked on the uncommitted insert")


class TestConcurrentRuns:
    def test_hazard_is_real_unsorted_opposite_orders_deadlock(
        self, db_engine: sa.Engine, committed_cleanup: list[str]
    ) -> None:
        committed_cleanup.extend(["conc-a", "conc-b"])
        errors, threads = _run_opposite_orders(
            db_engine, apply=_apply_in_given_order, barrier_timeout=10
        )
        assert not any(t.is_alive() for t in threads)
        assert len(errors) == 1
        assert "deadlock" in str(errors[0]).lower()

    def test_production_path_is_immune_under_the_same_forced_interleaving(
        self, db_engine: sa.Engine, committed_cleanup: list[str]
    ) -> None:
        committed_cleanup.extend(["conc-a", "conc-b"])
        errors, threads = _run_opposite_orders(
            db_engine, apply=_apply_production, barrier_timeout=2
        )
        assert not any(t.is_alive() for t in threads)
        assert errors == []
        with db_engine.connect() as conn:
            rows = conn.execute(
                sa.select(tenants.c.code).where(
                    tenants.c.code.in_(["conc-a", "conc-b"])
                )
            ).all()
        assert len(rows) == 2

    def _race(
        self,
        db_engine: sa.Engine,
        *,
        code: str,
        winner_name: str,
        loser_name: str,
    ) -> tuple[uuid.UUID, Tenant | None, BaseException | None]:
        winner_id = uuid.uuid4()
        outcome: dict[str, object] = {}
        pid_ready = threading.Event()

        def loser() -> None:
            try:
                with db_engine.begin() as conn:
                    _bounded(conn)
                    outcome["pid"] = conn.execute(
                        sa.text("SELECT pg_backend_pid()")
                    ).scalar_one()
                    pid_ready.set()
                    outcome["tenant"] = PgTenantStore(conn).ensure_tenant(
                        tenant_id=TenantId(uuid.uuid4()), code=code, name=loser_name
                    )
            except Exception as exc:
                outcome["error"] = exc
            finally:
                pid_ready.set()

        thread = threading.Thread(target=loser)
        try:
            with db_engine.connect() as winner_conn:
                winner_tx = winner_conn.begin()
                _bounded(winner_conn)
                PgTenantStore(winner_conn).ensure_tenant(
                    tenant_id=TenantId(winner_id), code=code, name=winner_name
                )
                thread.start()
                assert pid_ready.wait(timeout=10)
                if "pid" not in outcome:
                    raise AssertionError(
                        f"loser failed before connecting: {outcome.get('error')!r}"
                    )
                _wait_until_blocked_on_a_lock(db_engine, int(outcome["pid"]))  # type: ignore[call-overload]
                winner_tx.commit()
        finally:
            if thread.is_alive() or thread.ident is not None:
                thread.join(timeout=30)
        assert not thread.is_alive()
        return (
            winner_id,
            outcome.get("tenant"),  # type: ignore[return-value]
            outcome.get("error"),  # type: ignore[return-value]
        )

    def test_run_waiting_on_an_uncommitted_winner_sees_the_winners_row(
        self, db_engine: sa.Engine, committed_cleanup: list[str]
    ) -> None:
        committed_cleanup.append("race-same")
        winner_id, tenant, error = self._race(
            db_engine, code="race-same", winner_name="Same", loser_name="Same"
        )
        assert error is None
        assert tenant is not None
        assert (tenant.id, tenant.name) == (winner_id, "Same")

    def test_run_waiting_on_a_winner_with_another_name_fails(
        self, db_engine: sa.Engine, committed_cleanup: list[str]
    ) -> None:
        committed_cleanup.append("race-diff")
        _, tenant, error = self._race(
            db_engine, code="race-diff", winner_name="Winner", loser_name="Loser"
        )
        assert tenant is None
        assert isinstance(error, ConfigurationError)
        assert "race-diff" in str(error)


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

    def test_declaration_only_in_the_selected_overlay_is_created(
        self,
        keep_capture: None,
        db_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        committed_cleanup: list[str],
    ) -> None:
        committed_cleanup.append("overlay-only")
        base = self._config(tmp_path, "[logging]\nlevel='INFO'\n")
        overlay = tmp_path / "overlay.toml"
        overlay.write_text('[tenants.overlay-only]\nname = "Overlay Only"\n')
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(base))
        monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", str(overlay))
        monkeypatch.setenv(
            "DATABASE_URL", db_engine.url.render_as_string(hide_password=False)
        )
        with capture_logs() as logs:
            assert provision_tenants.main([]) == 0
        with db_engine.connect() as conn:
            created = PgTenantStore(conn).fetch_tenant_by_code("overlay-only")
        assert created is not None
        assert created.name == "Overlay Only"
        declared = [e for e in logs if e["event"] == "tenants_declared"]
        assert declared[0]["count"] == 1

    def test_main_rolls_back_valid_tenants_when_a_later_one_conflicts(
        self,
        keep_capture: None,
        db_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        committed_cleanup: list[str],
    ) -> None:
        committed_cleanup.extend(["aaa-main-atomic", "zzz-main-existing"])
        with db_engine.begin() as conn:
            PgTenantStore(conn).store_tenant(
                Tenant(
                    id=TenantId(uuid.uuid4()),
                    code="zzz-main-existing",
                    name="Old name",
                    created_at=ensure_utc(datetime(2026, 9, 1, tzinfo=UTC)),
                )
            )
        config = self._config(
            tmp_path,
            '[tenants.aaa-main-atomic]\nname = "First"\n'
            '[tenants.zzz-main-existing]\nname = "New name"\n',
        )
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))
        monkeypatch.setenv(
            "DATABASE_URL", db_engine.url.render_as_string(hide_password=False)
        )
        with capture_logs() as logs:
            assert provision_tenants.main([]) == 1
        with db_engine.connect() as separate:
            assert (
                PgTenantStore(separate).fetch_tenant_by_code("aaa-main-atomic") is None
            )
        assert any(e["event"] == "tenant_provisioning_failed" for e in logs)
