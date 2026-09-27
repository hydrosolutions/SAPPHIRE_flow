from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

from sapphire_flow.config.onboarding import StationQcThresholdSpec
from sapphire_flow.services.station_qc_overrides import (
    is_ingest_qc_applicable,
    resolve_station_qc_overrides,
)
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import GaugingStatus, StationStatus
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.station_qc import StationQcRejectionKind as Reason
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.conftest import make_station_config

_STEP = timedelta(minutes=10)
_RULES = QcRuleSet(
    version="test",
    rules=(
        QcRuleParams(
            rule_id="range_check",
            rule_version="generic",
            parameter="water_level",
            time_step=_STEP,
            thresholds={"value_min": -5.0, "value_max": 30.0},
        ),
        QcRuleParams(
            rule_id="range_check",
            rule_version="dhm",
            parameter="water_level",
            time_step=_STEP,
            thresholds={"value_min": -2.0, "value_max": 20.0},
            network="dhm",
        ),
    ),
)


def _spec(**changes: object) -> StationQcThresholdSpec:
    base = StationQcThresholdSpec(
        tenant_code="sapphire",
        code="2135",
        network="bafu",
        rule_id="range_check",
        parameter="water_level",
        time_step=_STEP,
        thresholds={"value_max": 25.0},
    )
    return replace(base, **changes)


def test_swiss_water_level_is_applicable_despite_measured_parameters() -> None:
    station = make_station_config(code="2135", water_level_datum_masl=260.0)
    result = resolve_station_qc_overrides(
        [_spec()],
        [station],
        _RULES,
        is_ingest_qc_applicable,
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert len(result.overrides) == 1
    assert result.overrides[0].thresholds == {"value_max": 25.0}
    assert result.rejected == ()


def test_calculated_station_is_not_applicable() -> None:
    station = make_station_config(
        code="2135",
        gauging_status=GaugingStatus.CALCULATED,
        water_level_datum_masl=260.0,
    )
    result = resolve_station_qc_overrides(
        [_spec()],
        [station],
        _RULES,
        is_ingest_qc_applicable,
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert result.not_applicable == (_spec(),)
    assert result.overrides == ()


def test_datum_skipped_rule_is_not_applicable() -> None:
    station = make_station_config(code="2135", water_level_datum_masl=None)
    result = resolve_station_qc_overrides(
        [_spec()],
        [station],
        _RULES,
        is_ingest_qc_applicable,
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert result.not_applicable == (_spec(),)


def test_wrong_tenant_is_nonwaivable_even_when_target_tenant_missing() -> None:
    station = make_station_config(code="2135")
    result = resolve_station_qc_overrides(
        [_spec(tenant_code="dhm")],
        [station],
        _RULES,
        is_ingest_qc_applicable,
        tenant_id=None,
    )
    assert result.rejected[0].reasons == (
        Reason.TENANT_NOT_FOUND,
        Reason.TENANT_MISMATCH,
    )


def test_rule_error_is_reported_when_station_is_missing() -> None:
    result = resolve_station_qc_overrides(
        [_spec(code="missing", rule_id="typo")],
        [],
        _RULES,
        is_ingest_qc_applicable,
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert result.rejected[0].reasons == (
        Reason.STATION_NOT_FOUND,
        Reason.RULE_NOT_FOUND,
    )


def test_independent_parameter_and_cadence_errors_are_retained() -> None:
    result = resolve_station_qc_overrides(
        [_spec(code="missing", parameter="temperature", time_step=timedelta(days=1))],
        [],
        _RULES,
        is_ingest_qc_applicable,
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert result.rejected[0].reasons == (
        Reason.STATION_NOT_FOUND,
        Reason.PARAMETER_MISMATCH,
        Reason.CADENCE_MISMATCH,
    )


def test_network_replacement_and_merged_bounds() -> None:
    tenant = TenantId(UUID("00000000-0000-0000-0000-000000000002"))
    station = make_station_config(
        code="447", network="dhm", tenant_id=tenant, water_level_datum_masl=100.0
    )
    valid = resolve_station_qc_overrides(
        [_spec(code="447", network="dhm", thresholds={"value_max": 19.0})],
        [station],
        _RULES,
        is_ingest_qc_applicable,
        tenant_id=tenant,
    )
    assert len(valid.overrides) == 1
    invalid = resolve_station_qc_overrides(
        [_spec(code="447", network="dhm", thresholds={"value_min": 21.0})],
        [station],
        _RULES,
        is_ingest_qc_applicable,
        tenant_id=tenant,
    )
    assert invalid.rejected[0].reasons == (Reason.MERGED_INVALID,)


def test_onboarding_station_is_not_applicable_at_pure_boundary() -> None:
    station = make_station_config(
        code="2135",
        station_status=StationStatus.ONBOARDING,
        water_level_datum_masl=100.0,
    )
    result = resolve_station_qc_overrides(
        [_spec()],
        [station],
        _RULES,
        is_ingest_qc_applicable,
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert result.not_applicable == (_spec(),)


def test_spike_mode_change_is_rejected() -> None:
    station = make_station_config(code="2135")
    rules = QcRuleSet(
        version="test",
        rules=(
            QcRuleParams(
                rule_id="spike",
                rule_version="1",
                parameter="discharge",
                time_step=_STEP,
                thresholds={"tolerance": 0.5},
            ),
        ),
    )
    result = resolve_station_qc_overrides(
        [
            _spec(
                rule_id="spike",
                parameter="discharge",
                thresholds={"max_delta": 2.0},
            )
        ],
        [station],
        rules,
        is_ingest_qc_applicable,
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert result.rejected[0].reasons == (Reason.MODE_MISMATCH,)


def test_two_rule_versions_report_fan_out() -> None:
    station = make_station_config(code="2135")
    rules = QcRuleSet(
        version="test",
        rules=tuple(
            QcRuleParams(
                rule_id="frozen_sensor",
                rule_version=version,
                parameter="discharge",
                time_step=_STEP,
                thresholds={"tolerance": 0.1, "min_consecutive": 12.0},
            )
            for version in ("1", "2")
        ),
    )
    result = resolve_station_qc_overrides(
        [
            _spec(
                rule_id="frozen_sensor",
                parameter="discharge",
                thresholds={"tolerance": 0.2},
            )
        ],
        [station],
        rules,
        is_ingest_qc_applicable,
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert len(result.overrides) == 1
    assert result.fan_out[0].rule_versions == ("1", "2")


def test_missing_tenant_does_not_hide_bad_rule() -> None:
    result = resolve_station_qc_overrides(
        [_spec(code="missing", rule_id="typo")],
        [],
        _RULES,
        is_ingest_qc_applicable,
        tenant_id=None,
    )
    assert result.rejected[0].reasons == (
        Reason.TENANT_NOT_FOUND,
        Reason.RULE_NOT_FOUND,
    )
