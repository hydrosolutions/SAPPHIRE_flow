from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy.exc import IntegrityError, OperationalError

from sapphire_flow.api.human_auth import (
    HumanAuthError,
    OidcIdentity,
    require_human_principal,
    require_human_station_permission,
)
from sapphire_flow.cli import hydrologists
from sapphire_flow.cli.hydrologists import (
    change_hydrologist_grant,
    create_hydrologist,
    disable_hydrologist,
)
from sapphire_flow.db.metadata import (
    audit_log,
    human_station_grants,
    stations,
    user_external_identities,
    users,
)
from sapphire_flow.store.human_identity_store import (
    HumanIdentityError,
    PgHumanIdentityStore,
)
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.human_auth import HumanPermission
from sapphire_flow.types.ids import StationId, TenantId, UserId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID, Tenant
from tests.conftest import make_station_config

_NOW = ensure_utc(datetime(2026, 9, 26, tzinfo=UTC))
_ISSUER = "https://idp.example.org/"

if TYPE_CHECKING:
    from collections.abc import Iterator


def _user(conn: sa.Connection, *, tenant_id: TenantId = DEFAULT_TENANT_ID) -> UserId:
    user_id = UserId(uuid4())
    PgHumanIdentityStore(conn).create_user(
        user_id=user_id,
        tenant_id=tenant_id,
        display_name="Duty hydrologist",
        issuer=_ISSUER,
        subject=f"hydro-{user_id}",
        now=_NOW,
    )
    return user_id


def _station(
    conn: sa.Connection, *, tenant_id: TenantId = DEFAULT_TENANT_ID
) -> StationId:
    station_id = StationId(uuid4())
    station = make_station_config(
        station_id=station_id, code=f"TEST-{station_id.hex[:8]}", tenant_id=tenant_id
    )
    PgStationStore(conn).store_station(station)
    return station.id


@pytest.fixture
def committed_human(db_engine: sa.Engine) -> Iterator[tuple[UserId, StationId]]:
    with db_engine.begin() as setup:
        user_id = _user(setup)
        station_id = _station(setup)
        store = PgHumanIdentityStore(setup)
        store.grant(user_id, station_id, HumanPermission.REVIEW, now=_NOW)
        store.grant(user_id, station_id, HumanPermission.PUBLISH, now=_NOW)
    yield user_id, station_id
    with db_engine.begin() as cleanup:
        cleanup.execute(
            sa.delete(human_station_grants).where(
                human_station_grants.c.user_id == user_id
            )
        )
        cleanup.execute(
            sa.delete(user_external_identities).where(
                user_external_identities.c.user_id == user_id
            )
        )
        cleanup.execute(sa.delete(users).where(users.c.id == user_id))
        cleanup.execute(sa.delete(stations).where(stations.c.id == station_id))


class TestHumanIdentityStore:
    def test_resolution_holds_grant_revocation_until_request_finishes(
        self, db_engine: sa.Engine, committed_human: tuple[UserId, StationId]
    ) -> None:
        user_id, station_id = committed_human
        with db_engine.connect() as request_conn, db_engine.connect() as operator_conn:
            with request_conn.begin():
                principal = PgHumanIdentityStore(request_conn).resolve_principal(
                    issuer=_ISSUER, subject=f"hydro-{user_id}"
                )
                assert principal is not None
                assert principal.allows(station_id, HumanPermission.REVIEW)
                with (
                    pytest.raises(OperationalError, match="lock timeout"),
                    operator_conn.begin(),
                ):
                    operator_conn.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
                    PgHumanIdentityStore(operator_conn).revoke(
                        user_id, station_id, HumanPermission.REVIEW
                    )
            with operator_conn.begin():
                assert PgHumanIdentityStore(operator_conn).revoke(
                    user_id, station_id, HumanPermission.REVIEW
                )
            with request_conn.begin():
                principal = PgHumanIdentityStore(request_conn).resolve_principal(
                    issuer=_ISSUER, subject=f"hydro-{user_id}"
                )
                assert principal is not None
                assert not principal.allows(station_id, HumanPermission.REVIEW)

    def test_revoke_review_prevents_concurrent_publish_regrant(
        self, db_engine: sa.Engine, committed_human: tuple[UserId, StationId]
    ) -> None:
        user_id, station_id = committed_human
        with db_engine.connect() as revoke_conn, db_engine.connect() as grant_conn:
            with revoke_conn.begin():
                assert PgHumanIdentityStore(revoke_conn).revoke(
                    user_id, station_id, HumanPermission.REVIEW
                )
                with (
                    pytest.raises(OperationalError, match="lock timeout"),
                    grant_conn.begin(),
                ):
                    grant_conn.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
                    PgHumanIdentityStore(grant_conn).grant(
                        user_id, station_id, HumanPermission.PUBLISH, now=_NOW
                    )
            with (
                grant_conn.begin(),
                pytest.raises(HumanIdentityError, match="requires a review"),
            ):
                PgHumanIdentityStore(grant_conn).grant(
                    user_id, station_id, HumanPermission.PUBLISH, now=_NOW
                )

    def test_review_and_publish_are_station_scoped_and_revocable(
        self, db_connection: sa.Connection
    ) -> None:
        user_id = _user(db_connection)
        station_id = _station(db_connection)
        other_id = _station(db_connection)
        store = PgHumanIdentityStore(db_connection)
        assert store.grant(user_id, station_id, HumanPermission.REVIEW, now=_NOW)
        assert store.grant(user_id, station_id, HumanPermission.PUBLISH, now=_NOW)
        principal = store.resolve_principal(issuer=_ISSUER, subject=f"hydro-{user_id}")
        assert principal is not None
        assert principal.user_id == user_id
        assert principal.allows(station_id, HumanPermission.REVIEW)
        assert principal.allows(station_id, HumanPermission.PUBLISH)
        assert not principal.allows(other_id, HumanPermission.REVIEW)

        assert store.revoke(user_id, station_id, HumanPermission.PUBLISH)
        principal = store.resolve_principal(issuer=_ISSUER, subject=f"hydro-{user_id}")
        assert principal is not None
        assert principal.allows(station_id, HumanPermission.REVIEW)
        assert not principal.allows(station_id, HumanPermission.PUBLISH)

    def test_revoking_review_also_removes_publish(
        self, db_connection: sa.Connection
    ) -> None:
        user_id = _user(db_connection)
        station_id = _station(db_connection)
        store = PgHumanIdentityStore(db_connection)
        store.grant(user_id, station_id, HumanPermission.REVIEW, now=_NOW)
        store.grant(user_id, station_id, HumanPermission.PUBLISH, now=_NOW)
        assert store.revoke(user_id, station_id, HumanPermission.REVIEW)
        principal = store.resolve_principal(issuer=_ISSUER, subject=f"hydro-{user_id}")
        assert principal is not None
        assert not principal.allows(station_id, HumanPermission.REVIEW)
        assert not principal.allows(station_id, HumanPermission.PUBLISH)

    def test_disabled_or_unlisted_identity_cannot_resolve(
        self, db_connection: sa.Connection
    ) -> None:
        user_id = _user(db_connection)
        store = PgHumanIdentityStore(db_connection)
        assert store.resolve_principal(issuer=_ISSUER, subject="unknown") is None
        store.disable_user(user_id, now=_NOW)
        assert (
            store.resolve_principal(issuer=_ISSUER, subject=f"hydro-{user_id}") is None
        )

    def test_cross_tenant_grant_refused_at_store_and_database(
        self, db_connection: sa.Connection
    ) -> None:
        other_tenant = Tenant(
            id=TenantId(uuid4()),
            code=f"other-{uuid4().hex[:6]}",
            name="Other",
            created_at=_NOW,
        )
        PgTenantStore(db_connection).store_tenant(other_tenant)
        user_id = _user(db_connection)
        foreign_station_id = _station(db_connection, tenant_id=other_tenant.id)
        store = PgHumanIdentityStore(db_connection)
        with pytest.raises(HumanIdentityError, match="outside"):
            store.grant(user_id, foreign_station_id, HumanPermission.REVIEW, now=_NOW)
        with pytest.raises(IntegrityError), db_connection.begin_nested():
            db_connection.execute(
                sa.insert(human_station_grants).values(
                    user_id=user_id,
                    tenant_id=DEFAULT_TENANT_ID,
                    station_id=foreign_station_id,
                    permission="review",
                    granted_at=_NOW,
                )
            )

    def test_publish_cannot_be_granted_without_review(
        self, db_connection: sa.Connection
    ) -> None:
        user_id = _user(db_connection)
        station_id = _station(db_connection)
        with pytest.raises(HumanIdentityError, match="requires a review grant"):
            PgHumanIdentityStore(db_connection).grant(
                user_id, station_id, HumanPermission.PUBLISH, now=_NOW
            )

    def test_api_dependency_uses_current_grant_and_rejects_service_key(
        self, db_connection: sa.Connection
    ) -> None:
        class Verifier:
            def verify(self, token: str) -> OidcIdentity:
                if token != "signed-human-token":
                    raise HumanAuthError("invalid")
                return OidcIdentity(issuer=_ISSUER, subject=f"hydro-{user_id}")

        user_id = _user(db_connection)
        station_id = _station(db_connection)
        store = PgHumanIdentityStore(db_connection)
        store.grant(user_id, station_id, HumanPermission.REVIEW, now=_NOW)
        store.grant(user_id, station_id, HumanPermission.PUBLISH, now=_NOW)
        app = FastAPI()
        app.state.human_oidc_verifier = Verifier()

        def request(token: str) -> Request:
            return Request(
                {
                    "type": "http",
                    "app": app,
                    "headers": [(b"authorization", f"Bearer {token}".encode())],
                }
            )

        principal = require_human_principal(
            request("signed-human-token"), db_connection
        )
        require_human_station_permission(principal, station_id, HumanPermission.PUBLISH)
        with pytest.raises(HTTPException) as service_denial:
            require_human_principal(request("service-key.secret"), db_connection)
        assert service_denial.value.status_code == 401

        store.revoke(user_id, station_id, HumanPermission.PUBLISH)
        principal = require_human_principal(
            request("signed-human-token"), db_connection
        )
        with pytest.raises(HTTPException) as grant_denial:
            require_human_station_permission(
                principal, station_id, HumanPermission.PUBLISH
            )
        assert grant_denial.value.status_code == 404

        store.disable_user(user_id, now=_NOW)
        with pytest.raises(HTTPException) as disabled_denial:
            require_human_principal(request("signed-human-token"), db_connection)
        assert disabled_denial.value.status_code == 401


class TestOperatorAudit:
    def test_cli_grant_revoke_and_identity_link_are_audited(
        self, db_connection: sa.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user_id = _user(db_connection)
        station_id = _station(db_connection)

        class TestEngine:
            @contextmanager
            def begin(self) -> Iterator[sa.Connection]:
                with db_connection.begin_nested():
                    yield db_connection

            def dispose(self) -> None:
                pass

        monkeypatch.setattr(hydrologists, "create_engine_from_env", TestEngine)

        def run_cli(*arguments: str) -> None:
            monkeypatch.setattr(
                sys,
                "argv",
                ["hydrologists", "--operator", "test-operator", *arguments],
            )
            hydrologists.main()

        run_cli("grant", str(user_id), str(station_id), "review")
        run_cli("grant", str(user_id), str(station_id), "publish")
        run_cli("revoke", str(user_id), str(station_id), "review")
        run_cli(
            "link-identity",
            str(user_id),
            "--issuer",
            _ISSUER,
            "--subject",
            "second-subject",
        )

        assert (
            db_connection.execute(
                sa.select(human_station_grants.c.user_id).where(
                    human_station_grants.c.user_id == user_id
                )
            ).all()
            == []
        )
        assert (
            db_connection.execute(
                sa.select(user_external_identities.c.subject).where(
                    user_external_identities.c.user_id == user_id,
                    user_external_identities.c.subject == "second-subject",
                )
            ).scalar_one()
            == "second-subject"
        )
        events = db_connection.execute(
            sa.select(
                audit_log.c.event_type, audit_log.c.actor_type, audit_log.c.detail
            )
            .where(
                audit_log.c.target_type == "user", audit_log.c.target_id == str(user_id)
            )
            .order_by(audit_log.c.id)
        ).all()
        assert [row.event_type for row in events] == [
            "human_grant_changed",
            "human_grant_changed",
            "human_grant_changed",
            "human_identity_linked",
        ]
        assert [row.detail.get("action") for row in events] == [
            "grant",
            "grant",
            "revoke",
            None,
        ]
        assert all(row.actor_type == "system" for row in events)
        assert all(row.detail["operator"] == "test-operator" for row in events)

    def test_create_grant_and_disable_are_audited(
        self, db_connection: sa.Connection
    ) -> None:
        user_id = UserId(uuid4())
        station_id = _station(db_connection)
        create_hydrologist(
            db_connection,
            tenant_code="sapphire",
            display_name="Duty hydrologist",
            issuer=_ISSUER,
            subject="hydro-1",
            operator="test-operator",
            now=_NOW,
            user_id=user_id,
        )
        change_hydrologist_grant(
            db_connection,
            user_id=user_id,
            station_id=station_id,
            permission=HumanPermission.REVIEW,
            action="grant",
            operator="test-operator",
            now=_NOW,
        )
        disable_hydrologist(
            db_connection, user_id=user_id, operator="test-operator", now=_NOW
        )
        events = db_connection.execute(
            sa.select(
                audit_log.c.event_type, audit_log.c.actor_type, audit_log.c.detail
            )
            .where(
                audit_log.c.target_type == "user", audit_log.c.target_id == str(user_id)
            )
            .order_by(audit_log.c.id)
        ).all()
        assert [row.event_type for row in events] == [
            "user_created",
            "human_grant_changed",
            "user_deactivated",
        ]
        assert all(row.actor_type == "system" for row in events)
        assert all(row.detail["operator"] == "test-operator" for row in events)
