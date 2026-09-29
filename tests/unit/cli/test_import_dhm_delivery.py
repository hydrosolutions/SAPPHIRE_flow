from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from sapphire_flow.adapters.dhm_files import parse_daily_flow, parse_rating_tables
from sapphire_flow.cli.import_dhm_delivery import (
    DELIVERY_ID,
    _require_chwrr_identity,
    _safe_failure_reason,
    load_station_metadata,
    register_stations,
    replace_delivery,
    run_delivery_qc,
)
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AuditActorType, AuditEventType
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.fakes.fake_stores import (
    FakeAuditLogStore,
    FakeObservationStore,
    FakeRatingCurveStore,
    FakeStationStore,
    FakeTenantStore,
)

METADATA_PATH = Path(__file__).resolve().parents[2] / "fixtures/dhm/stations.toml"
NOW = ensure_utc(datetime(2026, 9, 28, tzinfo=UTC))
ADMIN = DeploymentIdentityConfig(writable_tenants=frozenset(), global_admin=True)
CHWRR = DeploymentIdentityConfig(
    writable_tenants=frozenset({"chwrr"}), global_admin=False
)


def _ensure_chwrr(tenants: FakeTenantStore) -> TenantId:
    return tenants.ensure_tenant(
        tenant_id=TenantId(uuid4()), code="chwrr", name="CHWRR Nepal"
    ).id


def test_unexpected_cli_failure_redacts_database_parameters() -> None:
    reason = _safe_failure_reason(RuntimeError("SQL parameters contained 123.45"))
    assert reason == "RuntimeError; details withheld to protect delivered values"


def test_station_registration_requires_chwrr_write_authority() -> None:
    tenants = FakeTenantStore()
    stations = FakeStationStore()
    _ensure_chwrr(tenants)
    swiss_identity = DeploymentIdentityConfig(
        writable_tenants=frozenset({"sapphire"}), global_admin=False
    )
    with pytest.raises(ConfigurationError, match="CHWRR-scoped write authority"):
        register_stations(
            tenants,
            stations,
            swiss_identity,
            load_station_metadata(METADATA_PATH),
            audit_log_store=FakeAuditLogStore(),
            now=NOW,
        )
    assert stations.fetch_all_stations() == []


def test_station_registration_rejects_global_admin_routine_write() -> None:
    tenants = FakeTenantStore()
    stations = FakeStationStore()
    _ensure_chwrr(tenants)
    with pytest.raises(ConfigurationError, match="CHWRR-scoped write authority"):
        register_stations(
            tenants,
            stations,
            ADMIN,
            load_station_metadata(METADATA_PATH),
            audit_log_store=FakeAuditLogStore(),
            now=NOW,
        )
    assert stations.fetch_all_stations() == []


def test_station_registration_reuses_six_rows_without_duplicates() -> None:
    tenants = FakeTenantStore()
    stations = FakeStationStore()
    tenant_id = _ensure_chwrr(tenants)
    identity = DeploymentIdentityConfig(
        writable_tenants=frozenset({"chwrr"}), global_admin=False
    )
    metadata = load_station_metadata(METADATA_PATH)
    assert (
        register_stations(
            tenants,
            stations,
            identity,
            metadata,
            audit_log_store=FakeAuditLogStore(),
            now=NOW,
        )
        == 6
    )
    assert (
        register_stations(
            tenants,
            stations,
            identity,
            metadata,
            audit_log_store=FakeAuditLogStore(),
            now=NOW,
        )
        == 0
    )
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
    _ensure_chwrr(tenants)
    identity = DeploymentIdentityConfig(
        writable_tenants=frozenset({"chwrr"}), global_admin=False
    )
    metadata = load_station_metadata(METADATA_PATH)
    register_stations(
        tenants,
        stations,
        identity,
        metadata,
        audit_log_store=FakeAuditLogStore(),
        now=NOW,
    )
    foreign = stations.fetch_station_by_code("447", "dhm")
    assert foreign is not None
    other = FakeStationStore()
    other.store_station(replace(foreign, tenant_id=DEFAULT_TENANT_ID))
    with pytest.raises(ConfigurationError, match="another tenant"):
        register_stations(
            tenants,
            other,
            identity,
            metadata,
            audit_log_store=FakeAuditLogStore(),
            now=NOW,
        )
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


def _files_for(metadata: object) -> dict[str, tuple[object, object]]:
    daily = parse_daily_flow(
        (METADATA_PATH.parent / "synthetic_daily_flow.txt").read_text()
    )
    rating = parse_rating_tables(
        (METADATA_PATH.parent / "synthetic_rating_tables.txt").read_text()
    )
    return {
        spec.code: (
            replace(daily, station_code=spec.code),
            replace(rating, station_code=spec.code),
        )
        for spec in metadata.stations  # type: ignore[attr-defined]
    }


class TestImportAuditOnSuccess:
    def _seeded(self) -> tuple[FakeTenantStore, FakeStationStore, TenantId]:
        tenants = FakeTenantStore()
        stations = FakeStationStore()
        tenant_id = _ensure_chwrr(tenants)
        return tenants, stations, tenant_id

    def test_stations_writes_one_system_row_with_counts_only(self) -> None:
        tenants, stations, tenant_id = self._seeded()
        audit = FakeAuditLogStore()
        register_stations(
            tenants,
            stations,
            CHWRR,
            load_station_metadata(METADATA_PATH),
            audit_log_store=audit,
            now=NOW,
        )
        (entry,) = audit.entries
        assert entry.event_type is AuditEventType.DELIVERY_IMPORTED
        assert entry.actor_type is AuditActorType.SYSTEM
        assert (entry.target_type, entry.target_id) == ("tenant", str(tenant_id))
        assert entry.detail == {
            "command": "stations",
            "delivery_id": DELIVERY_ID,
            "created": 6,
            "declared": 6,
        }

    def test_a_repeat_run_still_records_that_it_ran(self) -> None:
        tenants, stations, _ = self._seeded()
        audit = FakeAuditLogStore()
        metadata = load_station_metadata(METADATA_PATH)
        for _ in range(2):
            register_stations(
                tenants, stations, CHWRR, metadata, audit_log_store=audit, now=NOW
            )
        assert [entry.detail["created"] for entry in audit.entries] == [6, 0]

    def test_replace_writes_one_row_with_counts_only(self) -> None:
        tenants, stations, tenant_id = self._seeded()
        audit = FakeAuditLogStore()
        metadata = load_station_metadata(METADATA_PATH)
        register_stations(
            tenants,
            stations,
            CHWRR,
            metadata,
            audit_log_store=FakeAuditLogStore(),
            now=NOW,
        )
        replace_delivery(
            tenants,
            stations,
            FakeRatingCurveStore(),
            FakeObservationStore(),
            CHWRR,
            _files_for(metadata),  # type: ignore[arg-type]
            audit_log_store=audit,
            now=NOW,
        )
        (entry,) = audit.entries
        assert entry.target_id == str(tenant_id)
        assert entry.detail == {
            "command": "replace",
            "delivery_id": DELIVERY_ID,
            "stations": 6,
            "curves": 12,
            "observations": 18,
        }

    def test_qc_writes_one_row_with_status_counts(self) -> None:
        tenants, stations, _ = self._seeded()
        observations = FakeObservationStore()
        metadata = load_station_metadata(METADATA_PATH)
        register_stations(
            tenants,
            stations,
            CHWRR,
            metadata,
            audit_log_store=FakeAuditLogStore(),
            now=NOW,
        )
        replace_delivery(
            tenants,
            stations,
            FakeRatingCurveStore(),
            observations,
            CHWRR,
            _files_for(metadata),  # type: ignore[arg-type]
            audit_log_store=FakeAuditLogStore(),
            now=NOW,
        )
        audit = FakeAuditLogStore()
        run_delivery_qc(
            tenants,
            stations,
            observations,
            CHWRR,
            Path(__file__).resolve().parents[3] / "config.toml",
            audit_log_store=audit,
            now=NOW,
        )
        (entry,) = audit.entries
        assert entry.detail == {
            "command": "qc",
            "delivery_id": DELIVERY_ID,
            "rows_evaluated": 18,
            "statuses": {"qc_passed": 12, "qc_unchecked": 6},
        }

    def test_a_refused_command_writes_no_audit_row(self) -> None:
        tenants, stations, _ = self._seeded()
        audit = FakeAuditLogStore()
        with pytest.raises(ConfigurationError, match="CHWRR-scoped"):
            register_stations(
                tenants,
                stations,
                ADMIN,
                load_station_metadata(METADATA_PATH),
                audit_log_store=audit,
                now=NOW,
            )
        assert audit.entries == []


class TestTenantCreationIsNotAnOperatorCommand:
    def test_the_bootstrap_tenant_subcommand_is_not_offered(self) -> None:
        from sapphire_flow.cli.import_dhm_delivery import _parser

        with pytest.raises(SystemExit) as exit_info:
            _parser().parse_args(["bootstrap-tenant", "--tenant", "chwrr"])
        assert exit_info.value.code == 2

    def test_main_refuses_the_retired_subcommand(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.cli.import_dhm_delivery import main

        monkeypatch.setenv("SAPPHIRE_CONFIG", "/nonexistent/config.toml")
        with pytest.raises(SystemExit) as exit_info:
            main(["bootstrap-tenant", "--tenant", "chwrr"])
        assert exit_info.value.code == 2

    def test_the_tenant_creating_code_paths_are_gone(self) -> None:
        from sapphire_flow.cli import import_dhm_delivery

        assert not hasattr(import_dhm_delivery, "bootstrap_tenant")
        assert not hasattr(import_dhm_delivery, "_require_admin_bootstrap_identity")

    def test_a_missing_tenant_error_says_it_is_created_at_deploy_time(self) -> None:
        with pytest.raises(ConfigurationError, match="deploy time"):
            register_stations(
                FakeTenantStore(),
                FakeStationStore(),
                CHWRR,
                load_station_metadata(METADATA_PATH),
                audit_log_store=FakeAuditLogStore(),
                now=NOW,
            )
