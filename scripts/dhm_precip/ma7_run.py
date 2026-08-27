#!/usr/bin/env python3
# ruff: noqa: T201
"""Plan 193 (M-A7) task T4 — report and Exit.

Composes T1 (`ma7_profiles`), T2 (`ma7_intensity`) and T3 (`ma7_transfer`)
into one report covering the Exit's full inventory: per-station AND
per-band diurnal profiles with per-hour exposure (D2), per-station AND
per-band wet-hour intensity distributions with body/tail bootstrap
uncertainty (D5/D9), the elevation-band and reporting-resolution
transferability cuts side by side with the D6 attribution explicitly
declined, and Olangchunggola's open D7 status.

`run_ma7_report()` is the tested, loader-agnostic core (CLAUDE.md
dependency injection: an `Ma7Inputs` bundle is passed in, never a bare call
to the real pipeline inside business logic) — exercised against synthetic
`MaskedGaugeSeries` fixtures with no real workbook required, following
`ma7_profiles`/`ma7_intensity`/`ma7_transfer`'s own test convention. `main()`
wires the real production DHM masked population (`ma6_pairs.
load_gauge_masked_population`, D10 — never a second implementation of the
masked series), the D12 coordinate table for elevation, and a fresh
UNMASKED `on_grid` read for D6's reporting-resolution classification
(Checked 2026-08-27: resolution-group membership is invariant under the
mask, but the classification is defined over `on_grid`, never the masked
series — `resolution.infer_reporting_resolution`'s own contract).

⛔ **PINNED (P1) — the bootstrap seed is `REPORT_SEED`, a single named
constant, never a CLI argument.** Every bootstrap in T1/T2 draws from ONE
`random.Random(REPORT_SEED)` threaded through the whole computation in a
fixed (sorted-station, fixed-season, fixed-band) order, so two runs of
`main()` produce a byte-identical report (modulo the `generated_at`
timestamp) by construction.

⛔ **The runner writes ONLY under `--out`** (Plan 184 T6 P5, reapplied
here) — it never touches `docs/design/dhm-precipitation-milestones.md` or
any other tracked file. Folding results into that document is a human step.

Usage:
    uv run python scripts/dhm_precip/ma7_run.py --out <dir>

Environment:
    DHM_PRECIP_XLSX     path to the source workbook (required)
    DHM_PRECIP_COORDS   path to the D12 coordinate table (optional)
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import structlog  # noqa: E402

from scripts.dhm_precip.domain_types import Station  # noqa: E402
from scripts.dhm_precip.loader import (  # noqa: E402
    PRODUCTION_SOURCE_SHA256,
    DhmPrecipLoaderError,
    load_long_frame,
    load_station_coordinates,
    resolve_coords_path,
    resolve_source_path,
)
from scripts.dhm_precip.ma6_estimands import (  # noqa: E402
    ElevationBand,
    NonFiniteElevationError,
    assign_elevation_band,
)
from scripts.dhm_precip.ma6_pairs import (  # noqa: E402
    GaugeMaskedPopulation,
    MaskedGaugeSeries,
    load_gauge_masked_population,
)
from scripts.dhm_precip.ma7_intensity import (  # noqa: E402
    BandIntensityDistribution,
    QuantileBootstrap,
    StationIntensityDistribution,
    bootstrap_band_quantile,
    bootstrap_station_quantile,
)
from scripts.dhm_precip.ma7_profiles import (  # noqa: E402
    OLANGCHUNGGOLA,
    BandDiurnalProfile,
    EmptyBootstrapResultError,
    HourlyMean,
    NoSeasonYearsError,
    PeakHourBootstrap,
    StationDiurnalProfile,
    bootstrap_band_peak_hour,
    bootstrap_station_peak_hour,
)
from scripts.dhm_precip.ma7_transfer import (  # noqa: E402
    ResolutionGroupLabel,
    TailPredictionError,
    TransferabilityComparison,
    build_resolution_groups,
    compare_transferability,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS, DhmPrecipParams  # noqa: E402
from scripts.dhm_precip.resolution import infer_reporting_resolution  # noqa: E402
from scripts.dhm_precip.seasons import Season  # noqa: E402
from scripts.dhm_precip.views import on_grid_view  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

log = structlog.get_logger(__name__)

REPORT_SEED = 193
"""P1 PINNED — the ONE named seed constant, plan number, never
caller-suppliable. A report built on an unseeded (or re-seedable) bootstrap
is non-reproducible by construction, and regenerability is an Exit
condition (M-A6's report failed this for a subtler reason and cost a
release; this one would fail trivially)."""

_SEASONS: tuple[Season, ...] = (Season.MAM, Season.JJAS, Season.ON, Season.DJF)
"""D8 as corrected — all four, in this fixed order, every station, no
omission; also the FIXED CONSUMPTION ORDER the seeded RNG is threaded
through (P1's byte-identical requirement)."""

_BANDS: tuple[ElevationBand, ...] = (
    ElevationBand.BELOW_700M,
    ElevationBand.B700_2000M,
    ElevationBand.B2000_3000M,
    ElevationBand.ABOVE_3000M,
)

_HEADLINE_SEASON = Season.JJAS
"""Mechanic this plan's text left open (flagged in the T4 report back):
T3's transferability comparison is computed for JJAS only, not all four
seasons. P7 locks exactly 'the four band-level leave-one-out prediction
errors' and 'the two resolution-group errors' — one number per band/group,
not one per band/group per season — and D8 names JJAS as the season that
'carries the headline'. Running T3 over all four seasons would produce 4x
as many numbers and contradict P7's own count."""


class MissingStationMetadataError(ValueError):
    """A station in the masked population has no known elevation or
    reporting-resolution group — checked eagerly in `Ma7Inputs.
    __post_init__` rather than surfacing later as an opaque
    `BandMembershipError`/`ResolutionGroupMembershipError` deep inside T1-T3."""


@dataclass(frozen=True, kw_only=True, slots=True)
class Ma7Inputs:
    """T4's DI seam (CLAUDE.md dependency injection) — `run_ma7_report`'s
    tested core takes this bundle and never touches disk itself, mirroring
    `coloc_run.DhmRetainedProvider`/`ma6_run.Ma6Inputs`."""

    masked: GaugeMaskedPopulation
    station_elev_m: Mapping[Station, float]
    station_resolution_group: Mapping[Station, ResolutionGroupLabel]
    provenance_lines: tuple[str, ...]
    params: DhmPrecipParams = DEFAULT_PARAMS

    def __post_init__(self) -> None:
        stations = frozenset(self.masked.by_station)
        missing_elev = stations - frozenset(self.station_elev_m)
        if missing_elev:
            raise MissingStationMetadataError(
                f"stations missing a known elevation: {sorted(missing_elev)}"
            )
        missing_group = stations - frozenset(self.station_resolution_group)
        if missing_group:
            raise MissingStationMetadataError(
                "stations missing a known reporting-resolution group: "
                f"{sorted(missing_group)}"
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class BootstrapRefusal:
    """P5 — a bootstrap that legitimately could not run (zero season-years,
    or every resample drew zero observed hours). Rendered as an explicit
    absence with `reason`, never a blank, a zero, or a dropped row."""

    reason: str


_T = TypeVar("_T")


def _bootstrap_outcome(compute: Callable[[], _T]) -> _T | BootstrapRefusal:
    try:
        return compute()
    except (NoSeasonYearsError, EmptyBootstrapResultError) as exc:
        return BootstrapRefusal(reason=str(exc))


PeakHourOutcome = PeakHourBootstrap | BootstrapRefusal
QuantileOutcome = QuantileBootstrap | BootstrapRefusal


@dataclass(frozen=True, kw_only=True, slots=True)
class StationSeasonProfile:
    profile: StationDiurnalProfile
    bootstrap: PeakHourOutcome


@dataclass(frozen=True, kw_only=True, slots=True)
class BandSeasonProfile:
    profile: BandDiurnalProfile
    bootstrap: PeakHourOutcome


@dataclass(frozen=True, kw_only=True, slots=True)
class StationSeasonIntensity:
    distribution: StationIntensityDistribution
    q50_bootstrap: QuantileOutcome
    q99_bootstrap: QuantileOutcome


@dataclass(frozen=True, kw_only=True, slots=True)
class BandSeasonIntensity:
    distribution: BandIntensityDistribution
    q50_bootstrap: QuantileOutcome
    q99_bootstrap: QuantileOutcome


@dataclass(frozen=True, kw_only=True, slots=True)
class Ma7Report:
    seed: int
    generated_at: datetime
    params: DhmPrecipParams
    provenance_lines: tuple[str, ...]
    stations: tuple[Station, ...]
    station_elev_m: Mapping[Station, float]
    station_resolution_group: Mapping[Station, ResolutionGroupLabel]
    bands_by_station: Mapping[Station, ElevationBand]
    station_profiles: Mapping[tuple[Station, Season], StationSeasonProfile]
    band_profiles: Mapping[tuple[ElevationBand, Season], BandSeasonProfile]
    station_intensities: Mapping[tuple[Station, Season], StationSeasonIntensity]
    band_intensities: Mapping[tuple[ElevationBand, Season], BandSeasonIntensity]
    transferability: TransferabilityComparison


def _station_season_profile(
    series: MaskedGaugeSeries,
    season: Season,
    params: DhmPrecipParams,
    rng: random.Random,
) -> StationSeasonProfile:
    profile = StationDiurnalProfile(series=series, season=season, params=params)
    bootstrap = _bootstrap_outcome(
        lambda: bootstrap_station_peak_hour(
            profile,
            rng=rng,
            n_resamples=params.ma7_bootstrap_resamples,
            min_season_years_for_adequacy=params.coloc_min_season_years_for_adequacy,
        )
    )
    return StationSeasonProfile(profile=profile, bootstrap=bootstrap)


def _band_season_profile(
    band: ElevationBand,
    members: tuple[StationDiurnalProfile, ...],
    station_elev_m: Mapping[Station, float],
    params: DhmPrecipParams,
    rng: random.Random,
) -> BandSeasonProfile:
    profile = BandDiurnalProfile(
        band=band, members=members, station_elev_m=station_elev_m
    )
    bootstrap = _bootstrap_outcome(
        lambda: bootstrap_band_peak_hour(
            profile,
            rng=rng,
            n_resamples=params.ma7_bootstrap_resamples,
            min_season_years_for_adequacy=params.coloc_min_season_years_for_adequacy,
        )
    )
    return BandSeasonProfile(profile=profile, bootstrap=bootstrap)


def _station_season_intensity(
    series: MaskedGaugeSeries,
    season: Season,
    params: DhmPrecipParams,
    rng: random.Random,
) -> StationSeasonIntensity:
    distribution = StationIntensityDistribution(
        series=series, season=season, params=params
    )
    q50 = _bootstrap_outcome(
        lambda: bootstrap_station_quantile(
            distribution,
            quantile=0.5,
            rng=rng,
            n_resamples=params.ma7_bootstrap_resamples,
            min_season_years_for_adequacy=params.coloc_min_season_years_for_adequacy,
        )
    )
    q99 = _bootstrap_outcome(
        lambda: bootstrap_station_quantile(
            distribution,
            quantile=0.99,
            rng=rng,
            n_resamples=params.ma7_bootstrap_resamples,
            min_season_years_for_adequacy=params.coloc_min_season_years_for_adequacy,
        )
    )
    return StationSeasonIntensity(
        distribution=distribution, q50_bootstrap=q50, q99_bootstrap=q99
    )


def _band_season_intensity(
    band: ElevationBand,
    members: tuple[StationIntensityDistribution, ...],
    station_elev_m: Mapping[Station, float],
    params: DhmPrecipParams,
    rng: random.Random,
) -> BandSeasonIntensity:
    distribution = BandIntensityDistribution(
        band=band, members=members, station_elev_m=station_elev_m
    )
    q50 = _bootstrap_outcome(
        lambda: bootstrap_band_quantile(
            distribution,
            quantile=0.5,
            rng=rng,
            n_resamples=params.ma7_bootstrap_resamples,
            min_season_years_for_adequacy=params.coloc_min_season_years_for_adequacy,
        )
    )
    q99 = _bootstrap_outcome(
        lambda: bootstrap_band_quantile(
            distribution,
            quantile=0.99,
            rng=rng,
            n_resamples=params.ma7_bootstrap_resamples,
            min_season_years_for_adequacy=params.coloc_min_season_years_for_adequacy,
        )
    )
    return BandSeasonIntensity(
        distribution=distribution, q50_bootstrap=q50, q99_bootstrap=q99
    )


def run_ma7_report(
    *,
    inputs: Ma7Inputs,
    rng: random.Random,
    seed: int,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Ma7Report:
    """The M-A7 Exit deliverable, composed. Iterates stations in SORTED
    order and seasons/bands in the FIXED module-level order above, so a
    caller supplying a fresh `random.Random(seed)` gets a byte-identical
    result every time (P1) — the RNG is threaded through every bootstrap
    call in this one deterministic traversal, never re-seeded mid-way."""
    params = inputs.params
    stations = tuple(sorted(inputs.masked.by_station))
    bands_by_station = {
        s: assign_elevation_band(inputs.station_elev_m[s]) for s in stations
    }

    station_profiles: dict[tuple[Station, Season], StationSeasonProfile] = {}
    station_intensities: dict[tuple[Station, Season], StationSeasonIntensity] = {}
    band_profiles: dict[tuple[ElevationBand, Season], BandSeasonProfile] = {}
    band_intensities: dict[tuple[ElevationBand, Season], BandSeasonIntensity] = {}
    jjas_station_intensity: dict[Station, StationIntensityDistribution] = {}

    for season in _SEASONS:
        season_dprofiles: dict[Station, StationDiurnalProfile] = {}
        season_idists: dict[Station, StationIntensityDistribution] = {}
        for station in stations:
            series = inputs.masked.by_station[station]
            sp = _station_season_profile(series, season, params, rng)
            station_profiles[station, season] = sp
            season_dprofiles[station] = sp.profile

            si = _station_season_intensity(series, season, params, rng)
            station_intensities[station, season] = si
            season_idists[station] = si.distribution
            if season is _HEADLINE_SEASON:
                jjas_station_intensity[station] = si.distribution

        for band in _BANDS:
            band_stations = [s for s in stations if bands_by_station[s] is band]
            if not band_stations:
                continue
            profile_members = tuple(season_dprofiles[s] for s in band_stations)
            band_profiles[band, season] = _band_season_profile(
                band, profile_members, inputs.station_elev_m, params, rng
            )
            intensity_members = tuple(season_idists[s] for s in band_stations)
            band_intensities[band, season] = _band_season_intensity(
                band, intensity_members, inputs.station_elev_m, params, rng
            )

    by_band = tuple(
        band_intensities[band, _HEADLINE_SEASON].distribution
        for band in _BANDS
        if (band, _HEADLINE_SEASON) in band_intensities
    )
    resolution_groups = build_resolution_groups(
        tuple(jjas_station_intensity[s] for s in stations),
        inputs.station_resolution_group,
    )
    transferability = compare_transferability(
        by_band=by_band, by_resolution_group=resolution_groups
    )

    return Ma7Report(
        seed=seed,
        generated_at=clock(),
        params=params,
        provenance_lines=inputs.provenance_lines,
        stations=stations,
        station_elev_m=inputs.station_elev_m,
        station_resolution_group=inputs.station_resolution_group,
        bands_by_station=bands_by_station,
        station_profiles=station_profiles,
        band_profiles=band_profiles,
        station_intensities=station_intensities,
        band_intensities=band_intensities,
        transferability=transferability,
    )


# --------------------------------------------------------------------------
# Rendering — small, generic helpers reused across every table (P2's fixed
# inventory), never a bespoke renderer per table.
# --------------------------------------------------------------------------


def _md_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---:"] * len(headers)) + "|",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    lines.append("")
    return lines


def _fmt_mm(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "n/a (n=0)"


def _fmt_peak_hour(hour: int | None) -> str:
    return f"{hour:02d} UTC" if hour is not None else "n/a (degenerate season)"


def _fmt_bootstrap_n_adequate(
    bootstrap: PeakHourOutcome | QuantileOutcome,
) -> tuple[str, str]:
    if isinstance(bootstrap, BootstrapRefusal):
        return "—", f"refused — {bootstrap.reason}"
    adequate = "yes" if bootstrap.adequate_sample else "NO (< 5 season-years)"
    return str(bootstrap.n_season_years), adequate


def _fmt_spread(bootstrap: PeakHourOutcome) -> str:
    """P3 — NEVER a confidence interval: `circular_range_hours` is the
    smallest arc containing every resampled value, so it is rendered as an
    arc with its resample count beside it, not a CI."""
    if isinstance(bootstrap, BootstrapRefusal):
        return f"refused — {bootstrap.reason}"
    return (
        f"resampled peak hours spanned a {bootstrap.spread_hours:.1f} h arc "
        f"over {len(bootstrap.resampled_peak_hours)} resamples"
    )


def _fmt_quantile_ci(bootstrap: QuantileOutcome) -> str:
    """Unlike `_fmt_spread`, this IS a 95% CI (D9: percentile method, linear
    — the pin holds for T2's quantile bootstraps, corrected only for T1's
    circular peak hour)."""
    if isinstance(bootstrap, BootstrapRefusal):
        return f"refused — {bootstrap.reason}"
    return (
        f"{_fmt_mm(bootstrap.point_estimate_mm_per_h)} "
        f"[{bootstrap.ci_low_mm_per_h:.3f}, {bootstrap.ci_high_mm_per_h:.3f}] 95% CI"
    )


def _hourly_rows(label: str, hourly: tuple[HourlyMean, ...]) -> list[tuple[str, ...]]:
    return [
        (label, f"{h.hour:02d}", _fmt_mm(h.mean_value_mm), str(h.n_retained))
        for h in hourly
    ]


def _station_profile_table(report: Ma7Report, season: Season) -> list[str]:
    summary_rows = [
        (
            str(station),
            str(report.bands_by_station[station]),
            _fmt_peak_hour(report.station_profiles[station, season].profile.peak_hour),
            str(report.station_profiles[station, season].profile.n_season_retained),
            *_fmt_bootstrap_n_adequate(
                report.station_profiles[station, season].bootstrap
            ),
            _fmt_spread(report.station_profiles[station, season].bootstrap),
        )
        for station in report.stations
    ]
    lines = [f"#### {season.value} — per-station summary", ""]
    lines += _md_table(
        (
            "station",
            "band",
            "peak hour",
            "n retained (season)",
            "n season-years",
            "adequate (>= 5 season-years)",
            "bootstrap spread",
        ),
        summary_rows,
    )
    lines += [f"#### {season.value} — per-station hourly profile (D2 exposure)", ""]
    detail_rows: list[tuple[str, ...]] = []
    for station in report.stations:
        detail_rows += _hourly_rows(
            str(station), report.station_profiles[station, season].profile.hourly
        )
    lines += _md_table(
        ("station", "hour (UTC)", "mean mm/h", "n retained"), detail_rows
    )
    return lines


def _band_profile_table(report: Ma7Report, season: Season) -> list[str]:
    bands_present = [b for b in _BANDS if (b, season) in report.band_profiles]
    summary_rows: list[tuple[str, ...]] = []
    for band in bands_present:
        entry = report.band_profiles[band, season]
        summary_rows.append(
            (
                str(band),
                str(entry.profile.station_count),
                _fmt_peak_hour(entry.profile.station_equal_peak_hour),
                _fmt_peak_hour(entry.profile.pooled_peak_hour) + " (sensitivity, D5a)",
                *_fmt_bootstrap_n_adequate(entry.bootstrap),
                _fmt_spread(entry.bootstrap),
            )
        )
    lines = [f"#### {season.value} — per-band summary", ""]
    lines += _md_table(
        (
            "band",
            "station count",
            "peak hour (station-equal)",
            "peak hour (pooled)",
            "n season-years (union, D9)",
            "adequate (>= 5 season-years)",
            "bootstrap spread",
        ),
        summary_rows,
    )
    lines += [
        f"#### {season.value} — per-band hourly profile, station-equal (D2 exposure)",
        "",
    ]
    detail_rows: list[tuple[str, ...]] = []
    for band in bands_present:
        detail_rows += _hourly_rows(
            str(band), report.band_profiles[band, season].profile.station_equal_hourly
        )
    lines += _md_table(
        ("band", "hour (UTC)", "mean mm/h", "n retained (sum)"), detail_rows
    )
    return lines


def _station_intensity_table(report: Ma7Report, season: Season) -> list[str]:
    rows: list[tuple[str, ...]] = []
    for station in report.stations:
        entry = report.station_intensities[station, season]
        d = entry.distribution
        rows.append(
            (
                str(station),
                str(report.bands_by_station[station]),
                str(d.n_wet_retained),
                str(d.n_total_retained),
                _fmt_mm(d.wet_hour_fraction),
                _fmt_quantile_ci(entry.q50_bootstrap),
                _fmt_quantile_ci(entry.q99_bootstrap),
                _fmt_mm(d.total_retained_mass_mm) + " (unthresholded, D4)",
            )
        )
    lines = [f"#### {season.value} — per-station intensity distribution", ""]
    lines += _md_table(
        (
            "station",
            "band",
            "n wet retained",
            "n total retained",
            "wet fraction",
            "q50 (body)",
            "q99 (tail)",
            "total retained mass mm",
        ),
        rows,
    )
    return lines


def _band_intensity_table(report: Ma7Report, season: Season) -> list[str]:
    rows: list[tuple[str, ...]] = []
    for band in _BANDS:
        key = (band, season)
        if key not in report.band_intensities:
            continue
        entry = report.band_intensities[key]
        d = entry.distribution
        rows.append(
            (
                str(band),
                str(d.station_count),
                str(d.n_wet_retained),
                str(d.n_total_retained),
                _fmt_quantile_ci(entry.q50_bootstrap) + " (station-equal)",
                _fmt_mm(d.pooled_q50_mm_per_h) + " (pooled, sensitivity)",
                _fmt_quantile_ci(entry.q99_bootstrap) + " (station-equal)",
                _fmt_mm(d.pooled_q99_mm_per_h) + " (pooled, sensitivity)",
            )
        )
    lines = [f"#### {season.value} — per-band intensity distribution", ""]
    lines += _md_table(
        (
            "band",
            "station count",
            "n wet retained",
            "n total retained",
            "q50 (body)",
            "q50 pooled",
            "q99 (tail)",
            "q99 pooled",
        ),
        rows,
    )
    return lines


def _prediction_error_row(label: str, error: TailPredictionError) -> tuple[str, ...]:
    return (
        label,
        str(error.station_count),
        str(error.n_stations_used),
        f"{error.median_abs_error:.4f}",
        f"{error.min_error:.4f}",
        f"{error.max_error:.4f}",
        f"{error.within_25pct_fraction:.4f}",
    )


def _transferability_section(report: Ma7Report) -> list[str]:
    t = report.transferability
    headers = (
        "grouping",
        "station count",
        "n stations used",
        "median abs error",
        "min error",
        "max error",
        "within 25% fraction",
    )
    band_rows = [
        _prediction_error_row(str(band), t.by_elevation_band[band])
        for band in _BANDS
        if band in t.by_elevation_band
    ]
    group_rows = [
        _prediction_error_row(f"resolution group {g}", t.by_resolution_group[g])
        for g in sorted(t.by_resolution_group)
    ]
    lines = [
        "## (e) Transferability — elevation cut and resolution-group cut, side by side",
        "",
        f"Headline season: {_HEADLINE_SEASON.value} (D8 — JJAS carries the "
        "headline; the other three seasons are reported in (a)-(d) but the "
        "leave-one-out prediction error is not re-run per season, matching "
        "P7's locked, small headline count).",
        "",
        "### Elevation-band cut",
        "",
    ]
    lines += _md_table(headers, band_rows)
    lines += ["### Reporting-resolution-group cut", ""]
    lines += _md_table(headers, group_rows)
    lines += [f"**{t.declined_attribution}**", ""]
    return lines


def _olangchunggola_section(report: Ma7Report) -> list[str]:
    key = (OLANGCHUNGGOLA, _HEADLINE_SEASON)
    lines = [
        "## (f) Olangchunggola's 03 UTC status — reported, not adjudicated (D7)",
        "",
    ]
    if key not in report.station_profiles:
        lines += ["Olangchunggola is not present in this masked population.", ""]
        return lines
    entry = report.station_profiles[key]
    hour_3 = entry.profile.hourly[3]
    lines += [
        entry.profile.open_anomaly_note or "",
        "",
        f"- {_HEADLINE_SEASON.value} point-estimate peak hour: "
        f"{_fmt_peak_hour(entry.profile.peak_hour)}",
        f"- hour 03 UTC: mean {_fmt_mm(hour_3.mean_value_mm)}, "
        f"n retained {hour_3.n_retained}",
        f"- {_fmt_spread(entry.bootstrap)}",
        "",
    ]
    return lines


def _inputs_and_seed_section(report: Ma7Report) -> list[str]:
    band_counts = {
        band: sum(1 for s in report.stations if report.bands_by_station[s] is band)
        for band in _BANDS
    }
    group_counts: dict[str, int] = {}
    for station in report.stations:
        group = report.station_resolution_group[station]
        group_counts[group] = group_counts.get(group, 0) + 1
    lines = [
        "## (g) Consumed inputs and the seed",
        "",
        f"- bootstrap seed (P1, fixed): `{report.seed}`",
        f"- bootstrap resamples: {report.params.ma7_bootstrap_resamples}",
        "- bootstrap interval (T2 quantiles, linear percentile): "
        f"{report.params.ma7_bootstrap_percentile_low} / "
        f"{report.params.ma7_bootstrap_percentile_high}",
        "- bootstrap interval (T1 peak hour): circular range over all "
        "resampled values (D9 corrected) — not a percentile interval (P3)",
        "- adequacy bar: "
        f">= {report.params.coloc_min_season_years_for_adequacy} season-years "
        "(Plan 182 D5)",
        f"- wet threshold: {report.params.wet_threshold_side} "
        f"{report.params.wet_threshold_mm_per_h} mm/h (D4)",
        f"- station count: {len(report.stations)}",
        *(f"- {band}: {band_counts[band]} station(s)" for band in _BANDS),
        *(
            f"- reporting-resolution group {g}: {n} station(s)"
            for g, n in sorted(group_counts.items())
        ),
        *(f"- {line}" for line in report.provenance_lines),
        "",
    ]
    return lines


def _method_lines() -> list[str]:
    return [
        "## Method notes",
        "",
        "- **D1 — masked input, always.** Every profile and distribution "
        "below is computed on the M-A3-masked series; the mask removes "
        "sentinels, stuck-high blocks and false-zero runs before any mean "
        "is taken.",
        "- **D2 — hour-of-day exposure travels with every profile.** The "
        "mask is MNAR and hour-dependent, so every diurnal mean below "
        "carries its own retained-hour count; no profile is reported "
        "without it.",
        "- **P4 — an inadequate result is labelled, never suppressed.** A "
        "station or band below the >= 5 season-year adequacy bar still "
        "appears, with its `n_season_years` and an explicit marker.",
        "- **P5 — a refusal renders.** Any statistic that could not "
        "legitimately be computed appears as an explicit `refused — "
        "<reason>`, never a blank, a zero, or a dropped row.",
        "- **P6 — all four seasons, every station, no omission** (D8 as "
        "corrected). This report is large by construction; that is the "
        "cost of setting no completeness threshold.",
        "- **D5a — a band profile/distribution is the STATION-EQUAL "
        "(unweighted) mean/quantile of its member stations** — never a "
        "pooled, retention-weighted figure. The pooled form is reported "
        "separately as a named sensitivity, never the headline.",
        "",
    ]


def _write_report(path: Path, report: Ma7Report) -> None:
    lines = [
        "# DHM precipitation — M-A7 temporal characterisation",
        "",
        f"- generated: `{report.generated_at.isoformat()}`",
        "",
    ]
    lines += _method_lines()
    lines += ["## (a) Per-station diurnal profiles", ""]
    for season in _SEASONS:
        lines += _station_profile_table(report, season)
    lines += ["## (b) Per-band diurnal profiles", ""]
    for season in _SEASONS:
        lines += _band_profile_table(report, season)
    lines += ["## (c) Per-station intensity distributions", ""]
    for season in _SEASONS:
        lines += _station_intensity_table(report, season)
    lines += ["## (d) Per-band intensity distributions", ""]
    for season in _SEASONS:
        lines += _band_intensity_table(report, season)
    lines += _transferability_section(report)
    lines += _olangchunggola_section(report)
    lines += _inputs_and_seed_section(report)
    path.write_text("\n".join(lines) + "\n")


def _resolution_label(raw: str) -> ResolutionGroupLabel:
    if raw not in ("A", "B"):
        raise ValueError(f"unexpected reporting-resolution group label {raw!r}")
    return "A" if raw == "A" else "B"


def _production_inputs(params: DhmPrecipParams = DEFAULT_PARAMS) -> Ma7Inputs:
    """Wires T1-T3's real production paths into one `Ma7Inputs` bundle —
    the only place in this module that touches disk. D10: `masked` is
    consumed from `load_gauge_masked_population`, never rebuilt. The
    reporting-resolution classification is computed from a FRESH,
    independent `on_grid` read (Checked 2026-08-27, D6) — deliberately not
    threaded through the masked population, since D6 defines that
    classification over the unmasked view."""
    masked = load_gauge_masked_population(params=params)
    stations = frozenset(masked.by_station)

    coords_path = resolve_coords_path()
    coords = load_station_coordinates(coords_path, expected_stations=stations)
    station_elev_m = {s: coords.by_station[s].elev_m for s in stations}

    source_path = resolve_source_path()
    long_frame, _inventory = load_long_frame(
        source_path, expected_sha256=PRODUCTION_SOURCE_SHA256
    )
    on_grid = on_grid_view(long_frame, params)
    resolution_frame = infer_reporting_resolution(on_grid, params)
    station_resolution_group: dict[Station, ResolutionGroupLabel] = {
        Station(str(row["station"])): _resolution_label(str(row["group"]))
        for row in resolution_frame.iter_rows(named=True)
        if Station(str(row["station"])) in stations
    }

    provenance_lines = (
        f"source workbook: `{source_path}` (sha256 {PRODUCTION_SOURCE_SHA256})",
        f"coordinate table: `{coords_path}`",
    )
    return Ma7Inputs(
        masked=masked,
        station_elev_m=station_elev_m,
        station_resolution_group=station_resolution_group,
        provenance_lines=provenance_lines,
        params=params,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True, help="output directory for the report"
    )
    args = parser.parse_args(argv)

    try:
        inputs = _production_inputs()
        report = run_ma7_report(
            inputs=inputs, rng=random.Random(REPORT_SEED), seed=REPORT_SEED
        )
    except (
        DhmPrecipLoaderError,
        MissingStationMetadataError,
        NonFiniteElevationError,
    ) as exc:
        log.error("ma7_run.cli.failed", error=str(exc), error_type=type(exc).__name__)
        print(f"error: {exc}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    out_path = args.out / "ma7_report.md"
    _write_report(out_path, report)
    log.info("ma7_run.cli.complete", out=str(out_path))
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
