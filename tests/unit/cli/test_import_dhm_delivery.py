from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sapphire_flow.cli.import_dhm_delivery import (
    _require_admin_bootstrap_identity,
    _require_chwrr_identity,
    _safe_failure_reason,
    bootstrap_tenant,
    load_station_metadata,
    register_stations,
)
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.fakes.fake_stores import FakeStationStore, FakeTenantStore

METADATA_PATH = Path(__file__).resolve().parents[2] / "fixtures/dhm/stations.toml"
NOW = ensure_utc(datetime(2026, 9, 28, tzinfo=UTC))
ADMIN = DeploymentIdentityConfig(writable_tenants=frozenset(), global_admin=True)


def test_tenant_bootstrap_is_idempotent() -> None:
    tenants = FakeTenantStore()
    first = bootstrap_tenant(tenants, ADMIN, tenant_code="chwrr", now=NOW)
    second = bootstrap_tenant(tenants, ADMIN, tenant_code="chwrr", now=NOW)
    assert first == second
    assert tenants.fetch_tenant_by_code("chwrr").name == "CHWRR Nepal"


def test_unexpected_cli_failure_redacts_database_parameters() -> None:
    reason = _safe_failure_reason(RuntimeError("SQL parameters contained 123.45"))
    assert reason == "RuntimeError; details withheld to protect delivered values"


def test_tenant_bootstrap_refuses_swiss_identity() -> None:
    tenants = FakeTenantStore()
    swiss_identity = DeploymentIdentityConfig(
        writable_tenants=frozenset({"sapphire"}), global_admin=False
    )
    with pytest.raises(ConfigurationError, match="global-admin"):
        bootstrap_tenant(tenants, swiss_identity, tenant_code="chwrr", now=NOW)
    assert tenants.fetch_tenant_by_code("chwrr") is None


def test_station_registration_requires_chwrr_write_authority() -> None:
    tenants = FakeTenantStore()
    stations = FakeStationStore()
    bootstrap_tenant(tenants, ADMIN, tenant_code="chwrr", now=NOW)
    swiss_identity = DeploymentIdentityConfig(
        writable_tenants=frozenset({"sapphire"}), global_admin=False
    )
    with pytest.raises(ConfigurationError, match="CHWRR-scoped write authority"):
        register_stations(
            tenants,
            stations,
            swiss_identity,
            load_station_metadata(METADATA_PATH),
            now=NOW,
        )
    assert stations.fetch_all_stations() == []


def test_station_registration_rejects_global_admin_routine_write() -> None:
    tenants = FakeTenantStore()
    stations = FakeStationStore()
    bootstrap_tenant(tenants, ADMIN, tenant_code="chwrr", now=NOW)
    with pytest.raises(ConfigurationError, match="CHWRR-scoped write authority"):
        register_stations(
            tenants,
            stations,
            ADMIN,
            load_station_metadata(METADATA_PATH),
            now=NOW,
        )
    assert stations.fetch_all_stations() == []


def test_station_registration_reuses_six_rows_without_duplicates() -> None:
    tenants = FakeTenantStore()
    stations = FakeStationStore()
    tenant_id = bootstrap_tenant(tenants, ADMIN, tenant_code="chwrr", now=NOW)
    identity = DeploymentIdentityConfig(
        writable_tenants=frozenset({"chwrr"}), global_admin=False
    )
    metadata = load_station_metadata(METADATA_PATH)
    assert register_stations(tenants, stations, identity, metadata, now=NOW) == 6
    assert register_stations(tenants, stations, identity, metadata, now=NOW) == 0
    assert len(stations.fetch_all_stations()) == 6
    assert {station.tenant_id for station in stations.fetch_all_stations()} == {
        tenant_id
    }
    assert all(
        station.forecast_targets is None for station in stations.fetch_all_stations()
    )


def test_cross_tenant_same_code_is_refused_before_any_insert() -> None:
    tenants = FakeTenantStore()
    stations = FakeStationStore()
    bootstrap_tenant(tenants, ADMIN, tenant_code="chwrr", now=NOW)
    identity = DeploymentIdentityConfig(
        writable_tenants=frozenset({"chwrr"}), global_admin=False
    )
    metadata = load_station_metadata(METADATA_PATH)
    register_stations(tenants, stations, identity, metadata, now=NOW)
    foreign = stations.fetch_station_by_code("447", "dhm")
    assert foreign is not None
    other = FakeStationStore()
    other.store_station(replace(foreign, tenant_id=DEFAULT_TENANT_ID))
    with pytest.raises(ConfigurationError, match="another tenant"):
        register_stations(tenants, other, identity, metadata, now=NOW)
    assert len(other.fetch_all_stations()) == 1


def test_chwrr_overlay_may_only_change_write_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[deployment]\nwritable_tenants = ["sapphire"]\n')
    overlay = tmp_path / "config/overlays/chwrr-import.toml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text('[deployment]\nwritable_tenants = ["chwrr"]\n')
    monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", str(overlay))
    assert _require_chwrr_identity(config).writable_tenants == frozenset({"chwrr"})
    overlay.write_text(
        '[deployment]\nwritable_tenants = ["chwrr"]\n[qc_rules]\nversion = "x"\n'
    )
    with pytest.raises(ConfigurationError, match="checked-in CHWRR"):
        _require_chwrr_identity(config)


def test_bootstrap_requires_out_of_checkout_admin_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    config = checkout / "config.toml"
    config.write_text('[deployment]\nwritable_tenants = ["sapphire"]\n')
    overlay = tmp_path / "admin.toml"
    overlay.write_text("[deployment]\nglobal_admin = true\nwritable_tenants = []\n")
    monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", str(overlay))
    _require_admin_bootstrap_identity(config)
    monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", str(checkout / "admin.toml"))
    (checkout / "admin.toml").write_text(overlay.read_text())
    with pytest.raises(ConfigurationError, match="outside the checkout"):
        _require_admin_bootstrap_identity(config)
