"""Plan 184 (M-A6) task T5 — the sub-freezing mass fraction.

For each magnitude estimand (D1's four: `MatchedHourMeanDifference`,
`ConditionalAccumulatedDifference`, `WetHourConditionalIntensityBias`,
`JointWetHourConditionalIntensityBias` — `ma6_estimands.BandMemberEstimand`),
the MASS-WEIGHTED fraction of that SAME period's gauge precipitation falling
at lapse-corrected ERA5-Land air temperature below a threshold (D4).
Mass-weighted, not hour-weighted: fraction = (sum of gauge mm on hours where
corrected T < threshold) / (sum of gauge mm over the classifiable subset).
**Annotates only — gates, adjusts and corrects nothing** (D4's own rule);
this module exposes no method that filters, thresholds or rescales
anything by the fraction it reports.

**Reuse, never re-derive (D6/D14).** The lapse correction
(`lapse_correct_to_station_degc`) and the standard rate constant
(`STANDARD_LAPSE_RATE_DEGC_PER_KM`) are T2's (`ma6_lapse_check.py`), applied
here verbatim to DHM gauge stations. `orography_elev_at_nearest_cell` is
NOT reused here even though T2 exports it — that function samples T2's own
orography raster at an ARBITRARY coordinate, which T2 needs only for
Pyramid stations (none of which is a DHM gauge station, so none appears in
`station_grid_elevation.csv`). Every station this module handles IS a DHM
gauge station, so its model-orography elevation is already a published
column of that same CSV (`station_grid_elevation.csv`'s own
`orography_elev_m`, built by M-A5) — reading it (via T2's own
`discover_and_load_station_grid_elevation_table`) is the reuse; resampling
the raster a second time would be a re-derivation of a number the pipeline
already computed once.

**The four T5 pins (owner, Plan 184 T5 kickoff — recorded here so a future
amendment starts from what was decided, not from a blank task):**

1. **Which magnitudes get a fraction:** all four of D1's — this module
   takes any `BandMemberEstimand` (see `build_sub_freezing_mass_fraction`).
2. **Band-level fraction = UNWEIGHTED MEAN of member-station fractions**,
   carrying `member_ns` — never a pooled-mass fraction (see
   `ElevationBandMassFraction.mean_sub_freezing_mass_fraction`).
3. **Sensitivity is ONE-AT-A-TIME, not a 3x3 grid** — five combinations
   sharing the primary 1.5 degC @ 6.5 degC/km (see
   `sensitivity_combinations`/`sub_freezing_mass_fraction_sensitivity`).
4. **Unclassifiable mass is REPORTED, never dropped or rescaled** (D13) —
   `SubFreezingMassFraction.unclassifiable_mass_share` is a first-class
   property, always computed, never silently folded into the classifiable
   denominator.

**Structural discipline (this track's recurring failure mode, closed the
same way T1/T3 close it): `SubFreezingMassFraction` stores exactly the
`PairedRetainedSubset` its magnitude was built from — never a value, `n`,
station or scale independently suppliable alongside it.** `station`,
`scale` and `n` are `@property`s read straight off `self.subset` — the
SAME object a magnitude estimand's own `.subset` is, when
`build_sub_freezing_mass_fraction` is given that estimand directly. This
is what makes Exit 9 ("no magnitude may be quoted without BOTH its
retained-hour `n` and its sub-freezing mass fraction in the SAME cell")
true by construction: the fraction's `n` cannot disagree with its
magnitude's `n` because there is only one subset object underneath both.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# Precedent: era5_extract.py:12 — xarray ships partial type stubs; the same
# three rules are relaxed repo-wide for every module that touches it.
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import numpy as np
import polars as pl
import xarray as xr

from scripts.dhm_precip.domain_types import DatumReconciliationStatus, Station
from scripts.dhm_precip.era5_errors import (
    ExtractionInputAbsentError,
    StationSetMismatchError,
)
from scripts.dhm_precip.extract_era5_t2m import (
    DEFAULT_T2M_DATA_ROOT,
    T2M_DATA_VARIABLE,
    discover_t2m_bundle,
)
from scripts.dhm_precip.ma6_estimands import (
    BandMemberEstimand,
    BandMembershipError,
    DuplicateBandMemberError,
    ElevationBand,
    EmptySubsetError,
    EstimandSubsetTypeError,
    MixedSelectionParamsError,
    ScaleNotSupportedError,
    assign_elevation_band,
)
from scripts.dhm_precip.ma6_lapse_check import (
    STANDARD_LAPSE_RATE_DEGC_PER_KM,
    lapse_correct_to_station_degc,
)
from scripts.dhm_precip.ma6_lapse_check import (
    discover_and_load_station_grid_elevation_table as _discover_and_load_elev_table,
)
from scripts.dhm_precip.ma6_pairs import PairedRetainedSubset, Scale
from scripts.dhm_precip.numeric import as_float

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_T2M_SERIES_FILENAME = "series_t2m_degc.nc"
"""The published t2m bundle's payload filename (`extract_era5_t2m.py`'s own
`_T2M_SERIES_FILENAME`, private there) — redeclared here as a local
constant, the same convention `ma6_pairs.py`'s own
`_ERA5_NEAREST_SERIES_FILENAME` uses rather than importing a private
symbol across modules."""

# D4's primary + D4/D14's sensitivity axes. One-at-a-time (pin 3): the
# threshold sweep holds lapse at the standard rate, the lapse sweep holds
# threshold at 1.5 degC — never the 3x3 cross product.
PRIMARY_THRESHOLD_DEGC: Final = 1.5
PRIMARY_LAPSE_RATE_DEGC_PER_KM: Final = STANDARD_LAPSE_RATE_DEGC_PER_KM
THRESHOLD_SENSITIVITY_DEGC: Final[tuple[float, ...]] = (0.0, 1.5, 2.0)
LAPSE_SENSITIVITY_DEGC_PER_KM: Final[tuple[float, ...]] = (5.0, 6.5, 9.8)


def sensitivity_combinations() -> tuple[tuple[float, float], ...]:
    """The five (threshold_degc, lapse_rate_degc_per_km) combinations D4/D14
    and pin 3 name, sharing the primary 1.5 @ 6.5 — never a 3x3 grid over
    both axes at once. Sorted for a deterministic, testable order."""
    combos = {(PRIMARY_THRESHOLD_DEGC, PRIMARY_LAPSE_RATE_DEGC_PER_KM)}
    combos.update(
        (threshold, PRIMARY_LAPSE_RATE_DEGC_PER_KM)
        for threshold in THRESHOLD_SENSITIVITY_DEGC
    )
    combos.update(
        (PRIMARY_THRESHOLD_DEGC, rate) for rate in LAPSE_SENSITIVITY_DEGC_PER_KM
    )
    return tuple(sorted(combos))


class MassFractionFrameSchemaError(ValueError):
    """`SubFreezingMassFraction.t2m_frame` is missing one of its required
    columns `(station, timestamp, grid_t2m_degc)` — the one-station raw
    ERA5-Land nearest-cell t2m series the lapse correction is applied to."""


class DuplicateTimestampError(ValueError):
    """`t2m_frame` carries more than one row for the same `timestamp` — the
    join in `SubFreezingMassFraction._classified_frame` would fan out a
    gauge hour's mass across duplicate rows, silently inflating it. A
    published `series_t2m_degc.nc` is one row per station per hour by
    construction; this guards against a caller supplying something else."""


class ZeroMassError(ValueError):
    """A `SubFreezingMassFraction`'s subset carries commonly-retained hours
    but ZERO total gauge mass over them — no mass fraction (classified or
    unclassifiable share) is computable. Never silently reported as 0.0 or
    NaN (the same discipline `ma6_estimands.EmptySubsetError` applies to a
    zero-row subset, one level up: this is a zero-MASS subset, which can
    have nonzero rows)."""


class ZeroClassifiableMassError(ValueError):
    """Every retained hour in a `SubFreezingMassFraction`'s subset carries
    an unclassifiable (non-finite or missing) corrected temperature — the
    classifiable denominator is zero, so `sub_freezing_mass_fraction` is
    undefined. `unclassifiable_mass_share` remains reportable (it would be
    1.0) — only the CLASSIFIED fraction is undefined here. Expected never
    to fire in practice (Plan 191: zero non-finite t2m over the full hourly
    axis), but the guard exists regardless (D13: never silently rescale)."""


@dataclass(frozen=True, kw_only=True, slots=True)
class StationElevationInputs:
    """The two elevations `lapse_correct_to_station_degc` needs, wrapped
    together deliberately: both are plain floats in metres, and swapping
    their order at a call site would silently FLIP THE SIGN of every
    correction (`rate * (orography_elev_m - station_elev_m) / 1000`) — the
    exact same-primitive-type mix-up CLAUDE.md's type-driven-development
    section calls out. `datum_reconciled` travels alongside them so a
    consumer of `SubFreezingMassFraction` never has to re-fetch
    `station_grid_elevation.csv` just to attach D7's label."""

    station_elev_m: float
    orography_elev_m: float
    datum_reconciled: DatumReconciliationStatus


@dataclass(frozen=True, kw_only=True, slots=True)
class SubFreezingMassFraction:
    """D4 — the mass-weighted sub-freezing fraction over the SAME
    `PairedRetainedSubset` a magnitude estimand was computed from.

    `t2m_frame` is this station's RAW (not lapse-corrected) ERA5-Land
    nearest-cell t2m series, `(station, timestamp, grid_t2m_degc)` — the
    lapse correction is applied HERE, live, via T2's
    `lapse_correct_to_station_degc` (never re-implemented). `station` is
    verified against `subset.station` in `__post_init__`
    (`StationSetMismatchError`) — this module does not trust a caller's
    label, the same discipline `ma6_pairs.pair_with_era5` applies to the
    gauge/ERA5 pairing.

    `station`, `scale` and `n` are `@property`s read off `self.subset` —
    NOT independently-suppliable constructor fields (module docstring) —
    so a mass fraction can never be reported for a station/scale/n other
    than the exact subset it was built from."""

    subset: PairedRetainedSubset
    t2m_frame: pl.DataFrame
    elevation: StationElevationInputs
    threshold_degc: float = PRIMARY_THRESHOLD_DEGC
    lapse_rate_degc_per_km: float = PRIMARY_LAPSE_RATE_DEGC_PER_KM

    def __post_init__(self) -> None:
        # Dataclasses do not enforce field types at runtime — same
        # defence-in-depth every other __post_init__ in this track applies.
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.subset, PairedRetainedSubset
        ):
            raise EstimandSubsetTypeError(
                "SubFreezingMassFraction.subset must be a real "
                f"PairedRetainedSubset, got {type(self.subset)}"
            )
        required_columns = {"station", "timestamp", "grid_t2m_degc"}
        missing = required_columns - set(self.t2m_frame.columns)
        if missing:
            raise MassFractionFrameSchemaError(
                f"t2m_frame is missing column(s) {sorted(missing)} — got "
                f"columns {self.t2m_frame.columns}"
            )
        t2m_stations = self.t2m_frame["station"].unique().to_list()
        if (
            len(t2m_stations) != 1
            or Station(str(t2m_stations[0])) != self.subset.station
        ):
            raise StationSetMismatchError(
                f"subset is {self.subset.station!r} but t2m_frame carries "
                f"station(s) {sorted(str(s) for s in t2m_stations)} — refusing "
                "to pair mismatched station identities"
            )
        if self.t2m_frame.height != self.t2m_frame["timestamp"].n_unique():
            raise DuplicateTimestampError(
                f"{self.subset.station!r}: t2m_frame carries "
                f"{self.t2m_frame.height} rows but only "
                f"{self.t2m_frame['timestamp'].n_unique()} distinct timestamps"
            )
        if not math.isfinite(self.threshold_degc):
            raise ValueError(f"threshold_degc {self.threshold_degc!r} is not finite")
        if not math.isfinite(self.lapse_rate_degc_per_km):
            raise ValueError(
                f"lapse_rate_degc_per_km {self.lapse_rate_degc_per_km!r} is not finite"
            )

    @property
    def station(self) -> Station:
        return self.subset.station

    @property
    def scale(self) -> Scale:
        return self.subset.scale

    @property
    def n(self) -> int:
        """The SAME commonly-retained count the magnitude this fraction
        accompanies reports as its own `n` — structurally guaranteed
        (module docstring, Exit 9) since both read `self.subset.
        n_common_retained`, the identical subset object."""
        return self.subset.n_common_retained

    @property
    def _classified_frame(self) -> pl.DataFrame:
        """LEFT-joins the subset's gauge mass against the lapse-corrected
        t2m series on `timestamp`: a retained hour absent from `t2m_frame`
        (or carrying a non-finite grid value) survives the join with a
        null `corrected_degc` — its mass is neither dropped nor rescaled
        (D13), only marked unclassifiable by `classifiable_mass_mm` and
        `unclassifiable_mass_mm` below."""
        corrected = np.asarray(
            lapse_correct_to_station_degc(
                self.t2m_frame["grid_t2m_degc"].to_numpy(),
                orography_elev_m=self.elevation.orography_elev_m,
                station_elev_m=self.elevation.station_elev_m,
                rate_degc_per_km=self.lapse_rate_degc_per_km,
            ),
            dtype=np.float64,
        )
        gauge = self.subset.frame.select("timestamp", "gauge_value_mm")
        # `timestamp`\'s dtype is DERIVED from `gauge`\'s own schema, never
        # pinned -- the same fix `ma6_pairs.pair_with_era5` already applies
        # to the gauge/ERA5-precipitation join: `series_t2m_degc.nc` is
        # read via xarray (datetime64[ns] -> polars Datetime("ns")), while
        # `gauge`\'s timestamps come from `pl.read_excel` (Datetime("ms")) --
        # joining without a cast raises `SchemaError` on real data (found by
        # the T5 gated real-data run, 2026-08-26).
        t2m_corrected = self.t2m_frame.select("timestamp").with_columns(
            pl.Series("corrected_degc", corrected, dtype=pl.Float64),
            pl.col("timestamp").cast(gauge.schema["timestamp"]),
        )
        return gauge.join(t2m_corrected, on="timestamp", how="left")

    @property
    def total_mass_mm(self) -> float:
        """Total gauge mass over EVERY retained hour in `subset` — the
        denominator D13 requires: never scaled down by classifiability."""
        return as_float(self._classified_frame["gauge_value_mm"].sum())

    @property
    def classifiable_mass_mm(self) -> float:
        return as_float(
            self._classified_frame.filter(pl.col("corrected_degc").is_finite())[
                "gauge_value_mm"
            ].sum()
        )

    @property
    def unclassifiable_mass_mm(self) -> float:
        """Summed directly over the unclassifiable rows — NOT
        `total_mass_mm - classifiable_mass_mm`. Both of those are
        separate floating-point sums over the same `gauge_value_mm`
        column in a different row order, so their difference is not
        exactly 0.0 even when every hour is classifiable (measured on the
        real archive: up to -9.09e-13 mm, e.g. Lukla Airport JJAS). A
        direct sum is correct by construction and returns exactly 0.0
        when there is nothing unclassifiable — never clamped with
        `max(0.0, ...)`, which would hide a genuine inconsistency instead
        of avoiding a nonexistent one.
        `is_finite()` on a null `corrected_degc` (a retained hour absent
        from `t2m_frame`) evaluates to null, not False, so `~is_finite()`
        alone would silently drop those rows from the filter too — hence
        `fill_null(True)`, making a null row count as unclassifiable, the
        exact complement of `classifiable_mass_mm`'s `is_finite()` filter.
        """
        return as_float(
            self._classified_frame.filter(
                (~pl.col("corrected_degc").is_finite()).fill_null(True)
            )["gauge_value_mm"].sum()
        )

    @property
    def unclassifiable_mass_share(self) -> float:
        """D13 — reported explicitly, always, never folded into the
        classified denominator. Expected 0.0 (Plan 191: zero non-finite
        t2m over the full hourly axis) but measured, not assumed."""
        total = self.total_mass_mm
        if total == 0.0:
            raise ZeroMassError(
                f"{self.station!r} at {self.scale}: zero total gauge mass "
                "over the retained subset — no unclassifiable mass share is "
                "computable"
            )
        return self.unclassifiable_mass_mm / total

    @property
    def sub_freezing_mass_mm(self) -> float:
        return as_float(
            self._classified_frame.filter(
                pl.col("corrected_degc").is_finite()
                & (pl.col("corrected_degc") < self.threshold_degc)
            )["gauge_value_mm"].sum()
        )

    @property
    def sub_freezing_mass_fraction(self) -> float:
        """D4's headline number: sub-freezing (classifiable) mass divided
        by the CLASSIFIABLE mass only — never by `total_mass_mm`, which
        would silently rescale a nonzero unclassifiable share into the
        denominator (D13 forbids exactly this)."""
        classifiable = self.classifiable_mass_mm
        if classifiable == 0.0:
            raise ZeroClassifiableMassError(
                f"{self.station!r} at {self.scale}: every retained hour is "
                "unclassifiable — no sub-freezing mass fraction is computable"
            )
        return self.sub_freezing_mass_mm / classifiable


def build_sub_freezing_mass_fraction(
    estimand: BandMemberEstimand,
    *,
    t2m_by_station: Mapping[Station, pl.DataFrame],
    elevations_by_station: Mapping[Station, StationElevationInputs],
    threshold_degc: float = PRIMARY_THRESHOLD_DEGC,
    lapse_rate_degc_per_km: float = PRIMARY_LAPSE_RATE_DEGC_PER_KM,
) -> SubFreezingMassFraction:
    """Pin 1's entry point — `estimand` may be ANY of D1's four magnitude
    kinds (`ma6_estimands.BandMemberEstimand`); `estimand.subset` is passed
    straight through, never copied or rebuilt, so the returned fraction's
    `n`/`station`/`scale` are structurally the magnitude's own (Exit 9,
    module docstring)."""
    station = estimand.station
    if station not in t2m_by_station:
        raise ExtractionInputAbsentError(
            f"no ERA5-Land t2m series available for {station!r}"
        )
    if station not in elevations_by_station:
        raise ExtractionInputAbsentError(
            f"no station_grid_elevation.csv row available for {station!r}"
        )
    return SubFreezingMassFraction(
        subset=estimand.subset,
        t2m_frame=t2m_by_station[station],
        elevation=elevations_by_station[station],
        threshold_degc=threshold_degc,
        lapse_rate_degc_per_km=lapse_rate_degc_per_km,
    )


def sub_freezing_mass_fraction_sensitivity(
    estimand: BandMemberEstimand,
    *,
    t2m_by_station: Mapping[Station, pl.DataFrame],
    elevations_by_station: Mapping[Station, StationElevationInputs],
) -> tuple[SubFreezingMassFraction, ...]:
    """Pin 3 — the five `sensitivity_combinations()` results for ONE
    estimand, one-at-a-time, never a 3x3 grid. ⛔ Callers must not rank
    the threshold-axis members against the lapse-axis members against each
    other (D4) — this function only assembles the five values; it computes
    no comparison between them."""
    return tuple(
        build_sub_freezing_mass_fraction(
            estimand,
            t2m_by_station=t2m_by_station,
            elevations_by_station=elevations_by_station,
            threshold_degc=threshold,
            lapse_rate_degc_per_km=lapse_rate,
        )
        for threshold, lapse_rate in sensitivity_combinations()
    )


def _verify_band_membership(
    band: ElevationBand,
    stations: tuple[Station, ...],
    station_elev_m: Mapping[Station, float],
) -> None:
    """The mass-fraction analogue of `ma6_estimands._verify_band_membership`
    (private there, so re-declared here rather than imported across a
    module boundary — the SAME structural check, `assign_elevation_band`
    reused verbatim, never re-derived; D4a's edges live in exactly one
    place)."""
    for station in stations:
        if station not in station_elev_m:
            raise BandMembershipError(
                f"{band}: {station!r} has no known elevation in "
                "station_elev_m — cannot verify band membership"
            )
        elev_m = station_elev_m[station]
        actual_band = assign_elevation_band(elev_m)
        if actual_band is not band:
            raise BandMembershipError(
                f"{band}: {station!r} at {elev_m} m belongs to {actual_band} "
                f"(D4a edges), not the declared {band}"
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class ElevationBandMassFraction:
    """Pin 2 — the band value is the UNWEIGHTED MEAN of member stations'
    OWN `sub_freezing_mass_fraction` values, never a pool of mass across
    stations (D4a's own reasoning, reused verbatim here rather than a
    second mechanism: a high-mass or high-retention station must not
    dominate the band figure). `member_ns` carries every member's own `n`
    so retention variation stays visible (D13).

    Follows `ma6_estimands.ElevationBandEstimand`'s exact structural
    pattern — `members` is the only collection field, every derived value
    is a `@property`, and commensurability (one estimand kind — trivially
    true here, `SubFreezingMassFraction` is the only member type — and one
    selection parameterisation: same scale, same `threshold_degc`, same
    `lapse_rate_degc_per_km`, same `DhmPrecipParams`) is refused at
    construction, not merely checked by a caller."""

    band: ElevationBand
    members: tuple[SubFreezingMassFraction, ...]
    station_elev_m: Mapping[Station, float]

    def __post_init__(self) -> None:
        if not self.members:
            raise EmptySubsetError(
                f"{self.band}: zero member stations — no band mass fraction "
                "is computable"
            )
        stations = tuple(m.station for m in self.members)
        if len(set(stations)) != len(stations):
            raise DuplicateBandMemberError(
                f"{self.band}: members carries a repeated station: {stations}"
            )
        scales = {m.scale for m in self.members}
        if len(scales) > 1:
            raise ScaleNotSupportedError(
                f"{self.band}: members span more than one scale: {scales}"
            )
        thresholds = {m.threshold_degc for m in self.members}
        if len(thresholds) > 1:
            raise MixedSelectionParamsError(
                f"{self.band}: members carry different threshold_degc "
                f"values: {thresholds}"
            )
        lapse_rates = {m.lapse_rate_degc_per_km for m in self.members}
        if len(lapse_rates) > 1:
            raise MixedSelectionParamsError(
                f"{self.band}: members carry different lapse_rate_degc_per_km "
                f"values: {lapse_rates}"
            )
        selection_params = {m.subset.params for m in self.members}
        if len(selection_params) > 1:
            raise MixedSelectionParamsError(
                f"{self.band}: members were selected under different "
                f"DhmPrecipParams: {selection_params}"
            )
        _verify_band_membership(self.band, stations, self.station_elev_m)

    @property
    def scale(self) -> Scale:
        return self.members[0].scale

    @property
    def threshold_degc(self) -> float:
        return self.members[0].threshold_degc

    @property
    def lapse_rate_degc_per_km(self) -> float:
        return self.members[0].lapse_rate_degc_per_km

    @property
    def station_count(self) -> int:
        return len(self.members)

    @property
    def member_ns(self) -> tuple[int, ...]:
        return tuple(m.n for m in self.members)

    @property
    def mean_sub_freezing_mass_fraction(self) -> float:
        """Pin 2 — `sum(values) / len(values)` over member FRACTIONS, never
        `sum(sub_freezing_mass) / sum(total_mass)` (which would pool mass
        across stations)."""
        return sum(m.sub_freezing_mass_fraction for m in self.members) / len(
            self.members
        )

    @property
    def mean_unclassifiable_mass_share(self) -> float:
        return sum(m.unclassifiable_mass_share for m in self.members) / len(
            self.members
        )


def elevation_band_mass_fraction(
    band: ElevationBand,
    members: tuple[SubFreezingMassFraction, ...],
    *,
    station_elev_m: Mapping[Station, float],
) -> ElevationBandMassFraction:
    """`members` is a plain tuple, station identity read off each member's
    own (intrinsic) `.station` — never a caller-aligned
    `Mapping[Station, ...]` (the same fix `ma6_estimands.
    band_matched_hour_mean_difference` etc. already apply)."""
    return ElevationBandMassFraction(
        band=band, members=members, station_elev_m=station_elev_m
    )


# --- production wiring: D6 reuse of T2's elevation table + the published t2m bundle ---


def load_dhm_station_elevations(
    precip_data_root: Path,
) -> tuple[dict[Station, StationElevationInputs], str]:
    """D6 — reuses T2's own discovery and read of `station_grid_elevation.csv`
    (`ma6_lapse_check.discover_and_load_station_grid_elevation_table`) —
    never re-derives orography or station elevation. `datum_reconciled` is
    read as the CSV's OWN value (today `UNRECONCILED` for every row, D7),
    never hardcoded, so a future reconciled row surfaces without a code
    change here. Returns the referenced precipitation bundle's
    `extraction_identity` alongside, so a caller (T6) can record it without
    re-deriving discovery."""
    table, identity = _discover_and_load_elev_table(precip_data_root)
    elevations = {
        Station(str(row["station"])): StationElevationInputs(
            station_elev_m=float(row["station_elev_m"]),
            orography_elev_m=float(row["orography_elev_m"]),
            datum_reconciled=DatumReconciliationStatus(str(row["datum_reconciled"])),
        )
        for row in table.iter_rows(named=True)
    }
    return elevations, identity


def _read_t2m_nearest_frames(series_path: Path) -> dict[Station, pl.DataFrame]:
    """Reads `series_t2m_degc.nc` verbatim, ALL rows — unlike
    `ma6_pairs._read_era5_nearest_frames` (which finite-filters the
    precipitation side before pairing), this does NOT finite-filter: D13
    requires the unclassifiable share to be MEASURED, so a non-finite or
    absent t2m hour must survive into `SubFreezingMassFraction`'s join,
    not be dropped before it can be seen."""
    with xr.open_dataset(series_path, engine="h5netcdf") as ds:
        loaded = ds.load()
    valid_time = loaded["valid_time"].values
    frames: dict[Station, pl.DataFrame] = {}
    for station_name in loaded["station"].to_numpy():
        station = Station(str(station_name))
        values = (
            loaded[T2M_DATA_VARIABLE]
            .sel(station=station_name)
            .to_numpy()
            .astype("float64")
        )
        frames[station] = pl.DataFrame(
            {
                "station": [str(station)] * len(valid_time),
                "timestamp": valid_time,
                "grid_t2m_degc": values,
            }
        )
    return frames


def discover_and_load_t2m_frames(
    t2m_data_root: Path = DEFAULT_T2M_DATA_ROOT,
) -> tuple[dict[Station, pl.DataFrame], str]:
    """T5's own entry point named in the plan: `discover_t2m_bundle`
    (`extract_era5_t2m.py:452`) resolves the highest `NNNN` whose manifest
    validates — never a run-numbered path, never a glob on identity (P3) —
    then this reads that bundle's `series_t2m_degc.nc` verbatim. Returns
    the bundle's own `extraction_identity` alongside, for T6."""
    bundle_dir, manifest = discover_t2m_bundle(t2m_data_root)
    frames = _read_t2m_nearest_frames(bundle_dir / _T2M_SERIES_FILENAME)
    return frames, manifest.extraction_identity
