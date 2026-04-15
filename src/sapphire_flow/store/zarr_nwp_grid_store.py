from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

import numcodecs
import structlog
import xarray as xr

from sapphire_flow.exceptions import StoreError
from sapphire_flow.types.weather import GriddedForecast

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)


def _safe_zarr_path(base_path: Path, nwp_source: str, cycle_time: UtcDatetime) -> Path:
    safe_source = Path(nwp_source).name
    zarr_path = base_path / f"{safe_source}/{cycle_time:%Y%m%dT%H}.zarr"
    if not zarr_path.resolve().is_relative_to(base_path.resolve()):
        raise StoreError(f"Path traversal detected for nwp_source={nwp_source!r}")
    return zarr_path


class ZarrNwpGridStore:
    def archive(self, forecast: GriddedForecast, base_path: Path) -> Path:
        zarr_path = _safe_zarr_path(base_path, forecast.nwp_source, forecast.cycle_time)
        tmp_path = zarr_path.with_suffix(".zarr.tmp")
        old_path = zarr_path.with_suffix(".zarr.old")

        t0 = time.perf_counter()
        log.info("nwp.archive_started", zarr_path=str(zarr_path))

        ds = forecast.values
        encoding: dict[str, dict[str, object]] = {
            str(v): {
                "chunks": (1, *ds[v].shape[1:]),
                "compressor": numcodecs.Zstd(level=3),  # pyright: ignore[reportUnknownMemberType]
            }
            for v in ds.data_vars
        }

        zarr_path.parent.mkdir(parents=True, exist_ok=True)
        ds.to_zarr(tmp_path, mode="w", consolidated=True, encoding=encoding)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]

        # Three-phase atomic swap
        if zarr_path.exists():
            zarr_path.rename(old_path)
        tmp_path.rename(zarr_path)
        if old_path.exists():
            shutil.rmtree(old_path)

        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        size_bytes = sum(f.stat().st_size for f in zarr_path.rglob("*") if f.is_file())
        log.info(
            "nwp.archive_completed",
            duration_ms=duration_ms,
            zarr_path=str(zarr_path),
            size_bytes=size_bytes,
        )
        return zarr_path

    def load(
        self, base_path: Path, nwp_source: str, cycle_time: UtcDatetime
    ) -> GriddedForecast:
        zarr_path = _safe_zarr_path(base_path, nwp_source, cycle_time)
        if not zarr_path.exists():
            log.warning("nwp.archive_not_found", zarr_path=str(zarr_path))
            raise StoreError(f"NWP archive not found: {zarr_path}")
        log.debug("nwp.archive_loaded", zarr_path=str(zarr_path))
        try:
            ds = xr.open_zarr(zarr_path, consolidated=True)  # pyright: ignore[reportUnknownMemberType]
        except Exception as exc:
            raise StoreError(f"Failed to load NWP archive {zarr_path}: {exc}") from exc
        return GriddedForecast(nwp_source=nwp_source, cycle_time=cycle_time, values=ds)
