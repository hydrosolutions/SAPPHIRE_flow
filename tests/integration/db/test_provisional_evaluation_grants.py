from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import (
    access_token_stations,
    access_tokens,
    provisional_evaluation_grants,
    stations,
    users,
)
from sapphire_flow.store.provisional_evaluation_grants import (
    PgEvaluationGrantProvisioner,
    PgEvaluationGrantReader,
)
from sapphire_flow.types.ids import StationId, TenantId
from sapphire_flow.types.provisional_evaluation import (
    EvaluationScope,
    EvaluationSubject,
    EvaluationSubjectKind,
)
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.integration.store.test_native_rating_forecasts import native_pair

if TYPE_CHECKING:
    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness


def scope_and_subject(
    conn: sa.Connection, kind: Literal["consumer", "admin", "human"]
) -> tuple[EvaluationScope, EvaluationSubject]:
    _, native = native_pair(conn)
    tenant = conn.execute(
        sa.select(stations.c.tenant_id).where(stations.c.id == native.station_id)
    ).scalar_one()
    scope = EvaluationScope(
        tenant_id=TenantId(tenant), station_id=StationId(native.station_id)
    )
    subject_id = uuid4()
    if kind == "human":
        conn.execute(
            sa.insert(users).values(
                id=subject_id, tenant_id=tenant, display_name="evaluation fixture"
            )
        )
        return scope, EvaluationSubject(
            kind=EvaluationSubjectKind.HUMAN_USER, id=subject_id
        )
    conn.execute(
        sa.insert(access_tokens).values(
            id=subject_id,
            token_hash=uuid4().hex,
            key_prefix=uuid4().hex,
            name="evaluation fixture",
            role=kind,
            tenant_id=None if kind == "admin" else tenant,
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        )
    )
    if kind == "consumer":
        conn.execute(
            sa.insert(access_token_stations).values(
                token_id=subject_id, station_id=scope.station_id
            )
        )
    return scope, EvaluationSubject(
        kind=EvaluationSubjectKind.SERVICE_TOKEN, id=subject_id
    )


@pytest.mark.parametrize("kind", ["consumer", "admin", "human"])
def test_explicit_grant_is_required_and_revocation_is_immediate(
    role_harness: _RoleBootstrapHarness,
    kind: Literal["consumer", "admin", "human"],
) -> None:
    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.connect() as conn, conn.begin():
        scope, subject = scope_and_subject(conn, kind)
        reader = PgEvaluationGrantReader(conn)
        assert reader.find_active_grant(scope, subject) is None
        provisioner = PgEvaluationGrantProvisioner(conn, id_gen=uuid4)
        grant = provisioner.grant(scope, subject, operator="fixture")
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        assert reader.find_active_grant(scope, subject) == grant.id
        with (
            pytest.raises(sa.exc.ProgrammingError, match="permission denied"),
            conn.begin_nested(),
        ):
            conn.execute(sa.select(provisional_evaluation_grants))
        conn.execute(sa.text("RESET ROLE"))
        revoked = provisioner.revoke(grant.id, operator="fixture revoke")
        assert revoked is not None and revoked.revoked_at is not None
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        assert reader.find_active_grant(scope, subject) is None


def test_scope_removal_and_disable_invalidate_projection_without_deleting_evidence(
    role_harness: _RoleBootstrapHarness,
) -> None:
    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.connect() as conn, conn.begin():
        scope, subject = scope_and_subject(conn, "consumer")
        grant = PgEvaluationGrantProvisioner(conn, id_gen=uuid4).grant(
            scope, subject, operator="fixture"
        )
        conn.execute(
            sa.delete(access_token_stations).where(
                access_token_stations.c.token_id == subject.id
            )
        )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        assert PgEvaluationGrantReader(conn).find_active_grant(scope, subject) is None
        conn.execute(sa.text("RESET ROLE"))
        assert (
            conn.scalar(
                sa.select(provisional_evaluation_grants.c.id).where(
                    provisional_evaluation_grants.c.id == grant.id
                )
            )
            == grant.id
        )
        with (
            pytest.raises(sa.exc.IntegrityError, match="foreign key"),
            conn.begin_nested(),
        ):
            conn.execute(
                sa.delete(access_tokens).where(access_tokens.c.id == subject.id)
            )
        conn.execute(
            sa.insert(access_token_stations).values(
                token_id=subject.id, station_id=scope.station_id
            )
        )
        conn.execute(
            sa.update(access_tokens)
            .where(access_tokens.c.id == subject.id)
            .values(disabled_at=sa.func.statement_timestamp())
        )
        assert PgEvaluationGrantReader(conn).find_active_grant(scope, subject) is None


@pytest.mark.parametrize("role", ["sapphire_api", "sapphire_worker"])
def test_application_roles_cannot_provision_grants(
    role_harness: _RoleBootstrapHarness, role: str
) -> None:
    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.connect() as conn, conn.begin():
        scope, subject = scope_and_subject(conn, "consumer")
        conn.execute(sa.text(f"SET LOCAL ROLE {role}"))
        with (
            pytest.raises(sa.exc.ProgrammingError, match="permission denied"),
            conn.begin_nested(),
        ):
            PgEvaluationGrantProvisioner(conn, id_gen=uuid4).grant(
                scope, subject, operator="forbidden"
            )


def test_maintenance_cannot_rewrite_or_unrevoke_grants(
    role_harness: _RoleBootstrapHarness,
) -> None:
    with role_harness.owner_engine.connect() as conn, conn.begin():
        scope, subject = scope_and_subject(conn, "human")
        provisioner = PgEvaluationGrantProvisioner(conn, id_gen=uuid4)
        grant = provisioner.grant(scope, subject, operator="fixture")
        with (
            pytest.raises(sa.exc.DBAPIError, match="one-way revocation"),
            conn.begin_nested(),
        ):
            conn.execute(
                sa.update(provisional_evaluation_grants)
                .where(provisional_evaluation_grants.c.id == grant.id)
                .values(granted_by="changed")
            )
        provisioner.revoke(grant.id, operator="fixture")
        with (
            pytest.raises(sa.exc.DBAPIError, match="one-way revocation"),
            conn.begin_nested(),
        ):
            conn.execute(
                sa.update(provisional_evaluation_grants)
                .where(provisional_evaluation_grants.c.id == grant.id)
                .values(revoked_at=None, revoked_by=None)
            )
        with pytest.raises(sa.exc.DBAPIError, match="immutable"), conn.begin_nested():
            conn.execute(
                sa.delete(provisional_evaluation_grants).where(
                    provisional_evaluation_grants.c.id == grant.id
                )
            )
        with pytest.raises(sa.exc.DBAPIError, match="immutable"), conn.begin_nested():
            conn.execute(sa.text("TRUNCATE provisional_evaluation_grants"))
        with (
            pytest.raises(sa.exc.IntegrityError, match="foreign key"),
            conn.begin_nested(),
        ):
            conn.execute(sa.delete(users).where(users.c.id == subject.id))


@pytest.mark.parametrize("role", ["sapphire_api", "sapphire_worker"])
def test_active_grant_view_is_api_only_after_bootstrap(
    role_harness: _RoleBootstrapHarness, role: str
) -> None:
    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.connect() as conn, conn.begin():
        allowed = conn.scalar(
            sa.text(
                "SELECT has_table_privilege(:role, "
                "'provisional_evaluation_active_grants', 'SELECT')"
            ),
            {"role": role},
        )
        assert allowed is (role == "sapphire_api")
