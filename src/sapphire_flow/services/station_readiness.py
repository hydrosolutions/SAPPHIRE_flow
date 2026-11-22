"""Plan 540 M4a: read-only station readiness facts (checks 1-10b).

Facts only: one PASS/FAIL/NOT_APPLICABLE line per check, no single verdict.
Nothing here writes. The per-check functions are reusable by later milestones.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from zoneinfo import ZoneInfo

import pandas as pd
import structlog

from sapphire_flow.adapters.recap_gateway import RECAP_VARIABLES, guarded_recap_fetch
from sapphire_flow.adapters.store_backed_reanalysis import StoreBackedReanalysisSource
from sapphire_flow.exceptions import AdapterError, ConfigurationError
from sapphire_flow.services.caravan_statics import (
    resolve_available_static_keys_for_stations,
)
from sapphire_flow.services.nepal_onboarding import (
    complete_training_rows,
    count_training_samples,
)
from sapphire_flow.services.station_observation_load import days_with_value_set
from sapphire_flow.services.station_observation_qc import USABLE_STATUSES
from sapphire_flow.services.training_data import assemble_station_training_data
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    ModelAssignmentStatus,
    QcStatus,
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
    from collections.abc import Callable, Sequence
    from datetime import date

    from sapphire_flow.adapters.recap_gateway import EcmwfApiLike
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
    from sapphire_flow.types.station import StationConfig, StationWeatherSource
    from sapphire_flow.types.station_readiness import (
        ForcingSummaryRow,
        ObservationSummaryRow,
    )

log = structlog.get_logger(__name__)

DEFAULT_LOOKBACK_DAYS = 30
# ERA5 trails real time by about 8 days; the operational product fills the gap at
# forecast time (docs/operations/new-station-by-hand.md, "ERA5 trails real time").
ERA5_LAG_DAYS = 8
_WEATHER_PARAMETERS = ("precipitation", "temperature")
_LISTED = 8
_PASS, _FAIL, _NA = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.NOT_APPLICABLE


@dataclass(frozen=True, kw_only=True, slots=True)
class TrainingWindowRequest:
    start: UtcDatetime
    end: UtcDatetime
    time_step: timedelta
    minimum_samples: int = 0

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError("training window start must precede end")
        if self.time_step <= timedelta(0):
            raise ValueError("training time step must be positive")
        if self.minimum_samples < 0:
            raise ValueError("minimum samples cannot be negative")


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


def _is_missing(value: object) -> bool:
    return (
        value is None
        or value is pd.NA
        or (isinstance(value, float) and math.isnan(value))
    )


def _midnight(day: date) -> UtcDatetime:
    return ensure_utc(datetime(day.year, day.month, day.day, tzinfo=UTC))


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
    overlap = (min(f_last, o_last).date() - max(f_first, o_first).date()).days + 1
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
    targets_unset = station.forecast_targets is None
    derived_from_level = (
        targets_unset
        and "water_level" in station.measured_parameters
        and "discharge" not in station.measured_parameters
    )
    if "discharge" not in targets and not derived_from_level:
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
    rows = [
        r
        for r in observations
        if r.parameter == "discharge" and r.qc_status is not QcStatus.MISSING
    ]
    if not rows:
        return _check("discharge_series", _FAIL, "no discharge values stored")
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
        note = (
            "" if described else " (no model assigned yet; pass --model to check one)"
        )
        return _check("group_and_model", _PASS, text + note)
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
    end = now.astimezone(zone).date() - timedelta(days=1)
    rows = observations.fetch_observations(
        station.id,
        "discharge",
        now - timedelta(days=days + 3),
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
    if scope_hru_name is not None and scope_polygon_name is None:
        return _check(
            "naming", _FAIL, f"{head}; {stored}; station is not in the weather scope"
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


def weather_dates(model: ForecastModel | None, *, now: UtcDatetime) -> list[date]:
    """UTC days of the lookback; like the forecast cycle, it ends yesterday."""
    end = now.date() - timedelta(days=1)
    return [
        end - timedelta(days=i) for i in range(lookback_days_for(model) - 1, -1, -1)
    ]


def _role_state(
    sources: Sequence[StationWeatherSource], role: WeatherSourceRole
) -> str:
    bound = [s for s in sources if s.role is role]
    if any(s.status is WeatherSourceStatus.ACTIVE for s in bound):
        return "bound"
    return "inactive" if bound else "missing"


def check_weather_wait_facts(
    station: StationConfig,
    model: ForecastModel | None,
    stores: ReadinessStores,
    *,
    now: UtcDatetime,
) -> ReadinessCheck:
    sources = stores.stations.fetch_weather_sources(station.id)
    roles = {r: _role_state(sources, r) for r in WeatherSourceRole}
    reanalysis = sorted(
        {
            s.nwp_source
            for s in sources
            if s.role is WeatherSourceRole.REANALYSIS
            and s.status is WeatherSourceStatus.ACTIVE
        }
    )
    all_days = weather_dates(model, now=now)
    cutoff = (now - timedelta(days=ERA5_LAG_DAYS)).date()
    wanted = [d for d in all_days if d <= cutoff]
    not_expected = len(all_days) - len(wanted)
    parameters = (
        sorted(model.data_requirements.past_dynamic_features)
        if model is not None
        else list(_WEATHER_PARAMETERS)
    )
    gaps: list[str] = []
    for parameter in parameters if wanted else ():
        covered: set[date] = set()
        for source in reanalysis:
            covered |= stores.forcing.fetch_covered_days(
                [station.id],
                source,
                parameter,
                SpatialRepresentation.BASIN_AVERAGE,
                _midnight(wanted[0]),
                _midnight(wanted[-1] + timedelta(days=1)),
            ).get(station.id, set())
        missing = [d.isoformat() for d in wanted if d not in covered]
        if missing:
            gaps.append(
                f"{parameter} missing {len(missing)} of {len(wanted)} days: "
                f"{_listed(missing)}"
            )
    roles_text = " ".join(f"{r.value}={state}" for r, state in roles.items())
    problems = [f"{r.value} binding {s}" for r, s in roles.items() if s != "bound"]
    ok = not gaps and not problems
    text = f"{roles_text}; stored reanalysis: " + (
        "; ".join(gaps) if gaps else f"all {len(wanted)} expected days present"
    )
    if not_expected:
        text += (
            f"; {not_expected} newest days not expected yet (Gateway lag, filled by "
            "the operational product at forecast time)"
        )
    return _check("weather_wait_facts", _PASS if ok else _FAIL, text)


def check_gateway_data(
    station: StationConfig,
    model: ForecastModel | None,
    polygons: GatewayPolygonBindingStore,
    make_api: Callable[[], EcmwfApiLike],
    *,
    now: UtcDatetime,
) -> ReadinessCheck:
    def unknown(reason: str) -> ReadinessCheck:
        return _check("gateway_data", CheckStatus.UNKNOWN, f"{reason}; not a pass")

    bindings = [
        b
        for b in polygons.fetch_bindings_for_station(station.id)
        if b.spatial_type is SpatialRepresentation.BASIN_AVERAGE
    ]
    if not bindings:
        return unknown("no basin-average polygon binding to ask the Gateway about")
    binding = bindings[0]
    wanted = weather_dates(model, now=now)
    try:
        api = make_api()
        frames = {
            parameter: guarded_recap_fetch(
                api.operational,
                hru_code=binding.gateway_hru_name,
                start_date=wanted[0],
                era5_variable_name=RECAP_VARIABLES[parameter].era5_name,
                ifs_variable_name=RECAP_VARIABLES[parameter].ifs_name,
                subdaily_resolution=6,
                include_provenance=True,
            )
            for parameter in _WEATHER_PARAMETERS
        }
    except (AdapterError, ConfigurationError, ImportError) as exc:
        log.warning("station_readiness_gateway_failed", cause=type(exc).__name__)
        return unknown(f"Gateway request failed ({type(exc).__name__})")
    gaps: list[str] = []
    for parameter, frame in frames.items():
        if (
            not isinstance(frame, pd.DataFrame)
            or binding.name not in frame.columns
            or not isinstance(frame.index, pd.DatetimeIndex)
        ):
            return unknown(f"unexpected Gateway response for {parameter}")
        column = cast("pd.Series", frame[binding.name])
        have = {
            cast("pd.Timestamp", ts).date()
            for ts, value in column.items()
            if not _is_missing(cast("object", value))
        }
        missing = [d.isoformat() for d in wanted if d not in have]
        if missing:
            gaps.append(
                f"{parameter} missing {len(missing)} of {len(wanted)} days: "
                f"{_listed(missing)}"
            )
    if gaps:
        return _check(
            "gateway_data",
            _FAIL,
            "; ".join(gaps) + "; try again after the backfill",
        )
    return _check("gateway_data", _PASS, f"OK: all {len(wanted)} days present")


def check_training_samples(
    station: StationConfig,
    model: ForecastModel,
    request: TrainingWindowRequest,
    stores: ReadinessStores,
) -> ReadinessCheck:
    requirements = model.data_requirements
    if request.time_step not in requirements.supported_time_steps:
        return _check(
            "training_samples", _FAIL, "time step is not supported by the model"
        )
    data = assemble_station_training_data(
        station.id,
        model,  # pyright: ignore[reportArgumentType]
        request.start,
        request.end,
        request.time_step,
        StoreBackedReanalysisSource(stores.forcing),
        stores.observations,
        stores.basins,
        stores.stations,
    )
    if data is None:
        return _check(
            "training_samples",
            _FAIL,
            "training inputs incomplete (QC-passed targets, reanalysis binding or "
            "stored weather missing); 0 complete samples",
        )
    samples = count_training_samples(
        complete_training_rows(data),
        request.time_step,
        requirements.lookback_steps,
        requirements.forecast_horizon_steps,
    )
    enough = samples > 0 and samples >= request.minimum_samples
    needed = f" (minimum {request.minimum_samples})" if request.minimum_samples else ""
    return _check(
        "training_samples",
        _PASS if enough else _FAIL,
        f"{samples} complete samples{needed} in {_day(request.start)}.."
        f"{_day(request.end)} (lookback {requirements.lookback_steps} + horizon "
        f"{requirements.forecast_horizon_steps} steps)",
    )


def build_station_readiness(
    station: StationConfig,
    stores: ReadinessStores,
    *,
    model: ForecastModel | None,
    model_id: ModelId | None,
    scope_hru_name: str | None,
    scope_polygon_name: str | None,
    now: UtcDatetime,
    training: TrainingWindowRequest | None = None,
    gateway: Callable[[], EcmwfApiLike] | None = None,
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
        check_weather_wait_facts(station, model, stores, now=now),
    )
    supplementary: list[ReadinessCheck] = []
    if gateway is not None:
        supplementary.append(
            check_gateway_data(station, model, stores.polygons, gateway, now=now)
        )
    if training is not None and model is not None:
        supplementary.append(check_training_samples(station, model, training, stores))
    return StationReadinessReport(
        station_id=station.id,
        code=station.code,
        network=station.network,
        checks=checks,
        supplementary=tuple(supplementary),
    )
