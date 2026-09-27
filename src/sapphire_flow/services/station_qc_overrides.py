from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.services.qc_datum import obs_skipped_rules
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet, StationQcOverride
from sapphire_flow.types.enums import GaugingStatus, StationKind, StationStatus
from sapphire_flow.types.station_qc import (
    Resolution,
    StationQcFanOut,
    StationQcRejection,
)
from sapphire_flow.types.station_qc import (
    StationQcRejectionKind as Reason,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sapphire_flow.config.onboarding import StationQcThresholdSpec
    from sapphire_flow.types.ids import TenantId
    from sapphire_flow.types.station import StationConfig


def is_ingest_station_judged(station: StationConfig) -> bool:
    return station.station_status == StationStatus.OPERATIONAL and (
        station.station_kind == StationKind.WEATHER
        or station.gauging_status == GaugingStatus.GAUGED
    )


def is_ingest_qc_applicable(
    station: StationConfig, rule_id: str, parameter: str
) -> bool:
    return is_ingest_station_judged(station) and rule_id not in obs_skipped_rules(
        parameter, station.water_level_datum_masl
    )


def _selected_rules(
    spec: StationQcThresholdSpec, rule_set: QcRuleSet
) -> tuple[QcRuleParams, ...]:
    return tuple(
        rule
        for rule in rule_set.rules_for(
            spec.parameter, spec.time_step, network=spec.network
        )
        if rule.rule_id == spec.rule_id
    )


def _rule_reasons(
    spec: StationQcThresholdSpec,
    rule_set: QcRuleSet,
    selected: tuple[QcRuleParams, ...],
) -> list[Reason]:
    candidates = [
        rule
        for rule in rule_set.rules
        if rule.rule_id == spec.rule_id and rule.network in (None, spec.network)
    ]
    if not candidates:
        return [Reason.RULE_NOT_FOUND]
    reasons: list[Reason] = []
    if not any(rule.parameter == spec.parameter for rule in candidates):
        reasons.append(Reason.PARAMETER_MISMATCH)
    if not any(rule.time_step == spec.time_step for rule in candidates):
        reasons.append(Reason.CADENCE_MISMATCH)
    if not selected:
        return reasons or [Reason.CADENCE_MISMATCH]
    for rule in selected:
        if spec.rule_id == "spike" and (
            ("max_delta" in spec.thresholds and "tolerance" in rule.thresholds)
            or ("tolerance" in spec.thresholds and "max_delta" in rule.thresholds)
        ):
            reasons.append(Reason.MODE_MISMATCH)
        merged = rule.thresholds | spec.thresholds
        if (
            merged.get("value_min", float("-inf"))
            > merged.get("value_max", float("inf"))
            or any(
                merged[key] < 0
                for key in ("max_rate", "max_delta", "tolerance", "k_sigma")
                if key in merged
            )
            or (
                "min_consecutive" in merged
                and (
                    merged["min_consecutive"] <= 0
                    or not float(merged["min_consecutive"]).is_integer()
                )
            )
        ):
            reasons.append(Reason.MERGED_INVALID)
    return list(dict.fromkeys(reasons))


def resolve_station_qc_overrides(
    specs: Sequence[StationQcThresholdSpec],
    stations: Sequence[StationConfig],
    rule_set: QcRuleSet,
    is_applicable: Callable[[StationConfig, str, str], bool],
    *,
    tenant_id: TenantId | None,
) -> Resolution:
    by_key = {(station.network, station.code): station for station in stations}
    overrides: list[StationQcOverride] = []
    rejected: list[StationQcRejection] = []
    not_applicable: list[StationQcThresholdSpec] = []
    fan_out: list[StationQcFanOut] = []

    for spec in specs:
        station = by_key.get((spec.network, spec.code))
        reasons: list[Reason] = []
        if tenant_id is None:
            reasons.append(Reason.TENANT_NOT_FOUND)
        if station is not None and station.tenant_id != tenant_id:
            reasons.append(Reason.TENANT_MISMATCH)
        elif station is None and tenant_id is not None:
            reasons.append(Reason.STATION_NOT_FOUND)

        selected = _selected_rules(spec, rule_set)
        reasons.extend(_rule_reasons(spec, rule_set, selected))
        if reasons:
            rejected.append(
                StationQcRejection(spec=spec, reasons=tuple(dict.fromkeys(reasons)))
            )
            continue
        if station is None:
            raise AssertionError("resolved station unexpectedly absent")
        if not is_applicable(station, spec.rule_id, spec.parameter):
            not_applicable.append(spec)
            continue
        overrides.append(
            StationQcOverride(
                station_id=station.id,
                rule_id=spec.rule_id,
                parameter=spec.parameter,
                time_step=spec.time_step,
                thresholds=dict(spec.thresholds),
            )
        )
        if len(selected) > 1:
            fan_out.append(
                StationQcFanOut(
                    spec=spec,
                    rule_versions=tuple(rule.rule_version for rule in selected),
                )
            )
    return Resolution(
        overrides=tuple(overrides),
        rejected=tuple(rejected),
        not_applicable=tuple(not_applicable),
        fan_out=tuple(fan_out),
    )
