# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
from __future__ import annotations

import shutil
import time
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlparse

import httpx
import numpy as np
import structlog
import xarray as xr

from sapphire_flow.exceptions import (
    AdapterError,
    BudgetExceededError,
    NoCycleAvailableError,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.weather import GriddedForecast

if TYPE_CHECKING:
    from collections.abc import Hashable

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationWeatherSource
    from sapphire_flow.types.weather import WeatherForecastResult

log = structlog.get_logger(__name__)

_MIN_ENSEMBLE_MEMBERS = 20

# ICON-CH2-EPS cycles publish every 6 h per the MeteoSwiss collection
# description (Plan 067 T1.b, D7). The old 3-hourly tuple caused
# _snap_to_cycle to snap to phantom slots (e.g. 21:00Z), burning a
# fallback step before reaching a real cycle.
_CYCLE_HOURS: tuple[int, ...] = (0, 6, 12, 18)
_CYCLE_INTERVAL_HOURS: int = 6

# Three-column: (STAC item-ID token, cfgrib shortName, typeOfLevel).
# Column 0 drives the STAC allowlist (client-side substring match on `-{token}-`).
# Columns 1-2 drive cfgrib `filter_by_keys` in `_parse_grib_files`.
# v0 minimum: 2 variables. Additional variables are one row each when a downstream
# model requires them (see project_nwp_v0_variable_allowlist memory):
#   ("h_snow", "sd", "surface"),
#   ("td_2m", "td_2m", "heightAboveGround"),
#   ("u_10m", "u_10m", "heightAboveGround"),
#   ("v_10m", "v_10m", "heightAboveGround"),
PARAM_GROUPS: list[tuple[str, str, str]] = [
    ("tot_prec", "tp", "surface"),
    # ICON-CH2-EPS 2-m temperature: WMO shortName is `2t`, cfgrib exposes
    # the data variable as `t2m` (CF convention). MeteoSwiss labels the
    # STAC item id with `-t_2m-` — that token lives in column 0.
    ("t_2m", "2t", "heightAboveGround"),
]

_DEFAULT_MAX_DOWNLOAD_BYTES: int = 4 * 1024 * 1024 * 1024  # 4 GB
_ASSET_SIZE_ESTIMATE_BYTES: int = 2 * 1024 * 1024  # 2 MB fallback
_MAX_FILE_COUNT: int = 500
_GRIB_MAGIC: bytes = b"GRIB"

# Pagination cap for _fetch_grib_files's 120 h-window walk.
# Plan 067 T1.f measured 552 pages for the current 4-cycle overlap at
# MeteoSwiss's 24 h retention; cap is sized at ~1.5x for safety margin.
# Server-side narrowing (CQL filter=forecast:reference_datetime=...) is NOT
# supported by MeteoSwiss (T1.e), so we always walk the full window.
# Raising this cap requires re-benchmarking pages observed at implementation
# time.
_MAX_PAGINATION_PAGES: int = 800

# Probe pagination cap: the availability probe walks up to this many pages
# to defeat MeteoSwiss's ref_dt-ascending item ordering (Phase 1 H-B).
# Newer cycles' step-0 items only surface after older cycles' items are
# exhausted, which at 100/page can be 40+ pages deep. 50 is a safe cap
# that keeps probe latency under ~10 s.
_MAX_PROBE_PAGES: int = 50


def _is_grib_asset(asset_key: str, asset: dict[str, object]) -> bool:
    media_type = str(asset.get("type", ""))
    href = str(asset.get("href", ""))
    href_path = urlparse(href).path
    return (
        media_type in ("application/x-grib2", "application/grib")
        or href_path.endswith(".grib2")
        or asset_key.endswith(".grib2")
    )


def _verify_grib_magic(path: Path) -> None:
    with path.open("rb") as f:
        head = f.read(4)
    if head != _GRIB_MAGIC:
        log.error("nwp.download_truncated", path=str(path), head=head.hex())
        raise AdapterError(f"truncated or non-GRIB2 download: {path}")


def _deaccumulate_precipitation(ds: xr.Dataset) -> xr.Dataset:
    ds["precipitation"] = (
        ds["tp"].pad({"valid_time": (1, 0)}, constant_values=0).diff("valid_time")
    )
    ds = ds.drop_vars(["tp"])
    return ds


def _convert_units(ds: xr.Dataset) -> xr.Dataset:
    # cfgrib exposes 2-m temperature as data var `t2m` (CF convention), not
    # `t_2m` — the latter is only the MeteoSwiss STAC item-id token.
    if "t2m" in ds:
        ds["temperature"] = ds["t2m"] - 273.15
        ds = ds.drop_vars(["t2m"])
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


def _normalise_number_dim(ds: xr.Dataset) -> xr.Dataset:
    """Ensure ``number`` is a 1-D dim (not a scalar coord).

    MeteoSwiss ICON-CH2-EPS publishes one file per GRIB message.
    Ctrl files expose ``number`` as a *scalar* coord (value 0). Perturb
    files expose ``number`` as a 1-D dim (length 20, values 1..20).
    For uniform concatenation we promote scalars to size-1 dims.
    """
    if "number" not in ds.coords:
        return ds
    if "number" in ds.dims:
        return ds
    # Scalar coord → promote to size-1 `number` dim.
    return ds.expand_dims("number")


def _combine_cfgrib_datasets(per_file: list[xr.Dataset]) -> xr.Dataset:
    """Combine per-file cfgrib datasets into a ``(number, valid_time, …)`` dataset.

    Each input dataset is one MeteoSwiss ICON-CH2-EPS GRIB message. Ctrl
    files have a scalar ``number`` coord; perturb files already carry a
    1-D ``number`` dim with 20 members. We normalise each file to a 1-D
    ``number`` dim, group by ``valid_time`` (which is scalar per file),
    concat each group along ``number``, then concat those groups along
    ``valid_time`` in monotonic order.

    The ensemble-member dim stays as ``number`` (cfgrib's ECMWF
    convention); downstream ``convert_raw_dataset`` renames it to
    ``member`` to match the extractor's expectation.
    """
    normalised = [_normalise_number_dim(d) for d in per_file]

    by_valid_time: dict[Hashable, list[xr.Dataset]] = {}
    for d in normalised:
        vt = d.coords["valid_time"].values.item()
        by_valid_time.setdefault(vt, []).append(d)

    per_time: list[xr.Dataset] = []
    for vt in sorted(by_valid_time.keys()):
        group = by_valid_time[vt]
        if len(group) == 1:
            combined = group[0]
        else:
            # Sort by first member value so the concat produces a monotonic
            # `number` axis (ctrl=0, perturb=1..20). Drop the scalar
            # `valid_time` / `step` / `time` coords from each file first:
            # they are identical across the group (we just grouped by
            # valid_time) and xr.concat would otherwise broadcast them into
            # 1-D coords indexed by `number`, which blocks the subsequent
            # `expand_dims("valid_time")`.
            scalar_drop = [
                c for c in ("valid_time", "step", "time") if c in group[0].coords
            ]
            # Preserve dtype-correct DataArrays (timedelta64 for `step`,
            # datetime64 for `valid_time`/`time`). Using `.values.item()`
            # would collapse timedelta64 to int, which later confuses
            # xarray's dtype promotion.
            shared_coords = {c: group[0].coords[c] for c in scalar_drop}
            stripped = [d.drop_vars(scalar_drop) for d in group]
            stripped_sorted = sorted(
                stripped, key=lambda d: int(d.coords["number"].values[0])
            )
            combined = xr.concat(
                stripped_sorted,
                dim="number",
                coords="all",
                compat="override",
                data_vars="all",
                join="outer",
            )
            # Re-attach the shared scalar coords.
            combined = combined.assign_coords(shared_coords)
        # Promote `valid_time` from a scalar coord to a size-1 dim so the
        # outer concat can extend along it without colliding with the
        # existing coord name.
        if "valid_time" not in combined.dims:
            combined = combined.expand_dims("valid_time")
        per_time.append(combined)

    if len(per_time) == 1:
        return per_time[0]

    return xr.concat(
        per_time,
        dim="valid_time",
        coords="all",
        compat="override",
        data_vars="all",
        join="outer",
    )


class MeteoSwissNwpAdapter:
    """ICON-CH2-EPS adapter.

    Caller contract: `http_client` must carry an explicit timeout, e.g.
    ``httpx.Client(timeout=httpx.Timeout(
        connect=10.0, read=300.0, write=None, pool=5.0))``.
    """

    NWP_SOURCE: ClassVar[str] = "icon_ch2_eps"

    PARAM_GROUPS: ClassVar[list[tuple[str, str, str]]] = PARAM_GROUPS

    def __init__(
        self,
        *,
        stac_base_url: str,
        stac_collection: str,
        scratch_path: Path,
        http_client: httpx.Client,
        max_download_bytes: int = _DEFAULT_MAX_DOWNLOAD_BYTES,
        cleanup_scratch_on_fetch: bool = True,
        max_fallback_steps: int = 2,
    ) -> None:
        # Plan 067 D2: max_fallback_steps is derived by the caller from
        # ``DeploymentConfig.nwp_max_fallback_age_hours`` as
        # ``math.ceil(age / 6.0)`` (ICON-CH2-EPS publishes every 6 h; see
        # Plan 067 T1.b). Default of 2 matches the default
        # ``nwp_max_fallback_age_hours=12.0`` policy (12 / 6 = 2) and exists
        # only for test convenience — production callers must pass the derived
        # value explicitly.
        self._stac_base_url = stac_base_url.rstrip("/")
        self._stac_collection = stac_collection
        self._scratch_path = scratch_path
        self._http_client = http_client
        self._max_download_bytes = max_download_bytes
        self._cleanup_scratch_on_fetch = cleanup_scratch_on_fetch
        self._max_fallback_steps = max_fallback_steps

    @property
    def max_fallback_steps(self) -> int:
        return self._max_fallback_steps

    def resolve_cycle_time(self, now_utc: UtcDatetime) -> UtcDatetime:
        if now_utc.tzinfo is None:
            raise ValueError(
                f"resolve_cycle_time requires tz-aware input, got {now_utc!r}"
            )
        snapped = self._snap_to_cycle(now_utc)
        candidate = snapped
        for step in range(self._max_fallback_steps + 1):
            if self._cycle_is_published(candidate):
                if step > 0:
                    log.warning(
                        "nwp.cycle_fallback_used",
                        snapped_cycle=snapped.isoformat(),
                        resolved_cycle=candidate.isoformat(),
                        fallback_steps=step,
                    )
                return candidate
            candidate = ensure_utc(candidate - timedelta(hours=_CYCLE_INTERVAL_HOURS))
        raise NoCycleAvailableError(
            f"No cycle available within {self._max_fallback_steps} fallback steps "
            f"from {snapped.isoformat()}"
        )

    @staticmethod
    def _snap_to_cycle(now_utc: UtcDatetime) -> UtcDatetime:
        cycle_hour = max(h for h in _CYCLE_HOURS if h <= now_utc.hour)
        return ensure_utc(
            now_utc.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)
        )

    def _cycle_is_published(self, cycle: UtcDatetime) -> bool:
        # T2a (Plan 067, revised 2026-04-23 post-Sprint-1.3 finding):
        # property-based match on forecast:reference_datetime, paginated up
        # to _MAX_PROBE_PAGES. The property check alone is NOT sufficient
        # — MeteoSwiss sorts items ref_dt-ascending so newer cycles' items
        # don't land on page 1; single-page check was returning False for
        # published cycles. Walk the rel=next chain until we see the target
        # ref_dt or exhaust the cap.
        cycle_iso = cycle.strftime("%Y-%m-%dT%H:%M:%SZ")
        url: str | None = (
            f"{self._stac_base_url}/collections/{self._stac_collection}/items"
            f"?datetime={cycle_iso}&limit=100"
        )
        pages_walked = 0
        while url and pages_walked < _MAX_PROBE_PAGES:
            pages_walked += 1
            try:
                resp = self._http_client.get(url)
                resp.raise_for_status()
            except Exception as exc:
                raise AdapterError(f"STAC availability probe failed: {exc}") from exc
            data = resp.json()
            features = data.get("features", [])
            if any(
                f.get("properties", {}).get("forecast:reference_datetime") == cycle_iso
                for f in features
            ):
                return True
            next_links = [
                link["href"]
                for link in data.get("links", [])
                if link.get("rel") == "next"
            ]
            url = next_links[0] if next_links else None
        return False

    def fetch_forecasts(
        self,
        station_configs: list[StationWeatherSource],  # noqa: ARG002
        cycle_time: UtcDatetime,
    ) -> GriddedForecast | dict[StationId, WeatherForecastResult]:
        resolved_cycle = self.resolve_cycle_time(cycle_time)
        log.info(
            "nwp.cycle_resolved",
            requested_cycle=cycle_time.isoformat(),
            resolved_cycle=resolved_cycle.isoformat(),
        )
        log.info(
            "nwp.fetch_started",
            nwp_source=self.NWP_SOURCE,
            cycle_time=resolved_cycle.isoformat(),
        )
        t0 = time.perf_counter()
        try:
            grib_files = self._fetch_grib_files(resolved_cycle)
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
                cycle_time=resolved_cycle,
                values=ds,
            )
        except AdapterError:
            raise
        except Exception as exc:
            log.warning("nwp.fetch_failed", error=str(exc))
            raise AdapterError(f"NWP fetch failed: {exc}") from exc

    def _fetch_grib_files(self, cycle_time: UtcDatetime) -> list[Path]:
        scratch_dir = self._scratch_path / cycle_time.strftime("%Y%m%dT%H%M")
        if self._cleanup_scratch_on_fetch:
            shutil.rmtree(scratch_dir, ignore_errors=True)
        scratch_dir.mkdir(parents=True, exist_ok=True)

        target_ref_dt = cycle_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        allow_tokens: list[str] = [row[0] for row in self.PARAM_GROUPS]

        window_end = cycle_time + timedelta(hours=120)
        datetime_q = f"{cycle_time:%Y-%m-%dT%H:%M:%SZ}/{window_end:%Y-%m-%dT%H:%M:%SZ}"
        url: str = (
            f"{self._stac_base_url}/collections/{self._stac_collection}/items"
            f"?datetime={datetime_q}&limit=100"
        )

        # T4b (Plan 067): server-side variable-name CQL filter
        # (e.g. filter=id LIKE '%-t_2m-%') would reduce per-cycle item count ~20x
        # (2 allowlisted variables vs ~40 total) but MeteoSwiss does not advertise
        # CQL-2 conformance and silently ignores filter= on /items (Phase 1 T1.e).
        # Allowlist stays client-side (see _is_grib_asset at line ~58 and
        # PARAM_GROUPS at line ~47). No behaviour change; this comment is the
        # T4b deliverable.
        grib_files: list[Path] = []
        accumulated_bytes = 0
        page_count = 0
        while url:
            page_count += 1
            if page_count > _MAX_PAGINATION_PAGES:
                raise AdapterError(
                    f"STAC pagination exceeded {_MAX_PAGINATION_PAGES} pages"
                )
            try:
                resp = self._http_client.get(url)
                resp.raise_for_status()
            except httpx.TimeoutException as exc:
                raise AdapterError(f"STAC request timed out: {exc}") from exc
            except Exception as exc:
                raise AdapterError(f"STAC request failed: {exc}") from exc

            data = resp.json()
            for item in data.get("features", []):
                item_id = str(item.get("id", ""))
                # T2b (Plan 067): filter by forecast:reference_datetime property,
                # not ID prefix. MeteoSwiss STAC does not support CQL (Phase 1
                # T1.e confirmed: `filter=` is silently ignored on /items and
                # POST /search returns HTTP 400 "non-queriable parameter: filter"),
                # so the `?datetime=<cycle>/<cycle+120h>` range returns items
                # from every cycle whose forecast horizon overlaps that window.
                # Phase 1 H-C confirmed: 4 distinct ref_dts observed; only ~27.6%
                # belong to the target cycle. Drop the rest here — the server
                # won't do it for us. Property-based match also removes the
                # latent coupling to the undocumented item-ID convention
                # (Phase 1 T1.d).
                feature_ref_dt = item.get("properties", {}).get(
                    "forecast:reference_datetime"
                )
                if feature_ref_dt != target_ref_dt:
                    continue
                if not any(f"-{t}-" in item_id for t in allow_tokens):
                    log.debug(
                        "nwp.variable_skipped",
                        item_id=item_id,
                        reason="not_in_allowlist",
                    )
                    continue
                for asset_key, asset in item.get("assets", {}).items():
                    if not _is_grib_asset(asset_key, asset):
                        continue
                    asset_size = asset.get("size")
                    bytes_add = (
                        int(asset_size)
                        if isinstance(asset_size, int)
                        else _ASSET_SIZE_ESTIMATE_BYTES
                    )
                    if accumulated_bytes + bytes_add > self._max_download_bytes:
                        log.error(
                            "nwp.size_cap_exceeded",
                            accumulated_bytes=accumulated_bytes,
                            max_download_bytes=self._max_download_bytes,
                            item_id=item_id,
                        )
                        raise BudgetExceededError(
                            f"Download size cap exceeded: "
                            f"{accumulated_bytes + bytes_add} "
                            f"> {self._max_download_bytes}"
                        )
                    href = str(asset.get("href", ""))
                    file_path = self._download_asset(href, asset_key, scratch_dir)
                    _verify_grib_magic(file_path)
                    grib_files.append(file_path)
                    accumulated_bytes += bytes_add
                    log.debug(
                        "nwp.file_downloaded",
                        href=href,
                        local_path=str(file_path),
                    )
                    if len(grib_files) > _MAX_FILE_COUNT:
                        raise BudgetExceededError(
                            f"GRIB file count exceeded: "
                            f"{len(grib_files)} > {_MAX_FILE_COUNT}"
                        )

            url = ""
            for link in data.get("links", []):
                if link.get("rel") == "next":
                    url = str(link["href"])
                    if not url.startswith(self._stac_base_url + "/"):
                        raise AdapterError(
                            f"STAC pagination URL {url!r} does not match base URL"
                        )
                    break

        if not grib_files:
            raise AdapterError(
                f"No matching GRIB2 files for cycle_time={cycle_time.isoformat()} "
                f"(allowlist tokens: {allow_tokens})"
            )
        return grib_files

    def _download_asset(self, href: str, asset_key: str, scratch_dir: Path) -> Path:
        if not href.startswith("https://"):
            raise AdapterError(f"Refusing non-HTTPS asset URL: {href!r}")
        url_path = urlparse(href).path
        file_name = url_path.rsplit("/", 1)[-1] or f"{asset_key}.grib2"
        file_name = Path(file_name).name
        dest = scratch_dir / file_name
        if not dest.resolve().is_relative_to(scratch_dir.resolve()):
            raise AdapterError(f"Path traversal in asset href: {href!r}")
        try:
            with self._http_client.stream("GET", href) as resp:
                resp.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=8192):
                        f.write(chunk)
        except httpx.TimeoutException as exc:
            raise AdapterError(f"Download timed out for {href}: {exc}") from exc
        except Exception as exc:
            raise AdapterError(f"Download failed for {href}: {exc}") from exc
        return dest

    def _parse_grib_files(self, grib_files: list[Path]) -> xr.Dataset:
        # MeteoSwiss ICON-CH2-EPS publishes one GRIB message per file (one
        # member × one step × one variable × one 2D grid). `xr.open_mfdataset`
        # with combine="nested"/concat_dim="valid_time" is not the right tool:
        # its compat/coords/data_vars kwarg semantics collide when scalar
        # coords (member, step, valid_time) vary per file. We open each file
        # individually, group matched datasets by member, concat each group
        # along valid_time (sorted), then concat members.
        datasets: list[xr.Dataset] = []
        str_paths = [str(p) for p in grib_files]
        for _stac_token, short_name, type_of_level in self.PARAM_GROUPS:
            per_file: list[xr.Dataset] = []
            for p in str_paths:
                try:
                    d = xr.open_dataset(
                        p,
                        engine="cfgrib",
                        backend_kwargs={
                            "filter_by_keys": {
                                "shortName": short_name,
                                "typeOfLevel": type_of_level,
                            },
                            # Disable cfgrib .idx cache: the scratch path is a
                            # container-local /tmp and index files must not
                            # persist (read-only FS can fail the open).
                            "indexpath": "",
                        },
                    )
                except Exception:
                    # File doesn't contain this var/level — cfgrib raises.
                    continue
                # Filter may match metadata-only with empty data_vars — skip.
                if not d.data_vars:
                    d.close()
                    continue
                per_file.append(d)

            if not per_file:
                log.debug(
                    "nwp.param_parse_skipped",
                    short_name=short_name,
                    type_of_level=type_of_level,
                    error="no files matched filter",
                )
                continue

            try:
                combined = _combine_cfgrib_datasets(per_file)
            except Exception as exc:
                log.debug(
                    "nwp.param_parse_skipped",
                    short_name=short_name,
                    type_of_level=type_of_level,
                    error=str(exc),
                )
                for d in per_file:
                    d.close()
                continue

            datasets.append(combined)

        if not datasets:
            raise AdapterError("No parameter groups could be parsed from GRIB2 files")

        merged = xr.merge(datasets, compat="override")
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
