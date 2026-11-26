from __future__ import annotations

from uuid import uuid4

import pytest

from sapphire_flow.exceptions import ConfigurationError, TenantIsolationError
from sapphire_flow.services.station_ingest_pause import set_station_ingest_paused
from sapphire_flow.types.enums import AuditEventType, StationStatus
from sapphire_flow.types.ids import PrincipalId, TenantId
from sapphire_flow.types.write_principal import WritePrincipal
from tests.fakes.fake_stores import FakeAuditLogStore
from tests.unit.adapters.test_dhm import NOW, station
from tests.unit.flows.test_ingest_observations_dhm import stations


def _set(store, config, audit, paused=True, reason="gauge destroyed", principal=None):  # noqa: ANN001, ANN202
    return set_station_ingest_paused(
        station_id=config.id,
        paused=paused,
        reason=reason,
        principal=principal or WritePrincipal(id=PrincipalId("ops"), tenant_id=None),
        stations=store,
        audit=audit,
        clock=lambda: NOW,
    )


def test_pause_sets_flag_and_writes_one_audit_row_without_touching_status() -> None:
    config = station()
    store, audit = stations(config), FakeAuditLogStore()
    assert _set(store, config, audit) is True
    stored = store.fetch_station(config.id)
    assert stored is not None
    assert stored.ingest_paused is True
    assert stored.station_status is config.station_status
    (entry,) = audit.entries
    assert entry.event_type is AuditEventType.STATION_INGEST_PAUSE_CHANGED
    assert entry.created_at == NOW
    assert entry.detail is not None
    assert entry.detail["reason"] == "gauge destroyed"
    assert entry.detail["operator"] == "ops"
    assert (entry.detail["from"], entry.detail["to"]) == (False, True)
    assert config.station_status is StationStatus.OPERATIONAL


def test_unpause_and_noop() -> None:
    config = station()
    store, audit = stations(config), FakeAuditLogStore()
    assert _set(store, config, audit, paused=False) is False
    assert audit.entries == []
    _set(store, config, audit)
    assert _set(store, config, audit, paused=False, reason="repaired") is True
    stored = store.fetch_station(config.id)
    assert stored is not None
    assert stored.ingest_paused is False
    assert len(audit.entries) == 2


def test_reason_required_and_nothing_written() -> None:
    config = station()
    store, audit = stations(config), FakeAuditLogStore()
    with pytest.raises(ConfigurationError):
        _set(store, config, audit, reason="  ")
    stored = store.fetch_station(config.id)
    assert stored is not None
    assert stored.ingest_paused is False


def test_other_tenant_is_refused_and_flag_unchanged() -> None:
    config = station()
    store, audit = stations(config), FakeAuditLogStore()
    other = WritePrincipal(id=PrincipalId("ops"), tenant_id=TenantId(uuid4()))
    with pytest.raises(TenantIsolationError):
        _set(store, config, audit, principal=other)
    stored = store.fetch_station(config.id)
    assert stored is not None
    assert stored.ingest_paused is False
