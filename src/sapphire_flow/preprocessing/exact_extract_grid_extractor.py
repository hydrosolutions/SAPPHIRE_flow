from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any, cast

import geopandas as gpd
import numpy as np
import pandas as pd
import polars as pl
import rioxarray  # type: ignore[import-untyped]  # noqa: F401
import structlog
from exactextract import exact_extract  # type: ignore[import-untyped]
from shapely.geometry import MultiPolygon, Polygon

from sapphire_flow.exceptions import ExtractionError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.weather import BasinAverageForecast

if TYPE_CHECKING:
    import xarray as xr

    from sapphire_flow.types.basin import Basin
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationWeatherSource
    from sapphire_flow.types.weather import ElevationBandForecast

log = structlog.get_logger(__name__)


def _to_utc_datetime(vt: Any) -> UtcDatetime:
    # NOTE: Plan 063 owns the MeteoSwiss adapter's tz-aware emission contract;
    # this guard asserts only at the extractor boundary. Do not silently coerce —
    # naive datetimes past the v0 boundary are a project-wide anti-pattern.
    ts = pd.Timestamp(vt)
    if ts.tzinfo is None:
        raise ValueError(
            f"valid_time {vt!r} is naive; GridExtractor requires tz-aware datetimes"
        )
    return ensure_utc(ts.to_pydatetime())  # type: ignore[arg-type]


class ExactExtractGridExtractor:
    def extract(
        self,
        grid: xr.Dataset,
        configs: list[StationWeatherSource],
        basins: dict[StationId, Basin],
        cycle_time: UtcDatetime,
        nwp_source: str,
    ) -> dict[StationId, BasinAverageForecast | ElevationBandForecast]:
        t0 = time.perf_counter()
        parameters = list(grid.data_vars)

        log.info(
            "extraction.started",
            station_count=len(configs),
            parameter_count=len(parameters),
        )

        valid_configs: list[StationWeatherSource] = []
        skipped = 0
        for cfg in configs:
            if cfg.station_id not in basins:
                log.warning(
                    "extraction.station_skipped",
                    station_id=str(cfg.station_id),
                    reason="no basin geometry",
                )
                skipped += 1
                continue
            geom = basins[cfg.station_id].geometry
            if not isinstance(geom, (Polygon, MultiPolygon)):
                log.warning(
                    "extraction.station_skipped",
                    station_id=str(cfg.station_id),
                    reason="invalid geometry type",
                )
                skipped += 1
                continue
            valid_configs.append(cfg)

        if not valid_configs:
            raise ExtractionError(
                "No valid stations with basin geometries for extraction"
            )

        gdf = gpd.GeoDataFrame(
            {"station_id": [str(cfg.station_id) for cfg in valid_configs]},
            geometry=[basins[cfg.station_id].geometry for cfg in valid_configs],
            crs="EPSG:4326",
        )

        grid = grid.rio.set_spatial_dims(
            x_dim="longitude", y_dim="latitude"
        ).rio.write_crs("EPSG:4326")

        members: np.ndarray[Any, Any] = (
            grid["member"].values if "member" in grid.dims else np.array([0])
        )
        has_member_dim = "member" in grid.dims
        valid_times: np.ndarray[Any, Any] = grid["valid_time"].values

        # Pre-allocate per-station result lists
        rows_by_station: dict[StationId, list[dict[str, Any]]] = {
            cfg.station_id: [] for cfg in valid_configs
        }

        for param in parameters:
            for member_val in members:
                for vt in valid_times:
                    # Slice to 2D (latitude, longitude)
                    sel_kwargs: dict[str, Any] = {"valid_time": vt}
                    if has_member_dim:
                        sel_kwargs["member"] = member_val
                    da_slice = grid[param].sel(**sel_kwargs)  # type: ignore[reportUnknownMemberType]

                    extracted = cast(
                        "pd.DataFrame",
                        exact_extract(da_slice, gdf, ops=["mean"], output="pandas"),
                    )

                    vt_utc = _to_utc_datetime(vt)

                    for row_idx, cfg in enumerate(valid_configs):
                        rows_by_station[cfg.station_id].append(
                            {
                                "valid_time": vt_utc,
                                "parameter": param,
                                "member_id": int(member_val),
                                "value": float(extracted.iloc[row_idx]["mean"]),  # type: ignore[reportUnknownArgumentType]
                            }
                        )

        out_of_extent: list[str] = []
        for cfg in valid_configs:
            rows = rows_by_station[cfg.station_id]
            member_ids = {r["member_id"] for r in rows}
            missing_members: set[int] = {
                m
                for m in member_ids
                if all(math.isnan(r["value"]) for r in rows if r["member_id"] == m)
            }
            if missing_members and len(missing_members) == len(member_ids):
                out_of_extent.append(str(cfg.station_id))
                continue
            for m in sorted(missing_members):
                log.info(
                    "extraction.member_skipped",
                    polygon_id=str(cfg.station_id),
                    member_id=m,
                    cycle_time=str(cycle_time),
                )
            if missing_members:
                rows_by_station[cfg.station_id] = [
                    r for r in rows if r["member_id"] not in missing_members
                ]

        if out_of_extent:
            log.error(
                "extraction.polygon_outside_extent",
                polygon_ids=out_of_extent,
                cycle_time=str(cycle_time),
            )
            raise ExtractionError(f"polygon(s) outside grid extent: {out_of_extent}")

        output: dict[StationId, BasinAverageForecast | ElevationBandForecast] = {}
        for cfg in valid_configs:
            rows = rows_by_station[cfg.station_id]
            df = pl.DataFrame(
                rows,
                schema={
                    "valid_time": pl.Datetime("us", "UTC"),
                    "parameter": pl.Utf8,
                    "member_id": pl.Int64,
                    "value": pl.Float64,
                },
            )
            output[cfg.station_id] = BasinAverageForecast(
                nwp_source=nwp_source,
                cycle_time=cycle_time,
                values=df,
            )

        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        log.info(
            "extraction.completed",
            duration_ms=duration_ms,
            stations_extracted=len(output),
            stations_skipped=skipped,
        )
        return output
