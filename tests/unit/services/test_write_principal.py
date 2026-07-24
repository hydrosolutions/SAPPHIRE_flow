"""Plan 147 Slice E (G3/G6/R5): WritePrincipal resolution + enforcement."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.exceptions import ConfigurationError, TenantIsolationError
from sapphire_flow.services.write_principal import (
    enforce_tenant_isolation,
    resolve_run_principal,
    target_tenant_id_for_unit,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AuditEventType
from sapphire_flow.types.ids import PrincipalId, TenantId
from sapphire_flow.types.tenant import DEFAULT_TENANT_CODE, DEFAULT_TENANT_ID, Tenant
from sapphire_flow.types.write_principal import WritePrincipal
from tests.conftest import make_station_config, make_training_unit
from tests.fakes.fake_stores import (
    FakeAuditLogStore,
    FakeStationGroupStore,
    FakeStationStore,
    FakeTenantStore,
)

_NOW = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))

_TENANT_B_ID = TenantId(__import__("uuid").UUID("00000000-0000-0000-0000-0000000000b2"))


def _seed_tenant_b(store: FakeTenantStore) -> None:
    store.store_tenant(
        Tenant(id=_TENANT_B_ID, code="tenant-b", name="Tenant B", created_at=_NOW)
    )


class TestResolveRunPrincipalCli:
    """The interactive CLI surface: an explicit tenant_code."""

    def test_explicit_tenant_in_writable_tenants_resolves(self) -> None:
        store = FakeTenantStore()
        identity = DeploymentIdentityConfig(
            writable_tenants=frozenset({DEFAULT_TENANT_CODE}), global_admin=False
        )
        principal = resolve_run_principal(
            store, identity, tenant_code=DEFAULT_TENANT_CODE
        )
        assert principal.tenant_id == DEFAULT_TENANT_ID

    def test_explicit_tenant_outside_writable_tenants_rejected(self) -> None:
        store = FakeTenantStore()
        _seed_tenant_b(store)
        identity = DeploymentIdentityConfig(
            writable_tenants=frozenset({DEFAULT_TENANT_CODE}), global_admin=False
        )
        with pytest.raises(ConfigurationError, match="writable_tenants"):
            resolve_run_principal(store, identity, tenant_code="tenant-b")

    def test_unknown_tenant_code_rejected(self) -> None:
        # global_admin bypasses the writable_tenants membership check, so
        # this exercises the DB-level "unknown code" resolution specifically.
        store = FakeTenantStore()
        identity = DeploymentIdentityConfig(
            writable_tenants=frozenset(), global_admin=True
        )
        with pytest.raises(ConfigurationError, match="unknown tenant code"):
            resolve_run_principal(store, identity, tenant_code="no-such-tenant")

    def test_global_admin_host_accepts_any_known_tenant_code(self) -> None:
        store = FakeTenantStore()
        _seed_tenant_b(store)
        identity = DeploymentIdentityConfig(
            writable_tenants=frozenset(), global_admin=True
        )
        principal = resolve_run_principal(store, identity, tenant_code="tenant-b")
        assert principal.tenant_id == _TENANT_B_ID

    def test_operator_override_wins_over_config_operator(self) -> None:
        store = FakeTenantStore()
        identity = DeploymentIdentityConfig(
            writable_tenants=frozenset({DEFAULT_TENANT_CODE}),
            global_admin=False,
            operator="config-operator",
        )
        principal = resolve_run_principal(
            store,
            identity,
            tenant_code=DEFAULT_TENANT_CODE,
            operator="cli-operator",
        )
        assert principal.id == PrincipalId("cli-operator")


class TestResolveRunPrincipalScheduled:
    """The scheduled-flow surface: tenant_code=None, config-only resolution."""

    def test_global_admin_with_no_tenant_code_is_unscoped(self) -> None:
        store = FakeTenantStore()
        identity = DeploymentIdentityConfig(
            writable_tenants=frozenset(), global_admin=True
        )
        principal = resolve_run_principal(store, identity)
        assert principal.tenant_id is None

    def test_single_writable_tenant_binds_that_tenant(self) -> None:
        store = FakeTenantStore()
        identity = DeploymentIdentityConfig(
            writable_tenants=frozenset({DEFAULT_TENANT_CODE}), global_admin=False
        )
        principal = resolve_run_principal(store, identity)
        assert principal.tenant_id == DEFAULT_TENANT_ID

    def test_multiple_writable_tenants_with_no_code_is_ambiguous(self) -> None:
        store = FakeTenantStore()
        _seed_tenant_b(store)
        identity = DeploymentIdentityConfig(
            writable_tenants=frozenset({DEFAULT_TENANT_CODE, "tenant-b"}),
            global_admin=False,
        )
        with pytest.raises(ConfigurationError, match="exactly one"):
            resolve_run_principal(store, identity)

    def test_unknown_writable_tenant_code_is_a_hard_startup_error(self) -> None:
        """Every declared writable_tenants code is resolved up front,
        regardless of which branch is exercised — an unknown code fails
        immediately (G6), not only when it happens to be selected."""
        store = FakeTenantStore()
        identity = DeploymentIdentityConfig(
            writable_tenants=frozenset({"no-such-tenant"}), global_admin=False
        )
        with pytest.raises(ConfigurationError, match="unknown tenant code"):
            resolve_run_principal(store, identity)


class TestEnforceTenantIsolation:
    def test_matching_tenant_is_a_noop(self) -> None:
        principal = WritePrincipal(id=None, tenant_id=DEFAULT_TENANT_ID)
        enforce_tenant_isolation(
            principal=principal,
            target_tenant_id=DEFAULT_TENANT_ID,
            audit_log_store=None,
            event_type=AuditEventType.MODEL_REJECTED,
            target_type="x",
            target_id="1",
            detail=None,
            now=_NOW,
        )  # does not raise

    def test_none_principal_is_a_noop(self) -> None:
        enforce_tenant_isolation(
            principal=None,
            target_tenant_id=DEFAULT_TENANT_ID,
            audit_log_store=None,
            event_type=AuditEventType.MODEL_REJECTED,
            target_type="x",
            target_id="1",
            detail=None,
            now=_NOW,
        )  # does not raise

    def test_unscoped_global_admin_is_a_noop(self) -> None:
        principal = WritePrincipal(id=None, tenant_id=None)
        enforce_tenant_isolation(
            principal=principal,
            target_tenant_id=_TENANT_B_ID,
            audit_log_store=None,
            event_type=AuditEventType.MODEL_REJECTED,
            target_type="x",
            target_id="1",
            detail=None,
            now=_NOW,
        )  # does not raise

    def test_mismatched_tenant_raises_and_persists_rejection(self) -> None:
        principal = WritePrincipal(id=PrincipalId("ops"), tenant_id=DEFAULT_TENANT_ID)
        audit = FakeAuditLogStore()
        with pytest.raises(TenantIsolationError, match=str(DEFAULT_TENANT_ID)):
            enforce_tenant_isolation(
                principal=principal,
                target_tenant_id=_TENANT_B_ID,
                audit_log_store=audit,
                event_type=AuditEventType.MODEL_REJECTED,
                target_type="model_artifact",
                target_id="artifact-1",
                detail={"model_id": "m1"},
                now=_NOW,
            )
        assert len(audit._entries) == 1  # type: ignore[attr-defined]
        entry = audit._entries[0]  # type: ignore[attr-defined]
        assert entry.event_type == AuditEventType.MODEL_REJECTED
        assert entry.detail["operator"] == PrincipalId("ops")
        assert entry.detail["outcome"] == "rejected_tenant_mismatch"


class TestTargetTenantIdForUnit:
    def test_station_scoped_unit_resolves_station_tenant(self) -> None:
        station_store = FakeStationStore()
        station = make_station_config(tenant_id=_TENANT_B_ID)
        station_store.store_station(station)
        unit = make_training_unit(station_id=station.id)

        result = target_tenant_id_for_unit(unit, station_store, FakeStationGroupStore())
        assert result == _TENANT_B_ID

    def test_missing_station_returns_none(self) -> None:
        station_store = FakeStationStore()
        unit = make_training_unit()

        result = target_tenant_id_for_unit(unit, station_store, FakeStationGroupStore())
        assert result is None
