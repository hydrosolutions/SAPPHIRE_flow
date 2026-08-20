"""Plan 184 (M-A6) task T1 — the paired, masked substrate.

Generalises `coloc_run.py:_production_dhm_retained_provider` — private,
JJAS-only, bound to the M-A10 two-station registry — into a public,
season-agnostic accessor over all 26 live gauge stations. No statistic is
computed here (T3's job); the M-A3 mask and D11's exclusion predicate are
consumed, never re-derived.

Two named outputs (Codex review 2026-08-20 — the plan is explicit that
these must be separate, not one derived from the other by convention):

1. `GaugeMaskedPopulation` — the GAUGE-ONLY M-A3-masked series, per station,
   season-agnostic (every retained hour, not one season). Public on its
   own, before any ERA5 pairing, so Plan 193 (M-A7, gauge-only) is never
   conditional on ERA5 availability.
2. `PairedSeries` — that same gauge series inner-joined against the
   published ERA5-Land NEAREST series (D1's declared operator) on
   COMMONLY-retained timestamps only (D2): an hour survives only when both
   the gauge value is M-A3-retained and the ERA5 value is finite. Masking
   one side only would measure selection, not weather.

Every subset either output's frame is sliced into — for whatever season,
scale or period a later task asks for — carries its own retained-hour count
as a PROPERTY, computed live from that subset's own row count. There is no
field a caller could instead pass a stale or mismatched count through
(Exit 1/4: "a JJAS-monthly statistic does not rest on the whole series'
retention").

That count is TWO DISTINCT ESTIMANDS, never one type wearing two names
(Finding 1, Plan 184 T1 independent review, 2026-08-20): a slice of
`GaugeMaskedPopulation` is GAUGE-retained exposure (`GaugeRetainedSubset.
n_gauge_retained`); a slice of `PairedSeries` is COMMONLY-retained exposure
(`PairedRetainedSubset.n_common_retained`) — the count D2 and Exit 1/4 mean
by "n". A single `RetainedSubset` type that reported both under the same
name let a caller attach gauge-only exposure to a paired statistic with no
static signal. `subset()` returns whichever type matches the frame kind it
was given, so a T3 function that requires a *paired* `n` can declare
`PairedRetainedSubset` in its signature and have a gauge subset rejected at
type-check time, not at review time.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# Precedent: era5_extract.py:12 — xarray ships partial type stubs; the same
# three rules are relaxed repo-wide for every module that touches it.
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, overload

import polars as pl
import xarray as xr

from sapphire_flow.types.datetime import ensure_utc
from scripts.dhm_precip import normalise, observations, qc_mask
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.era5_errors import (
    ExtractionInputAbsentError,
    StationSetMismatchError,
)
from scripts.dhm_precip.era5_extract_manifest import (
    ExtractionManifest,
    assert_payload_checksum_matches,
    manifest_filename,
    points_root,
    read_extraction_manifest,
)
from scripts.dhm_precip.loader import (
    PRODUCTION_SOURCE_SHA256,
    load_long_frame,
    load_station_coordinates,
    resolve_coords_path,
    resolve_source_path,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS, DhmPrecipParams
from scripts.dhm_precip.views import on_grid_view

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.types.datetime import UtcDatetime

_ERA5_NEAREST_SERIES_FILENAME = "series_nearest.nc"
_ERA5_VALUE_VAR = "precipitation_mm_per_h"

_PAIRED_ONLY_COLUMN = "era5_nearest_mm_per_h"


class RetainedSubsetSchemaError(ValueError):
    """A `GaugeRetainedSubset`/`PairedRetainedSubset` was constructed with a
    frame whose schema does not match its own estimand — the typing hole
    `subset()`'s `@overload` cannot close, since direct construction bypasses
    it entirely (Finding 1 follow-up, Plan 184 T1 independent review,
    2026-08-20). Gauge frames are `(timestamp, value_mm)`; paired frames are
    `(timestamp, gauge_value_mm, era5_nearest_mm_per_h)` — the presence or
    absence of `era5_nearest_mm_per_h` is the discriminator."""


@dataclass(frozen=True, kw_only=True, slots=True)
class MaskedGaugeSeries:
    """One station's M-A3-masked, on-grid gauge series — season-agnostic:
    every hour `qc_mask` retained, across the whole record, never one
    season's worth."""

    station: Station
    frame: pl.DataFrame
    """Columns `(timestamp, value_mm)`, sorted ascending."""


@dataclass(frozen=True, kw_only=True, slots=True)
class GaugeMaskedPopulation:
    """T1's first named output. `by_station` already has D11's excluded
    stations removed.

    `excluded` is Plan 173's M-A6 exclusion list (D11), consumed via
    `qc_mask.build_exclusion_list`, never re-derived. `accounting` is the
    SAME cross-classified removal-accounting rows the exclusion list was
    computed from — carried here so an empty `excluded` tuple is
    distinguishable from "not computed": `accounting` is non-empty
    whenever the mask/exclusion computation actually ran (it reconciles
    exactly to the input observation count, `qc_mask.ReconciliationError`
    otherwise), so a caller sees `excluded == ()` next to a populated
    `accounting` and knows the empty list is a MEASURED result, not a
    skipped step."""

    by_station: dict[Station, MaskedGaugeSeries]
    excluded: tuple[qc_mask.ExclusionListEntry, ...]
    accounting: tuple[qc_mask.RemovalAccountingRow, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class PairedSeries:
    """T1's second named output — one station's gauge series paired against
    the ERA5-Land NEAREST series on commonly-retained timestamps only
    (D2)."""

    station: Station
    frame: pl.DataFrame
    """Columns `(timestamp, gauge_value_mm, era5_nearest_mm_per_h)`, sorted
    ascending, restricted to timestamps present on both sides."""


@dataclass(frozen=True, kw_only=True, slots=True)
class GaugeRetainedSubset:
    """A slice of a `MaskedGaugeSeries.frame`, taken by whatever
    season/scale/period predicate a caller supplies (`subset()`), together
    with ITS OWN gauge-retained-hour count.

    `n_gauge_retained` is a PROPERTY derived from `frame.height`, never a
    separately-suppliable field — there is no constructor argument through
    which a caller could attach an `n` that doesn't match this subset's own
    rows. This is GAUGE exposure only — before any ERA5 pairing — and is a
    DIFFERENT TYPE from `PairedRetainedSubset` precisely so the two counts
    cannot be confused: a function that needs the commonly-retained count
    D2/Exit 1/4 mean by "n" cannot accept this type (Finding 1, Plan 184 T1
    review)."""

    frame: pl.DataFrame

    def __post_init__(self) -> None:
        if _PAIRED_ONLY_COLUMN in self.frame.columns:
            raise RetainedSubsetSchemaError(
                "GaugeRetainedSubset requires a gauge-only frame "
                "(timestamp, value_mm), but the given frame carries "
                f"{_PAIRED_ONLY_COLUMN!r} — this is a PAIRED frame, and "
                "belongs in PairedRetainedSubset instead"
            )

    @property
    def n_gauge_retained(self) -> int:
        return self.frame.height


@dataclass(frozen=True, kw_only=True, slots=True)
class PairedRetainedSubset:
    """A slice of a `PairedSeries.frame`, taken by whatever
    season/scale/period predicate a caller supplies (`subset()`), together
    with ITS OWN commonly-retained-hour count.

    `n_common_retained` is a PROPERTY derived from `frame.height`, never a
    separately-suppliable field — there is no constructor argument through
    which a caller could attach an `n` that doesn't match this subset's own
    rows. This is the structural answer to Exit 1/4: a JJAS-monthly
    statistic's `n` can only ever be that statistic's own JJAS-monthly row
    count, never the whole series' retention count — and, being a distinct
    type from `GaugeRetainedSubset`, it can never be gauge-only exposure
    wearing the common-retained name (Finding 1, Plan 184 T1 review)."""

    frame: pl.DataFrame

    def __post_init__(self) -> None:
        if _PAIRED_ONLY_COLUMN not in self.frame.columns:
            raise RetainedSubsetSchemaError(
                "PairedRetainedSubset requires a paired frame "
                f"(timestamp, gauge_value_mm, {_PAIRED_ONLY_COLUMN}), but "
                f"the given frame is missing {_PAIRED_ONLY_COLUMN!r} — this "
                "is a GAUGE-ONLY frame, and belongs in GaugeRetainedSubset "
                "instead"
            )

    @property
    def n_common_retained(self) -> int:
        return self.frame.height


@overload
def subset(series: MaskedGaugeSeries, predicate: pl.Expr) -> GaugeRetainedSubset: ...


@overload
def subset(series: PairedSeries, predicate: pl.Expr) -> PairedRetainedSubset: ...


def subset(
    series: MaskedGaugeSeries | PairedSeries, predicate: pl.Expr
) -> GaugeRetainedSubset | PairedRetainedSubset:
    """The one way T3+ take a season/scale/period slice of either named
    output. The RETURN TYPE tracks the INPUT type — a `MaskedGaugeSeries`
    yields a `GaugeRetainedSubset` (`n_gauge_retained`); a `PairedSeries`
    yields a `PairedRetainedSubset` (`n_common_retained`) — so a caller
    (and pyright, statically) can never mistake one estimand for the
    other. Either subset's count is always freshly computed from the
    RESULT of this filter."""
    filtered = series.frame.filter(predicate)
    if isinstance(series, MaskedGaugeSeries):
        return GaugeRetainedSubset(frame=filtered)
    return PairedRetainedSubset(frame=filtered)


def build_gauge_masked_population(
    on_grid: pl.DataFrame,
    *,
    live_stations: frozenset[Station],
    params: DhmPrecipParams = DEFAULT_PARAMS,
    now: UtcDatetime,
) -> GaugeMaskedPopulation:
    """The pure, testable core — no I/O. Mirrors
    `coloc_run._production_dhm_retained_provider`'s exact call sequence
    (`normalise_hourly_axis` -> `iter_observations_by_station` ->
    `qc_mask.iter_station_results`), generalised from that function's
    JJAS-only, two-station scope to every live station and every season.

    `on_grid` is the `ON_GRID` view (`views.on_grid_view`'s output) over the
    full 37-raw-column workbook; `live_stations` restricts which of those
    columns are real (D6b) — the same split `normalise_hourly_axis` already
    requires."""
    normalised = normalise.normalise_hourly_axis(on_grid, live_stations)
    station_observations = observations.iter_observations_by_station(
        normalised, parameter="precipitation", created_at=now
    )
    mask, accounting_rows = qc_mask.iter_station_results(station_observations, params)
    excluded = qc_mask.build_exclusion_list(accounting_rows, params)
    excluded_stations = frozenset(entry.station for entry in excluded)

    # The mask frame's timestamp dtype is DERIVED from the frame it is
    # about to be anti-joined against, never pinned (coloc_run.py's same
    # fix: `pl.read_excel` yields `Datetime("ms")`, so a hard-coded unit
    # makes the join raise `SchemaError` against real data).
    mask_df = pl.DataFrame(
        {
            "station": [str(station) for station, _ts in mask],
            "timestamp": [ts.replace(tzinfo=None) for _station, ts in mask],
        },
        schema={"station": pl.Utf8, "timestamp": on_grid.schema["timestamp"]},
    )
    retained_all = (
        on_grid.join(mask_df, on=["station", "timestamp"], how="anti")
        .filter(pl.col("value_mm").is_not_null())
        .sort(["station", "timestamp"])
    )

    by_station = {
        station: MaskedGaugeSeries(
            station=station,
            frame=retained_all.filter(pl.col("station") == str(station)).select(
                "timestamp", "value_mm"
            ),
        )
        for station in sorted(live_stations - excluded_stations)
    }
    return GaugeMaskedPopulation(
        by_station=by_station, excluded=excluded, accounting=accounting_rows
    )


def load_gauge_masked_population(
    *, params: DhmPrecipParams = DEFAULT_PARAMS
) -> GaugeMaskedPopulation:
    """Production wiring — loads the pinned production workbook and
    computes the M-A3 mask ONCE over every live station, mirroring
    `coloc_run._production_dhm_retained_provider`'s own I/O sequence
    (`resolve_source_path` -> `load_long_frame` -> `on_grid_view`)."""
    source_path = resolve_source_path()
    long_frame, inventory = load_long_frame(
        source_path, expected_sha256=PRODUCTION_SOURCE_SHA256
    )
    live_stations = frozenset(
        Station(name)
        for name in inventory.all_columns
        if name not in inventory.empty_columns
    )
    coords_path = resolve_coords_path()
    load_station_coordinates(coords_path, expected_stations=live_stations)

    on_grid = on_grid_view(long_frame, params)
    now = ensure_utc(datetime.now(UTC))
    return build_gauge_masked_population(
        on_grid, live_stations=live_stations, params=params, now=now
    )


def discover_precip_bundle(
    precip_data_root: Path,
) -> tuple[Path, ExtractionManifest]:
    """P2/P6's discovery convention — the highest `NNNN` whose manifest
    validates — applied to the payload T1 actually reads
    (`series_nearest.nc`). Same APPROACH as
    `extract_era5_t2m._discover_precip_bundle`, which reconciles a
    DIFFERENT payload (`station_grid_elevation.csv`) for a different
    purpose (D6); this is not that function reused verbatim, since the
    payload being trusted differs, but the discovery algorithm is not
    re-invented.

    An identity is a LABEL, never a lookup key (P3) — a bundle is never
    resolved by globbing `*-<identity>`, only by this highest-valid-`NNNN`
    scan."""
    root = points_root(precip_data_root)
    if not root.exists():
        raise ExtractionInputAbsentError(
            f"no precipitation extraction points root at {root} — M-A6 "
            "needs a published precipitation bundle's series_nearest.nc"
        )
    candidates = sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name != ".staging"),
        key=lambda p: p.name,
    )
    for candidate in reversed(candidates):
        manifest = read_extraction_manifest(candidate / manifest_filename())
        if manifest is not None:
            assert_payload_checksum_matches(
                candidate, manifest, _ERA5_NEAREST_SERIES_FILENAME
            )
            return candidate, manifest
    raise ExtractionInputAbsentError(
        "no published precipitation extraction bundle with a readable "
        f"manifest and a checksum-verified {_ERA5_NEAREST_SERIES_FILENAME!r} "
        f"found under {root}"
    )


def _read_era5_nearest_frames(series_path: Path) -> dict[Station, pl.DataFrame]:
    """One read of `series_nearest.nc` — D1's declared NEAREST operator,
    the PRIMARY series (`series_bilinear.nc` is never read here). Each
    station's frame is filtered to FINITE values only: D2's pairing must
    drop an hour ERA5 lacks, never carry a NaN through the join."""
    with xr.open_dataset(series_path, engine="h5netcdf") as ds:
        loaded = ds.load()
    valid_time = loaded["valid_time"].values
    frames: dict[Station, pl.DataFrame] = {}
    for station_name in loaded["station"].to_numpy():
        station = Station(str(station_name))
        values = (
            loaded[_ERA5_VALUE_VAR]
            .sel(station=station_name)
            .to_numpy()
            .astype("float64")
        )
        frame = pl.DataFrame({"timestamp": valid_time, "era5_nearest_mm_per_h": values})
        frames[station] = frame.filter(pl.col("era5_nearest_mm_per_h").is_finite())
    return frames


def pair_with_era5(gauge: MaskedGaugeSeries, era5_frame: pl.DataFrame) -> PairedSeries:
    """D2 — inner-join `gauge.frame` (already M-A3-retained) against
    `era5_frame` (already finite-filtered by the caller) on exact
    timestamp. An hour survives only when BOTH sides already kept it: an
    hour the gauge mask alone would have retained, but ERA5 lacks (or vice
    versa), is dropped HERE — this is what makes the pairing genuinely
    commonly-retained rather than gauge-only."""
    gauge_frame = gauge.frame.rename({"value_mm": "gauge_value_mm"})
    era5_aligned = era5_frame.with_columns(
        pl.col("timestamp").cast(gauge_frame.schema["timestamp"])
    )
    paired = gauge_frame.join(era5_aligned, on="timestamp", how="inner").sort(
        "timestamp"
    )
    return PairedSeries(station=gauge.station, frame=paired)


def build_paired_population(
    gauge_population: GaugeMaskedPopulation, precip_bundle_dir: Path
) -> dict[Station, PairedSeries]:
    """Production wiring for T1's second named output: pairs every station
    `GaugeMaskedPopulation` already retained (D11's exclusion already
    applied there) against the SAME published bundle's NEAREST series.
    Never re-reads or re-derives the mask."""
    series_path = precip_bundle_dir / _ERA5_NEAREST_SERIES_FILENAME
    era5_frames = _read_era5_nearest_frames(series_path)
    paired: dict[Station, PairedSeries] = {}
    for station, gauge_series in gauge_population.by_station.items():
        era5_frame = era5_frames.get(station)
        if era5_frame is None:
            raise StationSetMismatchError(
                f"{station!r} is present in the gauge-masked population but "
                f"absent from the ERA5-Land bundle's station set at "
                f"{series_path}"
            )
        paired[station] = pair_with_era5(gauge_series, era5_frame)
    return paired
