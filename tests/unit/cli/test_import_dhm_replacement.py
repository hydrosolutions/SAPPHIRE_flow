from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from sapphire_flow.adapters.dhm_files import parse_daily_flow, parse_rating_tables
from sapphire_flow.adapters.nepal_local_day import nepal_day_start
from sapphire_flow.cli.import_dhm_delivery import (
    DELIVERY_ID,
    _local_day_segments,
    _resolve_delivery_qc,
    bootstrap_tenant,
    load_station_metadata,
    register_stations,
    replace_delivery,
    run_delivery_qc,
)
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import RatingCurveId, StationId, TenantId
from sapphire_flow.types.observation import RawObservation
from tests.fakes.fake_stores import (
    FakeAuditLogStore,
    FakeObservationStore,
    FakeRatingCurveStore,
    FakeStationStore,
    FakeTenantStore,
)

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/dhm"
_NOW = ensure_utc(datetime(2026, 9, 28, tzinfo=UTC))
_ADMIN = DeploymentIdentityConfig(writable_tenants=frozenset(), global_admin=True)
_CHWRR = DeploymentIdentityConfig(
    writable_tenants=frozenset({"chwrr"}), global_admin=False
)


def test_boundary_replacement_removes_only_delivery_rows() -> None:
    tenants = FakeTenantStore()
    stations = FakeStationStore()
    observations = FakeObservationStore()
    ratings = FakeRatingCurveStore()
    bootstrap_tenant(tenants, _ADMIN, tenant_code="chwrr", now=_NOW)
    metadata = load_station_metadata(_FIXTURES / "stations.toml")
    register_stations(
        tenants,
        stations,
        _CHWRR,
        metadata,
        audit_log_store=FakeAuditLogStore(),
        now=_NOW,
    )
    daily = parse_daily_flow((_FIXTURES / "synthetic_daily_flow.txt").read_text())
    rating = parse_rating_tables(
        (_FIXTURES / "synthetic_rating_tables.txt").read_text()
    )
    files = {
        spec.code: (
            replace(daily, station_code=spec.code),
            replace(rating, station_code=spec.code),
        )
        for spec in metadata.stations
    }
    station = stations.fetch_station_by_code("447", "dhm")
    assert station is not None
    unrelated = RawObservation(
        station_id=station.id,
        timestamp=ensure_utc(datetime(1995, 1, 1, tzinfo=UTC)),
        parameter="discharge",
        value=5.0,
        source=ObservationSource.MANUAL_IMPORT,
    )
    observations.store_raw_observations([unrelated])

    assert replace_delivery(
        tenants,
        stations,
        ratings,
        observations,
        _CHWRR,
        files,
        audit_log_store=FakeAuditLogStore(),
        now=_NOW,
    ) == (12, 18)
    original_times = {
        obs.timestamp
        for obs in observations.fetch_delivery_observations(DELIVERY_ID, [station.id])
    }
    prior_curve = ratings.fetch_delivery_curves(DELIVERY_ID, [station.id])[0]
    unrelated_curve = replace(
        prior_curve,
        id=RatingCurveId(uuid4()),
        version=100,
        delivery_id=None,
        valid_from=ensure_utc(datetime(1990, 1, 1, tzinfo=UTC)),
        valid_to=ensure_utc(datetime(1990, 12, 31, tzinfo=UTC)),
    )
    ratings.store_rating_curve(unrelated_curve)

    def shifted(day: date) -> UtcDatetime:
        return ensure_utc(nepal_day_start(day) + timedelta(hours=1))

    assert replace_delivery(
        tenants,
        stations,
        ratings,
        observations,
        _CHWRR,
        files,
        audit_log_store=FakeAuditLogStore(),
        now=_NOW,
        day_start=shifted,
    ) == (12, 18)
    current = observations.fetch_delivery_observations(DELIVERY_ID, [station.id])
    assert not original_times.intersection({obs.timestamp for obs in current})
    replacement_curves = ratings.fetch_delivery_curves(DELIVERY_ID, [station.id])
    assert [curve.version for curve in replacement_curves] == [101, 102]
    assert unrelated_curve in ratings.fetch_all_curves_for_station(station.id)
    assert any(obs.delivery_id is None for obs in observations.observations())


def test_delivery_qc_checks_only_tagged_rows() -> None:
    tenants = FakeTenantStore()
    stations = FakeStationStore()
    observations = FakeObservationStore()
    ratings = FakeRatingCurveStore()
    bootstrap_tenant(tenants, _ADMIN, tenant_code="chwrr", now=_NOW)
    metadata = load_station_metadata(_FIXTURES / "stations.toml")
    register_stations(
        tenants,
        stations,
        _CHWRR,
        metadata,
        audit_log_store=FakeAuditLogStore(),
        now=_NOW,
    )
    daily = parse_daily_flow((_FIXTURES / "synthetic_daily_flow.txt").read_text())
    rating = parse_rating_tables(
        (_FIXTURES / "synthetic_rating_tables.txt").read_text()
    )
    files = {
        spec.code: (
            replace(daily, station_code=spec.code),
            replace(rating, station_code=spec.code),
        )
        for spec in metadata.stations
    }
    station = stations.fetch_station_by_code("447", "dhm")
    assert station is not None
    observations.store_raw_observations(
        [
            RawObservation(
                station_id=station.id,
                timestamp=ensure_utc(datetime(1990, 1, 1, tzinfo=UTC)),
                parameter="discharge",
                value=5.0,
                source=ObservationSource.MANUAL_IMPORT,
            )
        ]
    )
    replace_delivery(
        tenants,
        stations,
        ratings,
        observations,
        _CHWRR,
        files,
        audit_log_store=FakeAuditLogStore(),
        now=_NOW,
    )
    config_path = Path(__file__).resolve().parents[3] / "config.toml"
    tenant = tenants.fetch_tenant_by_code("chwrr")
    assert tenant is not None
    rules, overrides = _resolve_delivery_qc(
        config_path, stations.fetch_all_stations(), tenant.id
    )
    by_station_id = {
        station.id: station.code for station in stations.fetch_all_stations()
    }
    assert {
        by_station_id[override.station_id]: override.thresholds["value_max"]
        for override in overrides
    } == {
        "447": 20000.0,
        "450": 100000.0,
        "604.5": 25000.0,
        "647": 10000.0,
        "670": 50000.0,
        "684": 20000.0,
    }
    assert {
        rule.rule_id
        for rule in rules.rules_for("discharge", timedelta(days=1), network="dhm")
    } == {"range_check", "rate_of_change", "spike", "gross_outlier"}
    counts = run_delivery_qc(
        tenants,
        stations,
        observations,
        _CHWRR,
        config_path,
        audit_log_store=FakeAuditLogStore(),
        now=_NOW,
    )
    assert counts == {QcStatus.QC_PASSED: 12, QcStatus.QC_UNCHECKED: 6}
    assert (
        next(
            row for row in observations.observations() if row.delivery_id is None
        ).qc_status
        is QcStatus.RAW
    )


def test_local_day_segments_do_not_split_at_the_1986_clock_change() -> None:
    station_id = StationId(uuid4())
    store = FakeObservationStore()
    days = (
        date(1985, 12, 31),
        date(1986, 1, 1),
        date(1986, 1, 2),
        date(1986, 1, 4),
    )
    store.store_raw_observations(
        [
            RawObservation(
                station_id=station_id,
                timestamp=nepal_day_start(day),
                parameter="discharge",
                value=1.0,
                source=ObservationSource.MANUAL_IMPORT,
                delivery_id=DELIVERY_ID,
            )
            for day in days
        ]
    )
    assert [len(segment) for segment in _local_day_segments(store.observations())] == [
        3,
        1,
    ]


def test_qc_refuses_missing_explicit_rules(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("[onboarding]\n")
    with pytest.raises(ConfigurationError, match="explicit configured rules"):
        _resolve_delivery_qc(config, [], TenantId(uuid4()))


def test_qc_refuses_config_without_dhm_rule_rows(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    original = (Path(__file__).resolve().parents[3] / "config.toml").read_text()
    onboarding, qc_rules = original.split("# QC rules", 1)
    config.write_text(
        onboarding
        + "# QC rules"
        + qc_rules.replace('network = "dhm"', 'network = "other"')
    )
    with pytest.raises(ConfigurationError, match="DHM QC rules differ"):
        _resolve_delivery_qc(config, [], TenantId(uuid4()))


def test_qc_refuses_unresolved_station_ceiling() -> None:
    tenants = FakeTenantStore()
    stations = FakeStationStore()
    tenant_id = bootstrap_tenant(tenants, _ADMIN, tenant_code="chwrr", now=_NOW)
    register_stations(
        tenants,
        stations,
        _CHWRR,
        load_station_metadata(_FIXTURES / "stations.toml"),
        audit_log_store=FakeAuditLogStore(),
        now=_NOW,
    )
    partial = [
        station for station in stations.fetch_all_stations() if station.code != "447"
    ]
    config = Path(__file__).resolve().parents[3] / "config.toml"
    with pytest.raises(ConfigurationError, match="did not resolve one-to-one"):
        _resolve_delivery_qc(config, partial, tenant_id)
