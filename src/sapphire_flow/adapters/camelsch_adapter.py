# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportArgumentType=false
# pyright: reportGeneralTypeIssues=false
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pandas as pd
import structlog

from sapphire_flow.types.enums import (
    ObservationSource,
    SpatialRepresentation,
    StationKind,
    StationOwnership,
    StationStatus,
)
from sapphire_flow.types.ids import BasinId, StationId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sapphire_flow.types.basin import Basin
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.ids import TenantId
    from sapphire_flow.types.observation import RawObservation
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger(__name__)

_PARAM_NAME_MAP: dict[str, str] = {
    "temperature_mean": "temperature",
}


def timeseries_to_observations(
    df: pd.DataFrame,
    station_id: StationId,
    clock: Callable[[], UtcDatetime],
) -> list[RawObservation]:
    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.observation import RawObservation

    if "discharge_vol" not in df.columns:
        return []

    results: list[RawObservation] = []
    for ts, row in df.iterrows():
        value = row["discharge_vol"]
        if pd.isna(value):
            continue
        dt = pd.Timestamp(ts).to_pydatetime()
        if dt.tzinfo is None:
            import datetime as _dt

            dt = dt.replace(tzinfo=_dt.UTC)
        results.append(
            RawObservation(
                station_id=station_id,
                timestamp=ensure_utc(dt),
                parameter="discharge",
                value=float(value),
                source=ObservationSource.MANUAL_IMPORT,
            )
        )
    return results


def timeseries_to_waterlevel_observations(
    df: pd.DataFrame,
    station_id: StationId,
    clock: Callable[[], UtcDatetime],
) -> list[RawObservation]:
    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.observation import RawObservation

    if "waterlevel" not in df.columns:
        return []

    results: list[RawObservation] = []
    for ts, row in df.iterrows():
        value = row["waterlevel"]
        if pd.isna(value):
            continue
        dt = pd.Timestamp(ts).to_pydatetime()
        if dt.tzinfo is None:
            import datetime as _dt

            dt = dt.replace(tzinfo=_dt.UTC)
        results.append(
            RawObservation(
                station_id=station_id,
                timestamp=ensure_utc(dt),
                parameter="water_level",
                value=float(value),
                source=ObservationSource.MANUAL_IMPORT,
            )
        )
    return results


def timeseries_to_forcing(
    df: pd.DataFrame,
    station_id: StationId,
    parameters: list[str] | None = None,
) -> list[RawHistoricalForcing]:
    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing

    if parameters is None:
        parameters = ["precipitation", "temperature_mean"]

    results: list[RawHistoricalForcing] = []
    for col in parameters:
        if col not in df.columns:
            continue
        param_name = _PARAM_NAME_MAP.get(col, col)
        for ts, row in df.iterrows():
            value = row[col]
            if pd.isna(value):
                continue
            dt = pd.Timestamp(ts).to_pydatetime()
            if dt.tzinfo is None:
                import datetime as _dt

                dt = dt.replace(tzinfo=_dt.UTC)
            results.append(
                RawHistoricalForcing(
                    station_id=station_id,
                    source="camels-ch",
                    version="1.0",
                    valid_time=ensure_utc(dt),
                    parameter=param_name,
                    spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                    band_id=None,
                    member_id=None,
                    value=float(value),
                )
            )
    return results


def attributes_to_station(
    gauge_id: str,
    attrs: pd.Series,
    basin_id: BasinId | None,
    station_id: StationId,
    clock: Callable[[], UtcDatetime],
    water_level_datums_masl: dict[str, float] | None = None,
    water_level_units: dict[str, str] | None = None,
    *,
    tenant_id: TenantId = DEFAULT_TENANT_ID,
) -> StationConfig:
    from sapphire_flow.types.domain import GeoCoord
    from sapphire_flow.types.station import StationConfig

    name = str(attrs["gauge_name"]) if "gauge_name" in attrs.index else gauge_id
    now = clock()

    water_body_type = (
        str(attrs["water_body_type"]) if "water_body_type" in attrs.index else None
    )
    if water_body_type == "lake":
        station_kind = StationKind.LAKE
        forecast_targets: frozenset[str] = frozenset({"water_level"})
        measured_parameters: frozenset[str] = frozenset({"water_level"})
        water_level_datum_masl = (water_level_datums_masl or {}).get(str(gauge_id))
        water_level_unit = (water_level_units or {}).get(str(gauge_id), "m a.s.l.")
    elif water_body_type is None or water_body_type == "stream":
        station_kind = StationKind.RIVER
        forecast_targets: frozenset[str] = frozenset({"discharge"})
        measured_parameters = frozenset({"discharge"})
        water_level_datum_masl = None
        water_level_unit = None
    else:
        log.warning(
            "unrecognized_water_body_type",
            gauge_id=gauge_id,
            water_body_type=water_body_type,
        )
        station_kind = StationKind.RIVER
        forecast_targets: frozenset[str] = frozenset({"discharge"})
        measured_parameters = frozenset({"discharge"})
        water_level_datum_masl = None
        water_level_unit = None

    return StationConfig(
        id=station_id,
        code=gauge_id,
        name=name,
        location=GeoCoord(lon=float(attrs["gauge_lon"]), lat=float(attrs["gauge_lat"])),
        station_kind=station_kind,
        basin_id=basin_id,
        timezone="Europe/Zurich",
        regulation_type=None,
        forecast_targets=forecast_targets,
        measured_parameters=measured_parameters,
        station_status=StationStatus.ONBOARDING,
        created_at=now,
        updated_at=now,
        network="bafu",
        ownership=StationOwnership.OWN,
        wigos_id=None,
        water_level_datum_masl=water_level_datum_masl,
        water_level_unit=water_level_unit,
        tenant_id=tenant_id,
    )


def geometry_to_basin(
    gauge_id: str,
    geometry: Any,
    attrs: pd.Series,
    basin_id: BasinId,
    clock: Callable[[], UtcDatetime],
) -> Basin:
    import math

    from shapely import force_2d
    from shapely.geometry import MultiPolygon, Polygon

    from sapphire_flow.types.basin import Basin

    # Drop Z coordinate — DB column is 2D MULTIPOLYGON
    geometry = force_2d(geometry)

    if isinstance(geometry, Polygon):
        geometry = MultiPolygon([geometry])

    name = str(attrs["gauge_name"]) if "gauge_name" in attrs.index else gauge_id
    area = float(attrs["area"]) if "area" in attrs.index else None

    # Sanitise attributes: replace NaN/Inf with None
    # (non-finite floats are invalid JSON)
    raw_attrs = attrs.to_dict()
    clean_attrs = {
        k: (None if isinstance(v, float) and not math.isfinite(v) else v)
        for k, v in raw_attrs.items()
    }

    return Basin(
        id=basin_id,
        code=gauge_id,
        name=name,
        geometry=geometry,
        area_km2=area,
        attributes=clean_attrs,
        band_geometries=None,
        created_at=clock(),
        network="bafu",
    )


def load_stations(
    data_dir: str | Path,
    clock: Callable[[], UtcDatetime],
    basin_ids: list[str] | None = None,
    water_level_datums_masl: dict[str, float] | None = None,
    water_level_units: dict[str, str] | None = None,
    *,
    tenant_id: TenantId = DEFAULT_TENANT_ID,
) -> tuple[list[StationConfig], list[Basin]]:
    import camelsch

    attrs_df = camelsch.load_attributes(data_dir, basin_ids=basin_ids)
    # Load ALL geometries — camelsch geometry index may use float-suffixed
    # IDs ("2004.0") that don't match the basin_ids filter ("2004").
    # We filter client-side after normalising IDs.
    geom_gdf = camelsch.load_geometries(data_dir, crs="EPSG:4326")

    stations: list[StationConfig] = []
    basins: list[Basin] = []

    # Build a normalised geometry lookup — shapefile gauge_ids may have
    # a ".0" float suffix (e.g. "2004.0") while attributes use "2004".
    geom_lookup: dict[str, Any] = {}
    for gid in geom_gdf.index:
        normalised = str(gid).removesuffix(".0")
        geom_lookup[normalised] = geom_gdf.loc[gid]

    for gauge_id in attrs_df.index:
        bid = BasinId(uuid4())
        sid = StationId(uuid4())
        attrs = attrs_df.loc[gauge_id]
        station = attributes_to_station(
            gauge_id,
            attrs,
            bid,
            sid,
            clock,
            water_level_datums_masl=water_level_datums_masl,
            water_level_units=water_level_units,
            tenant_id=tenant_id,
        )
        stations.append(station)

        normalised_id = str(gauge_id).removesuffix(".0")
        if normalised_id in geom_lookup:
            geom_row = geom_lookup[normalised_id]
            geometry = (
                geom_row.geometry
                if hasattr(geom_row, "geometry")
                else geom_row["geometry"]
            )
            basin = geometry_to_basin(gauge_id, geometry, attrs, bid, clock)
            basins.append(basin)

    return stations, basins


def load_observations(
    data_dir: str | Path,
    station_map: dict[str, StationId],
    clock: Callable[[], UtcDatetime],
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[StationId, list[RawObservation]]:
    import camelsch

    ts_data = camelsch.load_timeseries(
        data_dir,
        basin_ids=list(station_map.keys()),
        variables=["discharge_vol", "waterlevel"],
        start_date=start_date,
        end_date=end_date,
    )
    result: dict[StationId, list[RawObservation]] = {}
    for gauge_id, df in ts_data.items():
        sid = station_map.get(str(gauge_id))
        if sid is None:
            continue
        discharge_obs = timeseries_to_observations(df, sid, clock)
        waterlevel_obs = timeseries_to_waterlevel_observations(df, sid, clock)
        combined = discharge_obs + waterlevel_obs
        if not combined:
            log.warning(
                "station_no_observations", gauge_id=gauge_id, station_id=str(sid)
            )
        result[sid] = combined
    return result


def load_forcing(
    data_dir: str | Path,
    station_map: dict[str, StationId],
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[StationId, list[RawHistoricalForcing]]:
    import camelsch

    ts_data = camelsch.load_timeseries(
        data_dir,
        basin_ids=list(station_map.keys()),
        variables=["precipitation", "temperature_mean"],
        start_date=start_date,
        end_date=end_date,
    )
    return {
        station_map[gauge_id]: timeseries_to_forcing(df, station_map[gauge_id])
        for gauge_id, df in ts_data.items()
        if gauge_id in station_map
    }
