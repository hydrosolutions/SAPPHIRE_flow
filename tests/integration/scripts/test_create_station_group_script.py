"""Plan 262 T3a: group creation against real Postgres.

The unit tests use fakes and prove the decision logic. What only a real database
proves is that the rows actually land and read back — `station_groups` held ZERO
rows on staging precisely because nothing outside tests had ever written one, so
"the fake accepted it" is not evidence this path works.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId, TenantId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID, Tenant
from sapphire_flow.types.write_principal import WritePrincipal
from scripts.create_station_group import (
    apply_station_group,
    plan_station_group,
)
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_NOW = ensure_utc(datetime(2026, 9, 11, 12, tzinfo=UTC))
_PRINCIPAL = WritePrincipal(id=None, tenant_id=DEFAULT_TENANT_ID)


@pytest.fixture(autouse=True)
def deployment_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(
        '[deployment]\nwritable_tenants = ["sapphire"]\noperator = "test-operator"\n'
    )
    monkeypatch.setenv("SAPPHIRE_CONFIG", str(path))
    monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
    return path


def _clock():  # noqa: ANN202
    return _NOW


def _seed_two_stations(conn: sa.Connection) -> tuple[str, str]:
    """Distinct ids matter: `make_station_config` seeds a deterministic one, so
    two calls without explicit ids collide on `stations_pkey`.
    """
    station_store = PgStationStore(conn)
    for code in ("2009", "2091"):
        station_store.store_station(
            make_station_config(
                station_id=StationId(uuid4()), code=code, network="bafu"
            )
        )
    return ("2009", "2091")


class TestGroupCreationAgainstPostgres:
    def test_the_group_and_its_members_read_back(
        self, db_connection: sa.Connection
    ) -> None:
        codes = _seed_two_stations(db_connection)
        group_store = PgStationGroupStore(db_connection)

        plan = plan_station_group(
            name="swiss-cmal-small-pilot",
            station_codes=list(codes),
            network="bafu",
            tenant_id=DEFAULT_TENANT_ID,
            station_store=PgStationStore(db_connection),
            group_store=group_store,
        )
        apply_station_group(
            plan,
            group_store=group_store,
            clock=_clock,
            principal=_PRINCIPAL,
            audit_log_store=PgAuditLogStore(db_connection),
        )

        stored = group_store.fetch_group_by_name(
            DEFAULT_TENANT_ID, "swiss-cmal-small-pilot"
        )
        assert stored is not None
        assert stored.id == plan.group_id
        assert len(stored.station_ids) == 2

    def test_a_second_run_is_a_no_op(self, db_connection: sa.Connection) -> None:
        """Idempotence against the real unique constraints, not just a fake dict."""
        codes = _seed_two_stations(db_connection)
        group_store = PgStationGroupStore(db_connection)
        common = {
            "name": "swiss-cmal-small-pilot",
            "station_codes": list(codes),
            "network": "bafu",
            "tenant_id": DEFAULT_TENANT_ID,
        }

        first = plan_station_group(
            station_store=PgStationStore(db_connection),
            group_store=group_store,
            **common,
        )
        apply_station_group(
            first,
            group_store=group_store,
            clock=_clock,
            principal=_PRINCIPAL,
            audit_log_store=PgAuditLogStore(db_connection),
        )

        second = plan_station_group(
            station_store=PgStationStore(db_connection),
            group_store=group_store,
            **common,
        )

        assert second.group_exists is True
        assert second.group_id == first.group_id
        assert second.is_noop is True

        apply_station_group(
            second,
            group_store=group_store,
            clock=_clock,
            principal=_PRINCIPAL,
            audit_log_store=PgAuditLogStore(db_connection),
        )

        stored = group_store.fetch_group_by_name(
            DEFAULT_TENANT_ID, "swiss-cmal-small-pilot"
        )
        assert stored is not None
        assert len(stored.station_ids) == 2, "a re-run must not duplicate members"


class TestTheWriteIsAtomic:
    """Independent review 2026-09-11 (major): `PgStationGroupStore` defaults its
    transaction factory to `conn.engine.begin` — a NEW engine-level transaction — so
    `store_group` would commit independently while `add_station_to_group` and the
    audit INSERT (both plain `self._conn.execute`) stayed in the caller's. A failure
    after the group row would leave an EMPTY, UNAUDITED group behind while the CLI
    reported failure.

    🪤 **This runs through `main()` on purpose.** A confirming review caught the first
    version of this test building its OWN store with the transaction factory —
    duplicating the production wiring instead of exercising it, so deleting the
    factory from `main()` would have restored the defect while the test stayed green.
    That is the same defect class as a seam test calling its helper directly. The
    only thing patched here is the audit store, which must fail on demand; the
    engine, the connection, the group store and its wiring are all real.
    """

    def test_a_failure_after_the_group_row_leaves_no_group(
        self,
        db_engine: sa.Engine,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from scripts.create_station_group import main as cli_main

        name = f"atomicity-{uuid4()}"
        code = f"C{uuid4().hex[:6]}"
        station_id = StationId(uuid4())

        # 🪤 This seed must COMMIT — `main()` opens its own connection and cannot see
        # an uncommitted row — so unlike every other test here it does NOT ride the
        # rolled-back `db_connection` fixture. That makes cleanup THIS test's
        # responsibility: `db_engine` is session-scoped, and a surviving station
        # breaks any later test asserting an empty fetch. It did exactly that before
        # the teardown below was added.
        self._committed_station_ids.append(station_id)
        with db_engine.begin() as seed:
            PgStationStore(seed).store_station(
                make_station_config(station_id=station_id, code=code, network="bafu")
            )

        class _ExplodingAudit:
            def __init__(self, conn: object) -> None:
                del conn

            def append_entry(self, entry: object) -> None:
                del entry
                raise RuntimeError("audit write failed")

        # 🪤 `str(url)` MASKS the password as `***`, so `main()` would fail to
        # connect, return 1, and leave no group — passing this test for an entirely
        # unrelated reason. Found by diagnosing why the mutation below did NOT fail.
        monkeypatch.setenv(
            "DATABASE_URL", db_engine.url.render_as_string(hide_password=False)
        )
        # Imported INSIDE `main()`, so it is not an attribute of that module —
        # patch it at its source.
        monkeypatch.setattr(
            "sapphire_flow.store.audit_log_store.PgAuditLogStore", _ExplodingAudit
        )

        exit_code = cli_main(
            ["--name", name, "--station-code", code, "--network", "bafu", "--apply"]
        )

        assert exit_code == 1, "the CLI must report failure"
        assert "audit write failed" in capsys.readouterr().err

        with db_engine.connect() as check:
            survivor = PgStationGroupStore(check).fetch_group_by_name(
                DEFAULT_TENANT_ID, name
            )

        assert survivor is None, (
            "the group row must roll back with the failed audit write — if it "
            "survives, main() let store_group open its own transaction"
        )

    @pytest.fixture(autouse=True)
    def _clean_committed_seeds(self, db_engine: sa.Engine):  # noqa: ANN202
        """Delete EXACTLY the rows this test commits, by primary key.

        🪤 **This is the only test here that commits.** Every other one rides the
        `db_connection` fixture, whose transaction is rolled back; this one cannot,
        because `main()` opens its own connection and would not see an uncommitted
        station. `db_engine` is SESSION-scoped, so the row outlives the test and is
        visible to every later one.

        Measured, not theorised: without this teardown the committed RIVER station
        made `test_station_store.py::test_empty_returns_empty_list` fail — it asserts
        that NO river stations exist. Clean main passes 719/719; this branch failed
        1 until the rows were removed.

        ⛔ Deletes by collected id, never a `LIKE` pattern — a broad delete in a
        shared database would reach other tests' rows and turn one leak into
        several.
        """
        committed: list[object] = []
        self._committed_station_ids = committed
        yield
        if not committed:
            return
        with db_engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM stations WHERE id = ANY(:ids)"),
                {"ids": [str(sid) for sid in committed]},
            )


@pytest.fixture
def committed_cli_seed(
    db_engine: sa.Engine,
) -> Iterator[tuple[str, str, str, TenantId]]:
    from sapphire_flow.db.metadata import (
        station_group_members,
        station_groups,
        stations,
        tenants,
    )

    name, code, foreign_code = (f"p262-{uuid4().hex}" for _ in range(3))
    station_id, tenant_id = StationId(uuid4()), TenantId(uuid4())
    with db_engine.begin() as conn:
        PgTenantStore(conn).store_tenant(
            Tenant(id=tenant_id, code=foreign_code, name=foreign_code, created_at=_NOW)
        )
        PgStationStore(conn).store_station(
            make_station_config(station_id=station_id, code=code, network="bafu")
        )
    try:
        yield name, code, foreign_code, tenant_id
    finally:
        with db_engine.begin() as conn:
            group_ids = sa.select(station_groups.c.id).where(
                station_groups.c.name == name,
                station_groups.c.tenant_id == DEFAULT_TENANT_ID,
            )
            conn.execute(
                sa.delete(station_group_members).where(
                    station_group_members.c.group_id.in_(group_ids)
                )
            )
            conn.execute(
                sa.delete(station_groups).where(station_groups.c.id.in_(group_ids))
            )
            conn.execute(sa.delete(stations).where(stations.c.id == station_id))
            conn.execute(sa.delete(tenants).where(tenants.c.id == tenant_id))


class TestCliAuthorizationAgainstPostgres:
    @pytest.mark.parametrize("authority", ["scoped", "global_admin", "foreign"])
    @pytest.mark.parametrize("apply", [False, True])
    def test_cli_authorizes_before_mutation_and_rejection_is_durable(
        self,
        authority: str,
        apply: bool,
        db_engine: sa.Engine,
        monkeypatch: pytest.MonkeyPatch,
        deployment_config: Path,
        committed_cli_seed: tuple[str, str, str, TenantId],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from sapphire_flow.db.metadata import (
            audit_log,
            station_group_members,
            station_groups,
        )
        from scripts.create_station_group import main

        name, code, foreign_code, foreign_id = committed_cli_seed
        identity = {
            "scoped": 'writable_tenants = ["sapphire"]',
            "global_admin": "global_admin = true",
            "foreign": f'writable_tenants = ["{foreign_code}"]',
        }[authority]
        deployment_config.write_text(
            f'[deployment]\n{identity}\noperator = "configured"\n'
        )
        monkeypatch.setenv(
            "DATABASE_URL", db_engine.url.render_as_string(hide_password=False)
        )
        with db_engine.connect() as conn:
            original_station = PgStationStore(conn).fetch_station_by_code(code, "bafu")
            group_ids = sa.select(station_groups.c.id).where(
                station_groups.c.name == name,
                station_groups.c.tenant_id == DEFAULT_TENANT_ID,
            )
            memberships_before = conn.scalar(
                sa.select(sa.func.count())
                .select_from(station_group_members)
                .where(station_group_members.c.group_id.in_(group_ids))
            )

        args = ["--name", name, "--station-code", code, "--operator", "cli-operator"]
        exit_code = main(args + (["--apply"] if apply else []))
        assert exit_code == (1 if authority == "foreign" else 0)
        if authority == "foreign":
            assert "not authorized" in capsys.readouterr().err

        # A fresh connection proves rejection auditing survived the CLI return.
        with db_engine.connect() as conn:
            group = PgStationGroupStore(conn).fetch_group_by_name(
                DEFAULT_TENANT_ID, name
            )
            entries = (
                conn.execute(
                    sa.select(audit_log.c.detail).where(
                        audit_log.c.detail["name"].astext == name
                    )
                )
                .scalars()
                .all()
            )
            station = PgStationStore(conn).fetch_station_by_code(code, "bafu")
            assert station == original_station
            assert len(entries) == int(apply)
            if apply and authority != "foreign":
                assert group is not None and station is not None
                assert group.station_ids == frozenset({station.id})
                assert entries[0]["tenant_id"] == str(DEFAULT_TENANT_ID)
            else:
                assert group is None
                assert (
                    conn.scalar(
                        sa.select(sa.func.count())
                        .select_from(station_group_members)
                        .where(station_group_members.c.group_id.in_(group_ids))
                    )
                    == memberships_before
                )
            if apply:
                assert entries[0]["operator"] == "cli-operator"
            if apply and authority == "foreign":
                assert entries[0]["outcome"] == "rejected_tenant_mismatch"
                assert entries[0]["principal_tenant_id"] == str(foreign_id)
                assert entries[0]["target_tenant_id"] == str(DEFAULT_TENANT_ID)
