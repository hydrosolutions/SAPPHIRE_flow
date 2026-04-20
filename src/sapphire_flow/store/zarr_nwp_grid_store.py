from __future__ import annotations

import os
import re
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

# CONSTRAINT: all per-cycle paths (symlink, versioned dirs, _tmp) must reside on
# the SAME filesystem as `base_path`. In Docker, this means the `nwp_grids` named
# volume — NOT /tmp — because os.replace is only atomic within one filesystem.

_VERSIONED_DIR_RE = re.compile(r"^(?P<stem>.+)_v(?P<v>\d+)$")
_STALE_TMP_MAX_AGE_S = 3600


def _safe_zarr_path(base_path: Path, nwp_source: str, cycle_time: UtcDatetime) -> Path:
    safe_source = Path(nwp_source).name
    zarr_path = base_path / f"{safe_source}/{cycle_time:%Y%m%dT%H}.zarr"
    if not zarr_path.resolve().is_relative_to(base_path.resolve()):
        raise StoreError(f"Path traversal detected for nwp_source={nwp_source!r}")
    return zarr_path


def _latest_version(parent: Path, cycle_stem: str) -> int:
    if not parent.exists():
        return 0
    versions: list[int] = []
    for child in parent.iterdir():
        if not child.is_dir() or child.name.endswith("_tmp"):
            continue
        m = _VERSIONED_DIR_RE.match(child.name)
        if m and m.group("stem") == cycle_stem:
            versions.append(int(m.group("v")))
    return max(versions, default=0)


def _cleanup_stale_artifacts(
    parent: Path, cycle_stem: str, current_version: int
) -> None:
    if not parent.exists():
        return
    now = time.time()
    for child in list(parent.iterdir()):
        name = child.name
        if name == f"{cycle_stem}_tmp_symlink" and child.is_symlink():
            age = now - child.lstat().st_mtime
            if age > _STALE_TMP_MAX_AGE_S:
                child.unlink(missing_ok=True)
                log.warning("nwp.stale_tmp_removed", path=str(child))
            continue
        if (
            name.startswith(cycle_stem + "_v")
            and name.endswith("_tmp")
            and child.is_dir()
        ):
            try:
                age = now - child.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > _STALE_TMP_MAX_AGE_S:
                shutil.rmtree(child, ignore_errors=True)
                log.warning("nwp.stale_tmp_removed", path=str(child))
            continue
        if name == f"{cycle_stem}.zarr.old" and child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            continue
        m = _VERSIONED_DIR_RE.match(name)
        if m and m.group("stem") == cycle_stem and child.is_dir():
            v = int(m.group("v"))
            if v < current_version - 1:
                shutil.rmtree(child, ignore_errors=True)
                log.info("nwp.old_version_removed", path=str(child), version=v)


class ZarrNwpGridStore:
    def archive(self, forecast: GriddedForecast, base_path: Path) -> Path:
        zarr_path = _safe_zarr_path(base_path, forecast.nwp_source, forecast.cycle_time)
        parent = zarr_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        cycle_stem = zarr_path.stem

        prev_n = _latest_version(parent, cycle_stem)
        new_n = prev_n + 1
        versioned_dir = parent / f"{cycle_stem}_v{new_n}"
        tmp_dir = parent / f"{cycle_stem}_v{new_n}_tmp"
        tmp_symlink = parent / f"{cycle_stem}_tmp_symlink"

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

        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        if tmp_symlink.is_symlink() or tmp_symlink.exists():
            tmp_symlink.unlink(missing_ok=True)

        ds.to_zarr(
            tmp_dir,
            mode="w",
            consolidated=True,
            encoding=encoding,
            zarr_format=2,
        )  # pyright: ignore[reportUnknownMemberType, reportArgumentType]

        try:
            tmp_dir.rename(versioned_dir)
            tmp_symlink.symlink_to(versioned_dir.name)
            os.replace(tmp_symlink, zarr_path)
        except Exception:
            log.warning("nwp.swap_failed_cleanup", zarr_path=str(zarr_path))
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            if tmp_symlink.is_symlink() or tmp_symlink.exists():
                tmp_symlink.unlink(missing_ok=True)
            if versioned_dir.exists() and prev_n == 0:
                shutil.rmtree(versioned_dir, ignore_errors=True)
            raise

        log.info("nwp.archive_swapped", zarr_path=str(zarr_path), version=new_n)

        _cleanup_stale_artifacts(parent, cycle_stem, current_version=new_n)

        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        size_bytes = sum(
            f.stat().st_size for f in versioned_dir.rglob("*") if f.is_file()
        )
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
