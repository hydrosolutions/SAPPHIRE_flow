"""Plan 540 M4a: read-only station readiness facts (checks 1-10b).

Facts only: one PASS/FAIL/NOT_APPLICABLE line per check, no single verdict.
Nothing here writes. The per-check functions are reusable by later milestones.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sapphire_flow.services.caravan_statics import (
    resolve_available_static_keys_for_stations,
)
from sapphire_flow.services.station_observation_load import days_with_value_set
from sapphire_flow.services.station_observation_qc import USABLE_STATUSES
from sapphire_flow.types.enums import (
    ModelAssignmentStatus,
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.station_readiness import (
    CheckName,
    CheckStatus,
    ReadinessCheck,
    StationReadinessReport,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.stores import (
        BasinStore,
        GatewayPolygonBindingStore,
        HistoricalForcingStore,
        ObservationStore,
        RatingCurveStore,
        StationGroupStore,
        StationStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId
    from sapphire_flow.types.station import StationConfig
    from sapphire_flow.types.station_readiness import (
        ForcingSummaryRow,
        ObservationSummaryRow,
    )

DEFAULT_LOOKBACK_DAYS = 30
_LISTED = 8
_PASS, _FAIL, _NA = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.NOT_APPLICABLE


@dataclass(frozen=True, kw_only=True, slots=True)
class ReadinessStores:
    stations: StationStore
    basins: BasinStore
    observations: ObservationStore
    forcing: HistoricalForcingStore
    ratings: RatingCurveStore
    groups: StationGroupStore
    polygons: GatewayPolygonBindingStore


def _check(name: CheckName, status: CheckStatus, detail: str) -> ReadinessCheck:
    return ReadinessCheck(name=name, status=status, detail=detail)


def _listed(items: Sequence[str]) -> str:
    shown = ", ".join(items[:_LISTED])
    return shown + (
        f", ... (+{len(items) - _LISTED} more)" if len(items) > _LISTED else ""
    )


def _day(ts: UtcDatetime) -> str:
    return ts.date().isoformat()


def check_station(station: StationConfig) -> ReadinessCheck:
    targets = sorted(station.forecast_targets) if station.forecast_targets else "none"
    return _check(
        "station",
        _PASS,
        f"status={station.station_status.value} "
        f"measured={sorted(station.measured_parameters)} targets={targets}",
    )


def check_datum(station: StationConfig) -> ReadinessCheck:
    if "water_level" not in station.measured_parameters:
        return _check("datum", _NA, "water_level is not measured")
    if station.water_level_datum_masl is None:
        return _check("datum", _FAIL, "water_level_datum_masl is not set")
    gauge = station.water_level_gauge_type
    sensor = station.water_level_sensor_masl
    return _check(
        "datum",
        _PASS,
        f"water_level_datum_masl={station.water_level_datum_masl} "
        f"gauge_type={gauge.value if gauge else 'unknown'}"
        + (f" sensor_masl={sensor}" if sensor is not None else ""),
    )


def check_basin(station: StationConfig, basins: BasinStore) -> ReadinessCheck:
    if station.basin_id is None:
        return _check("basin", _FAIL, "no basin assigned")
    if basins.fetch_basin(station.basin_id) is None:
        return _check("basin", _FAIL, f"basin {station.basin_id} not found")
    return _check("basin", _PASS, f"basin_id={station.basin_id}")


def check_statics(
    station: StationConfig, model: ForecastModel | None, stores: ReadinessStores
) -> ReadinessCheck:
    if model is None:
        return _check("statics", _NA, "no --model given")
    declared = sorted(model.data_requirements.static_features)
    available = resolve_available_static_keys_for_stations(
        model, [station.id], station_store=stores.stations, basin_store=stores.basins
    )[station.id]
    missing = [name for name in declared if name not in available]
    if missing:
        return _check(
            "statics",
            _FAIL,
            f"{len(missing)} of {len(declared)} missing: {_listed(missing)}",
        )
    return _check("statics", _PASS, f"all {len(declared)} declared statics resolve")


def check_weather_bindings(
    station: StationConfig, stations: StationStore
) -> ReadinessCheck:
    sources = stations.fetch_weather_sources(station.id)
    parts: list[str] = []
    ok = True
    for role in WeatherSourceRole:
        bound = [s for s in sources if s.role is role]
        active = [s for s in bound if s.status is WeatherSourceStatus.ACTIVE]
        ok = ok and bool(active)
        if active:
            parts.append(f"{role.value}={','.join(s.nwp_source for s in active)}")
        elif bound:
            parts.append(f"{role.value}=inactive({bound[0].nwp_source})")
        else:
            parts.append(f"{role.value}=none")
    return _check("weather_bindings", _PASS if ok else _FAIL, " ".join(parts))


def check_weather_history(
    forcing: Sequence[ForcingSummaryRow], observations: Sequence[ObservationSummaryRow]
) -> ReadinessCheck:
    if not forcing:
        return _check("weather_history", _FAIL, "no historical forcing stored")
    per_param: dict[str, tuple[int, UtcDatetime, UtcDatetime]] = {}
    for row in forcing:
        n, first, last = per_param.get(row.parameter, (0, row.first, row.last))
        per_param[row.parameter] = (
            n + row.count,
            min(first, row.first),
            max(last, row.last),
        )
    text = "; ".join(
        f"{p} n={n} {_day(a)}..{_day(b)}" for p, (n, a, b) in sorted(per_param.items())
    )
    f_first = min(a for _, a, _ in per_param.values())
    f_last = max(b for _, _, b in per_param.values())
    if not observations:
        return _check(
            "weather_history", _PASS, f"{text}; overlap with observations: none"
        )
    o_first = min(r.first for r in observations)
    o_last = max(r.last for r in observations)
    overlap = (min(f_last, o_last) - max(f_first, o_first)).days
    return _check(
        "weather_history",
        _PASS,
        f"{text}; overlap with observations: {max(overlap, 0)} days",
    )


def check_observations(
    observations: Sequence[ObservationSummaryRow], *, now: UtcDatetime
) -> ReadinessCheck:
    if not observations:
        return _check("observations", _FAIL, "no observations stored")
    parts: list[str] = []
    for parameter in sorted({r.parameter for r in observations}):
        rows = [r for r in observations if r.parameter == parameter]
        total = sum(r.count for r in rows)
        last = max(r.last for r in rows)
        shares: dict[str, int] = {}
        for r in rows:
            shares[r.qc_status.value] = shares.get(r.qc_status.value, 0) + r.count
        qc = " ".join(f"{k}={v / total:.0%}" for k, v in sorted(shares.items()))
        versions = sorted({r.qc_rule_version or "none" for r in rows})
        parts.append(
            f"{parameter} n={total} {_day(min(r.first for r in rows))}.."
            f"{_day(last)} newest {(now - last).days}d old; qc {qc}; "
            f"rules {','.join(versions)}"
        )
    return _check("observations", _PASS, "; ".join(parts))


def check_rating_curve(
    station: StationConfig, stores: ReadinessStores
) -> ReadinessCheck:
    targets = station.forecast_targets or frozenset()
    if "discharge" not in targets and "water_level" not in station.measured_parameters:
        return _check("rating_curve", _NA, "discharge not a target")
    curve = stores.ratings.fetch_active_curve(station.id)
    if curve is None:
        return _check("rating_curve", _FAIL, "no active rating curve")
    point_count = len(curve.points)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    return _check(
        "rating_curve",
        _PASS,
        f"version={curve.version} points={point_count} "
        f"valid_from={_day(curve.valid_from)}",
    )


def check_discharge_series(
    observations: Sequence[ObservationSummaryRow],
) -> ReadinessCheck:
    rows = [r for r in observations if r.parameter == "discharge"]
    if not rows:
        return _check("discharge_series", _FAIL, "no discharge observations")
    by_source: dict[str, int] = {}
    for r in rows:
        by_source[r.source] = by_source.get(r.source, 0) + r.count
    return _check(
        "discharge_series",
        _PASS,
        " ".join(f"{k}={v}" for k, v in sorted(by_source.items())),
    )


def check_group_and_model(
    station: StationConfig, model_id: ModelId | None, groups: StationGroupStore
) -> ReadinessCheck:
    member_of = groups.fetch_groups_for_station(station.id)
    if not member_of:
        return _check("group_and_model", _FAIL, "station is in no group")
    described: list[str] = []
    assigned = False
    for group in member_of:
        for a in groups.fetch_group_model_assignments(group.id):
            described.append(f"{group.name}:{a.model_id}({a.status.value})")
            assigned = assigned or (
                a.status is ModelAssignmentStatus.ACTIVE and a.model_id == model_id
            )
    text = f"groups={[g.name for g in member_of]} assignments={described or 'none'}"
    if model_id is None:
        return _check("group_and_model", _PASS, text)
    return _check(
        "group_and_model",
        _PASS if assigned else _FAIL,
        text + ("" if assigned else " no ACTIVE assignment of the requested model"),
    )


def lookback_days_for(model: ForecastModel | None) -> int:
    if model is None:
        return DEFAULT_LOOKBACK_DAYS
    requirements = model.data_requirements
    step = min(requirements.supported_time_steps)
    return max(1, math.ceil(requirements.lookback_steps * step / timedelta(days=1)))


def check_discharge_lookback(
    station: StationConfig,
    model: ForecastModel | None,
    observations: ObservationStore,
    *,
    now: UtcDatetime,
) -> ReadinessCheck:
    days = lookback_days_for(model)
    zone = ZoneInfo(station.timezone)
    end = now.astimezone(zone).date()
    rows = observations.fetch_observations(
        station.id,
        "discharge",
        now - timedelta(days=days + 2),
        now + timedelta(days=1),
        qc_status=USABLE_STATUSES,
    )
    have = days_with_value_set(
        [o.timestamp for o in rows], zone, end=end, lookback_days=days
    )
    wanted: list[date] = [end - timedelta(days=i) for i in range(days - 1, -1, -1)]
    missing = [d.isoformat() for d in wanted if d not in have]
    basis = "model lookback" if model is not None else "default lookback"
    text = f"{len(have)} of {days} days usable ({basis})"
    if missing:
        return _check(
            "discharge_lookback", _FAIL, f"{text}; missing: {_listed(missing)}"
        )
    return _check("discharge_lookback", _PASS, text)


def check_naming(
    station: StationConfig,
    polygons: GatewayPolygonBindingStore,
    *,
    scope_hru_name: str | None,
    scope_polygon_name: str | None,
) -> ReadinessCheck:
    bindings = [
        b
        for b in polygons.fetch_bindings_for_station(station.id)
        if b.spatial_type is SpatialRepresentation.BASIN_AVERAGE
    ]
    head = f"code={station.code} name={station.name!r}"
    if not bindings:
        return _check("naming", _FAIL, f"{head}; no basin-average polygon binding")
    binding = bindings[0]
    stored = (
        f"binding gateway_hru_name={binding.gateway_hru_name!r} name={binding.name!r}"
    )
    if scope_hru_name is None:
        return _check(
            "naming", _PASS, f"{head}; {stored}; weather scope comparison skipped"
        )
    problems = [
        f"scope {label}={want!r} but binding has {have!r}"
        for label, want, have in (
            ("runtime_gateway_hru_name", scope_hru_name, binding.gateway_hru_name),
            ("polygon_name", scope_polygon_name, binding.name),
        )
        if want is not None and want != have
    ]
    if problems:
        return _check("naming", _FAIL, f"{head}; {stored}; " + "; ".join(problems))
    return _check("naming", _PASS, f"{head}; {stored}; matches weather scope")


def build_station_readiness(
    station: StationConfig,
    stores: ReadinessStores,
    *,
    model: ForecastModel | None,
    model_id: ModelId | None,
    scope_hru_name: str | None,
    scope_polygon_name: str | None,
    now: UtcDatetime,
) -> StationReadinessReport:
    observations = stores.observations.fetch_station_summary(station.id)
    forcing = stores.forcing.fetch_station_summary(station.id)
    checks = (
        check_station(station),
        check_datum(station),
        check_basin(station, stores.basins),
        check_statics(station, model, stores),
        check_weather_bindings(station, stores.stations),
        check_weather_history(forcing, observations),
        check_observations(observations, now=now),
        check_rating_curve(station, stores),
        check_discharge_series(observations),
        check_group_and_model(station, model_id, stores.groups),
        check_discharge_lookback(station, model, stores.observations, now=now),
        check_naming(
            station,
            stores.polygons,
            scope_hru_name=scope_hru_name,
            scope_polygon_name=scope_polygon_name,
        ),
    )
    return StationReadinessReport(
        station_id=station.id, code=station.code, network=station.network, checks=checks
    )
