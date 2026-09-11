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

from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from scripts.create_station_group import (
    apply_station_group,
    plan_station_group,
)
from tests.conftest import make_station_config

if TYPE_CHECKING:
    import pytest
    import sqlalchemy as sa

_NOW = ensure_utc(datetime(2026, 9, 11, 12, tzinfo=UTC))


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
        apply_station_group(plan, group_store=group_store, clock=_clock)

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
        apply_station_group(first, group_store=group_store, clock=_clock)

        second = plan_station_group(
            station_store=PgStationStore(db_connection),
            group_store=group_store,
            **common,
        )

        assert second.group_exists is True
        assert second.group_id == first.group_id
        assert second.is_noop is True

        apply_station_group(second, group_store=group_store, clock=_clock)

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
        self, db_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.create_station_group import main as cli_main

        name = f"atomicity-{uuid4()}"
        code = f"C{uuid4().hex[:6]}"

        with db_engine.begin() as seed:
            PgStationStore(seed).store_station(
                make_station_config(
                    station_id=StationId(uuid4()), code=code, network="bafu"
                )
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

        with db_engine.connect() as check:
            survivor = PgStationGroupStore(check).fetch_group_by_name(
                DEFAULT_TENANT_ID, name
            )

        assert survivor is None, (
            "the group row must roll back with the failed audit write — if it "
            "survives, main() let store_group open its own transaction"
        )
