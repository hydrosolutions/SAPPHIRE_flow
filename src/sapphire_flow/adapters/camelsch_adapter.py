# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportArgumentType=false
# pyright: reportGeneralTypeIssues=false
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pandas as pd

from sapphire_flow.types.enums import (
    ObservationSource,
    SpatialRepresentation,
    StationKind,
    StationOwnership,
    StationStatus,
)
from sapphire_flow.types.ids import BasinId, StationId

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sapphire_flow.types.basin import Basin
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.observation import RawObservation
    from sapphire_flow.types.station import StationConfig

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
) -> StationConfig:
    from sapphire_flow.types.domain import GeoCoord
    from sapphire_flow.types.station import StationConfig

    name = str(attrs["gauge_name"]) if "gauge_name" in attrs.index else gauge_id
    now = clock()
    return StationConfig(
        id=station_id,
        code=gauge_id,
        name=name,
        location=GeoCoord(lon=float(attrs["gauge_lon"]), lat=float(attrs["gauge_lat"])),
        station_kind=StationKind.RIVER,
        basin_id=basin_id,
        timezone="Europe/Zurich",
        regulation_type=None,
        forecast_target="discharge",
        measured_parameters=frozenset({"discharge"}),
        station_status=StationStatus.ONBOARDING,
        created_at=now,
        updated_at=now,
        network="bafu",
        ownership=StationOwnership.OWN,
        wigos_id=None,
    )


def geometry_to_basin(
    gauge_id: str,
    geometry: Any,
    attrs: pd.Series,
    basin_id: BasinId,
    clock: Callable[[], UtcDatetime],
) -> Basin:
    from shapely.geometry import MultiPolygon, Polygon

    from sapphire_flow.types.basin import Basin

    if isinstance(geometry, Polygon):
        geometry = MultiPolygon([geometry])

    name = str(attrs["gauge_name"]) if "gauge_name" in attrs.index else gauge_id
    area = float(attrs["area"]) if "area" in attrs.index else None

    return Basin(
        id=basin_id,
        code=gauge_id,
        name=name,
        geometry=geometry,
        area_km2=area,
        attributes=attrs.to_dict(),
        band_geometries=None,
        created_at=clock(),
        network="bafu",
    )


def load_stations(
    data_dir: str | Path,
    clock: Callable[[], UtcDatetime],
    basin_ids: list[str] | None = None,
) -> tuple[list[StationConfig], list[Basin]]:
    import camelsch

    attrs_df = camelsch.load_attributes(data_dir, basin_ids=basin_ids)
    geom_gdf = camelsch.load_geometries(data_dir, basin_ids=basin_ids, crs="EPSG:4326")

    stations: list[StationConfig] = []
    basins: list[Basin] = []

    for gauge_id in attrs_df.index:
        bid = BasinId(uuid4())
        sid = StationId(uuid4())
        attrs = attrs_df.loc[gauge_id]
        station = attributes_to_station(gauge_id, attrs, bid, sid, clock)
        stations.append(station)

        if gauge_id in geom_gdf.index:
            geom_row = geom_gdf.loc[gauge_id]
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
        variables=["discharge_vol"],
        start_date=start_date,
        end_date=end_date,
    )
    return {
        station_map[gid]: timeseries_to_observations(df, station_map[gid], clock)
        for gid, df in ts_data.items()
        if gid in station_map
    }


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
