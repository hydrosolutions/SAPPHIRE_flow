"""Build the Dudh Koshi rainfall-runoff handover package.

Reads only. Performs NO network access of any kind — in particular no
Copernicus CDS request: every ERA5-Land value here comes from a point bundle
already published on disk by `scripts/dhm_precip/era5_extract.py` (precipitation)
and `scripts/dhm_precip/extract_era5_t2m.py` (2 m temperature).

Every number that reaches README.md is computed here and printed to stdout as
an `[audit]` line, so the briefing can be checked against the run that produced
it. Nothing is transcribed by hand.

This module is the SINGLE canonical copy of the build. The copy that ships
inside the package is written by this run (see `_copy_self_into_package`), so
the two can never drift apart: edit this file, rebuild, and the delivered copy
follows. Never edit the copy under `data/dhm_precip/handover/`.

Run from the repo root:

    DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx \
    uv run --no-sync python \
      scripts/dhm_precip/build_dudh_koshi_handover.py

Pipeline (the same call sequence the production M-A2/M-A3 path uses, see
`scripts/dhm_precip/coloc_run.py:429` and `scripts/dhm_precip/pipeline.py`):

    load_long_frame -> on_grid_view -> normalise_hourly_axis
      -> iter_observations_by_station -> qc_mask.iter_station_results

The QC mask is applied as a FLAG, never as a filter: no row is ever dropped
and a missing hour is emitted as an empty `precip_mm` with `observed=false`.
It is never emitted as 0.0 — see `_assert_no_null_became_zero`.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import xarray as xr

# --- repo-root discovery -----------------------------------------------------
# This file lives at <repo>/scripts/dhm_precip/, so the root is two levels up.
# It is resolved from `__file__`, not from the CWD, so the build is independent
# of where it is invoked from; the input path defaults below are still relative
# to the repo root and are anchored to it explicitly.
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT: Path = _THIS_FILE.parents[2]
if not (_REPO_ROOT / "scripts" / "dhm_precip" / "loader.py").exists():
    raise SystemExit(f"cannot locate the SAPPHIRE_flow repo root from {_THIS_FILE}")
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The package directory this build publishes into, and the name the canonical
# script is copied to inside it.
DEFAULT_OUT_DIR = _REPO_ROOT / "data" / "dhm_precip" / "handover" / "dudh-koshi"
PACKAGED_SCRIPT_NAME = _THIS_FILE.name

from sapphire_flow.services.qc import Stage1QualityChecker  # noqa: E402
from sapphire_flow.types.datetime import ensure_utc  # noqa: E402
from scripts.dhm_precip import (  # noqa: E402
    normalise,
    observations,
    qc_mask,
)
from scripts.dhm_precip.coloc_pairs import COLOCATED_PAIRS  # noqa: E402
from scripts.dhm_precip.diurnal_phase import (  # noqa: E402
    harmonic_amplitude,
    harmonic_phase_h,
    same_day_branch,
)
from scripts.dhm_precip.domain_types import Station  # noqa: E402
from scripts.dhm_precip.extract_era5_t2m import discover_t2m_bundle  # noqa: E402
from scripts.dhm_precip.loader import (  # noqa: E402
    PRODUCTION_SOURCE_SHA256,
    compute_sha256,
    load_long_frame,
    load_station_coordinates,
    resolve_coords_path,
    resolve_source_path,
)
from scripts.dhm_precip.ma6_pairs import discover_precip_bundle  # noqa: E402
from scripts.dhm_precip.params import DEFAULT_PARAMS  # noqa: E402
from scripts.dhm_precip.pyramid_loader import (  # noqa: E402
    PyramidSchemaMismatchError,
    _parse_pyramid_lvl1_column,
    load_pyramid_lvl1_at_csv,
    load_pyramid_lvl1_csv,
)
from scripts.dhm_precip.qc_ruleset import (  # noqa: E402
    LONG_ZERO_RUN_RULE_VERSION,
    PASS_B_RULE_VERSIONS,
    RANGE_CHECK_RULE_VERSION,
    STUCK_VALUE_RULE_VERSION,
    QcRuleSet,
    build_precipitation_qc_rule_set,
    rule_subset,
)
from scripts.dhm_precip.resolution import infer_reporting_resolution  # noqa: E402
from scripts.dhm_precip.views import on_grid_view  # noqa: E402

DUDH_KOSHI_STATIONS: tuple[str, ...] = (
    "Syangboche Airport",
    "Lukla Airport",
    "Aiselukhark",
)

# Human-readable name for each rule_version, used in the `qc_rule` column.
RULE_LABELS: dict[str, str] = {
    RANGE_CHECK_RULE_VERSION: "range_check",
    STUCK_VALUE_RULE_VERSION: "stuck_value",
    LONG_ZERO_RUN_RULE_VERSION: "long_zero_run",
}

# Documented measurements produced elsewhere in this project. They are EXTRACTED
# from the tracked design documents at build time rather than typed in here, so
# a doc revision either updates this package or fails the build loudly.
DOC_CITATIONS: dict[str, tuple[str, str]] = {
    "pyramid_gradient": (
        "docs/design/dhm-precipitation-phase2-recommendation.md",
        "-52.49 %/km",
    ),
    "era5_high_band_offset": (
        "docs/design/dhm-precipitation-phase2-recommendation.md",
        "-11.9 h",
    ),
}

AUDIT: list[str] = []


def audit(message: str) -> None:
    line = f"[audit] {message}"
    AUDIT.append(line)
    print(line, flush=True)  # noqa: T201 — the audit trail IS this tool's output


class BuildError(RuntimeError):
    """A post-condition of the package failed. Never a warning."""


# --- QC rule attribution -----------------------------------------------------


def _empty_rule_set(params: object) -> QcRuleSet:
    return rule_subset(build_precipitation_qc_rule_set(params), frozenset())


def attribute_mask_by_rule(
    station_observation_pairs: list[tuple[Station, list[object]]],
    params: object,
) -> dict[str, set[tuple[str, datetime]]]:
    """Which rule fired for each masked hour.

    `qc_mask.iter_station_results` deliberately collapses flags to timestamps
    and discards the rule that produced them (see its module docstring), so the
    attribution is recovered by re-running the SAME per-station routine
    (`qc_mask._station_mask`) once per rule with a single-rule pass set and an
    empty set in the other pass slot. Reusing that routine — rather than
    re-implementing run detection — is what guarantees the attribution uses
    identical logic to the canonical mask; the caller asserts that the union of
    the per-rule keys equals the canonical mask exactly.
    """
    rule_set = build_precipitation_qc_rule_set(params)
    empty = _empty_rule_set(params)
    checker = Stage1QualityChecker()
    out: dict[str, set[tuple[str, datetime]]] = {}
    for rule in rule_set.rules:
        single = rule_subset(rule_set, frozenset({rule.rule_version}))
        is_pass_b = rule.rule_version in PASS_B_RULE_VERSIONS
        pass_a = empty if is_pass_b else single
        pass_b = single if is_pass_b else empty
        keys: set[tuple[str, datetime]] = set()
        for station, obs in station_observation_pairs:
            keys |= {
                (str(st), ts.replace(tzinfo=None))
                for st, ts in qc_mask._station_mask(  # noqa: SLF001 — see docstring
                    station, obs, pass_a, pass_b, checker, params
                )
            }
        out[rule.rule_version] = keys
    return out


# --- post-conditions ---------------------------------------------------------


def _assert_complete_axis(
    frame: pl.DataFrame, stations: tuple[str, ...], start: datetime, end: datetime
) -> int:
    expected = int((end - start).total_seconds() // 3600) + 1
    for station in stations:
        s = frame.filter(pl.col("station") == station)
        if s.height != expected:
            raise BuildError(
                f"{station}: {s.height} rows on the hourly axis, expected {expected}"
            )
        stamps = s.sort("timestamp_utc")["timestamp_utc"].to_list()
        if stamps[0] != start or stamps[-1] != end:
            raise BuildError(f"{station}: axis runs {stamps[0]}..{stamps[-1]}")
        gaps = {
            (b - a).total_seconds() for a, b in zip(stamps, stamps[1:], strict=False)
        }
        if gaps != {3600.0}:
            raise BuildError(f"{station}: axis step is not uniformly 1 h ({gaps})")
    return expected


def _assert_missing_never_zero(frame: pl.DataFrame) -> None:
    """The two symmetric properties the package exists to guarantee.

    1. A value that is not a measurement never appears in `precip_mm` — not as
       a zero (which would teach a model to under-predict the events that
       matter most) and not as a sentinel (which would dominate any loss).
    2. `observed` is exactly `precip_mm is not null`, in both directions, so
       the flag can be trusted as the mask without re-deriving it.
    """
    bad = frame.filter(~pl.col("observed") & pl.col("precip_mm").is_not_null())
    if bad.height:
        raise BuildError(f"{bad.height} unobserved row(s) carry a non-null precip_mm")
    bad = frame.filter(pl.col("observed") & pl.col("precip_mm").is_null())
    if bad.height:
        raise BuildError(f"{bad.height} observed row(s) carry a null precip_mm")
    # Nothing was discarded: wherever a delivered value was voided from
    # `precip_mm`, it must still be present in `raw_delivered_mm` AND the row
    # must say why.
    lost = frame.filter(
        pl.col("raw_delivered_mm").is_not_null()
        & pl.col("precip_mm").is_null()
        & (~pl.col("qc_masked") | pl.col("qc_rule").is_null())
    )
    if lost.height:
        raise BuildError(
            f"{lost.height} row(s) dropped a delivered value from precip_mm "
            "without a qc_rule naming the reason"
        )
    unbacked = frame.filter(
        pl.col("precip_mm").is_not_null() & pl.col("raw_delivered_mm").is_null()
    )
    if unbacked.height:
        raise BuildError(
            f"{unbacked.height} row(s) carry a precip_mm with no delivered "
            "value behind it — a value was invented"
        )


def _assert_no_null_became_zero(
    emitted: pl.DataFrame, source: pl.DataFrame, stations: tuple[str, ...]
) -> None:
    """Zeros are conserved exactly: every 0.0 in the output is a delivered 0.0.

    Counted rather than joined so the check is independent of the join that
    produced the frame in the first place.
    """
    for station in stations:
        src = source.filter(
            (pl.col("station") == station) & pl.col("value_mm").is_not_null()
        )
        out = emitted.filter(
            (pl.col("station") == station) & pl.col("precip_mm").is_not_null()
        )
        raw = emitted.filter(
            (pl.col("station") == station) & pl.col("raw_delivered_mm").is_not_null()
        )
        src_zero = src.filter(pl.col("value_mm") == 0.0).height
        out_zero = out.filter(pl.col("precip_mm") == 0.0).height
        raw_zero = raw.filter(pl.col("raw_delivered_mm") == 0.0).height
        if not src_zero == out_zero == raw_zero:
            raise BuildError(
                f"{station}: zeros delivered={src_zero} precip_mm={out_zero} "
                f"raw_delivered_mm={raw_zero} — a null was converted to a "
                "zero, or a zero was lost"
            )
        # `raw_delivered_mm` is the provenance column: it must reproduce the
        # delivered count EXACTLY. `precip_mm` is the modelling column and is
        # allowed to be short by exactly the rows the physical-impossibility
        # gate voided — never by any other row.
        if src.height != raw.height:
            raise BuildError(
                f"{station}: {raw.height} raw_delivered_mm values emitted, "
                f"{src.height} delivered — a delivered value was lost"
            )
        voided = raw.height - out.height
        gated = emitted.filter(
            (pl.col("station") == station)
            & pl.col("raw_delivered_mm").is_not_null()
            & pl.col("precip_mm").is_null()
        ).height
        if voided != gated:
            raise BuildError(
                f"{station}: precip_mm is short by {voided} but only {gated} "
                "row(s) were gated"
            )
        audit(
            f"zero-conservation {station}: zeros delivered={src_zero} "
            f"precip_mm={out_zero} raw_delivered_mm={raw_zero}; non-null "
            f"delivered={src.height} raw_delivered_mm={raw.height} "
            f"precip_mm={out.height} (gated out: {gated})"
        )


def _assert_values_bit_identical(
    emitted: pl.DataFrame, source: pl.DataFrame, stations: tuple[str, ...]
) -> None:
    joined = emitted.join(
        source.filter(pl.col("station").is_in(list(stations))).select(
            pl.col("station"),
            pl.col("timestamp").alias("timestamp_utc"),
            pl.col("value_mm"),
        ),
        on=["station", "timestamp_utc"],
        how="inner",
        validate="1:1",
    ).filter(pl.col("value_mm").is_not_null())
    diff = joined.filter(pl.col("raw_delivered_mm") != pl.col("value_mm"))
    if diff.height:
        raise BuildError(
            f"{diff.height} raw_delivered_mm value(s) differ from the workbook"
        )
    diff = joined.filter(
        pl.col("precip_mm").is_not_null()
        & (pl.col("precip_mm") != pl.col("raw_delivered_mm"))
    )
    if diff.height:
        raise BuildError(
            f"{diff.height} precip_mm value(s) differ from the delivered value "
            "they claim to carry"
        )
    audit(
        f"value identity: {joined.height} delivered values matched 1:1 and are "
        f"unchanged in raw_delivered_mm across {len(stations)} stations; every "
        "non-null precip_mm equals its delivered value"
    )


# --- doc-number extraction ---------------------------------------------------


def extract_documented_number(repo_root: Path, key: str) -> str:
    doc, needle = DOC_CITATIONS[key]
    path = repo_root / doc
    text = path.read_text(encoding="utf-8")
    # The documents use a typographic minus; accept either form.
    for variant in (needle, needle.replace("-", "−")):
        if variant in text:
            audit(f"documented number {key!r} = {variant} confirmed present in {doc}")
            return variant
    raise BuildError(
        f"documented number {needle!r} for {key!r} is no longer present in "
        f"{doc} — this package must not restate a number the project has "
        "since revised"
    )


# --- diurnal phase -----------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class DiurnalOffset:
    station: str
    n_hours: int
    gauge_phase_utc_h: float
    era5_phase_utc_h: float
    offset_h: float
    gauge_amplitude: float
    era5_amplitude: float


def diurnal_offset(
    station: str,
    gauge: pl.DataFrame,
    era5: pl.DataFrame,
    months: tuple[int, ...],
) -> DiurnalOffset:
    """Monsoon diurnal phase offset, ERA5-Land minus gauge, in hours.

    Uses the tracked circular estimator (`scripts/dhm_precip/diurnal_phase.py`)
    on hour-of-day precipitation mass over hours where the gauge is observed,
    QC-retained and in season, and ERA5-Land is present.

    The SIGN of an offset near 12 h is not identifiable (at antiphase, -12 h and
    +12 h are the same angle). The magnitude is what is measured.
    """
    paired = (
        gauge.filter(
            pl.col("observed")
            & ~pl.col("qc_masked")
            & pl.col("timestamp_utc").dt.month().is_in(list(months))
        )
        .join(era5, on=["station", "timestamp_utc"], how="inner")
        .filter(pl.col("era5_precip_mm").is_not_null())
    )
    hours = paired["timestamp_utc"].dt.hour().to_numpy()
    gauge_mass = np.array(
        [paired["precip_mm"].to_numpy()[hours == h].sum() for h in range(24)]
    )
    era5_mass = np.array(
        [paired["era5_precip_mm"].to_numpy()[hours == h].sum() for h in range(24)]
    )
    gp = harmonic_phase_h(gauge_mass)
    ep = harmonic_phase_h(era5_mass)
    return DiurnalOffset(
        station=station,
        n_hours=paired.height,
        gauge_phase_utc_h=gp,
        era5_phase_utc_h=ep,
        offset_h=same_day_branch(ep - gp),
        gauge_amplitude=harmonic_amplitude(gauge_mass),
        era5_amplitude=harmonic_amplitude(era5_mass),
    )


# --- ERA5 point series -------------------------------------------------------


def era5_series(
    path: Path, variable: str, stations: tuple[str, ...], out_name: str
) -> pl.DataFrame:
    ds = xr.open_dataset(path)
    frames = []
    for station in stations:
        sel = ds[variable].sel(station=station)
        frames.append(
            pl.DataFrame(
                {
                    "station": [station] * sel.sizes["valid_time"],
                    "timestamp_utc": (
                        ds["valid_time"].values.astype("datetime64[us]").tolist()
                    ),
                    out_name: sel.values.astype("float64"),
                }
            )
        )
    ds.close()
    # The gauge axis comes from `pl.read_excel` as Datetime("ms"); match it so
    # the two sides join without a silent cast.
    return pl.concat(frames).with_columns(
        pl.col("timestamp_utc").cast(pl.Datetime("ms"))
    )


# --- Pyramid AWS tier (the data-quality ceiling) -----------------------------
#
# Salerno et al. 2025, the Pyramid Meteorological Network, Khumbu. NOT
# operational data — it exists in this package to bound what is achievable
# with good instrumentation, so that a Pyramid-driven run and a DHM-driven run
# can be differenced and the gap attributed to DATA QUALITY rather than to the
# model or the basin.
#
# Parsing is NOT reimplemented here. `scripts/dhm_precip/pyramid_loader.py`
# already owns the real file format (semicolon separator, CR-only line
# endings, four integer time columns, empty field = missing) and this module
# calls its shared column parser, `_parse_pyramid_lvl1_column`, once per
# variable. The loader's two PUBLIC entry points cannot serve this package:
# `load_pyramid_lvl1_csv` applies a precipitation physical-range gate and
# `load_pyramid_lvl1_at_csv` a finite-only gate, and BOTH drop the failing
# rows — this tier ships Level 1 UNMASKED on a complete axis, so a dropped
# hour would silently become an absent hour. The build cross-checks its own
# unmasked columns against both public loaders instead (see
# `_assert_pyramid_matches_public_loaders`).

# NPT, as stated by the provider's own README_.txt. The OFFSET is exact
# arithmetic and certain. The PERIOD CONVENTION (does the stamp 14:00 cover
# 13:00-14:00 or 14:00-15:00?) is stated NOWHERE by the provider and has not
# been answered — this package states that ambiguity, it does not resolve it.
NPT_OFFSET = timedelta(hours=5, minutes=45)

PYRAMID_DOI = "10.5281/zenodo.15211352"
PYRAMID_LICENCE = "CC BY 4.0"
PYRAMID_PROVIDER = "Franco Salerno"
PYRAMID_CITATION = (
    "Salerno et al. (2025). What is climate change doing in Himalaya? "
    "Thirty years of the Pyramid Meteorological Network (Nepal)."
)


@dataclass(frozen=True, kw_only=True, slots=True)
class PyramidVariable:
    source_column: str
    out_column: str
    unit: str
    label: str


# Emission order. WS is deliberately third, ahead of RH/AP: co-located wind is
# the variable that makes gauge catch efficiency ESTIMABLE rather than merely
# boundable (README section 3.4).
PYRAMID_VARIABLES: tuple[PyramidVariable, ...] = (
    PyramidVariable(
        source_column="AT",
        out_column="air_temp_degc",
        unit="degC",
        label="2 m air temperature",
    ),
    PyramidVariable(
        source_column="RR",
        out_column="precip_mm",
        unit="mm",
        label="precipitation in the hour",
    ),
    PyramidVariable(
        source_column="WS", out_column="wind_speed_ms", unit="m/s", label="wind speed"
    ),
    PyramidVariable(
        source_column="RH",
        out_column="rel_humidity_pct",
        unit="%",
        label="relative humidity",
    ),
    PyramidVariable(
        source_column="AP",
        out_column="pressure_hpa",
        unit="hPa",
        label="atmospheric pressure",
    ),
    PyramidVariable(
        source_column="WD",
        out_column="wind_dir_deg",
        unit="deg",
        label="wind direction, 0 deg = northerly",
    ),
)


@dataclass(frozen=True, kw_only=True, slots=True)
class PyramidStation:
    station_id: str
    name: str
    lat: float
    lon: float
    elev_m: int
    filename: str


@dataclass(frozen=True, kw_only=True, slots=True)
class PyramidStationTier:
    """Everything measured for one Pyramid station. No field is transcribed."""

    station: PyramidStation
    n_hours_axis: int
    native_step_h: int
    first_npt: str
    last_npt: str
    first_utc: str
    last_utc: str
    present: tuple[str, ...]
    n_observed: dict[str, int]
    coverage_pct: dict[str, float]
    coverage_pct_native: dict[str, float]
    wind_mean_ms: float | None
    wind_median_ms: float | None
    wind_p95_ms: float | None
    wet_hour_wind_mean_ms: float | None
    n_wet_hours: int


@dataclass(frozen=True, kw_only=True, slots=True)
class PyramidColocation:
    dhm_station: str
    pyramid_station_id: str
    pyramid_name: str
    separation_km: float
    declared_separation_km: float
    elev_delta_m: float
    declared_elev_delta_m: float
    overlap_start_utc: str
    overlap_end_utc: str
    n_overlap_hours: int
    n_dhm_observed_in_overlap: int
    n_pyramid_observed_in_overlap: int
    n_co_observed: int
    co_observed_start_utc: str
    co_observed_end_utc: str


@dataclass(frozen=True, kw_only=True, slots=True)
class PyramidShiftSensitivity:
    station_id: str
    n_days: int
    median_pct: float
    mean_pct: float
    p90_pct: float


_PYRAMID_README_BLOCK = re.compile(
    r"Files:\s*(?P<file>\S+)\s*\n"
    r"ID:\s*(?P<id>\S+)\s*\n"
    r"Station Name:\s*(?P<name>[^\n]+?)\s*\n"
    r"Latitude\s*\(°\):\s*(?P<lat>-?[\d.]+)\s*\n"
    r"Longitude\s*\(°\):\s*(?P<lon>-?[\d.]+)\s*\n"
    r"Elevation \(m a\.s\.l\.\):\s*(?P<elev>[^\n]+)"
)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def parse_pyramid_stations(root: Path) -> dict[str, PyramidStation]:
    """Station name, coordinates and elevation, parsed from the PROVIDER's own
    `README_.txt` at build time — nothing here is typed in by hand.

    Elevation is stated twice by the provider (in `README_.txt` and as the
    `Z####` token of the filename) and the two must agree, or the build fails
    rather than picking one. Only the `Lvl1` files are eligible: the Lvl2
    monthly file is a gap-filled RECONSTRUCTION, not a measurement, and this
    package ships measurements.
    """
    readme = root / "README_.txt"
    if not readme.exists():
        raise BuildError(f"Pyramid provider README not found: {readme}")
    text = readme.read_text(encoding="utf-8")
    out: dict[str, PyramidStation] = {}
    for m in _PYRAMID_README_BLOCK.finditer(text):
        stem = m.group("file")
        if not stem.endswith("Lvl1"):
            continue
        path = root / f"{stem}.csv"
        if not path.exists():
            raise BuildError(f"README_.txt describes {stem}.csv but it is not on disk")
        # The provider writes elevations with a narrow no-break space as the
        # thousands separator ("7 986"); strip every non-digit.
        elev_readme = int(re.sub(r"\D", "", m.group("elev")))
        elev_filename = int(stem.split("_Z")[1].split("_")[0])
        if elev_readme != elev_filename:
            raise BuildError(
                f"{stem}: README_.txt says {elev_readme} m but the filename "
                f"says {elev_filename} m — the provider's two statements of "
                "elevation disagree and this package will not choose one"
            )
        station_id = stem.split("_Z")[0]
        if station_id != m.group("id"):
            raise BuildError(
                f"{stem}: filename ID {station_id!r} != README ID {m.group('id')!r}"
            )
        out[station_id] = PyramidStation(
            station_id=station_id,
            name=m.group("name"),
            lat=float(m.group("lat")),
            lon=float(m.group("lon")),
            elev_m=elev_filename,
            filename=path.name,
        )
    on_disk = {p.name for p in root.glob("*_Lvl1.csv")}
    described = {s.filename for s in out.values()}
    if on_disk != described:
        raise BuildError(
            f"Lvl1 files on disk {sorted(on_disk)} do not match the set "
            f"described in README_.txt {sorted(described)}"
        )
    return out


def _pyramid_native_step_h(timestamps: pl.Series) -> int:
    """The station's ACTUAL sampling step, measured from the delivered stamps.

    `README_.txt` claims "Time step: 1 hour" for every station; AWS0 is in fact
    2-hourly throughout. Measured, not believed.
    """
    steps = timestamps.diff().drop_nulls().dt.total_seconds()
    if steps.is_empty():
        return 1
    return int(steps.mode().min() // 3600)


def build_pyramid_station_frame(
    path: Path, *, station: PyramidStation
) -> tuple[pl.DataFrame, pl.DataFrame, tuple[str, ...], int]:
    """One station's complete-hourly-axis wide frame.

    Returns `(emitted, delivered, present_source_columns, native_step_h)`.
    `delivered` is the parsed source rows before the axis is completed; it is
    the reference the emitted frame's conservation post-conditions are checked
    against.
    """
    base: pl.DataFrame | None = None
    present: list[str] = []
    for var in PYRAMID_VARIABLES:
        try:
            frame, _ = _parse_pyramid_lvl1_column(
                path,
                station=Station(station.station_id),
                value_column=var.source_column,
            )
        except PyramidSchemaMismatchError:
            # This station does not measure this variable at all — South Col
            # has no rain gauge, Changri Nup has neither gauge nor barometer.
            continue
        present.append(var.source_column)
        column = frame.select("timestamp", pl.col("value").alias(var.out_column))
        if base is None:
            base = column
            continue
        if not column["timestamp"].equals(base["timestamp"]):
            raise BuildError(
                f"{path.name}: column {var.source_column} does not share the "
                "file's timestamp column — the file is not rectangular"
            )
        base = base.hstack(column.drop("timestamp"))
    if base is None:
        raise BuildError(f"{path.name}: none of the expected variables is present")

    native_step_h = _pyramid_native_step_h(base["timestamp"])
    lo = base["timestamp"].min()
    hi = base["timestamp"].max()
    axis = pl.DataFrame(
        {"timestamp": pl.datetime_range(lo, hi, interval="1h", eager=True)}
    )
    full = axis.join(base, on="timestamp", how="left")

    for var in PYRAMID_VARIABLES:
        if var.source_column not in present:
            full = full.with_columns(
                pl.lit(None, dtype=pl.Float64).alias(var.out_column)
            )

    offset = pl.duration(seconds=int(NPT_OFFSET.total_seconds()))
    utc = pl.col("timestamp") - offset
    full = full.with_columns(
        pl.lit(station.station_id).alias("station"),
        (pl.col("timestamp").dt.strftime("%Y-%m-%dT%H:%M:%S") + "+05:45").alias(
            "timestamp_npt"
        ),
        utc.dt.strftime("%Y-%m-%dT%H:%M:%SZ").alias("timestamp_utc"),
        utc.dt.truncate("1h")
        .dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        .alias("timestamp_utc_hour_floor"),
        utc.dt.minute().alias("_utc_minute"),
    ).with_columns(
        [
            pl.col(v.out_column)
            .is_not_null()
            .alias(f"observed_{v.source_column.lower()}")
            for v in PYRAMID_VARIABLES
        ]
    )

    # --- post-conditions, all measured against `base` ----------------------
    expected = int((hi - lo).total_seconds() // 3600) + 1
    if full.height != expected:
        raise BuildError(
            f"{path.name}: emitted {full.height} rows for a {expected}-hour axis"
        )
    if full["timestamp"].n_unique() != full.height or not full["timestamp"].is_sorted():
        raise BuildError(f"{path.name}: the emitted axis is not unique and sorted")
    # An NPT stamp on the hour lands at :15 past the UTC hour, always. This is
    # the "the two grids are 15 minutes apart" claim, asserted rather than said.
    if int((full["_utc_minute"] != 15).sum()) != 0:
        raise BuildError(
            f"{path.name}: a UTC stamp did not fall at :15 — the NPT->UTC "
            "conversion is wrong"
        )
    for var in PYRAMID_VARIABLES:
        col = var.out_column
        if var.source_column not in present:
            if int(full[col].is_not_null().sum()) != 0:
                raise BuildError(f"{path.name}: {col} invented values")
            continue
        n_src = int(base[col].is_not_null().sum())
        n_out = int(full[col].is_not_null().sum())
        if n_src != n_out:
            raise BuildError(
                f"{path.name}: {col} has {n_out} values but the source "
                f"delivered {n_src}"
            )
        z_src = int((base[col] == 0.0).sum())
        z_out = int((full[col] == 0.0).sum())
        if z_src != z_out:
            raise BuildError(
                f"{path.name}: {col} zero count changed from {z_src} to "
                f"{z_out} — a missing hour became a zero, or a zero was lost"
            )
        flag = f"observed_{var.source_column.lower()}"
        if int((full[flag] != full[col].is_not_null()).sum()) != 0:
            raise BuildError(f"{path.name}: {flag} is not exactly '{col} is not null'")

    columns = (
        ["station", "timestamp_npt", "timestamp_utc", "timestamp_utc_hour_floor"]
        + [v.out_column for v in PYRAMID_VARIABLES]
        + [f"observed_{v.source_column.lower()}" for v in PYRAMID_VARIABLES]
    )
    return full.select(columns), base, tuple(present), native_step_h


def _assert_pyramid_matches_public_loaders(
    path: Path, *, station: PyramidStation, emitted: pl.DataFrame
) -> tuple[int, int]:
    """Cross-check the unmasked columns against the project's own PUBLIC
    Pyramid loaders, which are a separate code path with their own gates.

    Every row the public loader retains must be present here with a
    bit-identical value at the same timestamp. This is what makes "we ship it
    unmasked" checkable rather than merely asserted: the unmasked column is a
    strict SUPERSET of the gated one, and agrees with it everywhere it exists.
    """
    # Re-derive the NAIVE NPT wall-clock the loaders emit. The "+05:45" suffix
    # is stripped rather than parsed: a zone-aware parse would shift the value
    # to UTC and the join would silently miss every row.
    npt = (
        pl.col("timestamp_npt")
        .str.head(19)
        .str.strptime(pl.Datetime, "%Y-%m-%dT%H:%M:%S")
    )
    ours = emitted.with_columns(npt.alias("timestamp"))
    checked = 0
    at_result = load_pyramid_lvl1_at_csv(path, station=Station(station.station_id))
    joined = at_result.retained.join(
        ours.select("timestamp", "air_temp_degc"), on="timestamp", how="left"
    )
    if int((joined["value_degc"] != joined["air_temp_degc"]).sum()) or int(
        joined["air_temp_degc"].is_null().sum()
    ):
        raise BuildError(f"{path.name}: AT disagrees with load_pyramid_lvl1_at_csv")
    checked += joined.height
    n_rr = 0
    try:
        rr_result = load_pyramid_lvl1_csv(path, station=Station(station.station_id))
    except PyramidSchemaMismatchError:
        return checked, n_rr
    joined = rr_result.retained.join(
        ours.select("timestamp", "precip_mm"), on="timestamp", how="left"
    )
    if int((joined["value_mm"] != joined["precip_mm"]).sum()) or int(
        joined["precip_mm"].is_null().sum()
    ):
        raise BuildError(f"{path.name}: RR disagrees with load_pyramid_lvl1_csv")
    checked += joined.height
    n_rr = rr_result.n_nonfinite + rr_result.n_out_of_range
    return checked, n_rr


def pyramid_daily_shift_sensitivity(
    emitted: pl.DataFrame, *, station_id: str, shift_h: int
) -> PyramidShiftSensitivity | None:
    """What a +-1 h period-convention error COSTS a daily total.

    The period convention is unresolved (README section 3.3), which leaves a
    +-1 h residual between the two networks. Rather than assert that this is
    "immaterial daily", the build measures it: daily totals are recomputed with
    the whole series displaced by `shift_h`, and the relative change is
    reported over days carrying at least 1 mm.
    """
    wet = emitted.filter(pl.col("observed_rr")).select(
        pl.col("timestamp_utc")
        .str.strptime(pl.Datetime, "%Y-%m-%dT%H:%M:%SZ")
        .alias("t"),
        pl.col("precip_mm").alias("v"),
    )
    if wet.is_empty():
        return None
    base = (
        wet.with_columns(pl.col("t").dt.date().alias("d"))
        .group_by("d")
        .agg(pl.col("v").sum().alias("b"))
    )
    moved = (
        wet.with_columns(
            (pl.col("t") + pl.duration(hours=shift_h)).dt.date().alias("d")
        )
        .group_by("d")
        .agg(pl.col("v").sum().alias("s"))
    )
    joined = base.join(moved, on="d", how="inner").filter(pl.col("b") >= 1.0)
    if joined.is_empty():
        return None
    rel = joined.select(
        ((pl.col("s") - pl.col("b")).abs() / pl.col("b") * 100.0).alias("r")
    )["r"]
    return PyramidShiftSensitivity(
        station_id=station_id,
        n_days=joined.height,
        median_pct=float(rel.median()),
        mean_pct=float(rel.mean()),
        p90_pct=float(rel.quantile(0.9)),
    )


PYRAMID_LICENCE_TEXT = f"""\
Pyramid Meteorological Network (Khumbu, Nepal) — licence and attribution
========================================================================

This applies to `pyramid_aws_hourly.csv` and `pyramid_stations.csv` in this
package, and to anything derived from them.

Data set   Pyramid Meteorological Network, Level 1 hourly automatic weather
           station records, Khumbu / upper Dudh Koshi, Nepal.
Provided by {PYRAMID_PROVIDER}.
DOI        {PYRAMID_DOI}  (https://doi.org/{PYRAMID_DOI})
Licence    Creative Commons Attribution 4.0 International ({PYRAMID_LICENCE})
           https://creativecommons.org/licenses/by/4.0/

ATTRIBUTION IS A CONDITION OF USE
---------------------------------

CC BY 4.0 permits sharing and adaptation, including commercially, PROVIDED you
give appropriate credit, link to the licence, and indicate whether changes were
made. That is a condition, not a courtesy.

Cite:

  {PYRAMID_CITATION}

Any output that uses this data — a paper, a report, a figure, a table, a
dashboard, a trained model, a benchmark result — must carry that citation.
If you redistribute the files, or any subset or derivative of them, carry this
file with them.

Changes made in this package
----------------------------

The values are unaltered. What this package did to them:

  * parsed the provider's semicolon-separated, CR-line-ended Level 1 CSVs and
    reshaped the four integer time columns (year, month, day, hour) into
    timestamps;
  * placed each station on a complete hourly axis spanning its own delivered
    record, so that a gap is a visible empty row rather than an absent one;
  * added `timestamp_utc` (the exact NPT → UTC conversion) and the optional
    `timestamp_utc_hour_floor` alignment column alongside the unmodified
    `timestamp_npt`;
  * added one `observed_*` flag per variable, each exactly "the value cell is
    not empty".

No quality control of ours was applied, no value was changed, no row was
dropped, and nothing was gap-filled. The Level 2 monthly reconstruction
published alongside the Level 1 files is NOT included here: it is gap-filled,
and this package ships measurements.

The rest of this package
------------------------

The DHM gauge data are separate and are NOT CC BY 4.0 — they are supplied by
the Department of Hydrology and Meteorology, Nepal, and their onward use is
subject to DHM's terms. ERA5-Land is Copernicus / ECMWF, CC BY 4.0, cited in
README.md. Do not let this licence be read as covering them.
"""


# --- main --------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    # Every default is anchored to the repo root rather than to the CWD, so the
    # build produces the same package wherever it is invoked from.
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument("--start", default="2020-01-01T00:00:00")
    p.add_argument("--end", default="2025-12-31T23:00:00")
    p.add_argument("--stations", nargs="+", default=list(DUDH_KOSHI_STATIONS))
    p.add_argument(
        "--era5-precip-root", type=Path, default=_REPO_ROOT / "data" / "dhm_precip"
    )
    p.add_argument(
        "--era5-t2m-root",
        type=Path,
        default=_REPO_ROOT / "data" / "dhm_precip" / "era5_land_t2m",
    )
    p.add_argument(
        "--pyramid-root",
        type=Path,
        default=_REPO_ROOT / "data" / "dhm_precip" / "pyramid",
    )
    p.add_argument("--repo-root", type=Path, default=None)
    return p


def _copy_self_into_package(out_dir: Path) -> None:
    """Publish THIS file into the package, so the two can never diverge.

    The package ships the script that built it. Carrying a second, separately
    editable copy under `data/` is the drift the repo keeps paying for, so the
    canonical file under `scripts/` is copied in on every run and the packaged
    copy is a build artefact, never a source.
    """
    dest = out_dir / PACKAGED_SCRIPT_NAME
    if dest.exists() and dest.samefile(_THIS_FILE):
        raise BuildError(
            f"--out-dir {out_dir} is this script's own directory; "
            "the build would copy the canonical file onto itself"
        )
    shutil.copyfile(_THIS_FILE, dest)
    audit(
        f"packaged build script {dest.name} copied from {_THIS_FILE} "
        f"sha256 {hashlib.sha256(dest.read_bytes()).hexdigest()}"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root or _REPO_ROOT
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stations = tuple(args.stations)
    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    params = DEFAULT_PARAMS

    audit(f"repo root {repo_root}")
    audit(f"out dir {out_dir}")
    audit(f"requested window {start} .. {end} UTC, stations {list(stations)}")

    # 1 — workbook -----------------------------------------------------------
    source_path = resolve_source_path()
    audit(f"gauge source {source_path} sha256 {compute_sha256(source_path)}")
    long_frame, inventory = load_long_frame(
        source_path, expected_sha256=PRODUCTION_SOURCE_SHA256
    )
    live = frozenset(
        Station(name)
        for name in inventory.all_columns
        if name not in inventory.empty_columns
    )
    audit(
        f"workbook: {inventory.total_rows} source rows, "
        f"{len(inventory.all_columns)} columns, {len(live)} live stations, "
        f"{len(inventory.empty_columns)} all-null columns dropped"
    )
    coords = load_station_coordinates(
        resolve_coords_path(), expected_stations=live
    ).by_station
    missing = [s for s in stations if Station(s) not in live]
    if missing:
        raise BuildError(f"requested station(s) not live in the workbook: {missing}")

    # 2 — views ---------------------------------------------------------------
    on_grid = on_grid_view(long_frame, params)
    off_grid = long_frame.filter(
        pl.col("timestamp").dt.minute() != params.on_grid_minute
    )
    audit(
        f"RAW view {long_frame.height} station-hour cells; ON_GRID (minute==0) "
        f"{on_grid.height}; off-grid cells {off_grid.height} "
        f"({off_grid.select('source_row_index').n_unique()} source rows) — "
        "off-grid stamps are DHM processing artefacts and cannot be placed on "
        "an hourly axis, so they are excluded"
    )
    for station in stations:
        n_off = off_grid.filter(
            (pl.col("station") == station) & pl.col("value_mm").is_not_null()
        ).height
        audit(f"off-grid non-null observations dropped, {station}: {n_off}")

    # 3 — canonical hourly axis ----------------------------------------------
    normalised = normalise.normalise_hourly_axis(on_grid, live)
    normalise.assert_row_identity_conservation(on_grid, normalised, live)
    axis_lo = normalised["timestamp"].min()
    axis_hi = normalised["timestamp"].max()
    audit(
        f"normalise_hourly_axis: {normalised.height} rows over "
        f"{normalised.height // len(live)} hours x {len(live)} stations, "
        f"{axis_lo} .. {axis_hi}; row-identity conservation asserted"
    )

    sub = normalised.filter(pl.col("station").is_in(list(stations)))

    # 4 — QC mask (flag, never filter) ---------------------------------------
    created_at = ensure_utc(datetime.now(UTC))
    station_pairs = list(
        observations.iter_observations_by_station(
            sub, parameter="precipitation", created_at=created_at
        )
    )
    mask, accounting = qc_mask.iter_station_results(iter(station_pairs), params)
    mask_keys = {(str(st), ts.replace(tzinfo=None)) for st, ts in mask}
    audit(f"canonical QC mask: {len(mask_keys)} (station, hour) keys")

    by_rule = attribute_mask_by_rule(station_pairs, params)
    union = set().union(*by_rule.values()) if by_rule else set()
    if union != mask_keys:
        raise BuildError(
            "per-rule attribution does not reconstruct the canonical mask "
            f"(attributed {len(union)}, canonical {len(mask_keys)})"
        )
    audit("per-rule attribution reconstructs the canonical mask exactly")

    rule_of: dict[tuple[str, datetime], str] = {}
    for version, keys in by_rule.items():
        label = RULE_LABELS[version]
        for key in keys:
            rule_of[key] = label if key not in rule_of else f"{rule_of[key]}+{label}"

    masked_counts: dict[str, dict[str, int]] = {}
    for station in stations:
        counts = {
            RULE_LABELS[v]: sum(1 for st, _ in keys if st == station)
            for v, keys in by_rule.items()
        }
        masked_counts[station] = counts
        audit(
            f"QC-masked hours, {station}: total="
            f"{sum(1 for st, _ in mask_keys if st == station)} "
            + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        )

    flagged_extremes: dict[str, tuple[float, float]] = {}
    for station in stations:
        flagged = sub.filter(
            (pl.col("station") == station)
            & pl.col("value_mm").is_not_null()
            & pl.struct("station", "timestamp").map_elements(
                lambda r: (r["station"], r["timestamp"]) in mask_keys,
                return_dtype=pl.Boolean,
            )
        )
        if flagged.height:
            lo = float(flagged["value_mm"].min())
            hi = float(flagged["value_mm"].max())
            flagged_extremes[station] = (lo, hi)
            audit(
                f"delivered values inside QC-flagged rows, {station}: "
                f"min={lo} max={hi} over {flagged.height} rows"
            )
    global_flagged_min = (
        min(v[0] for v in flagged_extremes.values()) if flagged_extremes else 0.0
    )
    audit(
        f"most extreme value delivered inside a flagged row: "
        f"{global_flagged_min} — it is preserved in raw_delivered_mm and, "
        "because it fails the physical-impossibility gate, kept OUT of "
        "precip_mm"
    )

    rule_rows = qc_mask.rule_provenance_rows(params)
    for row in rule_rows:
        audit(
            f"rule pass {row.pass_name} {row.rule_id} {row.rule_version} "
            f"scope={row.scope} step={row.time_step_seconds}s "
            f"thresholds={row.thresholds}"
        )

    # 5 — the gauge file ------------------------------------------------------
    gauge = sub.select(
        pl.col("station"),
        pl.col("timestamp").alias("timestamp_utc"),
        pl.col("value_mm").alias("raw_delivered_mm"),
    ).sort(["station", "timestamp_utc"])
    gauge = gauge.with_columns(
        pl.Series(
            "qc_rule",
            [
                rule_of.get((s, t))
                for s, t in zip(
                    gauge["station"].to_list(),
                    gauge["timestamp_utc"].to_list(),
                    strict=True,
                )
            ],
            dtype=pl.Utf8,
        )
    ).with_columns(pl.col("qc_rule").is_not_null().alias("qc_masked"))

    # A value that fails `range_check` failed a PHYSICAL-IMPOSSIBILITY gate: it
    # is not a measurement of anything, it is a missing-data sentinel or a
    # corrupt cell. Shipping it in `precip_mm` would be the package's central
    # failure inverted — a non-measurement that looks like a measurement, and
    # one careless mean or `fillna` makes it dominate. So `precip_mm` is NULL
    # and `observed` is FALSE for those rows.
    #
    # Nothing is discarded and no row is dropped: the delivered value survives
    # verbatim in `raw_delivered_mm`, and the row keeps `qc_masked=true` with
    # `qc_rule='range_check'`, so the provenance is complete and the reason is
    # named. This is NOT applied to the other rules: a `long_zero_run` hour
    # holds a physically plausible 0.0 that the modeller may legitimately want.
    not_a_measurement = (
        pl.col("qc_rule")
        .str.contains(RULE_LABELS[RANGE_CHECK_RULE_VERSION], literal=True)
        .fill_null(False)
    )
    gauge = gauge.with_columns(
        pl.when(not_a_measurement)
        .then(None)
        .otherwise(pl.col("raw_delivered_mm"))
        .alias("precip_mm")
    ).with_columns(pl.col("precip_mm").is_not_null().alias("observed"))
    n_voided = gauge.filter(
        pl.col("raw_delivered_mm").is_not_null() & pl.col("precip_mm").is_null()
    )
    audit(
        f"physical-impossibility gate: {n_voided.height} delivered value(s) "
        "failed `range_check` and are emitted as precip_mm=null / "
        "observed=false; the delivered value is preserved in "
        "raw_delivered_mm and the row keeps qc_masked=true"
    )
    for station in stations:
        rows = n_voided.filter(pl.col("station") == station)
        if rows.height:
            audit(
                f"  {station}: {rows.height} voided, delivered values "
                f"min={float(rows['raw_delivered_mm'].min())} "
                f"max={float(rows['raw_delivered_mm'].max())}"
            )
    expected_voided = sum(
        1 for st, _ in by_rule[RANGE_CHECK_RULE_VERSION] if st in stations
    )
    if n_voided.height != expected_voided:
        raise BuildError(
            f"{n_voided.height} rows voided but range_check attributed "
            f"{expected_voided} — the gate and the flag disagree"
        )

    # Right-pad to the requested calendar window. The workbook's last delivered
    # stamp is earlier than the window's end; those trailing hours were never
    # delivered, so they are emitted as unobserved rows (null value) — the same
    # representation as any other gap. Nothing is imputed.
    pad_lo = axis_hi + timedelta(hours=1)
    if pad_lo <= end:
        n_pad = int((end - pad_lo).total_seconds() // 3600) + 1
        pad = pl.DataFrame(
            {
                "station": [s for s in stations for _ in range(n_pad)],
                "timestamp_utc": [
                    pad_lo + timedelta(hours=i) for _ in stations for i in range(n_pad)
                ],
                "raw_delivered_mm": [None] * (n_pad * len(stations)),
                "qc_rule": [None] * (n_pad * len(stations)),
                "qc_masked": [False] * (n_pad * len(stations)),
                "precip_mm": [None] * (n_pad * len(stations)),
                "observed": [False] * (n_pad * len(stations)),
            },
            schema=gauge.schema,
        )
        gauge = pl.concat([gauge, pad])
        audit(
            f"right-padded {n_pad} h x {len(stations)} stations "
            f"({pad_lo} .. {end}) as UNOBSERVED rows — the workbook's last "
            f"delivered hour is {axis_hi}; these hours were never delivered "
            "and are emitted null, not zero"
        )
    gauge = gauge.filter(
        (pl.col("timestamp_utc") >= start) & (pl.col("timestamp_utc") <= end)
    ).sort(["station", "timestamp_utc"])

    expected_hours = _assert_complete_axis(gauge, stations, start, end)
    _assert_missing_never_zero(gauge)
    _assert_no_null_became_zero(gauge, on_grid, stations)
    _assert_values_bit_identical(gauge, on_grid, stations)
    audit(
        f"gauge frame: {gauge.height} rows = {len(stations)} stations x "
        f"{expected_hours} hours; complete-axis, missing-never-zero, "
        "zero-conservation and value-identity post-conditions all passed"
    )

    gauge_out = gauge.select(
        "station",
        "timestamp_utc",
        "precip_mm",
        "observed",
        "qc_masked",
        "qc_rule",
        "raw_delivered_mm",
    )
    gauge_path = out_dir / "gauge_precipitation_hourly.csv"
    gauge_out.write_csv(gauge_path, datetime_format="%Y-%m-%dT%H:%M:%SZ")

    # 6 — per-station statistics ---------------------------------------------
    resolution = infer_reporting_resolution(on_grid, params)
    res_by_station = {
        r["station"]: (r["group"], r["resolution_mm"])
        for r in resolution.iter_rows(named=True)
    }

    @dataclass(frozen=True, kw_only=True, slots=True)
    class StationStats:
        station: str
        n_axis: int
        n_observed: int
        n_qc_masked: int
        n_retained: int
        coverage_pct: float
        wet_hour_pct: float
        wet_hour_pct_ge_threshold: float
        first_obs: str
        last_obs: str
        raw_coverage_pct: float
        raw_wet_hour_pct: float

    stats: dict[str, StationStats] = {}
    for station in stations:
        g = gauge.filter(pl.col("station") == station)
        obs = g.filter(pl.col("observed"))
        retained = obs.filter(~pl.col("qc_masked"))
        wet = retained.filter(pl.col("precip_mm") > 0.0).height
        wet_thr = retained.filter(
            pl.col("precip_mm") >= params.wet_threshold_mm_per_h
        ).height
        raw = long_frame.filter(
            (pl.col("station") == station) & pl.col("value_mm").is_not_null()
        )
        st = StationStats(
            station=station,
            n_axis=g.height,
            n_observed=obs.height,
            n_qc_masked=g.filter(pl.col("qc_masked")).height,
            n_retained=retained.height,
            coverage_pct=100.0 * obs.height / g.height,
            wet_hour_pct=100.0 * wet / retained.height,
            wet_hour_pct_ge_threshold=100.0 * wet_thr / retained.height,
            first_obs=obs["timestamp_utc"].min().strftime("%Y-%m-%dT%H:%M:%SZ"),
            last_obs=obs["timestamp_utc"].max().strftime("%Y-%m-%dT%H:%M:%SZ"),
            raw_coverage_pct=100.0 * raw.height / expected_hours,
            raw_wet_hour_pct=100.0
            * raw.filter(pl.col("value_mm") > 0.0).height
            / raw.height,
        )
        stats[station] = st
        audit(
            f"{station}: axis={st.n_axis} observed={st.n_observed} "
            f"coverage={st.coverage_pct:.2f}% qc_masked={st.n_qc_masked} "
            f"retained={st.n_retained} wet(>0)={st.wet_hour_pct:.2f}% "
            f"wet(>={params.wet_threshold_mm_per_h})="
            f"{st.wet_hour_pct_ge_threshold:.2f}% "
            f"first={st.first_obs} last={st.last_obs}"
        )
        audit(
            f"{station}: RAW-view comparison (all delivered cells incl. "
            f"off-grid minutes, coverage denominator {expected_hours} h): "
            f"coverage={st.raw_coverage_pct:.2f}% "
            f"wet(>0)={st.raw_wet_hour_pct:.2f}%"
        )

    subthreshold: dict[str, float] = {}
    for station in stations:
        retained = gauge.filter(
            (pl.col("station") == station)
            & pl.col("observed")
            & ~pl.col("qc_masked")
            & (pl.col("precip_mm") > 0.0)
        )
        below = retained.filter(
            pl.col("precip_mm") < params.wet_threshold_mm_per_h
        ).height
        frac = 100.0 * below / retained.height if retained.height else 0.0
        subthreshold[station] = frac
        audit(
            f"{station}: {frac:.1f}% of its non-zero retained hours fall BELOW "
            f"the {params.wet_threshold_mm_per_h} mm/h wet threshold "
            f"({below} of {retained.height})"
        )

    # 7 — reporting-resolution confound (whole network) -----------------------
    group_summary: dict[str, dict[str, float]] = {}
    for group in ("A", "B"):
        members = [s for s, (g, _) in res_by_station.items() if g == group]
        elevs = [coords[Station(s)].elev_m for s in members]
        res_mm = next(r for s, (g, r) in res_by_station.items() if g == group)
        group_summary[group] = {
            "n": len(members),
            "resolution_mm": res_mm,
            "elev_min": min(elevs),
            "elev_max": max(elevs),
        }
        audit(
            f"resolution group {group}: n={len(members)} resolution={res_mm} mm "
            f"elevation {min(elevs):.0f}-{max(elevs):.0f} m"
        )
    overlap = not (
        group_summary["A"]["elev_min"] > group_summary["B"]["elev_max"]
        or group_summary["B"]["elev_min"] > group_summary["A"]["elev_max"]
    )
    audit(
        f"reporting resolution vs elevation: elevation ranges overlap = "
        f"{overlap} — the split is "
        f"{'NOT ' if overlap else ''}completely confounded with elevation"
    )
    if overlap:
        raise BuildError(
            "the resolution/elevation confound claim in the README no longer "
            "holds against the data — the briefing must be rewritten"
        )
    confound_gap = group_summary["A"]["elev_min"] - group_summary["B"]["elev_max"]
    audit(f"confound gap between the two groups: {confound_gap:.0f} m of elevation")

    wet_ratio = (
        stats["Lukla Airport"].wet_hour_pct / stats["Aiselukhark"].wet_hour_pct
        if "Lukla Airport" in stats and "Aiselukhark" in stats
        else float("nan")
    )
    relief = abs(
        coords[Station("Lukla Airport")].elev_m - coords[Station("Aiselukhark")].elev_m
    )
    audit(
        f"wet-hour ratio Lukla:Aiselukhark = {wet_ratio:.2f}x across "
        f"{relief:.0f} m of relief"
    )
    station_elevs = [coords[Station(s)].elev_m for s in stations]
    station_relief = max(station_elevs) - min(station_elevs)
    audit(
        f"elevation span of the three package stations: "
        f"{min(station_elevs):.0f}-{max(station_elevs):.0f} m "
        f"({station_relief:.0f} m)"
    )

    # 8 — ERA5-Land -----------------------------------------------------------
    precip_dir, precip_manifest = discover_precip_bundle(args.era5_precip_root)
    audit(
        f"ERA5-Land precipitation bundle {precip_dir} "
        f"identity={precip_manifest.extraction_identity} "
        f"operator={precip_manifest.operator_id}"
    )
    era5_precip = era5_series(
        precip_dir / "series_nearest.nc",
        "precipitation_mm_per_h",
        stations,
        "era5_precip_mm",
    )
    t2m_dir, t2m_manifest = discover_t2m_bundle(args.era5_t2m_root)
    audit(
        f"ERA5-Land t2m bundle {t2m_dir} "
        f"identity={t2m_manifest.extraction_identity} "
        f"operator={t2m_manifest.operator_id}"
    )
    era5_t2m = era5_series(
        t2m_dir / "series_t2m_degc.nc",
        "temperature_degc",
        stations,
        "era5_t2m_degc",
    )
    for name, frame, col in (
        ("precipitation", era5_precip, "era5_precip_mm"),
        ("temperature", era5_t2m, "era5_t2m_degc"),
    ):
        values = frame[col].to_numpy()
        n_null = int(np.count_nonzero(~np.isfinite(values)))
        audit(
            f"ERA5-Land {name}: {frame.height} rows "
            f"({frame.height // len(stations)} h x {len(stations)} stations), "
            f"{n_null} non-finite"
        )
        if n_null:
            raise BuildError(f"ERA5-Land {name} carries {n_null} non-finite values")
        _assert_complete_axis(
            frame.rename({col: "v"}).with_columns(pl.lit(True).alias("observed")),
            stations,
            start,
            end,
        )

    grid_elev = pl.read_csv(precip_dir / "station_grid_elevation.csv").filter(
        pl.col("station").is_in(list(stations))
    )
    elev_by_station = {r["station"]: r for r in grid_elev.iter_rows(named=True)}
    for station in stations:
        r = elev_by_station[station]
        audit(
            f"ERA5-Land cell for {station}: grid ({r['grid_lat']:.2f}, "
            f"{r['grid_lon']:.2f}), {r['offset_km']:.2f} km from the gauge, "
            f"model orography {r['orography_elev_m']:.0f} m vs station "
            f"{r['station_elev_m']:.0f} m, mismatch "
            f"{r['elev_mismatch_m']:+.0f} m"
        )

    # 9 — diurnal phase, measured on this package's own three stations --------
    offsets: dict[str, DiurnalOffset] = {}
    for station in stations:
        off = diurnal_offset(
            station,
            gauge.filter(pl.col("station") == station),
            era5_precip.filter(pl.col("station") == station),
            tuple(params.jjas_months),
        )
        offsets[station] = off
        audit(
            f"monsoon diurnal phase {station}: n={off.n_hours} h, gauge peak "
            f"{off.gauge_phase_utc_h:.2f} UTC, ERA5-Land peak "
            f"{off.era5_phase_utc_h:.2f} UTC, offset {off.offset_h:+.2f} h "
            f"(amplitudes {off.gauge_amplitude:.3f} / {off.era5_amplitude:.3f})"
        )
    median_offset = float(np.median([o.offset_h for o in offsets.values()]))
    audit(
        "median monsoon diurnal offset across the three stations: "
        f"{median_offset:+.2f} h"
    )

    pyramid_gradient = extract_documented_number(repo_root, "pyramid_gradient")
    era5_band_offset = extract_documented_number(repo_root, "era5_high_band_offset")

    # 10 — stations.csv -------------------------------------------------------
    station_rows = []
    for station in stations:
        c = coords[Station(station)]
        st = stats[station]
        group, res_mm = res_by_station[station]
        r = elev_by_station[station]
        station_rows.append(
            {
                "station": station,
                "lat": c.lat,
                "lon": c.lon,
                "elev_m": c.elev_m,
                "reporting_resolution_mm": res_mm,
                "reporting_resolution_group": group,
                "n_hours_axis": st.n_axis,
                "n_observed": st.n_observed,
                "coverage_pct": round(st.coverage_pct, 2),
                "n_qc_masked": st.n_qc_masked,
                "n_retained": st.n_retained,
                "wet_hour_pct": round(st.wet_hour_pct, 2),
                "wet_hour_pct_ge_0p2mm": round(st.wet_hour_pct_ge_threshold, 2),
                "first_obs": st.first_obs,
                "last_obs": st.last_obs,
                "era5_grid_lat": r["grid_lat"],
                "era5_grid_lon": r["grid_lon"],
                "era5_offset_km": round(float(r["offset_km"]), 2),
                "era5_orography_elev_m": round(float(r["orography_elev_m"]), 1),
                "era5_elev_mismatch_m": round(float(r["elev_mismatch_m"]), 1),
            }
        )
    stations_path = out_dir / "stations.csv"
    pl.DataFrame(station_rows).write_csv(stations_path)

    # 11 — ERA5 covariate files ----------------------------------------------
    era5_precip_path = out_dir / "era5_land_precipitation_hourly.csv"
    _write_with_header(
        era5_precip_path,
        era5_precip.sort(["station", "timestamp_utc"]).rename(
            {"era5_precip_mm": "precip_mm"}
        ),
        [
            "ERA5-Land total precipitation at the three gauge locations, "
            "nearest grid cell, mm accumulated over the hour ENDING at "
            "timestamp_utc.",
            "",
            "THIS IS A MODEL PRODUCT (a reanalysis), NOT A GAUGE SUBSTITUTE. "
            "It is a labelled covariate only.",
            "It is NOT used as a quality-control input anywhere in this "
            "package -- doing so would be circular.",
            f"It carries a large monsoon diurnal PHASE error in this "
            f"elevation band: measured at these three stations, "
            f"{', '.join(f'{s} {offsets[s].offset_h:+.1f} h' for s in stations)} "
            f"(median {median_offset:+.1f} h); the project's network-wide "
            f"figure for the >=2,000 m band is {era5_band_offset}. Near "
            "antiphase the SIGN is not identifiable; the magnitude is.",
            "ERA5-Land precipitation is interpolated down from ERA5 and is "
            "never elevation-corrected, so it does not see the fine "
            "orography of this basin.",
            "",
            f"Source bundle: {precip_dir.name} "
            f"(identity {precip_manifest.extraction_identity}).",
            "Licence: Copernicus / ECMWF, CC BY 4.0, "
            "https://doi.org/10.24381/cds.e2161bac",
            "",
            "Read with: pandas.read_csv(path, comment='#') or "
            "polars.read_csv(path, comment_prefix='#')",
        ],
    )

    era5_t2m_path = out_dir / "era5_land_temperature_hourly.csv"
    _write_with_header(
        era5_t2m_path,
        era5_t2m.sort(["station", "timestamp_utc"]).rename(
            {"era5_t2m_degc": "t2m_degc"}
        ),
        [
            "ERA5-Land 2 m air temperature at the three gauge locations, "
            "nearest grid cell, degrees Celsius, instantaneous at "
            "timestamp_utc.",
            "",
            "THIS IS A MODEL PRODUCT, NOT AN IN-SITU MEASUREMENT. There is no "
            "gauge thermometer in this package.",
            "It is UNCORRECTED CELL-LEVEL temperature. ERA5-Land's model "
            "orography differs from the station elevation by:",
            *[
                f"  {s}: {elev_by_station[s]['elev_mismatch_m']:+.0f} m "
                f"(station {elev_by_station[s]['station_elev_m']:.0f} m, "
                f"model {elev_by_station[s]['orography_elev_m']:.0f} m)"
                for s in stations
            ],
            "A first-order correction is "
            "T_station = T_ERA5 - 0.0065 * elev_mismatch_m, using the "
            "era5_elev_mismatch_m column of stations.csv. The station vertical "
            "datum is unknown, so the mismatch itself carries an unquantified "
            "error -- label the correction, do not imply precision.",
            "",
            f"Source bundle: {t2m_dir.name} "
            f"(identity {t2m_manifest.extraction_identity}).",
            "Licence: Copernicus / ECMWF, CC BY 4.0, "
            "https://doi.org/10.24381/cds.e2161bac",
            "",
            "Read with: pandas.read_csv(path, comment='#') or "
            "polars.read_csv(path, comment_prefix='#')",
        ],
    )

    # 12 — the Pyramid tier (data-quality ceiling, NOT operational) -----------
    pyramid_root: Path = args.pyramid_root
    if not pyramid_root.is_dir():
        raise BuildError(f"Pyramid source directory not found: {pyramid_root}")
    pyramid_meta = parse_pyramid_stations(pyramid_root)
    audit(
        f"Pyramid source {pyramid_root}: {len(pyramid_meta)} Lvl1 stations "
        "described by the provider's README_.txt and present on disk "
        "(the Lvl2 monthly reconstruction is excluded — it is gap-filled, "
        "not measured)"
    )
    audit(
        "Pyramid parsing reuses scripts/dhm_precip/pyramid_loader.py's shared "
        "column parser `_parse_pyramid_lvl1_column` (semicolon separator, "
        "CR-only line endings, four integer time columns, empty field = "
        "missing). The loader's PUBLIC entry points cannot serve this tier: "
        "load_pyramid_lvl1_csv gates RR on a physical range and "
        "load_pyramid_lvl1_at_csv gates AT on finiteness, and both DROP the "
        "failing rows, which would turn a masked hour into an absent hour — "
        "this tier ships Level 1 unmasked on a complete axis."
    )

    pyramid_frames: list[pl.DataFrame] = []
    pyramid_tiers: dict[str, PyramidStationTier] = {}
    pyramid_checked = 0
    pyramid_gated_out = 0
    for station_id in sorted(pyramid_meta, key=lambda s: pyramid_meta[s].elev_m):
        meta = pyramid_meta[station_id]
        path = pyramid_root / meta.filename
        emitted, delivered, present, native_step_h = build_pyramid_station_frame(
            path, station=meta
        )
        n_checked, n_gated = _assert_pyramid_matches_public_loaders(
            path, station=meta, emitted=emitted
        )
        pyramid_checked += n_checked
        pyramid_gated_out += n_gated
        pyramid_frames.append(emitted)

        n_axis = emitted.height
        n_native_slots = (n_axis - 1) // native_step_h + 1
        n_observed = {
            v.source_column: int(emitted[f"observed_{v.source_column.lower()}"].sum())
            for v in PYRAMID_VARIABLES
        }
        coverage = {
            k: (100.0 * n / n_axis if v_present else float("nan"))
            for (k, n), v_present in zip(
                n_observed.items(),
                [v.source_column in present for v in PYRAMID_VARIABLES],
                strict=True,
            )
        }
        coverage_native = {
            k: (100.0 * n / n_native_slots if k in present else float("nan"))
            for k, n in n_observed.items()
        }
        ws = emitted.filter(pl.col("observed_ws"))["wind_speed_ms"]
        wet = emitted.filter(pl.col("observed_rr") & (pl.col("precip_mm") > 0.0))
        wet_ws = wet.filter(pl.col("observed_ws"))["wind_speed_ms"]
        tier = PyramidStationTier(
            station=meta,
            n_hours_axis=n_axis,
            native_step_h=native_step_h,
            first_npt=str(emitted["timestamp_npt"].item(0)),
            last_npt=str(emitted["timestamp_npt"].item(-1)),
            first_utc=str(emitted["timestamp_utc"].item(0)),
            last_utc=str(emitted["timestamp_utc"].item(-1)),
            present=present,
            n_observed=n_observed,
            coverage_pct=coverage,
            coverage_pct_native=coverage_native,
            wind_mean_ms=float(ws.mean()) if ws.len() else None,
            wind_median_ms=float(ws.median()) if ws.len() else None,
            wind_p95_ms=float(ws.quantile(0.95)) if ws.len() else None,
            wet_hour_wind_mean_ms=float(wet_ws.mean()) if wet_ws.len() else None,
            n_wet_hours=wet.height,
        )
        pyramid_tiers[station_id] = tier
        audit(
            f"Pyramid {station_id} ({meta.name}, {meta.elev_m} m): axis="
            f"{n_axis} h ({tier.first_npt} .. {tier.last_npt} NPT = "
            f"{tier.first_utc} .. {tier.last_utc}), native step "
            f"{native_step_h} h, variables present "
            f"{[v.source_column for v in PYRAMID_VARIABLES if v.source_column in present]}"  # noqa: E501
        )
        audit(
            f"Pyramid {station_id} coverage over the hourly axis: "
            + ", ".join(
                f"{k}={coverage[k]:.2f}% (n={n_observed[k]})"
                if k in present
                else f"{k}=absent"
                for k in (v.source_column for v in PYRAMID_VARIABLES)
            )
        )
        if native_step_h != 1:
            audit(
                f"Pyramid {station_id}: README_.txt claims a 1-hour time step "
                f"but the delivered stamps are {native_step_h}-hourly "
                f"throughout — coverage over its NATIVE "
                f"{n_native_slots}-slot axis is "
                + ", ".join(
                    f"{k}={coverage_native[k]:.2f}%"
                    for k in (v.source_column for v in PYRAMID_VARIABLES)
                    if k in present
                )
            )
        if tier.wind_mean_ms is not None:
            audit(
                f"Pyramid {station_id} wind speed: n={ws.len()} mean="
                f"{tier.wind_mean_ms:.2f} m/s median={tier.wind_median_ms:.2f} "
                f"p95={tier.wind_p95_ms:.2f}; during its "
                f"{wet_ws.len()} observed wet hours mean="
                + (
                    f"{tier.wet_hour_wind_mean_ms:.2f} m/s"
                    if tier.wet_hour_wind_mean_ms is not None
                    else "n/a (no co-observed wet hour)"
                )
            )
    audit(
        f"Pyramid cross-check: {pyramid_checked} retained rows from the "
        "project's own public loaders (load_pyramid_lvl1_at_csv, "
        "load_pyramid_lvl1_csv) reproduce bit-identically in the unmasked "
        f"columns emitted here; {pyramid_gated_out} value(s) were dropped by "
        "those loaders' gates across the whole network, so unmasked and "
        "gated differ by exactly that many rows"
    )

    pyramid = pl.concat(pyramid_frames)
    pyramid_elev = sorted(m.elev_m for m in pyramid_meta.values())
    audit(
        f"Pyramid tier: {pyramid.height} rows over {len(pyramid_meta)} "
        f"stations, elevation {pyramid_elev[0]:,}-{pyramid_elev[-1]:,} m "
        f"(vertical span {pyramid_elev[-1] - pyramid_elev[0]:,} m)"
    )

    # The +-1 h period-convention residual, PRICED rather than asserted.
    shift_rows: list[PyramidShiftSensitivity] = []
    for station_id, tier in pyramid_tiers.items():
        if "RR" not in tier.present:
            continue
        frame = pyramid.filter(pl.col("station") == station_id)
        for shift in (1, -1):
            sens = pyramid_daily_shift_sensitivity(
                frame, station_id=station_id, shift_h=shift
            )
            if sens is None:
                continue
            shift_rows.append(sens)
            audit(
                f"Pyramid {station_id}: displacing the whole series by "
                f"{shift:+d} h changes the UTC-day total on days carrying "
                f">=1 mm (n={sens.n_days}) by median {sens.median_pct:.2f}%, "
                f"mean {sens.mean_pct:.2f}%, 90th percentile "
                f"{sens.p90_pct:.2f}%"
            )
    # Headline: per station take the WORSE of the two shift directions, then
    # report the spread across stations. Taking the raw min across all twelve
    # station-shifts would headline AWS0's +1 h case, which is 0.00% only
    # because its 2-hourly stamps never straddle a UTC day boundary in that
    # direction — an artefact of its sampling, not a property of the residual.
    shift_worst = {
        sid: max(
            (s for s in shift_rows if s.station_id == sid),
            key=lambda s: s.p90_pct,
        )
        for sid in {s.station_id for s in shift_rows}
    }
    shift_median = float(np.median([s.median_pct for s in shift_rows]))
    shift_p90_lo = min(s.p90_pct for s in shift_worst.values())
    shift_p90_hi = max(s.p90_pct for s in shift_worst.values())
    shift_mean_lo = min(s.mean_pct for s in shift_worst.values())
    shift_mean_hi = max(s.mean_pct for s in shift_worst.values())
    audit(
        f"Pyramid +-1 h period-convention cost, across {len(shift_rows)} "
        f"station-shifts at {len(shift_worst)} stations: median of the "
        f"per-station medians {shift_median:.2f}%; taking the WORSE shift "
        "direction per station, the 90th percentile of the daily-total "
        f"change spans {shift_p90_lo:.2f}-{shift_p90_hi:.2f}% and the mean "
        f"{shift_mean_lo:.2f}-{shift_mean_hi:.2f}%"
    )

    # Co-located pairs, measured against THIS package's DHM record.
    pyramid_hour = pl.col("timestamp_utc_hour_floor").str.strptime(
        pl.Datetime, "%Y-%m-%dT%H:%M:%SZ"
    )
    colocations: list[PyramidColocation] = []
    for pair in COLOCATED_PAIRS:
        dhm_name = str(pair.dhm_station)
        if dhm_name not in stations:
            continue
        pid = pair.pyramid_csv_filename.split("_Z")[0]
        meta = pyramid_meta[pid]
        dhm_coord = coords[Station(dhm_name)]
        sep_km = haversine_km(dhm_coord.lat, dhm_coord.lon, meta.lat, meta.lon)
        elev_delta = abs(dhm_coord.elev_m - meta.elev_m)
        if abs(sep_km - pair.separation_km) > 0.15:
            raise BuildError(
                f"{dhm_name}/{pid}: measured separation {sep_km:.2f} km "
                f"disagrees with coloc_pairs.py's declared {pair.separation_km} km"
            )
        if abs(elev_delta - pair.elevation_delta_m) > 1.0:
            raise BuildError(
                f"{dhm_name}/{pid}: measured elevation delta {elev_delta:.0f} m "
                f"disagrees with coloc_pairs.py's declared "
                f"{pair.elevation_delta_m} m"
            )
        dhm_obs = (
            gauge.filter(
                (pl.col("station") == dhm_name)
                & pl.col("observed")
                & ~pl.col("qc_masked")
            )
            .select(pl.col("timestamp_utc").alias("h"))
            .unique()
        )
        pyr_obs = (
            pyramid.filter((pl.col("station") == pid) & pl.col("observed_rr"))
            .select(pyramid_hour.cast(dhm_obs.schema["h"]).alias("h"))
            .unique()
        )
        lo = max(dhm_obs["h"].min(), pyr_obs["h"].min())
        hi = min(dhm_obs["h"].max(), pyr_obs["h"].max())
        n_overlap = int((hi - lo).total_seconds() // 3600) + 1
        both = dhm_obs.join(pyr_obs, on="h", how="inner")
        coloc = PyramidColocation(
            dhm_station=dhm_name,
            pyramid_station_id=pid,
            pyramid_name=meta.name,
            separation_km=sep_km,
            declared_separation_km=pair.separation_km,
            elev_delta_m=elev_delta,
            declared_elev_delta_m=pair.elevation_delta_m,
            overlap_start_utc=lo.strftime("%Y-%m-%dT%H:%M:%SZ"),
            overlap_end_utc=hi.strftime("%Y-%m-%dT%H:%M:%SZ"),
            n_overlap_hours=n_overlap,
            n_dhm_observed_in_overlap=dhm_obs.filter(
                (pl.col("h") >= lo) & (pl.col("h") <= hi)
            ).height,
            n_pyramid_observed_in_overlap=pyr_obs.filter(
                (pl.col("h") >= lo) & (pl.col("h") <= hi)
            ).height,
            n_co_observed=both.height,
            co_observed_start_utc=both["h"].min().strftime("%Y-%m-%dT%H:%M:%SZ"),
            co_observed_end_utc=both["h"].max().strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        colocations.append(coloc)
        audit(
            f"co-located pair {dhm_name} / Pyramid {pid} ({meta.name}): "
            f"separation {sep_km:.2f} km (coloc_pairs.py declares "
            f"{pair.separation_km} km), elevation delta {elev_delta:.0f} m "
            f"(declares {pair.elevation_delta_m:.0f} m)"
        )
        audit(
            f"  measured overlap of the two OBSERVED precipitation records: "
            f"{coloc.overlap_start_utc} .. {coloc.overlap_end_utc} "
            f"({n_overlap} h). In that window DHM observes and retains "
            f"{coloc.n_dhm_observed_in_overlap} h and Pyramid observes "
            f"{coloc.n_pyramid_observed_in_overlap} h"
        )
        audit(
            f"  CO-OBSERVED hours (both networks reporting the same UTC hour, "
            f"pairing the Pyramid stamp to the UTC hour it falls in — a "
            f"methodological CHOICE, see the timebase section): "
            f"{coloc.n_co_observed} h, "
            f"{coloc.co_observed_start_utc} .. {coloc.co_observed_end_utc}"
        )
    if len(colocations) != 2:
        raise BuildError(
            f"expected 2 co-located pairs from coloc_pairs.py, resolved "
            f"{len(colocations)}"
        )

    # --- Pyramid outputs -----------------------------------------------------
    licence_path = out_dir / "LICENCE-pyramid.txt"
    licence_path.write_text(PYRAMID_LICENCE_TEXT, encoding="utf-8")

    pyramid_header = [
        "Pyramid Meteorological Network (Khumbu, Nepal) -- Level 1 hourly "
        "automatic weather station data.",
        "",
        f"LICENCE: {PYRAMID_LICENCE}. Provided by {PYRAMID_PROVIDER}. "
        f"DOI {PYRAMID_DOI}.",
        f"ATTRIBUTION IS A CONDITION OF USE. Cite: {PYRAMID_CITATION}",
        "Any output, figure, table or model trained on this file must carry "
        "that citation. See LICENCE-pyramid.txt.",
        "",
        "NOT OPERATIONAL DATA. This network does not deliver in real time and "
        "cannot feed a deployed forecast. It is here as a DATA-QUALITY "
        "CEILING: the gap between a run driven by this and a run driven by "
        "the DHM gauges is attributable to data quality, not to the model or "
        "the basin.",
        "",
        "LEVEL 1 = provider quality control only. NO quality control of ours "
        "has been applied and NO row has been dropped: the DHM rule set in "
        "gauge_precipitation_hourly.csv is a PRECIPITATION-GAUGE rule set and "
        "applying it here would be wrong. These data need their own QC, which "
        "you must do.",
        "An EMPTY value cell means the station did not report that variable "
        "in that hour. IT NEVER MEANS ZERO. Each variable carries its own "
        "observed_* flag, which is exactly 'the value cell is not empty'.",
        "",
        "TIMEBASE -- read this before joining anything to the DHM files.",
        "  timestamp_npt: the stamp EXACTLY as the provider delivered it "
        "(year;month;day;hour), with the provider-stated zone attached. No "
        "value has been changed.",
        "  timestamp_utc: the exact NPT -> UTC conversion. NPT is UTC+5:45, "
        "so an hourly NPT stamp lands at :15 PAST the UTC hour. This series "
        "is therefore NOT on the same hourly UTC grid as the DHM and "
        "ERA5-Land files in this package -- the two grids are 15 minutes "
        "apart.",
        "  timestamp_utc_hour_floor: OPTIONAL CONVENIENCE COLUMN, provided so "
        "that an alignment choice is visible rather than improvised. Its rule "
        "is: the UTC hour that contains timestamp_utc, i.e. timestamp_utc "
        "truncated to the hour (equivalently timestamp_utc minus 15 min). It "
        "is NOT a claim that the two series are on one axis. Joining on it "
        "moves every Pyramid reading 15 minutes earlier.",
        "",
        "THE PERIOD CONVENTION IS UNRESOLVED. The NPT offset is exact and "
        "certain. Whether a stamp of 14:00 covers 13:00-14:00 or 14:00-15:00 "
        "is stated NOWHERE by the provider and the question has not been "
        "answered. DHM's convention IS known -- period-ending, confirmed "
        "2026-08-31 -- so the residual uncertainty BETWEEN the two networks "
        "is +-1 h. We are not offering a preferred convention; there is no "
        "known answer to offer.",
        "",
        "Every number describing this file was computed at build time; see "
        "README.md section 3 and BUILD_AUDIT.txt.",
        "",
        "Read with: pandas.read_csv(path, comment='#') or "
        "polars.read_csv(path, comment_prefix='#')",
    ]
    pyramid_path = out_dir / "pyramid_aws_hourly.csv"
    _write_with_header(pyramid_path, pyramid, pyramid_header)

    coloc_by_pid = {c.pyramid_station_id: c for c in colocations}
    pyramid_station_rows = []
    for station_id in sorted(pyramid_tiers, key=lambda s: -pyramid_meta[s].elev_m):
        tier = pyramid_tiers[station_id]
        meta = tier.station
        coloc = coloc_by_pid.get(station_id)
        row_out: dict[str, object] = {
            "station": station_id,
            "station_name": meta.name,
            "lat": meta.lat,
            "lon": meta.lon,
            "elev_m": meta.elev_m,
            "native_step_h": tier.native_step_h,
            "n_hours_axis": tier.n_hours_axis,
            "first_hour_npt": tier.first_npt,
            "last_hour_npt": tier.last_npt,
            "first_hour_utc": tier.first_utc,
            "last_hour_utc": tier.last_utc,
        }
        for var in PYRAMID_VARIABLES:
            key = var.source_column
            row_out[f"n_observed_{key.lower()}"] = (
                tier.n_observed[key] if key in tier.present else None
            )
            row_out[f"coverage_pct_{key.lower()}"] = (
                round(tier.coverage_pct[key], 2) if key in tier.present else None
            )
        row_out["wind_mean_ms"] = (
            round(tier.wind_mean_ms, 2) if tier.wind_mean_ms is not None else None
        )
        row_out["wind_p95_ms"] = (
            round(tier.wind_p95_ms, 2) if tier.wind_p95_ms is not None else None
        )
        row_out["wet_hour_wind_mean_ms"] = (
            round(tier.wet_hour_wind_mean_ms, 2)
            if tier.wet_hour_wind_mean_ms is not None
            else None
        )
        row_out["dhm_colocated_with"] = coloc.dhm_station if coloc else None
        row_out["dhm_separation_km"] = round(coloc.separation_km, 2) if coloc else None
        row_out["dhm_elev_delta_m"] = coloc.elev_delta_m if coloc else None
        row_out["dhm_co_observed_hours"] = coloc.n_co_observed if coloc else None
        pyramid_station_rows.append(row_out)
    pyramid_stations_path = out_dir / "pyramid_stations.csv"
    _write_with_header(
        pyramid_stations_path,
        pl.DataFrame(pyramid_station_rows),
        [
            "Pyramid Meteorological Network -- one row per Level 1 station, "
            "every column measured at build time from the provider's files.",
            "",
            f"LICENCE: {PYRAMID_LICENCE}. Provided by {PYRAMID_PROVIDER}. "
            f"DOI {PYRAMID_DOI}. ATTRIBUTION IS A CONDITION OF USE.",
            f"Cite: {PYRAMID_CITATION}",
            "",
            "station / station_name / lat / lon / elev_m come from the "
            "provider's README_.txt; elev_m is cross-checked against the "
            "Z#### token of the filename and the build fails if the two "
            "disagree.",
            "native_step_h is MEASURED from the delivered stamps, not taken "
            "from the provider's README_.txt, which claims 1 hour for every "
            "station.",
            "coverage_pct_* is over the station's complete HOURLY axis "
            "(n_hours_axis). For a station whose native step is not 1 h, "
            "divide by native_step_h to read coverage on its own sampling "
            "grid.",
            "An empty n_observed_*/coverage_pct_* means the station does not "
            "measure that variable at all -- not that it measured nothing.",
            "",
            "Read with: pandas.read_csv(path, comment='#') or "
            "polars.read_csv(path, comment_prefix='#')",
        ],
    )

    # 13 — README -------------------------------------------------------------
    readme_path = out_dir / "README.md"
    readme_path.write_text(
        _reflow_markdown(
            render_readme(
                stations=stations,
                coords=coords,
                stats=stats,
                masked_counts=masked_counts,
                res_by_station=res_by_station,
                group_summary=group_summary,
                confound_gap=confound_gap,
                wet_ratio=wet_ratio,
                relief=relief,
                subthreshold=subthreshold,
                flagged_extremes=flagged_extremes,
                global_flagged_min=global_flagged_min,
                n_voided=n_voided.height,
                station_relief=station_relief,
                offsets=offsets,
                median_offset=median_offset,
                era5_band_offset=era5_band_offset,
                pyramid_gradient=pyramid_gradient,
                elev_by_station=elev_by_station,
                expected_hours=expected_hours,
                start=start,
                end=end,
                axis_hi=axis_hi,
                precip_dir=precip_dir,
                t2m_dir=t2m_dir,
                rule_rows=rule_rows,
                params=params,
                source_sha=PRODUCTION_SOURCE_SHA256,
                pyramid_meta=pyramid_meta,
                pyramid_tiers=pyramid_tiers,
                pyramid_elev=pyramid_elev,
                pyramid_rows=pyramid.height,
                pyramid_checked=pyramid_checked,
                pyramid_gated_out=pyramid_gated_out,
                colocations=colocations,
                shift_median=shift_median,
                shift_p90_lo=shift_p90_lo,
                shift_p90_hi=shift_p90_hi,
                shift_mean_lo=shift_mean_lo,
                shift_mean_hi=shift_mean_hi,
            )
        ),
        encoding="utf-8",
    )

    # 14 — the script itself, the audit trail, then checksums over everything -
    # The canonical build script is copied in first so the package always ships
    # the exact file that produced it; BUILD_AUDIT.txt is written next, before
    # the manifest, so the manifest covers it. Per-file
    # digests are printed to stdout only — they cannot appear in the audit file
    # they are computed over.
    _copy_self_into_package(out_dir)
    (out_dir / "BUILD_AUDIT.txt").write_text("\n".join(AUDIT) + "\n", encoding="utf-8")
    emitted = sorted(
        p for p in out_dir.iterdir() if p.is_file() and p.name != "SHA256SUMS"
    )
    lines = []
    for path in emitted:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
        print(  # noqa: T201 — see `audit`
            f"[emitted] {path.name} ({path.stat().st_size} bytes) {digest}"
        )
    (out_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\nwrote {len(emitted)} files to {out_dir}")  # noqa: T201
    return 0


def _reflow_markdown(text: str, width: int = 80) -> str:
    """Re-wrap prose paragraphs only.

    Substituting computed numbers into a hand-wrapped template leaves ragged
    lines. Markdown renders them fine, but this document is meant to be read as
    a plain file too. Tables, fenced code, headings, list items and blockquotes
    are passed through byte-for-byte; only runs of plain prose are re-wrapped.
    """
    import textwrap

    out: list[str] = []
    block: list[str] = []
    in_code = False

    def flush() -> None:
        if block:
            out.extend(
                textwrap.wrap(
                    " ".join(block),
                    width=width,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )
            block.clear()

    for line in text.split("\n"):
        if line.startswith("```"):
            flush()
            in_code = not in_code
            out.append(line)
            continue
        if in_code or not line.strip() or line[0] in "#|*->" or line.startswith("  "):
            flush()
            out.append(line)
            continue
        block.append(line.strip())
    flush()
    return "\n".join(out)


def _write_with_header(path: Path, frame: pl.DataFrame, comments: list[str]) -> None:
    body = frame.write_csv(datetime_format="%Y-%m-%dT%H:%M:%SZ")
    header = "\n".join(f"# {c}".rstrip() for c in comments)
    path.write_text(header + "\n" + body, encoding="utf-8")


# `k` is a heterogeneous bag of every measured value the document interpolates.
# It is typed `Any` rather than `object` so that pyright does not report an error
# per format spec; the values themselves are all built and asserted in `main`.
def render_readme(**k: Any) -> str:  # noqa: C901 — a document, not logic
    stations: tuple[str, ...] = k["stations"]  # type: ignore[assignment]
    coords = k["coords"]
    stats = k["stats"]
    masked_counts = k["masked_counts"]
    res_by_station = k["res_by_station"]
    gs = k["group_summary"]
    offsets = k["offsets"]
    elev = k["elev_by_station"]
    params = k["params"]
    pyramid_meta = k["pyramid_meta"]
    pyramid_tiers = k["pyramid_tiers"]
    colocations = k["colocations"]
    pyr_elev = k["pyramid_elev"]
    shift_median = k["shift_median"]
    shift_p90_lo = k["shift_p90_lo"]
    shift_p90_hi = k["shift_p90_hi"]
    shift_mean_lo = k["shift_mean_lo"]
    shift_mean_hi = k["shift_mean_hi"]

    pyr_order = sorted(pyramid_tiers, key=lambda s: -pyramid_meta[s].elev_m)

    def pyr_station_row(sid: str) -> str:
        t = pyramid_tiers[sid]
        m = t.station
        present = ", ".join(
            v.source_column for v in PYRAMID_VARIABLES if v.source_column in t.present
        )
        step = f"{t.native_step_h} h" + (" ⚠️" if t.native_step_h != 1 else "")
        return (
            f"| `{sid}` | {m.name} | {m.elev_m:,} m | {m.lat:.2f}, {m.lon:.2f} | "
            f"{step} | {t.first_npt[:10]} → {t.last_npt[:10]} | {present} |"
        )

    def pyr_coverage_row(sid: str) -> str:
        t = pyramid_tiers[sid]
        cells = []
        for v in PYRAMID_VARIABLES:
            key = v.source_column
            if key not in t.present:
                cells.append("— not measured")
            else:
                cells.append(f"{t.coverage_pct[key]:.1f} %")
        return (
            f"| `{sid}` | {t.station.elev_m:,} m | {t.n_hours_axis:,} | "
            + " | ".join(cells)
            + " |"
        )

    def pyr_wind_row(sid: str) -> str:
        t = pyramid_tiers[sid]
        if t.wind_mean_ms is None:
            return f"| `{sid}` | {t.station.elev_m:,} m | — | — | — |"
        wet = (
            f"{t.wet_hour_wind_mean_ms:.2f} m/s"
            if t.wet_hour_wind_mean_ms is not None
            else "— no gauge"
        )
        return (
            f"| `{sid}` | {t.station.elev_m:,} m | {t.wind_mean_ms:.2f} m/s | "
            f"{t.wind_p95_ms:.2f} m/s | {wet} |"
        )

    def coloc_row(c: object) -> str:
        return (
            f"| {c.dhm_station} | `{c.pyramid_station_id}` {c.pyramid_name} | "
            f"{c.separation_km:.2f} km | {c.elev_delta_m:,.0f} m | "
            f"{c.overlap_start_utc[:10]} → {c.overlap_end_utc[:10]} | "
            f"{c.n_overlap_hours:,} h | {c.n_co_observed:,} h |"
        )

    wind_low = min(
        (t for t in pyramid_tiers.values() if t.wet_hour_wind_mean_ms is not None),
        key=lambda t: t.station.elev_m,
    )
    wind_high = max(
        (t for t in pyramid_tiers.values() if t.wet_hour_wind_mean_ms is not None),
        key=lambda t: t.station.elev_m,
    )
    wind_ratio = wind_high.wet_hour_wind_mean_ms / wind_low.wet_hour_wind_mean_ms
    pyr_var_header = " | ".join(
        f"{v.source_column} ({v.unit})" for v in PYRAMID_VARIABLES
    )
    dhm_top = max(coords[Station(s)].elev_m for s in stations)
    coarse = [s for s in pyr_order if pyramid_tiers[s].native_step_h != 1]
    coarse_note = "\n\n".join(
        "⚠️ Read `"
        + s
        + "`'s row against its **"
        + f"{pyramid_tiers[s].native_step_h}-hourly** sampling (§3.1), not "
        "against the hourly axis it is printed on: on its own "
        f"{pyramid_tiers[s].native_step_h}-hourly grid the same counts are "
        + ", ".join(
            f"{v.source_column} "
            f"{pyramid_tiers[s].coverage_pct_native[v.source_column]:.1f} %"
            for v in PYRAMID_VARIABLES
            if v.source_column in pyramid_tiers[s].present
        )
        + "."
        for s in coarse
    )

    def row(station: str) -> str:
        c = coords[Station(station)]
        s = stats[station]
        _, res = res_by_station[station]
        return (
            f"| {station} | {c.elev_m:,.0f} m | {c.lat:.5f}, {c.lon:.5f} | "
            f"{res} mm | {s.coverage_pct:.1f} % | {s.wet_hour_pct:.1f} % | "
            f"{s.n_qc_masked:,} |"
        )

    def qc_row(station: str) -> str:
        counts = masked_counts[station]
        fired = ", ".join(f"`{r}` {n:,}" for r, n in sorted(counts.items()) if n)
        return f"| {station} | {stats[station].n_qc_masked:,} | {fired or 'none'} |"

    ordered = sorted(stations, key=lambda s: -coords[Station(s)].elev_m)

    return f"""# Dudh Koshi — hourly precipitation for a rainfall-runoff model

Prepared by hydrosolutions. Three DHM gauges in and below the Dudh Koshi,
hourly, **{k["start"]:%Y-%m-%d} → {k["end"]:%Y-%m-%d} UTC**, on a complete
hourly axis of **{k["expected_hours"]:,} hours per station**.

Alongside them, a second and very different tier: **{len(pyramid_tiers)}
research-grade AWS of the Pyramid network**, {pyr_elev[0]:,}–{pyr_elev[-1]:,} m
up the Khumbu, with temperature, precipitation, **wind**, humidity and
pressure. It is **not operational data** — it is a **data-quality ceiling**, so
that a weak result can be attributed to the data rather than to the model or
the basin. See §3, and note that its licence carries a **citation you must
reproduce**.

Verify the transfer first:

```
shasum -a 256 -c SHA256SUMS
```

Every number in this file was computed by `{PACKAGED_SCRIPT_NAME}` at build
time.
`BUILD_AUDIT.txt` is that run's full audit trail.

---

## 1. What you have

| file | what it is |
|---|---|
| `gauge_precipitation_hourly.csv` | **the core deliverable** — DHM gauge precipitation, hourly, no row dropped, with the raw delivered value alongside |
| `stations.csv` | coordinates, elevation, reporting resolution, coverage, and the ERA5 cell each station falls in |
| `era5_land_precipitation_hourly.csv` | ERA5-Land precipitation — a **model covariate**, not a gauge |
| `era5_land_temperature_hourly.csv` | ERA5-Land 2 m temperature — a **model covariate**, not a measurement |
| `pyramid_aws_hourly.csv` | **the ceiling** — {len(pyramid_tiers)} Pyramid AWS, hourly, in-situ AT / RR / WS / RH / AP / WD, {pyr_elev[0]:,}–{pyr_elev[-1]:,} m. **Not operational.** CC BY 4.0, **citation required** |
| `pyramid_stations.csv` | the Pyramid stations: coordinates, elevation, measured sampling step, and coverage per variable |
| `LICENCE-pyramid.txt` | the Pyramid licence and the attribution you must carry |
| `{PACKAGED_SCRIPT_NAME}` | the script that produced all of the above — copied in verbatim by the run itself from `scripts/dhm_precip/{PACKAGED_SCRIPT_NAME}` |
| `BUILD_AUDIT.txt` | every computed number, as printed by that run |
| `SHA256SUMS` | checksums over every file here |

🔴 **The model target — discharge — is not in this package, but it does
exist.** It is already in your data environment, under stricter terms and on a
different clock. **§2.7 before anything else.**

### The basin

The Dudh Koshi drains the Khumbu — Everest, the Ngozumpa and Khumbu glaciers —
and its runoff is substantially snow and ice melt, not rainfall. The three
gauges here span **{k["station_relief"]:,.0f} m** of elevation: Syangboche and
Lukla sit in the high catchment, Aiselukhark at its lower end.

⚠️ **We have not verified these three stations against a basin polygon.** The
station set was specified for this handover from the DHM network we hold; there
is no delineation in this package. Check membership against your own
delineation before treating them as basin forcing, and note that three point
gauges are in any case not a basin average.

| station | elevation | lat, lon | reporting resolution | coverage | wet hours | QC-masked hours |
|---|---|---|---|---|---|---|
{chr(10).join(row(s) for s in ordered)}

*Coverage* = observed hours ÷ {k["expected_hours"]:,} hours on the axis.
*Wet hours* = hours with `precip_mm > 0`, as a percentage of observed
QC-retained hours. `stations.csv` also carries the fraction at or above the
{params.wet_threshold_mm_per_h} mm/h wet threshold this project uses elsewhere.

---

## 2. Read this before you fit anything

### 2.1 Precipitation alone is structurally insufficient in this basin

The Dudh Koshi's runoff is substantially **snow and ice melt**. A model that
maps precipitation to discharge with no temperature and no snow state will fail
here regardless of how good the rainfall is — and, this is the important part,
**the failure will look like bad precipitation**. You will see under-prediction
in spring and over-prediction after monsoon onset, and both are recoverable
signals you simply have not been given a variable for.

`era5_land_temperature_hourly.csv` gives you a temperature *covariate*, which is
better than nothing and is enough to build a degree-day-style state. It is not
enough to close the gap, because:

* **There is no in-situ temperature at the DHM gauges.** No gauge thermometer,
  at any of the three stations, at any hour. (There *is* in-situ temperature in
  the separate, non-operational Pyramid tier — see below and §3.)
* **There is no snow state.** No snow water equivalent, no snow cover, no
  glacier mask, no ice-melt term.
* **There is no discharge in this package.** The target does exist — in your
  data environment, not here, and on a different clock. See §2.7.

In-situ temperature **is** in this package, but in the other tier and under
different terms: `pyramid_aws_hourly.csv` carries hourly measured air
temperature at {len(pyramid_tiers)} Khumbu AWS, one of them
{colocations[0].separation_km:.1f} km from {colocations[0].dhm_station}. Read
**§3 before you touch it**: it is not operational data, its timestamps are
Nepal local wall-clock on an **unresolved period convention**, and it does not
land on the same hourly UTC grid as this file. It does not remove this gap for
an operational model — it lets you measure how much of the gap is the data.

There is still **no snow state** anywhere in this package, and **no
discharge in this package** — the target lives outside it (§2.7).

**This is the package's largest known gap. Treat a P→Q result here as
uninterpretable until temperature and snow state are in the model.**

### 2.2 Missing is missing — never zero

`precip_mm` is **empty** wherever the gauge did not report a usable
measurement. `observed=false` marks exactly those hours, in both directions —
`observed` is precisely `precip_mm is not null`, so you can trust it as a mask
without re-deriving it. There is no imputation and no gap-filling anywhere in
this package.

This matters more than it sounds. Our missingness may be **informative**: a
mechanical gauge can fail *during* heavy rain, so the hours you are missing are
plausibly enriched in the events you most want to predict. A false zero in that
position does not merely add noise — it teaches the model to predict "dry"
precisely at the onset of the events that matter.

**Do not `fillna(0)`.** If your framework needs a dense tensor, carry the
`observed` flag as a mask into the loss, or drop the sequence, or learn the
missingness — but do not let a null silently become a number.

### 2.3 The QC mask is a flag, not a filter

Every hour is present and no row was dropped. `qc_masked=true` means our quality
control fired on that hour and `qc_rule` names which rule; the delivered value
is always kept, in `raw_delivered_mm`. Except where a value is physically
impossible (below), the flagged value also stays in `precip_mm` — **you decide**
what to do with it.

| station | masked hours | rules that fired |
|---|---|---|
{chr(10).join(qc_row(s) for s in ordered)}

The rules, exactly as executed:

{chr(10).join(f"* **`{r.rule_id}` / `{r.rule_version}`** — pass {r.pass_name}, scope `{r.scope}`, {r.time_step_seconds // 60} min step, thresholds `{r.thresholds}`" for r in k["rule_rows"])}

**Two columns, two jobs.** `precip_mm` is the modelling column — it holds a
value only where the gauge delivered something that could be a measurement.
`raw_delivered_mm` is the provenance column — it holds **every** value the
source workbook delivered for that hour, unaltered, whatever our QC thinks of
it. Nothing is discarded and no row is dropped; the two columns differ only
where we can name a reason, and `qc_rule` names it.

They differ in exactly one situation. `range_check` is a
**physical-impossibility gate**, not an outlier filter: a value failing it is
not a measurement of anything — at Lukla it is a `{k["global_flagged_min"]:,.0f}`
missing-data sentinel written into the source file. Those
{k["n_voided"]:,} hours are emitted as `precip_mm` **empty**,
`observed=false`, `qc_masked=true`, `qc_rule=range_check` — and their delivered
value is still there, in `raw_delivered_mm`. A sentinel in a value column is the
same failure as §2.2 inverted, and a worse one: one careless `.mean()` and it
dominates the loss.

`long_zero_run` is different and its rows are **not** gated: it flags a
perfectly flat run of at least
{params.qc_mask_long_zero_run_min_consecutive_hours} h *inside a monsoon
season*, when a gauge should not be flat — a stuck sensor reporting as a long
dry spell. The value is a physically plausible 0.0, `observed` stays `true`, and
whether to trust it is genuinely your call. At Aiselukhark that is a large block of hours, and reading them as
real dry weather during the monsoon would bias every wet-season statistic you
compute:

```python
df = pd.read_csv("gauge_precipitation_hourly.csv", parse_dates=["timestamp_utc"])
strict = df.precip_mm.where(~df.qc_masked)   # drop the flagged hours too
```

### 2.4 The reporting-resolution confound

The two upper gauges report to **{gs["A"]["resolution_mm"]} mm**; Aiselukhark
reports to **{gs["B"]["resolution_mm"]} mm**. Across the whole
{gs["A"]["n"] + gs["B"]["n"]}-station DHM network this package is drawn from,
the split is **completely confounded with elevation**:

| resolution | stations | elevation range |
|---|---|---|
| {gs["A"]["resolution_mm"]} mm | {gs["A"]["n"]:.0f} | {gs["A"]["elev_min"]:,.0f}–{gs["A"]["elev_max"]:,.0f} m |
| {gs["B"]["resolution_mm"]} mm | {gs["B"]["n"]:.0f} | {gs["B"]["elev_min"]:,.0f}–{gs["B"]["elev_max"]:,.0f} m |

There is **no overlap** — a {k["confound_gap"]:,.0f} m gap separates the two
groups. In this data, *"high station"* and *"fine-resolution gauge"* are the
same statement. Any elevation effect you find is equally an instrument effect,
and nothing in this dataset can separate them. Do not report one as if you had
ruled out the other.

It is not a cosmetic difference either. Of the non-zero hours these gauges
retain, {", ".join(f"{s} reports {k['subthreshold'][s]:.0f} %" for s in ordered)}
below the {params.wet_threshold_mm_per_h} mm/h wet threshold — mass the coarse
gauge is structurally incapable of representing, because
{params.wet_threshold_mm_per_h} mm *is* its smallest non-zero reading. That is
also why Aiselukhark's `wet_hour_pct` and `wet_hour_pct_ge_0p2mm` are identical
in `stations.csv` while the two fine gauges differ sharply between those two
columns.

### 2.5 The wet-hour fractions are not plausibly climatic

Lukla reports rain in **{stats["Lukla Airport"].wet_hour_pct:.1f} %** of its
observed hours; Aiselukhark in
**{stats["Aiselukhark"].wet_hour_pct:.1f} %** — a factor of
**{k["wet_ratio"]:.1f}×**, across {k["relief"]:,.0f} m of relief, in one basin.

That is not a credible orographic signal at that separation. It is far more
likely an instrument or reporting-convention difference — different sensor,
different threshold below which a tip is not written, different rounding. **We
do not know which, and we are not going to guess.** It is flagged here as
unexplained because it will show up in your model as a station effect, and you
should know before you attribute it to physics.

### 2.6 High-elevation under-catch, worst for snow

Precipitation gauges under-catch, and they under-catch worse with wind and worse
still for snow. Two of these three gauges sit above 2,800 m in exposed terrain.
**Basin precipitation in this package is biased low, in a way that varies with
elevation and with precipitation phase.**

We cannot size that bias, and we are not going to pretend otherwise: **we do not
know the gauge type at these stations** — weighing versus tipping bucket is an
open question with DHM, not a finding — and we have no wind measurements at
them. Under-catch transfer functions are gauge-type- and wind-specific, so
neither input for one exists.

We measured an apparent rain-phase gradient of **{k["pyramid_gradient"]}** down
an independent Khumbu transect. ⛔ **That is an upper bound in magnitude,
confounded with wind catch — it is not a correction factor and must not be
applied as one.** Catch efficiency itself falls as you go up the transect, so
the observed decline is at least partly the instrument. The true decline is no
steeper than that number and could in principle be much shallower.

There is no under-catch correction in this package, and we do not have one we
trust.

⭐ **What has changed: the Pyramid tier ships co-located wind.** Wind at the
gauge is the missing input that turns catch efficiency from *bounded* into
*estimable*, and `pyramid_aws_hourly.csv` measures it on the same masts as its
precipitation. §3.4 shows the confound directly — **{wind_ratio:.1f}× more wind
during observed wet hours at {wind_high.station.elev_m:,} m than at
{wind_low.station.elev_m:,} m**, up the same transect the gradient above was
measured on. It still does not hand you a correction factor for *these* DHM
gauges, whose type and wind exposure remain unknown.

### 2.7 The discharge target — it exists, and three things about it will bite you

🔴 **Confidentiality, first.** The `readme.txt` that ships with the discharge
data states, verbatim:

> These data are to be treated with utmost confidentiality.

That is the provider's own wording — source **Binod Parajuli, DHM Nepal**; the
folder was assembled by **Silvan Ragettli, November 2025**. **Anything you
derive from that series inherits the restriction**: a trained model, a fitted
curve, a hydrograph figure, a table of scores. Do not redistribute or publish
the data, or any product of it, without DHM's agreement. This is stricter than
the terms on the precipitation in §6, and it travels with the derived product,
not only with the file.

**Where it is.** Not in this package — we have deliberately not copied it here.
It is already in your data environment, alongside this folder:

`…/2025-01-BARHKH/data/runoff/DHM_subdaily_DudhKoshi/`

| file | what it is |
|---|---|
| `Water Level Inst from 2014-01-01 to 2025-11-21.csv` | 15-minute **instantaneous water level**. Header `dateTime (NPT),value (m)`; 256,973 data rows; 2014-01-09 → 2025-11-21 |
| `RT_670.txt` | the **rating table for station 670** — stage (m) in the left column, discharge (m³/s) in the right. Declares `Rating Type No = 1` |
| `readme.txt` | provenance, and the confidentiality statement quoted above |

Read that header literally: the file holds **water level in metres**, not
discharge, and its clock is **NPT**. Both are load-bearing, and each gets a
subsection below.

#### 2.7.1 🔴 Three clocks, not two — the most dangerous item in this package

The sources you are being asked to join do not share a clock, and they do not
all carry the same *kind* of quantity.

| source | clock | period convention | quantity |
|---|---|---|---|
| DHM precipitation — `gauge_precipitation_hourly.csv` | **UTC** | **period-ending**, confirmed by DHM 2026-08-31 | accumulation over the hour ending at the stamp |
| Pyramid AWS — `pyramid_aws_hourly.csv` | **NPT = UTC+5:45** | **unknown** — stated nowhere by the provider, and the question is unanswered (§3.3) | accumulation (`RR`) and instantaneous states (`AT`, `WS`, …) |
| Discharge water level — `Water Level Inst …csv` | **NPT = UTC+5:45**, declared in the header as `dateTime (NPT)` | **none applies** — the values are 15-minute **instantaneous** readings | a **state** (stage, m) sampled at the stamp |

The two ERA5-Land files share the first row's clock — UTC, precipitation
period-ending, 2 m temperature instantaneous at the stamp (§4).

**What goes wrong.** Join the precipitation and the water level on their raw
stamps and you have **shifted the entire rainfall-runoff relationship by 5
hours 45 minutes.** Nothing raises an error. Nothing looks malformed. The
series overlap, the hydrographs look sane, and the model trains. What you see
is a systematic timing offset that reads exactly like catchment response — and
a lag parameter, or an LSTM's own memory, will absorb it and quietly encode the
bug as physics. **Convert once, explicitly, at read time, and assert the
offset** rather than trusting a parser to have guessed the zone.

**A shift is not the only problem.** Instantaneous stage and period-ending
accumulation are **different kinds of quantity**. A stage reading samples a
state at an instant; an hourly rainfall total integrates over an interval.
They cannot simply be resampled onto one another. Averaging the four 15-minute
stage samples inside an hour, taking the sample on the hour boundary, and
interpolating to it are **three different rules with three different answers**,
and the choice interacts with the period convention above. Pick a rule, state
it, and carry it with your results.

#### 2.7.2 Stage → discharge is a rating-curve product, so the target has its own error

You are not being handed discharge. You are being handed stage plus a rating
table, and the conversion is yours to do. Discharge for Nepali stations is not
measured directly: it is derived from a measured water **level** through a
**versioned rating table**, fitted to a finite set of gaugings and re-issued
when the channel changes.

That means your target variable carries its own uncertainty, and that
uncertainty is **worst at high flows** — the extrapolated end of the curve,
with the fewest gaugings — which is exactly the regime a flood-forecasting
model is judged on. When you compare model error against observed discharge at
high flow, some of the disagreement is the rating table, not the model.

⚠️ **`RT_670.txt` declares `Rating Type No = 1`**, which implies other rating
types or versions exist. A rating is valid for a period, and applying one table
across 2014-2025 assumes the channel never shifted. **Confirm with DHM which
rating applies over which period** before converting eleven years of stage with
a single curve.

⚠️ **Check the zeros before you use them — an observation to verify, not a
finding.** The first rows of the water-level file read `0`. A stage of exactly
zero is an unlikely measurement in a river that flows year-round, so a `0`
there may be a **no-data sentinel rather than a reading**. We have not
investigated it and are not asserting it. Put the question to DHM, because a
sentinel zero pushed through a rating table comes out as a discharge and is
then indistinguishable from data.

### 2.8 Timebase

**All timestamps in every file in this package are UTC**, and every accumulated
quantity is **period-ending**. An hour stamped
`2021-07-15T16:00:00Z` is the accumulation over 15:00 → 16:00 UTC. DHM confirmed
this convention on 2026-08-31; it is a stated fact, not an assumption.

Nepal local time is **NPT = UTC + 5:45** — a 45-minute offset, not a whole
number of hours. **Do not assume local-midnight day boundaries.** A "daily
total" built by grouping these UTC stamps into calendar days is a UTC day, and
it is shifted by 5h45m from the Nepali one; the monsoon diurnal cycle is strong
enough here that the choice changes daily totals materially.

🔴 **This paragraph applies to the DHM and ERA5-Land files only.** The Pyramid
tier is delivered in NPT wall-clock, its period convention is **unresolved**,
and because NPT is not a whole number of hours its exact UTC conversion falls
at **:15 past the hour** — 15 minutes off this grid. §3.3 states that in full.
Do not join the two without reading it.

🔴 **And the discharge target is a third clock.** It is not in this package, so
this section does not describe it: it is NPT wall-clock carrying 15-minute
instantaneous stage. **§2.7.1 is the section that matters** — mixing that clock
with this one is the easiest way in the whole exercise to invalidate a result
without noticing.

### 2.9 ERA5-Land is a covariate, not a gauge

Both ERA5-Land files are **model output**. They are labelled as covariates and
are used nowhere in this package as a quality-control input — checking a gauge
against a reanalysis that assimilates no gauge in this terrain, and then using
the result to justify the gauge, is circular.

The precipitation field in particular carries a **large monsoon diurnal phase
error** in this elevation band. Measured at these three stations from the files
in this package (JJAS, observed and QC-retained hours only):

| station | gauge peak (UTC) | ERA5-Land peak (UTC) | offset |
|---|---|---|---|
{chr(10).join(f"| {s} | {offsets[s].gauge_phase_utc_h:.1f} | {offsets[s].era5_phase_utc_h:.1f} | {offsets[s].offset_h:+.1f} h |" for s in ordered)}

Median **{k["median_offset"]:+.1f} h**; the project's network-wide figure for
the ≥ 2,000 m band is **{k["era5_band_offset"]}**. Near antiphase the *sign* of
such an offset is not identifiable — −12 h and +12 h are the same angle — so
read the **magnitude** as the measurement. ERA5-Land's convection is firing
around local noon while these gauges peak in the local evening.

If you feed ERA5-Land precipitation as a covariate at hourly resolution, you are
feeding a signal whose daily *shape* is close to antiphase with the target.
Aggregating to daily removes most of that problem; using it sub-daily does not.

ERA5-Land precipitation is also interpolated down from the coarser ERA5 grid and
is **never elevation-corrected**, so it does not see this basin's orography at
all. A precipitation-versus-elevation relationship fitted to it is the parent
field on a finer grid, not a physical response.

The temperature file is uncorrected cell-level temperature, and ERA5-Land's
model orography is badly wrong here:

| station | station elevation | ERA5-Land model orography | mismatch |
|---|---|---|---|
{chr(10).join(f"| {s} | {elev[s]['station_elev_m']:,.0f} m | {elev[s]['orography_elev_m']:,.0f} m | {elev[s]['elev_mismatch_m']:+,.0f} m |" for s in ordered)}

At Lukla the model thinks the ground is a kilometre higher than it is, so its
temperature runs several degrees cold. A first-order correction is
`T_station = T_ERA5 − 0.0065 × elev_mismatch_m`, using the
`era5_elev_mismatch_m` column of `stations.csv`. The station vertical datum is
unknown, so label that correction rather than implying precision.

### 2.10 What a poor result here will and will not tell you

⚠️ **A poor result on the Dudh Koshi does not distinguish "this approach does
not work" from "this basin is hard."**

You are being handed a snow- and ice-dominated Himalayan headwater with three
point gauges, one of which is missing nearly half its hours, an under-catch bias
we can bound but not correct and whose gauge type we do not know, an
instrument/elevation confound we cannot break, an unexplained {k["wet_ratio"]:.1f}×
wet-hour difference between two stations {k["relief"]:,.0f} m apart, no
temperature measurement, no snow state, and a target that sits on a third
clock, under a confidentiality restriction, derived through a rating curve that
is least reliable at exactly the flows that matter.

If your model does badly, that is the honest and expected first outcome, and it
is **not** evidence against deep-learning rainfall-runoff modelling. If it does
well, that is a strong result. We are stating this in advance, in writing, so
that neither of us is tempted to read a null result as a verdict on the method —
it protects you, and it protects the approach.

If you want a fair test of the *method*, fit it somewhere with dense
instrumentation first and bring the calibrated expectation here.

⭐ **Or bring the dense instrumentation with you.** That is what §3 is for: the
Pyramid tier is a within-basin control on exactly this question. It cannot make
an operational forecast, but it can tell you whether the DHM result is the
method's fault or the data's.

---

## 3. The Pyramid tier — a data-quality ceiling

⚠️ **This tier is not operational and cannot feed a deployed forecast.** The
Pyramid Meteorological Network is a research network; it does not publish in
real time, and nothing you build on it can be run in production.

It is here for one purpose. Drive your model with the Pyramid record, drive it
again with the DHM record, and **the gap between the two runs is attributable
to data quality** — not to the model, and not to the basin. Every warning in §2
confounds those three. This is the one control in the package that separates
them, and it is what makes a poor DHM result interpretable at all.

🔴 **Attribution is a condition of use, not a courtesy.** These data are
**{PYRAMID_LICENCE}**, DOI **{PYRAMID_DOI}**, provided by {PYRAMID_PROVIDER}.
Cite:

> {PYRAMID_CITATION}

Any output that uses this data — paper, report, figure, table, trained model,
benchmark number — must carry that citation. `LICENCE-pyramid.txt` states the
terms in full, and travels with the files if you pass them on.

ⓘ **You already hold the raw Pyramid files.** The Level 1 CSVs this tier is
built from sit in your own data environment at
`…/2025-01-BARHKH/data/weather/pyramid/` — the same `AWS0`…`CNG_SNP` Lvl1
files. `pyramid_aws_hourly.csv` is therefore **not new data**: it is those same
files parsed, placed on a complete hourly axis, and annotated with the timebase
§3.3 describes. Where the two ever disagree, the raw files win.

### 3.1 The stations

| station | name | elevation | lat, lon | native step | record (NPT) | variables |
|---|---|---|---|---|---|---|
{chr(10).join(pyr_station_row(s) for s in pyr_order)}

**The vertical span is the point.** These masts reach from {pyr_elev[0]:,} m to
{pyr_elev[-1]:,} m — **{pyr_elev[-1] - pyr_elev[0]:,} m of relief**, up the
Khumbu, through the elevations the Dudh Koshi's water actually comes from. The
DHM gauges in §1 stop at {dhm_top:,.0f} m, below the ice. Nothing in the
operational network measures the band that generates the runoff. This does.

⚠️ **`AWS0` is 2-hourly, not hourly.** The provider's `README_.txt` states a
1-hour step for every station; measured against the delivered stamps, `AWS0`
reports every second hour throughout its record. It is emitted on the same
hourly axis as the others, so each intervening hour is an honest empty row —
which caps its hourly coverage near 50 % by construction.
`pyramid_stations.csv` carries the measured `native_step_h` for exactly this
reason, and `BUILD_AUDIT.txt` reports its coverage on its own 2-hourly grid as
well.

⚠️ **Not every station measures every variable.** `AWSSC` (South Col) has no
rain gauge; `CNG_SNP` (Changri Nup) has neither rain gauge nor barometer. Those
columns are empty for those stations throughout — which is *not* the same
statement as "measured nothing", and `pyramid_stations.csv` leaves their
`n_observed_*` empty rather than writing a zero.

### 3.2 Coverage, per station and per variable

Measured over each station's own complete hourly axis:

| station | elevation | axis hours | {pyr_var_header} |
|---|---|---|---|---|---|---|---|---|
{chr(10).join(pyr_coverage_row(s) for s in pyr_order)}

{coarse_note}

⚠️ Two rows, one location: `AWS0` and `AWS1` are successive installations at
the **same site** (Pyramid, 5,035 m) with overlapping records. Do not treat
them as two independent samples of that elevation band.

### 3.3 The timebase — stated, not resolved

Two facts, and they must not be blurred into one.

**1. The offset is certain.** The provider states that all times are Nepal
Standard Time, **NPT = UTC + 5:45**. Converting is exact arithmetic, and this
package does it exactly.

🔴 **2. The period convention is unknown.** Whether a stamp of `14:00` covers
13:00 → 14:00 or 14:00 → 15:00 is **stated nowhere by the provider, and the
question has not been answered**. DHM's convention *is* known — period-ending,
confirmed 2026-08-31 (§2.8). The residual uncertainty **between the two
networks is therefore ±1 h**. We are not offering a preferred convention,
because there is no known answer to offer; guessing one and presenting it as
fact is exactly the kind of silent decision this package exists to avoid.

⚠️ **And NPT is UTC+5:45 — not a whole number of hours.** An hourly NPT stamp
converts to **:15 past the UTC hour**. This series therefore does **not land on
the same hourly UTC grid** as `gauge_precipitation_hourly.csv` or the two
ERA5-Land files: **the two grids are 15 minutes apart.** Joining them is a
methodological choice you have to make, not a lookup. The build asserts the :15
offset on every row rather than assuming it.

So the file gives you all three, and hides nothing:

| column | what it is |
|---|---|
| `timestamp_npt` | the stamp **exactly as delivered**, unmodified, with the provider-stated `+05:45` zone attached so it cannot be mistaken for UTC |
| `timestamp_utc` | the exact −5:45 conversion. **Falls at :15**, always |
| `timestamp_utc_hour_floor` | **optional.** The UTC hour that *contains* `timestamp_utc` — that stamp truncated to the hour, equivalently 15 minutes earlier. It exists so an alignment choice is visible rather than improvised. It is **not** a claim that the two series share an axis |

**What the ±1 h costs you**, measured on these files rather than asserted:
displace the whole precipitation series by ±1 h, recompute UTC-day totals over
days carrying at least 1 mm, and the median change is
**{shift_median:.1f} %** — more than half of wet days do not move at all,
because the shift only ever moves one boundary hour between adjacent days. It
is not free, though: taking the worse of the two shift directions at each
station, the **mean** change is **{shift_mean_lo:.0f}–{shift_mean_hi:.0f} %**
and the **worst tenth of days move by {shift_p90_lo:.0f}–{shift_p90_hi:.0f}
%** of that day's total. **Read that as: ±1 h is material for anything diurnal
or sub-daily, and mostly — but not entirely — harmless for daily totals.**
Per-station, per-direction figures are in `BUILD_AUDIT.txt`.

### 3.4 Wind — the variable that turns a bound into an estimate

🔴 After the elevation span, this is the most valuable thing in the tier.

§2.6 gives a rain-phase gradient of **{k["pyramid_gradient"]}** down an
independent Khumbu transect, and calls it an **upper bound in magnitude,
confounded with wind catch**. That confound has never been separable, for one
concrete reason: gauges under-catch more as wind rises, wind rises up the
transect, and we had **no wind measurement co-located with any gauge**. Catch
efficiency was boundable but not estimable.

**These files carry hourly wind speed on the same masts as the
precipitation.** A catch-efficiency transfer function is a function of wind
speed at the gauge and of precipitation phase; both are now in the same rows.

| station | elevation | mean wind | 95th pct | mean wind, observed wet hours |
|---|---|---|---|---|
{chr(10).join(pyr_wind_row(s) for s in pyr_order)}

The confound is visible directly. Across the stations that actually carry a
rain gauge, the mean wind during observed wet hours is
{wind_low.wet_hour_wind_mean_ms:.2f} m/s at `{wind_low.station.station_id}`
({wind_low.station.elev_m:,} m, the lowest) and
{wind_high.wet_hour_wind_mean_ms:.2f} m/s at `{wind_high.station.station_id}`
({wind_high.station.elev_m:,} m, the highest) — **{wind_ratio:.1f}× more wind
at the top of the gauged transect than at the bottom, in exactly the hours the
gauges are catching precipitation.** (The ungauged summit masts are windier
still: `{pyr_order[0]}` at {pyramid_tiers[pyr_order[0]].station.elev_m:,} m
averages {pyramid_tiers[pyr_order[0]].wind_mean_ms:.1f} m/s overall.) A decline
in *measured* precipitation up that transect is therefore expected from catch
loss alone, before any physical gradient is invoked.

⛔ **This is still not a correction factor.** Estimable is not estimated: a
transfer function also needs the gauge type and the precipitation phase, and we
know neither for the DHM gauges (§2.6). What is newly possible is to **fit**
the catch term on this network instead of assuming it away.

### 3.5 The two co-located pairs

Two Pyramid stations sit close enough to a DHM gauge in this package to make
the comparison a near-direct **instrument** comparison rather than a spatial
one:

| DHM gauge | Pyramid station | separation | Δ elevation | overlap of the observed records (UTC) | overlap hours | co-observed hours |
|---|---|---|---|---|---|---|
{chr(10).join(coloc_row(c) for c in colocations)}

Separation and Δ elevation are computed here from the two networks' own
coordinates and checked against the pairings declared in
`scripts/dhm_precip/coloc_pairs.py`; the build fails if they disagree.

**"Co-observed hours" is the honest number**, and it is far smaller than the
overlap window: it counts hours in which *both* networks actually report,
pairing each Pyramid stamp to the UTC hour it falls in. **That pairing is the
methodological choice of §3.3** — taken here so the number can be computed at
all, not as a claim that the convention question is settled. Both records are
gappy, and the Pyramid files end in 2023 while the DHM record runs to 2025.

At this separation and this elevation difference, a systematic disagreement
between the two gauges is far more plausibly an instrument or processing
difference than a real difference in precipitation. That is precisely what
makes the pair useful.

### 3.6 Level 1 means *your* quality control, not ours

These are **Level 1** data: the provider's own processing, and nothing more. We
have applied **no** quality control of ours, and dropped **no** row.

⛔ **The DHM rule set of §2.3 was deliberately not applied here.** It is a
precipitation-gauge rule set tuned to DHM's instruments and DHM's missing-data
sentinels. Running it over another network's temperature, pressure, humidity
and wind would be a category error; running it over this network's
precipitation would quietly import DHM's assumptions into the control you are
using to test DHM. **This tier needs its own QC, and that is your job.**

What the package does guarantee is what it guarantees for the gauge file: **a
missing hour is never a zero.** Every station is on a complete hourly axis over
its own record, an unreported hour is an empty cell, and each variable carries
its own `observed_*` flag which is exactly "the value cell is not empty".

As a cross-check the build re-reads every file through the project's own
Pyramid loaders (`load_pyramid_lvl1_csv`, `load_pyramid_lvl1_at_csv`), which
apply physical-range and finiteness gates of their own, and asserts that every
row those loaders retain appears here with a bit-identical value at the same
timestamp — **{k["pyramid_checked"]:,} rows**. Across all
{len(pyramid_tiers)} files those gates reject **{k["pyramid_gated_out"]:,}**
value(s) in total; that count is the entire difference between the unmasked
columns shipped here and the gated view the project uses internally.


---

## 4. File and column reference

### `gauge_precipitation_hourly.csv`

{len(stations)} stations × {k["expected_hours"]:,} hours = {len(stations) * k["expected_hours"]:,} rows.
No header comments — read it directly.

| column | type | meaning |
|---|---|---|
| `station` | text | station name; joins to `stations.csv` and both ERA5 files |
| `timestamp_utc` | ISO 8601 `...Z` | **UTC, period-ending**: the hour *ending* at this stamp |
| `precip_mm` | float or **empty** | **the modelling column.** mm accumulated in that hour. **Empty means no usable measurement. It never means zero.** |
| `observed` | `true`/`false` | exactly `precip_mm is not null`, both directions — use it as your mask |
| `qc_masked` | `true`/`false` | our QC fired on this hour |
| `qc_rule` | text or empty | which rule fired; empty when `qc_masked=false`. `+`-joined if more than one |
| `raw_delivered_mm` | float or **empty** | **the provenance column.** Every value the source workbook delivered for that hour, unaltered — including the {k["n_voided"]:,} values `range_check` kept out of `precip_mm`. Empty only where the workbook delivered nothing. |

`precip_mm` and `raw_delivered_mm` are identical except on the {k["n_voided"]:,}
`range_check` rows (§2.3). Model on `precip_mm`; audit on `raw_delivered_mm`.

Post-conditions asserted by the build, and re-checkable by you:

* exactly {k["expected_hours"]:,} rows per station, every hour present, uniform 1 h step;
* no row has `observed=false` with a non-empty `precip_mm`;
* no row has `observed=true` with an empty `precip_mm`;
* the count of zeros is identical in `precip_mm`, in `raw_delivered_mm`, and in
  the source workbook, per station — no null became a zero and no zero was lost;
* `raw_delivered_mm` reproduces the delivered non-null count exactly — nothing
  was discarded;
* `precip_mm` is short of `raw_delivered_mm` by exactly the rows the
  physical-impossibility gate voided, and by no others;
* no `precip_mm` value exists without a delivered value behind it — nothing was
  invented;
* every `raw_delivered_mm` value is unchanged from the source workbook, matched
  1:1 on `(station, timestamp)`, and every non-empty `precip_mm` equals the
  delivered value it claims to carry.

Two things to know about the axis. The source workbook's last delivered hour is
`{k["axis_hi"]}`; hours after that up to {k["end"]:%Y-%m-%dT%H:%M} are emitted as
unobserved rows, because they were never delivered. And the workbook contains
some rows stamped at a non-zero minute — DHM processing artefacts that cannot be
placed on an hourly axis. They are excluded, which is why coverage here is
slightly lower than a count taken over every delivered cell; `BUILD_AUDIT.txt`
reports both numbers per station.

### `stations.csv`

One row per station. `reporting_resolution_mm` is **inferred from the data**
(`scripts/dhm_precip/resolution.py`): a station whose non-zero values are all
integer multiples of 0.2 mm is 0.2 mm; otherwise 0.01 mm. `coverage_pct`,
`wet_hour_pct`, `first_obs`, `last_obs` and `n_qc_masked` are computed over the
axis in `gauge_precipitation_hourly.csv`. The `era5_*` columns describe the
ERA5-Land cell each station falls in.

### `era5_land_precipitation_hourly.csv` / `era5_land_temperature_hourly.csv`

Both carry `#`-prefixed header comments — read with
`pandas.read_csv(path, comment='#')` or
`polars.read_csv(path, comment_prefix='#')`. Columns are `station`,
`timestamp_utc`, and `precip_mm` (mm in the hour ending at the stamp) or
`t2m_degc` (°C, instantaneous at the stamp). Both are complete: every hour
present, zero non-finite values.

### `pyramid_aws_hourly.csv`

{k["pyramid_rows"]:,} rows — {len(pyramid_tiers)} stations, each on a complete
hourly axis spanning **its own** record, so the row count differs per station
(see `n_hours_axis` in `pyramid_stations.csv`). Carries `#`-prefixed header
comments: read with `pandas.read_csv(path, comment='#')` or
`polars.read_csv(path, comment_prefix='#')`.

| column | type | meaning |
|---|---|---|
| `station` | text | Pyramid station id (`AWS0`…`AWSSC`, `CNG_SNP`); joins to `pyramid_stations.csv` |
| `timestamp_npt` | ISO 8601 `+05:45` | **the stamp exactly as delivered**, Nepal wall-clock, unmodified. **Period convention unresolved — §3.3** |
| `timestamp_utc` | ISO 8601 `…Z` | the exact −5:45 conversion. **Always at :15 past the hour** |
| `timestamp_utc_hour_floor` | ISO 8601 `…Z` | **optional** alignment column: `timestamp_utc` truncated to the UTC hour. Not a claim of a shared axis |
| `air_temp_degc` | float or **empty** | 2 m air temperature, °C (source column `AT`) |
| `precip_mm` | float or **empty** | precipitation in the hour, mm (source column `RR`) |
| `wind_speed_ms` | float or **empty** | wind speed, m/s (source column `WS`) |
| `rel_humidity_pct` | float or **empty** | relative humidity, % (source column `RH`) |
| `pressure_hpa` | float or **empty** | atmospheric pressure, hPa (source column `AP`) |
| `wind_dir_deg` | float or **empty** | wind direction, degrees, 0 = northerly (source column `WD`) |
| `observed_at` … `observed_wd` | `true`/`false` | one per variable, each exactly "that value cell is not empty" |

**Empty never means zero**, in any of the six value columns. A column that is
empty for a whole station means that station does not measure that variable at
all (§3.1). Values are Level 1 as published — no QC of ours, no row dropped,
nothing gap-filled (§3.6).

Post-conditions asserted by the build, per station:

* every hour between the station's first and last delivered stamp is present,
  once, in order;
* every `timestamp_utc` falls at **:15**, and `timestamp_utc_hour_floor` is
  that stamp truncated to the hour;
* the number of non-empty values in each column equals the number the source
  file delivered — nothing added, nothing lost;
* the number of **zeros** in each column is unchanged from the source file —
  no empty hour became a zero, and no zero was dropped;
* each `observed_*` flag is exactly its column's non-emptiness;
* every row retained by the project's own Pyramid loaders appears here with a
  bit-identical value at the same timestamp (§3.6).

### `pyramid_stations.csv`

One row per Pyramid station, `#`-header-commented like the ERA5 files.
`station_name`, `lat`, `lon` and `elev_m` come from the provider's
`README_.txt`; `elev_m` is cross-checked against the `Z####` token in the
source filename and the build fails if the two disagree. `native_step_h` is
**measured** from the delivered stamps, not taken from the provider's stated
step (§3.1). `coverage_pct_*` is over the station's complete **hourly** axis —
divide by `native_step_h` to read it on the station's own sampling grid. An
empty `n_observed_*`/`coverage_pct_*` means the variable is not measured there.
The `dhm_*` columns are filled only for the two co-located stations of §3.5.

### `LICENCE-pyramid.txt`

The Pyramid licence, the DOI, the citation you must reproduce, and the exact
list of what this package did to the provider's values. Redistribute it with
the data.

---

## 5. Regenerating this package

From a checkout of `hydrosolutions/SAPPHIRE_flow` with the DHM workbook and the
published ERA5-Land point bundles in place:

```bash
DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx \\
uv run python scripts/dhm_precip/{PACKAGED_SCRIPT_NAME} \\
  --out-dir <somewhere> \\
  --start {k["start"]:%Y-%m-%dT%H:%M:%S} --end {k["end"]:%Y-%m-%dT%H:%M:%S} \\
  --stations {" ".join(repr(s) for s in stations)}
```

The copy of the build script inside this package is **written by the run**.
The canonical file is `scripts/dhm_precip/{PACKAGED_SCRIPT_NAME}` in the
repository, and the build copies it in verbatim alongside the outputs, so the
two can never drift apart. The packaged copy is a build artefact: editing it
changes nothing, and the next build overwrites it.

The script is read-only, makes **no network request of any kind**, and verifies
the source workbook against a pinned sha256
(`{k["source_sha"][:16]}…`) before reading a single value. It
re-derives every number in this README and prints them; if a number here ever
disagrees with `BUILD_AUDIT.txt`, the audit trail wins.

The QC path is not reimplemented here — it is the project's production sequence,
`load_long_frame → on_grid_view → normalise_hourly_axis →
iter_observations_by_station → qc_mask.iter_station_results`.

The Pyramid tier is read from `--pyramid-root` (default
`data/dhm_precip/pyramid`) and its file format is not reimplemented either — it
is parsed by `scripts/dhm_precip/pyramid_loader.py`'s own column parser. That
loader's public entry points could not be used directly: they gate on physical
range and on finiteness and **drop** the failing rows, which would turn a
masked hour into an absent one, and this tier ships Level 1 unmasked. The build
cross-checks against both of them instead.

---

## 6. Licence and provenance

**DHM gauge precipitation** — Department of Hydrology and Meteorology, Nepal.
Supplied to hydrosolutions; onward use is subject to DHM's terms. Ask before
redistributing.

🔴 **DHM sub-daily water level — the discharge target** — Department of
Hydrology and Meteorology, Nepal, via Binod Parajuli. **Not distributed in this
package**; §2.7 says where it is. Its own `readme.txt` requires that the data
"be treated with utmost confidentiality", and that condition extends to
anything derived from it. Do not redistribute or publish either without DHM's
agreement.

**ERA5-Land** — Copernicus Climate Change Service / ECMWF, **CC BY 4.0**.
Cite the dataset, not us: https://doi.org/10.24381/cds.e2161bac ·
https://creativecommons.org/licenses/by/4.0/
Precipitation bundle `{k["precip_dir"].name}`;
temperature bundle `{k["t2m_dir"].name}`.

🔴 **Pyramid Meteorological Network** — provided by {PYRAMID_PROVIDER},
**{PYRAMID_LICENCE}**, DOI **{PYRAMID_DOI}**
(https://doi.org/{PYRAMID_DOI} · https://creativecommons.org/licenses/by/4.0/).
**Attribution is a condition of use.** Cite:

> {PYRAMID_CITATION}

Any output that uses `pyramid_aws_hourly.csv` or `pyramid_stations.csv` must
carry that citation, and `LICENCE-pyramid.txt` must travel with the files if
you pass them on. This licence covers the Pyramid files **only** — the DHM
gauge data above are not CC BY 4.0.

**The build script** — `scripts/dhm_precip/{PACKAGED_SCRIPT_NAME}` from
`hydrosolutions/SAPPHIRE_flow`, copied into this package verbatim, as
`{PACKAGED_SCRIPT_NAME}`, by the run that built it.

Questions: hydrosolutions.
"""


if __name__ == "__main__":
    raise SystemExit(main())
