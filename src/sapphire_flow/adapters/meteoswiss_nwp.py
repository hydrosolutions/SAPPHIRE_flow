# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
from __future__ import annotations

import time
from typing import TYPE_CHECKING, ClassVar

import numpy as np
import structlog
import xarray as xr

from sapphire_flow.exceptions import AdapterError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.weather import GriddedForecast

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationWeatherSource
    from sapphire_flow.types.weather import WeatherForecastResult

log = structlog.get_logger(__name__)

_MIN_ENSEMBLE_MEMBERS = 20

PARAM_GROUPS: list[tuple[str, str]] = [
    ("tp", "surface"),
    ("t_2m", "heightAboveGround"),
    ("relhum_2m", "heightAboveGround"),
    ("u_10m", "heightAboveGround"),
    ("v_10m", "heightAboveGround"),
    ("sd", "surface"),
]


def _deaccumulate_precipitation(ds: xr.Dataset) -> xr.Dataset:
    ds["precipitation"] = (
        ds["tp"].pad({"valid_time": (1, 0)}, constant_values=0).diff("valid_time")
    )
    ds = ds.drop_vars(["tp"])
    return ds


def _convert_units(ds: xr.Dataset) -> xr.Dataset:
    if "t_2m" in ds:
        ds["temperature"] = ds["t_2m"] - 273.15
        ds = ds.drop_vars(["t_2m"])
    if "sd" in ds:
        ds["snow_depth"] = ds["sd"] * 100
        ds = ds.drop_vars(["sd"])
    if "relhum_2m" in ds:
        ds["humidity"] = ds["relhum_2m"]
        ds = ds.drop_vars(["relhum_2m"])
    return ds


def _compute_wind_speed(ds: xr.Dataset) -> xr.Dataset:
    if "u_10m" in ds and "v_10m" in ds:
        ds["wind_speed"] = np.sqrt(ds["u_10m"] ** 2 + ds["v_10m"] ** 2)
        ds = ds.drop_vars(["u_10m", "v_10m"])
    return ds


def convert_raw_dataset(ds: xr.Dataset) -> xr.Dataset:
    if "tp" in ds:
        ds = _deaccumulate_precipitation(ds)
    ds = _convert_units(ds)
    ds = _compute_wind_speed(ds)
    if "number" in ds.dims:
        ds = ds.rename({"number": "member"})
    return ds


class MeteoSwissNwpAdapter:
    NWP_SOURCE: ClassVar[str] = "icon_ch2_eps"

    PARAM_GROUPS: ClassVar[list[tuple[str, str]]] = PARAM_GROUPS

    def __init__(
        self,
        *,
        stac_base_url: str,
        stac_collection: str,
        scratch_path: Path,
        http_client: httpx.Client,
    ) -> None:
        self._stac_base_url = stac_base_url.rstrip("/")
        self._stac_collection = stac_collection
        self._scratch_path = scratch_path
        self._http_client = http_client

    def fetch_forecasts(
        self,
        station_configs: list[StationWeatherSource],
        cycle_time: UtcDatetime,
    ) -> GriddedForecast | dict[StationId, WeatherForecastResult]:
        log.info(
            "nwp.fetch_started",
            nwp_source=self.NWP_SOURCE,
            cycle_time=str(cycle_time),
        )
        t0 = time.perf_counter()
        try:
            grib_files = self._fetch_grib_files(cycle_time)
            ds = self._parse_grib_files(grib_files)
            duration_ms = int((time.perf_counter() - t0) * 1000)
            log.info(
                "nwp.fetch_completed",
                duration_ms=duration_ms,
                file_count=len(grib_files),
                total_bytes=sum(f.stat().st_size for f in grib_files),
            )
            return GriddedForecast(
                nwp_source=self.NWP_SOURCE,
                cycle_time=ensure_utc(cycle_time),
                values=ds,
            )
        except AdapterError:
            raise
        except Exception as exc:
            log.warning("nwp.fetch_failed", error=str(exc))
            raise AdapterError(f"NWP fetch failed: {exc}") from exc

    def _fetch_grib_files(self, cycle_time: UtcDatetime) -> list[Path]:
        url = (
            f"{self._stac_base_url}/collections/{self._stac_collection}/items"
            f"?datetime={cycle_time.isoformat()}"
        )
        items: list[dict] = []  # type: ignore[type-arg]
        while url:
            try:
                resp = self._http_client.get(url)
                resp.raise_for_status()
            except Exception as exc:
                raise AdapterError(f"STAC request failed: {exc}") from exc

            data = resp.json()
            items.extend(data.get("features", []))

            url = ""
            for link in data.get("links", []):
                if link.get("rel") == "next":
                    url = link["href"]
                    break

        grib_files: list[Path] = []
        for item in items:
            assets = item.get("assets", {})
            for asset_key, asset in assets.items():
                media_type = asset.get("type", "")
                href = asset.get("href", "")
                if media_type == "application/x-grib2" or href.endswith(".grib2"):
                    file_path = self._download_asset(href, asset_key)
                    grib_files.append(file_path)
                    log.debug(
                        "nwp.file_downloaded",
                        href=href,
                        local_path=str(file_path),
                    )

        if not grib_files:
            raise AdapterError(
                f"No GRIB2 files found for cycle_time={cycle_time.isoformat()}"
            )
        return grib_files

    def _download_asset(self, href: str, asset_key: str) -> Path:
        from pathlib import Path

        file_name = href.split("/")[-1] or f"{asset_key}.grib2"
        dest = Path(self._scratch_path) / file_name
        try:
            with self._http_client.stream("GET", href) as resp:
                resp.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=8192):
                        f.write(chunk)
        except Exception as exc:
            raise AdapterError(f"Download failed for {href}: {exc}") from exc
        return dest

    def _parse_grib_files(self, grib_files: list[Path]) -> xr.Dataset:
        datasets: list[xr.Dataset] = []
        str_paths = [str(p) for p in grib_files]
        for short_name, type_of_level in self.PARAM_GROUPS:
            try:
                ds = xr.open_mfdataset(
                    str_paths,
                    engine="cfgrib",
                    combine="nested",
                    concat_dim="valid_time",
                    filter_by_keys={
                        "shortName": short_name,
                        "typeOfLevel": type_of_level,
                    },
                )
                datasets.append(ds)
            except Exception as exc:
                log.debug(
                    "nwp.param_parse_skipped",
                    short_name=short_name,
                    type_of_level=type_of_level,
                    error=str(exc),
                )
                continue

        if not datasets:
            raise AdapterError("No parameter groups could be parsed from GRIB2 files")

        merged = xr.merge(datasets)
        merged = convert_raw_dataset(merged)

        if "member" in merged.dims:
            n_members = merged.sizes["member"]
            if n_members < _MIN_ENSEMBLE_MEMBERS:
                raise AdapterError(
                    f"Only {n_members} ensemble members parsed, "
                    f"minimum {_MIN_ENSEMBLE_MEMBERS} required"
                )
            if n_members < 21:
                log.warning(
                    "nwp.missing_members",
                    found=n_members,
                    expected=21,
                )

        return merged
