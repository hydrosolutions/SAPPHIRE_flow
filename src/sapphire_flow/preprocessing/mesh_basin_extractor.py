# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportPrivateUsage=false
from __future__ import annotations

import math
import time
import warnings
from typing import TYPE_CHECKING, Any

import geopandas as gpd
import numpy as np
import polars as pl
import shapely
import structlog
from shapely.geometry import MultiPolygon, Polygon

from sapphire_flow.exceptions import ExtractionError
from sapphire_flow.preprocessing.exact_extract_grid_extractor import _to_utc_datetime
from sapphire_flow.types.weather import BasinAverageForecast

if TYPE_CHECKING:
    import xarray as xr

    from sapphire_flow.types.basin import Basin
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationWeatherSource
    from sapphire_flow.types.weather import ElevationBandForecast

log = structlog.get_logger(__name__)

# Plan 087 D-fallback: a basin smaller than one ~2 km ICON cell captures zero
# cell centroids. It snaps to its nearest cell centre iff within this distance
# (~2x the mean cell spacing; ~2 km cell ~= 0.018 deg). Beyond it, the basin is
# out of the mesh domain and raises ExtractionError (out_of_extent parity). A
# distance threshold is used over bbox containment because the mesh domain is
# non-rectangular.
_MAX_NEAREST_CELL_DEG = 0.04


class MeshBasinExtractor:
    """Point-in-polygon basin extraction over an unstructured ICON mesh.

    Assigns each of the cube's ``values``-dim cell centres to a basin by
    point-in-polygon, then takes a count-weighted per-basin mean WITH NO
    REGRID, emitting the identical ``BasinAverageForecast`` schema as
    ``ExactExtractGridExtractor`` with the same out_of_extent / per-member-NaN
    contract and a small-basin nearest-cell fallback.
    """

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

        if "latitude" not in grid.coords or "longitude" not in grid.coords:
            raise ExtractionError(
                "mesh grid is missing latitude/longitude coords on the values dim; "
                "coord-attach must run before MeshBasinExtractor"
            )

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

        lon = np.asarray(grid["longitude"].values, dtype=np.float64)
        lat = np.asarray(grid["latitude"].values, dtype=np.float64)
        cell_points = shapely.points(lon, lat)

        cell_indices_by_station = self._assign_cells(valid_configs, basins, cell_points)

        members: np.ndarray[Any, Any] = (
            grid["member"].values if "member" in grid.dims else np.array([0])
        )
        has_member_dim = "member" in grid.dims
        valid_times: np.ndarray[Any, Any] = grid["valid_time"].values

        rows_by_station: dict[StationId, list[dict[str, Any]]] = {
            cfg.station_id: [] for cfg in valid_configs
        }

        for param in parameters:
            for cfg in valid_configs:
                cell_idx = cell_indices_by_station[cfg.station_id]
                mat = self._basin_mean(grid[param], cell_idx, has_member_dim)
                for member_pos, member_val in enumerate(members):
                    for vt_pos, vt in enumerate(valid_times):
                        rows_by_station[cfg.station_id].append(
                            {
                                "valid_time": _to_utc_datetime(vt),
                                "parameter": param,
                                "member_id": int(member_val),
                                "value": float(mat[member_pos, vt_pos]),
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
            df = pl.DataFrame(
                rows_by_station[cfg.station_id],
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

    def _assign_cells(
        self,
        valid_configs: list[StationWeatherSource],
        basins: dict[StationId, Basin],
        cell_points: Any,
    ) -> dict[StationId, np.ndarray[Any, Any]]:
        cells_gdf = gpd.GeoDataFrame(
            {"cell": np.arange(len(cell_points))},
            geometry=gpd.GeoSeries(cell_points, crs="EPSG:4326"),
            crs="EPSG:4326",
        )
        basins_gdf = gpd.GeoDataFrame(
            {"station_idx": np.arange(len(valid_configs))},
            geometry=[basins[cfg.station_id].geometry for cfg in valid_configs],
            crs="EPSG:4326",
        )
        joined = gpd.sjoin(cells_gdf, basins_gdf, predicate="within", how="inner")

        result: dict[StationId, np.ndarray[Any, Any]] = {}
        tree: shapely.STRtree | None = None
        for station_idx, cfg in enumerate(valid_configs):
            matched = joined.loc[joined["station_idx"] == station_idx, "cell"]
            if len(matched) > 0:
                result[cfg.station_id] = np.asarray(matched.to_numpy(), dtype=np.int64)
                continue
            # Small-basin fallback: nearest cell via shapely.STRtree.nearest.
            if tree is None:
                tree = shapely.STRtree(cell_points)
            point = basins[cfg.station_id].geometry.representative_point()
            nearest_idx = int(tree.nearest(point))
            distance = float(shapely.distance(point, cell_points[nearest_idx]))
            if distance <= _MAX_NEAREST_CELL_DEG:
                result[cfg.station_id] = np.asarray([nearest_idx], dtype=np.int64)
            else:
                # Out of the mesh domain → empty assignment yields an all-NaN
                # mean, which the missing-member pass converts to out_of_extent.
                result[cfg.station_id] = np.asarray([], dtype=np.int64)
        return result

    def _basin_mean(
        self,
        da: xr.DataArray,
        cell_idx: np.ndarray[Any, Any],
        has_member_dim: bool,
    ) -> np.ndarray[Any, Any]:
        n_times = int(da.sizes["valid_time"])
        n_members = int(da.sizes["member"]) if has_member_dim else 1
        if cell_idx.size == 0:
            return np.full((n_members, n_times), np.nan, dtype=np.float64)
        sub = da.isel(values=cell_idx)
        with warnings.catch_warnings():
            # All-NaN slice over `values` → NaN mean (caught downstream as a
            # missing member); numpy emits a benign "Mean of empty slice" warning.
            warnings.simplefilter("ignore", category=RuntimeWarning)
            mean_da = sub.mean(dim="values", skipna=True)
        if has_member_dim:
            mean_da = mean_da.transpose("member", "valid_time")
            return np.asarray(mean_da.values, dtype=np.float64)
        mean_da = mean_da.transpose("valid_time")
        return np.asarray(mean_da.values, dtype=np.float64).reshape(1, n_times)
