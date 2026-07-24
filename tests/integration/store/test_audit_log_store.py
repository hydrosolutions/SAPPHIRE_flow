from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import audit_log, station_groups
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AuditActorType, AuditEventType
from sapphire_flow.types.ids import StationGroupId
from sapphire_flow.types.station import StationGroup
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.integration.store.test_station_group_store import savepoint_factory

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


def _make_entry(
    event_type: AuditEventType = AuditEventType.API_KEY_CREATED,
    actor_type: AuditActorType = AuditActorType.SYSTEM,
    target_type: str | None = "access_token",
    target_id: str | None = None,
    detail: dict | None = None,  # type: ignore[type-arg]
) -> AuditEntry:
    return AuditEntry(
        event_type=event_type,
        actor_id=None,
        actor_type=actor_type,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        ip_address=None,
        created_at=_NOW,
    )


class TestAuditLogSchemaConformsToContract:
    """F4: the authoritative shape — no `tenant_id`/`action`/`at` columns,
    no FK on `actor_id`."""

    def test_columns_match_contract(self, db_engine: sa.Engine) -> None:
        inspector = sa.inspect(db_engine)
        columns = {c["name"] for c in inspector.get_columns("audit_log")}
        assert columns == {
            "id",
            "event_type",
            "actor_id",
            "actor_type",
            "target_type",
            "target_id",
            "detail",
            "ip_address",
            "created_at",
        }

    def test_no_tenant_id_column(self, db_engine: sa.Engine) -> None:
        inspector = sa.inspect(db_engine)
        columns = {c["name"] for c in inspector.get_columns("audit_log")}
        assert "tenant_id" not in columns

    def test_not_null_columns(self, db_engine: sa.Engine) -> None:
        inspector = sa.inspect(db_engine)
        columns = {c["name"]: c for c in inspector.get_columns("audit_log")}
        assert columns["event_type"]["nullable"] is False
        assert columns["actor_type"]["nullable"] is False
        assert columns["created_at"]["nullable"] is False
        assert columns["actor_id"]["nullable"] is True
        assert columns["target_type"]["nullable"] is True
        assert columns["target_id"]["nullable"] is True
        assert columns["detail"]["nullable"] is True
        assert columns["ip_address"]["nullable"] is True

    def test_actor_id_has_no_foreign_key(self, db_engine: sa.Engine) -> None:
        # Append-only rows must survive token revocation/deletion — no FK,
        # no cascade.
        inspector = sa.inspect(db_engine)
        assert inspector.get_foreign_keys("audit_log") == []


class TestAuditLogStoreAppendEntry:
    def test_inserts_exactly_one_well_formed_row(
        self, db_connection: sa.Connection
    ) -> None:
        target_id = str(uuid.uuid4())
        entry = _make_entry(
            event_type=AuditEventType.API_KEY_CREATED,
            actor_type=AuditActorType.SYSTEM,
            target_type="access_token",
            target_id=target_id,
            detail={"consumer_name": "Bipad Portal"},
        )

        PgAuditLogStore(db_connection).append_entry(entry)

        rows = (
            db_connection.execute(
                sa.select(audit_log).where(audit_log.c.target_id == target_id)
            )
            .mappings()
            .all()
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["event_type"] == "api_key_created"
        assert row["actor_type"] == "system"
        assert row["actor_id"] is None
        assert row["target_type"] == "access_token"
        assert row["detail"] == {"consumer_name": "Bipad Portal"}
        assert row["ip_address"] is None

    def test_system_actor_has_null_actor_id_no_reserved_uuid(
        self, db_connection: sa.Connection
    ) -> None:
        target_id = str(uuid.uuid4())
        entry = _make_entry(
            event_type=AuditEventType.MODEL_PROMOTED,
            actor_type=AuditActorType.SYSTEM,
            target_type="model_artifact",
            target_id=target_id,
        )

        PgAuditLogStore(db_connection).append_entry(entry)

        row = (
            db_connection.execute(
                sa.select(audit_log).where(audit_log.c.target_id == target_id)
            )
            .mappings()
            .one()
        )
        assert row["actor_type"] == "system"
        assert row["actor_id"] is None

    def test_actor_type_check_constraint_rejects_unknown_value(
        self, db_connection: sa.Connection
    ) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            db_connection.execute(
                sa.insert(audit_log).values(
                    event_type="api_key_created",
                    actor_type="bogus",
                    created_at=_NOW,
                )
            )


class TestAuditLogAppendOnlyGuard:
    """Slice B owns append-only: a role-independent DB trigger rejects
    UPDATE/DELETE even for the table owner (this test's DB user, which
    created the table via the Alembic migration)."""

    def test_update_is_rejected(self, db_connection: sa.Connection) -> None:
        target_id = str(uuid.uuid4())
        PgAuditLogStore(db_connection).append_entry(_make_entry(target_id=target_id))
        with pytest.raises(sa.exc.DBAPIError, match="append-only"):
            db_connection.execute(
                sa.update(audit_log)
                .where(audit_log.c.target_id == target_id)
                .values(event_type="api_key_revoked")
            )

    def test_delete_is_rejected(self, db_connection: sa.Connection) -> None:
        target_id = str(uuid.uuid4())
        PgAuditLogStore(db_connection).append_entry(_make_entry(target_id=target_id))
        with pytest.raises(sa.exc.DBAPIError, match="append-only"):
            db_connection.execute(
                sa.delete(audit_log).where(audit_log.c.target_id == target_id)
            )


class TestAuditAtomicity:
    """F17/F5: mutation + success-audit share ONE real (non-AUTOCOMMIT)
    transaction via the existing injectable `transaction_factory` seam
    (`station_group_store.py:29-36`) — no repo-wide connection refactor."""

    def test_audit_insert_failure_rolls_back_paired_domain_mutation(
        self, db_connection: sa.Connection
    ) -> None:
        group_id = StationGroupId(uuid.uuid4())
        group = StationGroup(
            id=group_id,
            name="atomicity-test-group",
            station_ids=frozenset(),
            description=None,
            created_at=_NOW,
            tenant_id=DEFAULT_TENANT_ID,
        )

        with pytest.raises(sa.exc.IntegrityError), db_connection.begin_nested():
            group_store = PgStationGroupStore(
                db_connection,
                transaction_factory=savepoint_factory(db_connection),
            )
            group_store.store_group(group)
            # Deliberately violate the actor_type CHECK constraint so the
            # audit INSERT fails inside the SAME (nested) transaction as
            # the domain mutation above.
            db_connection.execute(
                sa.insert(audit_log).values(
                    event_type="station_onboarded",
                    actor_type="bogus",
                    created_at=_NOW,
                )
            )

        # The failed audit insert rolled back the paired domain mutation too.
        fetched = PgStationGroupStore(db_connection).fetch_group(group_id)
        assert fetched is None

    def test_rejection_persists_in_separate_transaction_after_mutation_rollback(
        self, db_connection: sa.Connection
    ) -> None:
        bad_group_id = uuid.uuid4()

        # 1. The domain mutation is refused (simulated cross-tenant write —
        #    an unknown tenant_id violates the FK) — rolled back, no state
        #    change.
        with pytest.raises(sa.exc.IntegrityError), db_connection.begin_nested():
            db_connection.execute(
                sa.insert(station_groups).values(
                    id=bad_group_id,
                    name="rejected-group",
                    tenant_id=uuid.uuid4(),
                    created_at=_NOW,
                )
            )

        assert (
            db_connection.execute(
                sa.select(station_groups).where(station_groups.c.id == bad_group_id)
            )
            .mappings()
            .one_or_none()
            is None
        )

        # 2. The rejection event is durably recorded in a SEPARATE,
        #    independently-committed transaction — the attempted event_type
        #    + detail.outcome="rejected".
        with db_connection.begin_nested():
            PgAuditLogStore(db_connection).append_entry(
                AuditEntry(
                    event_type=AuditEventType.STATION_ONBOARDED,
                    actor_id=None,
                    actor_type=AuditActorType.SYSTEM,
                    target_type="station_group",
                    target_id=str(bad_group_id),
                    detail={"outcome": "rejected", "reason": "tenant_isolation"},
                    ip_address=None,
                    created_at=_NOW,
                )
            )

        row = (
            db_connection.execute(
                sa.select(audit_log).where(audit_log.c.target_id == str(bad_group_id))
            )
            .mappings()
            .one()
        )
        assert row["detail"] == {
            "outcome": "rejected",
            "reason": "tenant_isolation",
        }
        assert row["event_type"] == "station_onboarded"
