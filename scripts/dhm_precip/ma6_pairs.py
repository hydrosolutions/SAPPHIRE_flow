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

**Root-cause structural fix (Plan 184 phase 2, 2026-08-24): a subset now
carries its OWN identity.** Eight independent T3 review findings all traced
to the same hole one layer below where each was found: `PairedSeries` knows
its `station`, but `subset()` used to throw that away — `GaugeRetainedSubset`
and `PairedRetainedSubset` carried only `frame`, and `scale` was never
carried at all. Every downstream estimand therefore had to ACCEPT `station`
and `scale` as independently-suppliable constructor fields just to re-attach
what the subset itself already knew — and that re-supply was the hole a
mismatched station or scale could pass through undetected.

`GaugeRetainedSubset` and `PairedRetainedSubset` now carry `station: Station`
and `scale: Scale` as plain fields, populated ONCE, at `subset()` time, from
the series being sliced (`series.station`) and the `scale` `subset()` itself
was called with — never re-derived or re-suppliable downstream. `Scale`
lives here, not in `ma6_estimands.py`, for the same reason: a subset's scale
is a property of HOW it was taken, not of what statistic is later computed
from it. `ma6_estimands.py` imports it from here.

**Round 2 (Plan 184 phase 2, 2026-08-26): a stamp is still a label nothing
checks.** `subset()` stamping `station`/`scale` onto its result closed WHERE
identity could be re-supplied, but a `station=`/`scale=` constructor field is
still just data a caller hands in alongside the frame — nothing enforces
that it actually describes the frame underneath it. This round removes the
ability to STATE identity at all, on every type that carries a frame:
`station` is no longer a field anywhere in this module — `MaskedGaugeSeries`,
`PairedSeries`, `GaugeRetainedSubset`, `PairedRetainedSubset` and the new
`Era5NearestSeries` all derive it as a `@property` read off their own
frame's `station` column (`_frame_station`), raising `StationIdentityError`
if that column is absent or does not resolve to exactly one distinct
station. `scale`, unlike `station`, cannot be derived this way (a JJAS
subset's own sub-subset is still JJAS-consistent) — it stays a plain field
on the two subset types, but is now VERIFIED against the frame's own
timestamps in `__post_init__` (`_check_scale_consistency`), raising
`ScaleConsistencyError` on a mismatch. `pair_with_era5` takes the new
`Era5NearestSeries` (not a bare `pl.DataFrame`) and rejects a station
mismatch between its two arguments, rather than trusting the gauge side's
label for both."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# Precedent: era5_extract.py:12 — xarray ships partial type stubs; the same
# three rules are relaxed repo-wide for every module that touches it.
#
# Declared non-goals (Plan 184 T3 round 7, owner decision): passing a `str`
# where a `Scale` enum is annotated (pyright catches this statically) and
# mutating a `pl.DataFrame` in place after it is inside a frozen dataclass
# (outside the threat model — an analyst wiring a consumer wrong, not one
# deliberately defeating a frozen object's own internals).
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
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


class Scale(StrEnum):
    """The D3 scales a subset can be taken at — `DAILY`/`MONTHLY` are D12's
    categorical grain; `JJAS`/`DJF` are the two seasons this track already
    treats as canonical (Rule 1). Lives here, not in `ma6_estimands.py`,
    because a subset's scale is a property of HOW it was taken (`subset()`'s
    own `scale` argument), not of what statistic is later computed from it
    (module docstring, root-cause structural fix)."""

    DAILY = "DAILY"
    MONTHLY = "MONTHLY"
    JJAS = "JJAS"
    DJF = "DJF"


class RetainedSubsetSchemaError(ValueError):
    """A `GaugeRetainedSubset`/`PairedRetainedSubset` was constructed with a
    frame whose schema does not match its own estimand — the typing hole
    `subset()`'s `@overload` cannot close, since direct construction bypasses
    it entirely (Finding 1 follow-up, Plan 184 T1 independent review,
    2026-08-20). Gauge frames are `(timestamp, value_mm)`; paired frames are
    `(timestamp, gauge_value_mm, era5_nearest_mm_per_h)` — the presence or
    absence of `era5_nearest_mm_per_h` is the discriminator."""


class StationIdentityError(ValueError):
    """`station` is derived from a frame's own `station` column, never a
    separately-suppliable constructor field (Plan 184 phase 2, round 2
    root-cause fix — module docstring). Raised when that column is absent,
    or when it does not resolve to exactly one distinct station.

    A subset filtered down to zero rows (e.g. a station with no DJF hours
    at all) genuinely carries no station identity any more — there is no
    row left to read it from. `GaugeRetainedSubset`/`PairedRetainedSubset`
    therefore derive `station` LAZILY (only when the property is actually
    read), so a legitimately empty subset stays constructible and usable
    for its own `n_*_retained == 0` check; only an attempt to read ITS
    station then raises. `MaskedGaugeSeries`/`PairedSeries`/
    `Era5NearestSeries` are never expected to be empty (a station's own
    whole-record series), so they verify eagerly, at construction."""


def _frame_station(frame: pl.DataFrame, *, type_name: str) -> Station:
    if "station" not in frame.columns:
        raise StationIdentityError(
            f"{type_name} requires a 'station' column on its frame, got "
            f"columns {frame.columns}"
        )
    stations = frame["station"].unique().to_list()
    if len(stations) != 1:
        raise StationIdentityError(
            f"{type_name}'s frame must carry exactly one distinct station, "
            f"got {sorted(str(s) for s in stations)}"
        )
    (value,) = stations
    # A2 fix: a single distinct value passes the count check above even
    # when that value is null or blank — `Station(str(None))` would
    # otherwise silently mint the literal station "None". Null/empty is
    # not a station identity, real or degenerate.
    if value is None or not str(value).strip():
        raise StationIdentityError(
            f"{type_name}'s frame's 'station' column carries a null or "
            f"empty value — not a real station identity: {value!r}"
        )
    return Station(str(value))


class ScaleConsistencyError(ValueError):
    """A `GaugeRetainedSubset`/`PairedRetainedSubset` was constructed with a
    `scale` its own frame's timestamps do not actually satisfy — e.g.
    `Scale.JJAS` requires every retained hour's month to be in
    `params.jjas_months`. `scale` cannot be derived the way `station` is (a
    JJAS subset's own sub-subset is still JJAS-consistent, so there is no
    single "the" scale a frame implies), but it IS exactly checkable
    against the frame it claims to describe — so it is checked, not merely
    trusted (Plan 184 phase 2, round 2 root-cause fix)."""


def _scale_months(scale: Scale, params: DhmPrecipParams) -> frozenset[int] | None:
    """DAILY/MONTHLY return `None` — D12's categorical grain is reported
    over the whole record, with no season restriction to check (module
    docstring's scope decision). JJAS/DJF read `params.jjas_months`/
    `params.djf_months` — the SAME field `ma6_estimands.season_membership_
    predicate` builds its selecting predicate from — never a private copy
    (A1 fix, Plan 184 T3 round 7: a validator must not re-derive the
    computation it is checking)."""
    if scale is Scale.JJAS:
        return frozenset(params.jjas_months)
    if scale is Scale.DJF:
        return frozenset(params.djf_months)
    return None


def _check_scale_consistency(
    frame: pl.DataFrame, scale: Scale, params: DhmPrecipParams, *, type_name: str
) -> None:
    """DAILY/MONTHLY have no month restriction — nothing is checked for
    those two scales. An EMPTY frame is deliberately treated as vacuously
    consistent with any scale, not rejected: a legitimately empty subset
    (e.g. a station with no DJF hours at all) is already flagged by its own
    `EmptySubsetError` downstream (`ma6_estimands.py`); this check exists
    to catch a scale that disagrees with data that IS there, not to
    duplicate the emptiness check."""
    months = _scale_months(scale, params)
    if months is None or frame.height == 0:
        return
    bad = frame.filter(~pl.col("timestamp").dt.month().is_in(months))
    if bad.height > 0:
        bad_months = sorted(set(bad["timestamp"].dt.month().to_list()))
        raise ScaleConsistencyError(
            f"{type_name} declares scale={scale} but its frame carries "
            f"{bad.height} row(s) outside months {sorted(months)} — found "
            f"month(s) {bad_months}"
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class MaskedGaugeSeries:
    """One station's M-A3-masked, on-grid gauge series — season-agnostic:
    every hour `qc_mask` retained, across the whole record, never one
    season's worth.

    `station` is a `@property` derived from the frame's own `station`
    column (`_frame_station`) — NOT a constructor field (Plan 184 phase 2,
    round 2 root-cause fix, module docstring) — verified eagerly in
    `__post_init__` since this type is never expected to be legitimately
    empty."""

    frame: pl.DataFrame
    """Columns `(station, timestamp, value_mm)`, sorted ascending."""

    def __post_init__(self) -> None:
        _ = self.station

    @property
    def station(self) -> Station:
        return _frame_station(self.frame, type_name="MaskedGaugeSeries")


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
    skipped step.

    A3 fix (Plan 184 T3 round 7): `__post_init__` verifies every
    `by_station` entry's KEY against that entry's own frame-derived
    `.station` — a consumer that looks a series up by key (e.g.
    `ma6_representativeness.compute_within_cell_pair`) is trusting the key,
    not the data, unless this holds; checking it ONCE here, for every
    entry, fixes it for every such consumer at once rather than at one
    call site."""

    by_station: dict[Station, MaskedGaugeSeries]
    excluded: tuple[qc_mask.ExclusionListEntry, ...]
    accounting: tuple[qc_mask.RemovalAccountingRow, ...]

    def __post_init__(self) -> None:
        for key, series in self.by_station.items():
            if series.station != key:
                raise StationIdentityError(
                    f"GaugeMaskedPopulation.by_station key {key!r} does not "
                    f"match its series' own derived station {series.station!r}"
                )


@dataclass(frozen=True, kw_only=True, slots=True)
class PairedSeries:
    """T1's second named output — one station's gauge series paired against
    the ERA5-Land NEAREST series on commonly-retained timestamps only
    (D2).

    `station` is a `@property` derived from the frame's own `station`
    column, exactly as `MaskedGaugeSeries`'s is (Plan 184 phase 2, round 2
    root-cause fix) — verified eagerly in `__post_init__`."""

    frame: pl.DataFrame
    """Columns `(station, timestamp, gauge_value_mm, era5_nearest_mm_per_h)`,
    sorted ascending, restricted to timestamps present on both sides."""

    def __post_init__(self) -> None:
        _ = self.station

    @property
    def station(self) -> Station:
        return _frame_station(self.frame, type_name="PairedSeries")


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
    review).

    `scale` is a plain field, populated ONCE by `subset()` itself from the
    scale it was called with, and VERIFIED against the frame's own
    timestamps in `__post_init__` (`_check_scale_consistency`) — round 2's
    fix, module docstring. `station` is NOT a field at all any more — it is
    a `@property` derived from the frame's own `station` column
    (`_frame_station`), read LAZILY (never eagerly checked here) so a
    legitimately empty subset stays constructible; only reading `.station`
    on one raises `StationIdentityError`.

    `params` is the SAME `DhmPrecipParams` `subset()` was called with,
    stamped here for exactly one purpose: `_check_scale_consistency` reads
    `params.jjas_months`/`params.djf_months` from it, the identical source
    `ma6_estimands.season_membership_predicate` reads to build the
    predicate this subset was filtered by (A1 fix, Plan 184 T3 round 7) —
    never a private, independently-maintained copy of the season months."""

    scale: Scale
    frame: pl.DataFrame
    params: DhmPrecipParams = DEFAULT_PARAMS

    def __post_init__(self) -> None:
        if _PAIRED_ONLY_COLUMN in self.frame.columns:
            raise RetainedSubsetSchemaError(
                "GaugeRetainedSubset requires a gauge-only frame "
                "(station, timestamp, value_mm), but the given frame "
                f"carries {_PAIRED_ONLY_COLUMN!r} — this is a PAIRED frame, "
                "and belongs in PairedRetainedSubset instead"
            )
        if "station" not in self.frame.columns:
            raise RetainedSubsetSchemaError(
                "GaugeRetainedSubset requires a 'station' column on its "
                f"frame, got columns {self.frame.columns}"
            )
        _check_scale_consistency(
            self.frame, self.scale, self.params, type_name="GaugeRetainedSubset"
        )

    @property
    def station(self) -> Station:
        return _frame_station(self.frame, type_name="GaugeRetainedSubset")

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
    wearing the common-retained name (Finding 1, Plan 184 T1 review).

    `scale` is a plain field, populated ONCE by `subset()` itself from the
    scale it was called with, and VERIFIED against the frame's own
    timestamps in `__post_init__` (`_check_scale_consistency`) — round 2's
    fix, module docstring. `station` is NOT a field at all any more — it is
    a `@property` derived from the frame's own `station` column
    (`_frame_station`), read LAZILY (never eagerly checked here) so a
    legitimately empty subset stays constructible; only reading `.station`
    on one raises `StationIdentityError`. Every T3 estimand built from this
    subset derives its own `station`/`scale` from these, never accepting
    them as independent arguments.

    `params` — see `GaugeRetainedSubset`'s own docstring; the identical
    role here (A1 fix, Plan 184 T3 round 7)."""

    scale: Scale
    frame: pl.DataFrame
    params: DhmPrecipParams = DEFAULT_PARAMS

    def __post_init__(self) -> None:
        if _PAIRED_ONLY_COLUMN not in self.frame.columns:
            raise RetainedSubsetSchemaError(
                "PairedRetainedSubset requires a paired frame "
                f"(station, timestamp, gauge_value_mm, {_PAIRED_ONLY_COLUMN}), "
                f"but the given frame is missing {_PAIRED_ONLY_COLUMN!r} — "
                "this is a GAUGE-ONLY frame, and belongs in "
                "GaugeRetainedSubset instead"
            )
        if "station" not in self.frame.columns:
            raise RetainedSubsetSchemaError(
                "PairedRetainedSubset requires a 'station' column on its "
                f"frame, got columns {self.frame.columns}"
            )
        _check_scale_consistency(
            self.frame, self.scale, self.params, type_name="PairedRetainedSubset"
        )

    @property
    def station(self) -> Station:
        return _frame_station(self.frame, type_name="PairedRetainedSubset")

    @property
    def n_common_retained(self) -> int:
        return self.frame.height


@overload
def subset(
    series: MaskedGaugeSeries,
    predicate: pl.Expr,
    *,
    scale: Scale,
    params: DhmPrecipParams = DEFAULT_PARAMS,
) -> GaugeRetainedSubset: ...


@overload
def subset(
    series: PairedSeries,
    predicate: pl.Expr,
    *,
    scale: Scale,
    params: DhmPrecipParams = DEFAULT_PARAMS,
) -> PairedRetainedSubset: ...


def subset(
    series: MaskedGaugeSeries | PairedSeries,
    predicate: pl.Expr,
    *,
    scale: Scale,
    params: DhmPrecipParams = DEFAULT_PARAMS,
) -> GaugeRetainedSubset | PairedRetainedSubset:
    """The one way T3+ take a season/scale/period slice of either named
    output. The RETURN TYPE tracks the INPUT type — a `MaskedGaugeSeries`
    yields a `GaugeRetainedSubset` (`n_gauge_retained`); a `PairedSeries`
    yields a `PairedRetainedSubset` (`n_common_retained`) — so a caller
    (and pyright, statically) can never mistake one estimand for the
    other. Either subset's count is always freshly computed from the
    RESULT of this filter.

    `scale` is stamped onto the result HERE, from this call's own `scale`
    argument. `station` is NOT stamped any more (round 2, module
    docstring) — filtering `series.frame` on `predicate` preserves its
    `station` column, so the result's `.station` property derives straight
    from that filtered frame, never from `series.station` re-supplied as an
    argument. `params` is likewise stamped onto the result — the season
    consistency check `__post_init__` runs reads its months from THIS
    `params`, the same object a caller's own `predicate` was (typically)
    built from (`ma6_estimands.season_membership_predicate`), so the
    selector and the validator can never disagree (A1 fix, Plan 184 T3
    round 7). The `isinstance` checks are not merely a `MaskedGaugeSeries`/
    else dispatch: something that duck-types a `.frame`/`.station` pair
    without actually being a `MaskedGaugeSeries` or `PairedSeries` (for
    instance, a `PairedRetainedSubset` passed back in by mistake) is
    rejected outright, rather than silently re-subset — this is what makes
    it impossible to launder an unconditioned subset through `subset()` a
    second time and have it come out looking freshly taken."""
    filtered = series.frame.filter(predicate)
    if isinstance(series, MaskedGaugeSeries):
        return GaugeRetainedSubset(frame=filtered, scale=scale, params=params)
    if isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
        series, PairedSeries
    ):
        return PairedRetainedSubset(frame=filtered, scale=scale, params=params)
    raise TypeError(
        f"subset() requires a MaskedGaugeSeries or PairedSeries, got {type(series)}"
    )


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
            frame=retained_all.filter(pl.col("station") == str(station)).select(
                "station", "timestamp", "value_mm"
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


@dataclass(frozen=True, kw_only=True, slots=True)
class Era5NearestSeries:
    """One station's ERA5-Land NEAREST series, already finite-filtered by
    the caller (`_read_era5_nearest_frames`). `station` is derived from the
    frame's own `station` column — the SAME `_frame_station` mechanism
    `MaskedGaugeSeries`/`PairedSeries` use, not a parallel one (Plan 184
    phase 2 round 2, change 3) — so a Station-B ERA5 frame can never be
    silently paired under Station-A's label; `pair_with_era5` verifies the
    two sides agree instead of trusting the gauge side's label for both."""

    frame: pl.DataFrame
    """Columns `(station, timestamp, era5_nearest_mm_per_h)`."""

    def __post_init__(self) -> None:
        _ = self.station

    @property
    def station(self) -> Station:
        return _frame_station(self.frame, type_name="Era5NearestSeries")


def _read_era5_nearest_frames(series_path: Path) -> dict[Station, Era5NearestSeries]:
    """One read of `series_nearest.nc` — D1's declared NEAREST operator,
    the PRIMARY series (`series_bilinear.nc` is never read here). Each
    station's frame is filtered to FINITE values only: D2's pairing must
    drop an hour ERA5 lacks, never carry a NaN through the join. Each
    frame carries its own `station` column (round 2, module docstring) so
    the identity `pair_with_era5` checks is the SAME data the join itself
    reads, not a dict key supplied alongside it."""
    with xr.open_dataset(series_path, engine="h5netcdf") as ds:
        loaded = ds.load()
    valid_time = loaded["valid_time"].values
    series: dict[Station, Era5NearestSeries] = {}
    for station_name in loaded["station"].to_numpy():
        station = Station(str(station_name))
        values = (
            loaded[_ERA5_VALUE_VAR]
            .sel(station=station_name)
            .to_numpy()
            .astype("float64")
        )
        frame = pl.DataFrame(
            {
                "station": [str(station)] * len(valid_time),
                "timestamp": valid_time,
                "era5_nearest_mm_per_h": values,
            }
        ).filter(pl.col("era5_nearest_mm_per_h").is_finite())
        series[station] = Era5NearestSeries(frame=frame)
    return series


def pair_with_era5(gauge: MaskedGaugeSeries, era5: Era5NearestSeries) -> PairedSeries:
    """D2 — inner-join `gauge.frame` (already M-A3-retained) against
    `era5.frame` (already finite-filtered by the caller) on exact
    timestamp. An hour survives only when BOTH sides already kept it: an
    hour the gauge mask alone would have retained, but ERA5 lacks (or vice
    versa), is dropped HERE — this is what makes the pairing genuinely
    commonly-retained rather than gauge-only.

    `gauge.station` and `era5.station` are each derived from their OWN
    frame — this function REJECTS a mismatch between the two rather than
    labelling the result from the gauge side alone (Plan 184 phase 2 round
    2, change 3: a Station-B ERA5 frame must never silently pair with
    Station-A gauge data and emit as A)."""
    if gauge.station != era5.station:
        raise StationSetMismatchError(
            f"gauge series is {gauge.station!r} but era5 series is "
            f"{era5.station!r} — pair_with_era5 refuses to pair mismatched "
            "station identities"
        )
    gauge_frame = gauge.frame.rename({"value_mm": "gauge_value_mm"})
    era5_aligned = era5.frame.select("timestamp", "era5_nearest_mm_per_h").with_columns(
        pl.col("timestamp").cast(gauge_frame.schema["timestamp"])
    )
    paired = gauge_frame.join(era5_aligned, on="timestamp", how="inner").sort(
        "timestamp"
    )
    return PairedSeries(frame=paired)


def build_paired_population(
    gauge_population: GaugeMaskedPopulation, precip_bundle_dir: Path
) -> dict[Station, PairedSeries]:
    """Production wiring for T1's second named output: pairs every station
    `GaugeMaskedPopulation` already retained (D11's exclusion already
    applied there) against the SAME published bundle's NEAREST series.
    Never re-reads or re-derives the mask."""
    series_path = precip_bundle_dir / _ERA5_NEAREST_SERIES_FILENAME
    era5_series = _read_era5_nearest_frames(series_path)
    paired: dict[Station, PairedSeries] = {}
    for station, gauge_series in gauge_population.by_station.items():
        era5_for_station = era5_series.get(station)
        if era5_for_station is None:
            raise StationSetMismatchError(
                f"{station!r} is present in the gauge-masked population but "
                f"absent from the ERA5-Land bundle's station set at "
                f"{series_path}"
            )
        paired[station] = pair_with_era5(gauge_series, era5_for_station)
    return paired
