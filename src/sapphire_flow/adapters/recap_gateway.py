# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, ClassVar, NewType, Protocol, cast, runtime_checkable

import pandas as pd
import polars as pl
import structlog

from sapphire_flow.exceptions import AdapterError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.historical_forcing import RawHistoricalForcing
from sapphire_flow.types.weather import BasinAverageForecast, ElevationBandForecast

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import (
        GatewayPolygonBindingRow,
        StationWeatherSource,
    )
    from sapphire_flow.types.weather import WeatherForecastResult

log = structlog.get_logger(__name__)

GatewayHruName = NewType("GatewayHruName", str)
GatewayPolygonName = NewType("GatewayPolygonName", str)


def _metres_to_mm(value: float) -> float:
    return value * 1000.0


def _kelvin_to_celsius(value: float) -> float:
    return value - 273.15


@dataclass(frozen=True, kw_only=True, slots=True)
class RecapVariable:
    """One SAP3 canonical weather parameter mapped to its Recap source names.

    `convert` applies the grounded source→canonical unit conversion at the adapter
    boundary. `convert is None` is the deliberate sentinel for the snow variables
    (`hs`/`rof`/`swe`): their Gateway source-unit magnitudes are UNCONFIRMED, so no
    factor is committed in Plan 081 — that is a Plan 082 live-smoke item.
    """

    canonical: str
    unit: str
    era5_name: str | None = None
    ifs_name: str | None = None
    snow_name: str | None = None
    convert: Callable[[float], float] | None = None


# SAP3-owned Recap variable catalog. Separate, never-merged structure from
# MeteoSwiss `PARAM_GROUPS` (a Swiss STAC/cfgrib extraction allowlist). Precip and
# temperature carry grounded conversions; snow names are confirmed but their
# magnitude factors are deferred to Plan 082 (`convert=None`).
RECAP_VARIABLES: dict[str, RecapVariable] = {
    "precipitation": RecapVariable(
        canonical="precipitation",
        unit="mm",
        era5_name="total_precipitation",
        ifs_name="tp",
        convert=_metres_to_mm,
    ),
    "temperature": RecapVariable(
        canonical="temperature",
        unit="°C",
        era5_name="2m_temperature",
        ifs_name="2t",
        convert=_kelvin_to_celsius,
    ),
    "snow_depth": RecapVariable(
        canonical="snow_depth",
        unit="cm",
        snow_name="hs",
        convert=None,
    ),
    "snowmelt": RecapVariable(
        canonical="snowmelt",
        unit="mm",
        snow_name="rof",
        convert=None,
    ),
    "swe": RecapVariable(
        canonical="swe",
        unit="mm",
        snow_name="swe",
        convert=None,
    ),
}


@dataclass(frozen=True, kw_only=True, slots=True)
class GatewayPolygonRef:
    hru_name: GatewayHruName
    polygon_name: GatewayPolygonName
    station_id: StationId
    spatial_type: SpatialRepresentation
    band_id: int | None


@runtime_checkable
class GatewayPolygonResolver(Protocol):
    def resolve(self, source: StationWeatherSource) -> GatewayPolygonRef | None: ...


@runtime_checkable
class GatewayPolygonBindingStoreLike(Protocol):
    def fetch_bindings_for_station(
        self, station_id: StationId
    ) -> list[GatewayPolygonBindingRow]: ...


class StoreBackedGatewayPolygonResolver:
    """§5a-table-backed :class:`GatewayPolygonResolver` (Plan 082 Task 2D).

    Recap v1 is basin-average-only (``_validate_resolved_ref``): a station
    with zero or only ``ELEVATION_BAND`` bindings resolves to ``None``. When a
    station carries more than one ``BASIN_AVERAGE`` row (not expected — the
    resolver is 1:1, see ``_group_by_hru``), the first is used and a warning
    is logged rather than raising, since the ambiguity is a data-quality
    concern for the Plan 120 importer, not a fetch-time abort.
    """

    def __init__(self, store: GatewayPolygonBindingStoreLike) -> None:
        self._store = store

    def resolve(self, source: StationWeatherSource) -> GatewayPolygonRef | None:
        bindings = self._store.fetch_bindings_for_station(source.station_id)
        basin_average = [
            b for b in bindings if b.spatial_type is SpatialRepresentation.BASIN_AVERAGE
        ]
        if not basin_average:
            return None
        if len(basin_average) > 1:
            log.warning(
                "recap.resolver_multiple_basin_average_bindings",
                station_id=str(source.station_id),
                count=len(basin_average),
            )
        row = basin_average[0]
        return GatewayPolygonRef(
            hru_name=GatewayHruName(row.gateway_hru_name),
            polygon_name=GatewayPolygonName(row.name),
            station_id=row.station_id,
            spatial_type=row.spatial_type,
            band_id=row.band_id,
        )


@runtime_checkable
class EcmwfApiLike(Protocol):
    def ifs_forecast(
        self,
        *,
        variable: str,
        run_date: object,
        hru_code: str,
        ifs_type: str,
        member: str | None = None,
        **kwargs: object,
    ) -> object: ...

    def era5_land_reanalysis(
        self,
        *,
        variable: str,
        start_date: object,
        end_date: object | None = None,
        hru_code: str,
        **kwargs: object,
    ) -> object: ...


@runtime_checkable
class SnowApiLike(Protocol):
    def reanalysis(
        self,
        *,
        hru_code: str,
        variable: str,
        start_date: object,
        end_date: object,
        **kwargs: object,
    ) -> object: ...

    # Plan 082 Task 2H-snow. Matches the client (../recap-dg-client
    # recap_client/snow.py:63-86): run_hour:int default 0, valid 0/6/12/18.
    def forecast(
        self,
        *,
        hru_code: str,
        variable: str,
        run_date: object,
        run_hour: int = 0,
        **kwargs: object,
    ) -> object: ...


@runtime_checkable
class RecapClientLike(Protocol):
    ecmwf: EcmwfApiLike
    snow: SnowApiLike


class GatewayResolutionError(AdapterError):
    """Every station in a Recap Gateway batch was unmappable to a polygon."""

    def __init__(self, message: str, *, station_id: StationId) -> None:
        super().__init__(message)
        self.station_id = station_id


class RecapDataUnavailableError(AdapterError):
    """Gateway reported the requested source data is unavailable (retriable)."""

    def __init__(self, message: str, *, code: str | None) -> None:
        super().__init__(message)
        self.code = code


class RecapConfigurationError(AdapterError):
    """Gateway rejected a request parameter (HRU/variable) — a config/metadata error."""

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        supported_values: list[object] | None = None,
    ) -> None:
        super().__init__(message)
        self.field = field
        self.supported_values = supported_values


class RecapAuthError(AdapterError):
    """Gateway rejected the request as unauthorized/forbidden (Plan 082 Task 2G).

    Structurally discriminated from ``ApiRequestError.status_code`` (client
    ``http.py:28``) — a 401/403 with no structured error body, which
    ``_map_recap_error`` previously fell through to the generic
    ``AdapterError`` (Plan 081 note).
    """

    def __init__(self, message: str, *, status_code: int | None) -> None:
        super().__init__(message)
        self.status_code = status_code


_PROVENANCE_COLUMNS: tuple[str, str] = ("source", "source_run")
_ERA5_SOURCE = "recap_era5_land_reanalysis"
_SNOW_SOURCE = "recap_snow_reanalysis"
_FC_MEMBER_ID = 0
_PF_MEMBER_MIN = 1
_PF_MEMBER_MAX = 50

# Plan 082 Task 3B item 3: client per-row provenance literals (client
# README.md:102-105) — observed (admitted into reanalysis) vs forecast-fill
# (dropped). The client's forecast tail / gap-fill rows must never leak into
# training-history admission.
_OBSERVED_SOURCES = frozenset({"era5_land", "jsnow_reanalysis"})


def _drop_forecast_fill_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Leakage guard: keep only OBSERVED (``era5_land``/``jsnow_reanalysis``)
    rows, reading the client's raw per-row ``source`` column BEFORE
    ``_split_provenance`` drops it. A frame with no ``source`` column is
    returned unchanged (nothing to filter on)."""
    if "source" not in df.columns:
        return df
    mask = df["source"].isin(list(_OBSERVED_SOURCES))
    return cast("pd.DataFrame", df[mask])


def _map_recap_error(exc: BaseException) -> AdapterError:
    """Map a recap-dg-client structured error to a SAP3 AdapterError, structurally.

    Reads discriminators via getattr(exc, "code"/"field"/"supported_values", None) —
    never isinstance against the client's error classes. Clone b3ce520 attribute sets
    (clone http.py:15-85): ApiRequestError -> url/params/status_code/body;
    ApiValidationError -> + code/field/hint/supported_values/details;
    ApiDataUnavailableError -> code/field/hint/details (no supported_values). The
    getattr-None defaults make the mapper total over all three without a shared type.
    """
    code = getattr(exc, "code", None)
    field = getattr(exc, "field", None)
    supported_values = getattr(exc, "supported_values", None)
    status_code = getattr(exc, "status_code", None)
    if code == "source_data_missing":
        return RecapDataUnavailableError(str(exc), code=code)
    if supported_values is not None or field == "hru_code":
        return RecapConfigurationError(
            str(exc), field=field, supported_values=supported_values
        )
    if status_code in (401, 403):
        return RecapAuthError(str(exc), status_code=status_code)
    return AdapterError(str(exc))


_IFS_CADENCE_HOURS = 6.0
_PROBE_VARIABLE = "tp"


def _floor_to_cadence(moment: UtcDatetime, cadence_hours: float) -> UtcDatetime:
    cadence_seconds = cadence_hours * 3600.0
    elapsed = moment.timestamp()
    floored = elapsed - (elapsed % cadence_seconds)
    return ensure_utc(datetime.fromtimestamp(floored, tz=UTC))


def resolve_latest_cycle(
    client: RecapClientLike,
    *,
    hru_code: str,
    now: UtcDatetime,
    max_age_hours: float,
    cadence_hours: float = _IFS_CADENCE_HOURS,
) -> UtcDatetime | None:
    """Probe candidate IFS ``run_date``/``run_hour`` newest-first.

    No Gateway health/latest-cycle endpoint exists (Resolved Gateway
    Question 5) — this issues real ``ifs_forecast`` probe calls, walking back
    in ``cadence_hours`` steps from the cadence-floored ``now`` until either a
    candidate returns data or ``max_age_hours`` is exhausted. A
    ``source_data_missing`` response marks a candidate unavailable and moves
    to the next older one; any other mapped error propagates immediately
    (not swallowed as "unavailable").
    """
    candidate = _floor_to_cadence(now, cadence_hours)
    steps = int(max_age_hours // cadence_hours) + 1
    for _ in range(steps):
        try:
            _guarded_fetch(
                client.ecmwf.ifs_forecast,
                variable=_PROBE_VARIABLE,
                run_date=candidate,
                run_hour=candidate.hour,
                hru_code=hru_code,
                ifs_type="fc",
            )
        except RecapDataUnavailableError:
            candidate = ensure_utc(candidate - timedelta(hours=cadence_hours))
            continue
        else:
            return candidate
    return None


def _guarded_fetch(fn: Callable[..., object], /, **kwargs: object) -> object:
    try:
        return fn(**kwargs)
    except Exception as exc:  # structural map; no recap-dg-client symbol referenced
        raise _map_recap_error(exc) from exc


def _split_provenance(df: pd.DataFrame) -> tuple[pd.DataFrame, object | None]:
    """Split off the ``source``/``source_run`` columns by literal name, if present.

    Reimplements the client's provenance drop (does NOT import its ``drop_provenance``)
    so the adapter stays importable with ``recap-dg-client`` absent. Returns the numeric
    frame plus a representative ``source_run`` value (first non-null), or ``None``.
    """
    source_run: object | None = None
    if "source_run" in df.columns:
        non_null = df["source_run"].dropna()
        if len(non_null) > 0:
            source_run = non_null.iloc[0]
    drop_cols = [c for c in _PROVENANCE_COLUMNS if c in df.columns]
    numeric = df.drop(columns=drop_cols) if drop_cols else df
    return numeric, source_run


def _to_pydatetime(value: object) -> datetime:
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, datetime):
        return value
    parsed = pd.Timestamp(cast("str | float | datetime", value)).to_pydatetime()
    return cast("datetime", parsed)


def _normalize_source_run_to_utc(value: object | None) -> UtcDatetime | None:
    if value is None:
        return None
    try:
        ts = pd.Timestamp(cast("str | float | datetime", value))
    except (ValueError, TypeError):
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ensure_utc(cast("datetime", ts.to_pydatetime()))


def _source_run_to_version(value: object | None) -> str:
    normalized = _normalize_source_run_to_utc(value)
    return normalized.isoformat() if normalized is not None else ""


def _iter_long_rows(
    df: pd.DataFrame,
    refs_by_polygon: dict[GatewayPolygonName, GatewayPolygonRef],
    convert: Callable[[float], float] | None,
) -> tuple[
    list[tuple[GatewayPolygonRef, UtcDatetime, float, object | None]], object | None
]:
    """Reshape one wide, one-variable Gateway frame to typed long rows.

    Numeric polygon columns map back to a resolved ``GatewayPolygonRef`` by
    ``polygon_name`` (never by parsing the column text). Converts every value once and
    yields native ``datetime`` valid-times — no pandas object crosses this boundary.
    Each emitted row carries its OWN ``source_run`` (aligned to the row's valid-time):
    recap-dg-client documents provenance as per-row (the producing run/product date),
    so a multi-day reanalysis window can carry different runs across rows. The scalar
    ``source_run`` returned alongside is only a representative (first non-null) used for
    the forecast ``cycle_time`` — never as a per-row version.
    """
    numeric, source_run = _split_provenance(df)
    # Every resolved polygon in this HRU must have a column in the response. A
    # missing column would otherwise silently drop that station from the batch
    # (the demux only iterates columns that are present, and fetch_forecasts only
    # returns stations that accumulated rows). Fail loud on a corrupt/incomplete
    # response naming the affected stations rather than shipping a silently-partial
    # batch as SUCCESS.
    present = {GatewayPolygonName(str(c)) for c in numeric.columns}
    missing = [ref for name, ref in refs_by_polygon.items() if name not in present]
    if missing:
        ids = ", ".join(str(ref.station_id) for ref in missing)
        polys = ", ".join(str(ref.polygon_name) for ref in missing)
        raise AdapterError(
            f"Recap Gateway response missing expected polygon column(s) {polys} "
            f"for station(s) {ids}"
        )
    index = [ensure_utc(_to_pydatetime(ts)) for ts in numeric.index]
    has_run = "source_run" in df.columns
    run_col: list[object | None] = (
        df["source_run"].to_list() if has_run else [None] * len(index)
    )
    rows: list[tuple[GatewayPolygonRef, UtcDatetime, float, object | None]] = []
    for col in numeric.columns:
        ref = refs_by_polygon.get(GatewayPolygonName(str(col)))
        if ref is None:
            continue
        for valid_time, raw, row_run in zip(
            index, numeric[col].to_list(), run_col, strict=True
        ):
            value = float(raw)
            if convert is not None:
                value = convert(value)
            rows.append((ref, valid_time, value, row_run))
    return rows, source_run


def _prefilter(
    station_configs: list[StationWeatherSource],
    *,
    nwp_source: str,
    role: WeatherSourceRole,
) -> list[StationWeatherSource]:
    """Exclude wrong-source, wrong-role, inactive, and non-basin-average bindings.

    Applied BEFORE resolution: an excluded binding is never resolved and yields no
    Gateway call and no output row (distinct from a resolver miss).
    """
    return [
        c
        for c in station_configs
        if c.nwp_source == nwp_source
        and c.role is role
        and c.status == WeatherSourceStatus.ACTIVE
        and c.extraction_type == SpatialRepresentation.BASIN_AVERAGE
    ]


def _validate_resolved_ref(
    ref: GatewayPolygonRef, config: StationWeatherSource
) -> None:
    """Enforce the Plan 081 basin-average-only lock on the resolved ref itself.

    The prefilter only checks the config's declared ``extraction_type``; a resolver
    that returns an ``ELEVATION_BAND``/banded ref (or a ref for the wrong station)
    would otherwise be grouped and fetched, then either emit an
    ``ElevationBandForecast`` (forecast) or be silently rewritten to basin-average
    (reanalysis). Recap v1 is basin-average-only, so a non-conforming resolved ref is
    a config/resolution error — fail loud rather than proceed or rewrite.
    """
    if ref.station_id != config.station_id:
        raise RecapConfigurationError(
            f"Recap Gateway resolver returned a ref for station {ref.station_id} "
            f"while resolving config for station {config.station_id}",
            field="station_id",
        )
    if ref.spatial_type is not SpatialRepresentation.BASIN_AVERAGE or (
        ref.band_id is not None
    ):
        raise RecapConfigurationError(
            f"Recap Gateway is basin-average-only (Plan 081); station "
            f"{config.station_id} resolved to spatial_type={ref.spatial_type.name} "
            f"band_id={ref.band_id}",
            field="spatial_type",
        )


def _resolve_all(
    resolver: GatewayPolygonResolver,
    in_scope: list[StationWeatherSource],
) -> tuple[list[GatewayPolygonRef], list[StationId]]:
    resolved: list[GatewayPolygonRef] = []
    skipped: list[StationId] = []
    for config in in_scope:
        ref = resolver.resolve(config)
        if ref is None:
            skipped.append(config.station_id)
            log.warning("recap.station_unmapped", station_id=str(config.station_id))
        else:
            _validate_resolved_ref(ref, config)
            resolved.append(ref)
    return resolved, skipped


def _require_some_resolved(
    in_scope: list[StationWeatherSource],
    resolved: list[GatewayPolygonRef],
    skipped: list[StationId],
) -> None:
    # Per-station misses are skipped-and-logged; the all-unmappable case is a genuine
    # caller/config error worth failing loud on.
    if in_scope and not resolved:
        raise GatewayResolutionError(
            f"all {len(in_scope)} Recap Gateway station(s) unmappable to a polygon",
            station_id=skipped[0],
        )


def _group_by_hru(
    refs: list[GatewayPolygonRef],
) -> dict[GatewayHruName, dict[GatewayPolygonName, GatewayPolygonRef]]:
    # The resolver is 1:1 (Plan 081): each station occupies exactly one polygon and
    # no two stations share one. If two distinct stations resolve to the same
    # (hru_name, polygon_name) the per-polygon dict would silently overwrite the
    # first, dropping a station's result from the batch. Fail loud on that config
    # error instead, naming both conflicting stations.
    grouped: dict[GatewayHruName, dict[GatewayPolygonName, GatewayPolygonRef]] = {}
    for ref in refs:
        existing = grouped.setdefault(ref.hru_name, {}).get(ref.polygon_name)
        if existing is not None and existing.station_id != ref.station_id:
            raise RecapConfigurationError(
                f"stations {existing.station_id} and {ref.station_id} both resolve "
                f"to polygon {ref.polygon_name} in HRU {ref.hru_name}; the Recap "
                "Gateway resolver must be 1:1 (one station per polygon)",
                field="polygon_name",
            )
        grouped[ref.hru_name][ref.polygon_name] = ref
    return grouped


def _ifs_variables() -> list[RecapVariable]:
    return [v for v in RECAP_VARIABLES.values() if v.ifs_name is not None]


def _snow_variables() -> list[RecapVariable]:
    return [v for v in RECAP_VARIABLES.values() if v.snow_name is not None]


def _pf_member_id(member: int) -> int:
    # Guard: a pf member must be 1..50 and can never collide with fc's member_id=0.
    if member == _FC_MEMBER_ID or not (_PF_MEMBER_MIN <= member <= _PF_MEMBER_MAX):
        raise ValueError(f"pf member {member} must be in 1..50 (0 reserved for fc)")
    return member


def _requested_reanalysis_variables(parameters: list[str]) -> list[RecapVariable]:
    seen: set[str] = set()
    result: list[RecapVariable] = []
    for name in parameters:
        variable = RECAP_VARIABLES.get(name)
        if variable is None or name in seen:
            continue
        if variable.era5_name is None and variable.snow_name is None:
            continue
        seen.add(name)
        result.append(variable)
    return result


def _build_forecast_result(
    ref: GatewayPolygonRef,
    rows: list[dict[str, object]],
    cycle_time: UtcDatetime,
    nwp_source: str,
) -> WeatherForecastResult:
    if ref.spatial_type is SpatialRepresentation.ELEVATION_BAND:
        # Typed seam: Nepal v1 forecast is basin-average-only. `_validate_resolved_ref`
        # rejects any ELEVATION_BAND/banded ref at resolution time, so this branch is
        # unreachable in Recap v1. Present so the future banded extension + the
        # elevation_band_to_records storage path compile.
        return ElevationBandForecast(
            nwp_source=nwp_source,
            cycle_time=cycle_time,
            values=pl.DataFrame(rows),
        )
    basin_rows = [
        {k: r[k] for k in ("valid_time", "parameter", "member_id", "value")}
        for r in rows
    ]
    return BasinAverageForecast(
        nwp_source=nwp_source,
        cycle_time=cycle_time,
        values=pl.DataFrame(basin_rows),
    )


class RecapGatewayForecastAdapter:
    """``WeatherForecastSource`` over the Recap Gateway IFS forecast endpoint.

    Module: ``adapters/recap_gateway.py``; satisfies ``WeatherForecastSource``;
    ``NWP_SOURCE="ifs_ecmwf"`` (the ``role==FORECAST`` binding storage key). Every
    forecast record's ``nwp_source`` equals this value by construction.
    """

    NWP_SOURCE: ClassVar[str] = "ifs_ecmwf"

    def __init__(
        self,
        *,
        client: RecapClientLike,
        resolver: GatewayPolygonResolver,
    ) -> None:
        self._client = client
        self._resolver = resolver

    def fetch_forecasts(
        self,
        station_configs: list[StationWeatherSource],
        cycle_time: UtcDatetime,
    ) -> dict[StationId, WeatherForecastResult]:
        in_scope = _prefilter(
            station_configs,
            nwp_source=self.NWP_SOURCE,
            role=WeatherSourceRole.FORECAST,
        )
        if not in_scope:
            return {}
        resolved, skipped = _resolve_all(self._resolver, in_scope)
        _require_some_resolved(in_scope, resolved, skipped)

        by_hru = _group_by_hru(resolved)
        station_ref = {ref.station_id: ref for ref in resolved}
        acc: dict[StationId, list[dict[str, object]]] = {}
        cycle_source_run: object | None = None

        for hru_name, refs_by_polygon in by_hru.items():
            for variable in _ifs_variables():
                ifs_name = variable.ifs_name
                if ifs_name is None:
                    continue
                cycle_source_run = self._accumulate_member(
                    acc,
                    refs_by_polygon,
                    variable=variable,
                    ifs_name=ifs_name,
                    hru_name=hru_name,
                    cycle_time=cycle_time,
                    ifs_type="fc",
                    member=None,
                    member_id=_FC_MEMBER_ID,
                    prior=cycle_source_run,
                )
                for member in range(_PF_MEMBER_MIN, _PF_MEMBER_MAX + 1):
                    cycle_source_run = self._accumulate_member(
                        acc,
                        refs_by_polygon,
                        variable=variable,
                        ifs_name=ifs_name,
                        hru_name=hru_name,
                        cycle_time=cycle_time,
                        ifs_type="pf",
                        member=str(member),
                        member_id=_pf_member_id(member),
                        prior=cycle_source_run,
                    )

        cycle = _normalize_source_run_to_utc(cycle_source_run) or cycle_time
        return {
            station_id: _build_forecast_result(
                station_ref[station_id], rows, cycle, self.NWP_SOURCE
            )
            for station_id, rows in acc.items()
        }

    def _accumulate_member(
        self,
        acc: dict[StationId, list[dict[str, object]]],
        refs_by_polygon: dict[GatewayPolygonName, GatewayPolygonRef],
        *,
        variable: RecapVariable,
        ifs_name: str,
        hru_name: GatewayHruName,
        cycle_time: UtcDatetime,
        ifs_type: str,
        member: str | None,
        member_id: int,
        prior: object | None,
    ) -> object | None:
        # run_hour is mandatory: the client's _iso_date strips run_date to a bare
        # date and defaults run_hour=0, so a 06/12/18Z cycle would silently fetch
        # the 00Z run unless we pass the cycle hour explicitly (Plan 081: run_hour
        # aligns with the IFS cycle).
        call_kwargs: dict[str, object] = {
            "variable": ifs_name,
            "run_date": cycle_time,
            "run_hour": cycle_time.hour,
            "hru_code": hru_name,
            "ifs_type": ifs_type,
        }
        if member is not None:
            call_kwargs["member"] = member
        df = cast(
            "pd.DataFrame",
            _guarded_fetch(self._client.ecmwf.ifs_forecast, **call_kwargs),
        )
        rows, source_run = _iter_long_rows(df, refs_by_polygon, variable.convert)
        for ref, valid_time, value, _row_run in rows:
            acc.setdefault(ref.station_id, []).append(
                {
                    "valid_time": valid_time,
                    "parameter": variable.canonical,
                    "band_id": ref.band_id,
                    "member_id": member_id,
                    "value": value,
                }
            )
        return prior if prior is not None else source_run

    def fetch_snow_forecast(
        self,
        station_configs: list[StationWeatherSource],
        cycle_time: UtcDatetime,
    ) -> dict[StationId, WeatherForecastResult]:
        """Deterministic snow-forecast fetch (Plan 082 Task 2H-snow).

        NOT part of the ``WeatherForecastSource`` Protocol — called separately
        by the model-input service, which performs the daily-snow ->
        sub-daily 51-member IFS broadcast (no resample/broadcast happens
        here). Snow rows carry ``member_id=None`` (deterministic, single
        run) — see ``RecapGatewayReanalysisAdapter`` for the same convention
        on the reanalysis side.
        """
        in_scope = _prefilter(
            station_configs,
            nwp_source=self.NWP_SOURCE,
            role=WeatherSourceRole.FORECAST,
        )
        if not in_scope:
            return {}
        resolved, skipped = _resolve_all(self._resolver, in_scope)
        _require_some_resolved(in_scope, resolved, skipped)

        by_hru = _group_by_hru(resolved)
        station_ref = {ref.station_id: ref for ref in resolved}
        acc: dict[StationId, list[dict[str, object]]] = {}

        for hru_name, refs_by_polygon in by_hru.items():
            for variable in _snow_variables():
                snow_name = variable.snow_name
                if snow_name is None:
                    continue
                self._accumulate_snow(
                    acc,
                    refs_by_polygon,
                    variable=variable,
                    snow_name=snow_name,
                    hru_name=hru_name,
                    cycle_time=cycle_time,
                )

        return {
            station_id: _build_forecast_result(
                station_ref[station_id], rows, cycle_time, self.NWP_SOURCE
            )
            for station_id, rows in acc.items()
        }

    def _accumulate_snow(
        self,
        acc: dict[StationId, list[dict[str, object]]],
        refs_by_polygon: dict[GatewayPolygonName, GatewayPolygonRef],
        *,
        variable: RecapVariable,
        snow_name: str,
        hru_name: GatewayHruName,
        cycle_time: UtcDatetime,
    ) -> None:
        # run_hour is mandatory for the same reason as the IFS fetch: the
        # client's _iso_date strips run_date to a bare date and defaults
        # run_hour=0 (client snow.py:63-86 default valid_hour 0/6/12/18).
        call_kwargs: dict[str, object] = {
            "hru_code": hru_name,
            "variable": snow_name,
            "run_date": cycle_time,
            "run_hour": cycle_time.hour,
        }
        df = cast(
            "pd.DataFrame",
            _guarded_fetch(self._client.snow.forecast, **call_kwargs),
        )
        rows, _ = _iter_long_rows(df, refs_by_polygon, variable.convert)
        for ref, valid_time, value, _row_run in rows:
            acc.setdefault(ref.station_id, []).append(
                {
                    "valid_time": valid_time,
                    "parameter": variable.canonical,
                    "band_id": ref.band_id,
                    "member_id": None,
                    "value": value,
                }
            )


class RecapGatewayReanalysisAdapter:
    """``WeatherReanalysisSource`` over the Recap Gateway ERA5-Land + snow endpoints.

    Module: ``adapters/recap_gateway.py``; satisfies ``WeatherReanalysisSource``;
    ``NWP_SOURCE="era5_land"``. Emits basin-average ``RawHistoricalForcing`` rows with
    endpoint-provenance ``source`` literals and deterministic ``member_id=None``.
    """

    NWP_SOURCE: ClassVar[str] = "era5_land"

    def __init__(
        self,
        *,
        client: RecapClientLike,
        resolver: GatewayPolygonResolver,
    ) -> None:
        self._client = client
        self._resolver = resolver

    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]:
        requested = _requested_reanalysis_variables(parameters)
        if not requested:
            return []
        in_scope = _prefilter(
            station_configs,
            nwp_source=self.NWP_SOURCE,
            role=WeatherSourceRole.REANALYSIS,
        )
        if not in_scope:
            return []
        resolved, skipped = _resolve_all(self._resolver, in_scope)
        _require_some_resolved(in_scope, resolved, skipped)

        by_hru = _group_by_hru(resolved)
        out: list[RawHistoricalForcing] = []
        for hru_name, refs_by_polygon in by_hru.items():
            for variable in requested:
                out.extend(
                    self._rows_for_variable(
                        variable, hru_name, refs_by_polygon, start, end
                    )
                )
        return out

    def _rows_for_variable(
        self,
        variable: RecapVariable,
        hru_name: GatewayHruName,
        refs_by_polygon: dict[GatewayPolygonName, GatewayPolygonRef],
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[RawHistoricalForcing]:
        era5_name = variable.era5_name
        snow_name = variable.snow_name
        if era5_name is not None:
            df = cast(
                "pd.DataFrame",
                _guarded_fetch(
                    self._client.ecmwf.era5_land_reanalysis,
                    variable=era5_name,
                    start_date=start,
                    end_date=end,
                    hru_code=hru_name,
                ),
            )
            source = _ERA5_SOURCE
        elif snow_name is not None:
            df = cast(
                "pd.DataFrame",
                _guarded_fetch(
                    self._client.snow.reanalysis,
                    hru_code=hru_name,
                    variable=snow_name,
                    start_date=start,
                    end_date=end,
                ),
            )
            source = _SNOW_SOURCE
        else:
            return []
        # Leakage guard (Task 3B item 3): drop forecast-fill rows BEFORE
        # reshaping — reads the client's raw source column, which
        # `_split_provenance`/`_iter_long_rows` would otherwise discard
        # without ever inspecting per-row values.
        df = _drop_forecast_fill_rows(df)
        rows, _ = _iter_long_rows(df, refs_by_polygon, variable.convert)
        # Two boundary guards on the reshaped rows:
        #  - version is the row's OWN source_run (per-row provenance), not a single
        #    collapsed value shared across a multi-day window.
        #  - filter to [start, end): the recap client's _iso_date strips the
        #    request window to bare dates, so a non-midnight [start, end) window can
        #    return rows before start or at/after end; drop them here.
        return [
            RawHistoricalForcing(
                station_id=ref.station_id,
                source=source,
                version=_source_run_to_version(row_run),
                valid_time=valid_time,
                parameter=variable.canonical,
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
                member_id=None,
                value=value,
            )
            for ref, valid_time, value, row_run in rows
            if start <= valid_time < end
        ]
