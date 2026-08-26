"""Plan 184 (M-A6) task T1 — the paired, masked substrate.

Seam tests: every assertion here is on a VALUE actually produced (a row
count, a set of retained months, a raised exception), never on an argument
passed to a mock — this track's own recurring failure mode
(`docs/plans/184-gauge-vs-era5land-comparison.md` T1's "Seam tests" note).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import pytest
import xarray as xr

from sapphire_flow.types.datetime import ensure_utc
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.era5_errors import (
    ExtractionInputAbsentError,
    ExtractionPostConditionError,
    StationSetMismatchError,
)
from scripts.dhm_precip.era5_extract_manifest import (
    ExtractionManifest,
    checksum_file,
    manifest_filename,
    points_root,
    write_extraction_manifest,
)
from scripts.dhm_precip.ma6_pairs import (
    Era5NearestSeries,
    GaugeMaskedPopulation,
    GaugeRetainedSubset,
    MaskedGaugeSeries,
    PairedRetainedSubset,
    PairedSeries,
    RetainedSubsetSchemaError,
    Scale,
    ScaleConsistencyError,
    StationIdentityError,
    _read_era5_nearest_frames,
    build_gauge_masked_population,
    build_paired_population,
    discover_precip_bundle,
    pair_with_era5,
    subset,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS

if TYPE_CHECKING:
    from pathlib import Path

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


def _on_grid_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))


def _with_station(frame: pl.DataFrame, station: Station) -> pl.DataFrame:
    """Every `MaskedGaugeSeries`/`PairedSeries`/`Era5NearestSeries`/subset
    frame now carries its OWN `station` column — `.station` is derived from
    it, never a separately-suppliable constructor field (Plan 184 phase 2
    round 2). Test fixtures build a bare frame and stamp the column on with
    this helper, exactly as production code does in `build_gauge_masked_
    population`/`_read_era5_nearest_frames`."""
    return frame.with_columns(pl.lit(str(station)).alias("station"))


def _hourly_rows(
    station: str, start: datetime, n_hours: int, values: list[float]
) -> list[dict[str, object]]:
    assert len(values) == n_hours
    return [
        {
            "source_row_index": i,
            "station": station,
            "timestamp": start + timedelta(hours=i),
            "value_mm": values[i],
        }
        for i in range(n_hours)
    ]


_VARYING_PATTERN = (1.0, 1.4, 1.8, 1.2)


def _varying_values(
    n_hours: int, *, zero_run_start: int | None = None, zero_run_length: int = 0
) -> list[float]:
    """A repeating, never-constant pattern (all well under the 5.0
    stuck-value floor) — a CONSTANT value across the whole window would
    itself form a long flat run once JJAS-scoped Pass B sees enough hours
    of it (`qc_ruleset.py`: 'no exclusion floor — every value is eligible,
    so this is really "any long flat run"'), which is not what these
    fixtures are testing. An optional exact-zero block injects a REAL zero-
    run defect signature on top of the safe background."""
    values = [_VARYING_PATTERN[i % len(_VARYING_PATTERN)] for i in range(n_hours)]
    if zero_run_start is not None:
        for i in range(zero_run_start, zero_run_start + zero_run_length):
            values[i] = 0.0
    return values


class TestBuildGaugeMaskedPopulationIsSeasonAgnostic:
    def test_retained_frame_spans_more_than_one_calendar_season(self) -> None:
        # Feb 27 -> Mar 2 (2024, leap year, DJF->MAM boundary) plus a short
        # July window (JJAS) — the JJAS hours matter here for a second
        # reason: D11's exclusion predicate treats a station with ZERO
        # observed JJAS hours as excluded (`NO_OBSERVED_JJAS_HOURS`), so a
        # Feb/Mar-only fixture would vanish from `by_station` entirely
        # before the season-agnostic assertion below could even run.
        djf_mam_start = datetime(2024, 2, 27, 0)
        djf_mam_hours = 24 * 5
        jjas_start = datetime(2024, 7, 1, 0)
        jjas_hours = 6
        on_grid = pl.concat(
            [
                _on_grid_frame(
                    _hourly_rows(
                        "A",
                        djf_mam_start,
                        djf_mam_hours,
                        _varying_values(djf_mam_hours),
                    )
                ),
                _on_grid_frame(
                    _hourly_rows(
                        "A", jjas_start, jjas_hours, _varying_values(jjas_hours)
                    )
                ),
            ]
        )

        population = build_gauge_masked_population(
            on_grid, live_stations=frozenset({Station("A")}), now=_NOW
        )

        frame = population.by_station[Station("A")].frame
        months = set(frame["timestamp"].dt.month().to_list())
        assert months == {2, 3, 7}, (
            "a season-agnostic accessor must retain February (DJF), March "
            "(MAM) AND July (JJAS) hours together — a JJAS-only provider "
            "(the old coloc_run.py behaviour this generalises) would keep "
            "only 7"
        )
        assert frame.height == djf_mam_hours + jjas_hours


class TestBuildGaugeMaskedPopulationExclusionList:
    """D11 — the exclusion list is CONSUMED (`qc_mask.build_exclusion_list`),
    never re-derived, and its emptiness on a given delivery must be a
    MEASURED result (accounting rows exist) rather than a skipped step
    (accounting rows absent)."""

    @staticmethod
    def _two_station_on_grid() -> pl.DataFrame:
        # A 72-hour JJAS window (July 2024). Station B carries a 20-hour
        # exact-zero run on top of the safe varying background — a real
        # defect signature, just shorter than the production 168h
        # threshold so the fixture stays small; the test overrides
        # `qc_mask_long_zero_run_min_consecutive_hours` to 20 so the SAME
        # rule (never re-derived) fires on it. Station A carries no defect.
        start = datetime(2024, 7, 1, 0)
        n_hours = 72
        a_values = _varying_values(n_hours)
        b_values = _varying_values(n_hours, zero_run_start=0, zero_run_length=20)
        return pl.concat(
            [
                _on_grid_frame(_hourly_rows("A", start, n_hours, a_values)),
                _on_grid_frame(_hourly_rows("B", start, n_hours, b_values)),
            ]
        )

    def test_default_threshold_measures_empty_but_accounting_is_non_empty(
        self,
    ) -> None:
        # The defect removes 20/72 JJAS hours from B (retained fraction
        # ~0.722) — comfortably above the production 0.50 floor, so nothing
        # is excluded. This is the real-delivery shape (D8/D11: "expect this
        # to be EMPTY on real data"): `excluded == ()` must be paired with
        # non-empty `accounting`, proving the computation ran rather than
        # being skipped.
        params = replace(DEFAULT_PARAMS, qc_mask_long_zero_run_min_consecutive_hours=20)
        on_grid = self._two_station_on_grid()

        population = build_gauge_masked_population(
            on_grid,
            live_stations=frozenset({Station("A"), Station("B")}),
            params=params,
            now=_NOW,
        )

        assert population.excluded == ()
        assert len(population.accounting) > 0, (
            "an empty exclusion list next to EMPTY accounting would be "
            "indistinguishable from 'not computed' — accounting rows are "
            "the evidence the computation actually ran"
        )
        assert set(population.by_station) == {Station("A"), Station("B")}

    def test_a_tightened_threshold_excludes_the_defective_station(self) -> None:
        # Same fixture, same rule, only the RETENTION FLOOR is tightened
        # (0.90) — proving the exclusion mechanism has teeth: it is not
        # merely a pass-through that always returns empty.
        params = replace(
            DEFAULT_PARAMS,
            qc_mask_long_zero_run_min_consecutive_hours=20,
            minimum_jjas_retained_fraction=0.90,
        )
        on_grid = self._two_station_on_grid()

        population = build_gauge_masked_population(
            on_grid,
            live_stations=frozenset({Station("A"), Station("B")}),
            params=params,
            now=_NOW,
        )

        excluded_stations = {entry.station for entry in population.excluded}
        assert excluded_stations == {Station("B")}
        assert Station("B") not in population.by_station, (
            "D11: an excluded station must not appear in the population "
            "later tasks consume"
        )
        assert Station("A") in population.by_station


class TestPairWithEra5DropsHoursEra5Lacks:
    """D2 — pairing on COMMONLY-retained timestamps, never gauge-only."""

    def test_an_hour_the_gauge_mask_alone_would_have_kept_is_dropped(self) -> None:
        t0 = datetime(2024, 7, 1, 0)
        t1 = datetime(2024, 7, 1, 1)
        t2 = datetime(2024, 7, 1, 2)
        gauge = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame(
                    {
                        "timestamp": [t0, t1, t2],
                        "value_mm": [1.0, 2.0, 3.0],
                    }
                ).with_columns(pl.col("timestamp").cast(pl.Datetime("ms"))),
                Station("A"),
            ),
        )
        # ERA5 lacks t2 entirely (already finite-filtered by the caller, as
        # `_read_era5_nearest_frames` would have done).
        era5 = Era5NearestSeries(
            frame=_with_station(
                pl.DataFrame(
                    {
                        "timestamp": [t0, t1],
                        "era5_nearest_mm_per_h": [0.5, 0.6],
                    }
                ).with_columns(pl.col("timestamp").cast(pl.Datetime("ns"))),
                Station("A"),
            ),
        )

        paired = pair_with_era5(gauge, era5)

        assert paired.frame["timestamp"].to_list() == [t0, t1]
        assert gauge.frame.height == 3, (
            "the gauge-only population must still carry t2 — only the "
            "PAIRED frame narrows on the common timestamps"
        )
        assert paired.frame.height == 2

    def test_a_station_mismatch_between_gauge_and_era5_is_rejected(self) -> None:
        # Plan 184 phase 2 round 2, change 3: the ERA5 side carries its OWN
        # identity now — a Station-B ERA5 frame must never silently pair
        # with Station-A gauge data and emit as A.
        t0 = datetime(2024, 7, 1, 0)
        gauge = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame({"timestamp": [t0], "value_mm": [1.0]}).with_columns(
                    pl.col("timestamp").cast(pl.Datetime("ms"))
                ),
                Station("A"),
            ),
        )
        era5 = Era5NearestSeries(
            frame=_with_station(
                pl.DataFrame(
                    {"timestamp": [t0], "era5_nearest_mm_per_h": [0.5]}
                ).with_columns(pl.col("timestamp").cast(pl.Datetime("ns"))),
                Station("B"),
            ),
        )

        with pytest.raises(StationSetMismatchError, match="mismatched station"):
            pair_with_era5(gauge, era5)


class TestSubsetComputesItsOwnN:
    """Finding 1 (Plan 184 T1 review, 2026-08-20): the gauge-retained and
    commonly-retained counts are DISTINCT TYPES (`GaugeRetainedSubset` /
    `PairedRetainedSubset`), never one `RetainedSubset` type reporting both
    under the same `n_common_retained` name."""

    def test_a_gauge_subsets_n_differs_from_the_full_frame_n(self) -> None:
        july_hours = [datetime(2024, 7, 1, h) for h in range(6)]
        january_hours = [datetime(2024, 1, 1, h) for h in range(4)]
        gauge = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame(
                    {
                        "timestamp": july_hours + january_hours,
                        "value_mm": [1.0] * 10,
                    }
                ),
                Station("A"),
            ),
        )

        # MONTHLY carries no season-consistency check (unlike JJAS/DJF), so
        # a predicate that spans two calendar months is legitimately
        # sliceable at this scale — the point of this test is n, not scale.
        full = subset(gauge, pl.lit(True), scale=Scale.MONTHLY)
        july_only = subset(gauge, pl.col("timestamp").dt.month() == 7, scale=Scale.JJAS)

        assert isinstance(full, GaugeRetainedSubset)
        assert full.n_gauge_retained == 10
        assert july_only.n_gauge_retained == 6
        assert july_only.n_gauge_retained != full.n_gauge_retained, (
            "if a subset's n always equalled the series-level n the "
            "per-subset requirement would be vacuous (Exit 1/4)"
        )

    def test_a_paired_subsets_n_is_the_common_retained_count(self) -> None:
        july_hours = [datetime(2024, 7, 1, h) for h in range(6)]
        january_hours = [datetime(2024, 1, 1, h) for h in range(4)]
        paired = PairedSeries(
            frame=_with_station(
                pl.DataFrame(
                    {
                        "timestamp": july_hours + january_hours,
                        "gauge_value_mm": [1.0] * 10,
                        "era5_nearest_mm_per_h": [1.0] * 10,
                    }
                ),
                Station("A"),
            ),
        )

        full = subset(paired, pl.lit(True), scale=Scale.MONTHLY)
        july_only = subset(
            paired, pl.col("timestamp").dt.month() == 7, scale=Scale.JJAS
        )

        assert isinstance(full, PairedRetainedSubset)
        assert full.n_common_retained == 10
        assert july_only.n_common_retained == 6

    def test_n_cannot_diverge_from_the_subsets_own_rows(self) -> None:
        # Both n_gauge_retained and n_common_retained are PROPERTIES, not
        # fields: there is no constructor path that lets a caller attach a
        # mismatched n. Scale.MONTHLY carries no season-consistency check,
        # so plain integer timestamps (irrelevant to this test) are fine.
        gauge = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame({"timestamp": [1, 2, 3], "value_mm": [1.0, 2.0, 3.0]}),
                Station("A"),
            ),
        )
        result = subset(gauge, pl.col("timestamp") <= 2, scale=Scale.MONTHLY)
        assert result.n_gauge_retained == result.frame.height == 2

    def test_a_gauge_subset_does_not_carry_the_paired_count(self) -> None:
        # The two estimands are different TYPES, not one type wearing two
        # names: a gauge-retained subset has no `n_common_retained` at all
        # (runtime confirmation of the static guarantee proven separately
        # with pyright), so a caller cannot read gauge exposure as if it
        # were commonly-retained exposure.
        gauge = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame({"timestamp": [1, 2], "value_mm": [1.0, 2.0]}),
                Station("A"),
            ),
        )
        gauge_subset = subset(gauge, pl.lit(True), scale=Scale.MONTHLY)

        assert not hasattr(gauge_subset, "n_common_retained")


class TestSubsetCarriesItsOwnIdentity:
    """Round 2 (Plan 184 phase 2, 2026-08-26): `station` is no longer
    stamped by `subset()` at all — it is derived, lazily, from the result
    frame's own `station` column (filtering preserves it). `scale` remains
    a plain field, stamped from `subset()`'s own argument and VERIFIED
    against the frame's timestamps (`TestScaleConsistency` below)."""

    def test_gauge_subset_carries_the_series_station_and_the_call_scale(self) -> None:
        gauge = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame({"timestamp": [1, 2, 3], "value_mm": [1.0, 2.0, 3.0]}),
                Station("Kirtipur"),
            ),
        )

        result = subset(gauge, pl.lit(True), scale=Scale.MONTHLY)

        assert result.station == Station("Kirtipur")
        assert result.scale is Scale.MONTHLY

    def test_paired_subset_carries_the_series_station_and_the_call_scale(self) -> None:
        paired = PairedSeries(
            frame=_with_station(
                pl.DataFrame(
                    {
                        "timestamp": [1, 2, 3],
                        "gauge_value_mm": [1.0, 2.0, 3.0],
                        "era5_nearest_mm_per_h": [0.1, 0.2, 0.3],
                    }
                ),
                Station("Khumaltar"),
            ),
        )

        result = subset(paired, pl.lit(True), scale=Scale.MONTHLY)

        assert result.station == Station("Khumaltar")
        assert result.scale is Scale.MONTHLY

    def test_two_stations_sliced_the_same_way_carry_different_identities(self) -> None:
        # The station a subset carries comes from the FRAME it was taken
        # from, never from a shared default — two different series produce
        # subsets that disagree on station even under the identical
        # predicate/scale.
        gauge_a = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame({"timestamp": [1, 2], "value_mm": [1.0, 2.0]}),
                Station("A"),
            ),
        )
        gauge_b = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame({"timestamp": [1, 2], "value_mm": [1.0, 2.0]}),
                Station("B"),
            ),
        )

        subset_a = subset(gauge_a, pl.lit(True), scale=Scale.MONTHLY)
        subset_b = subset(gauge_b, pl.lit(True), scale=Scale.MONTHLY)

        assert subset_a.station != subset_b.station
        assert subset_a.station == Station("A")
        assert subset_b.station == Station("B")

    def test_subset_rejects_a_paired_retained_subset_passed_back_in(self) -> None:
        # Closes the duck-typing hole: a PairedRetainedSubset also carries a
        # `.frame`/`.station`, so without an explicit isinstance check
        # `subset()` would silently "re-subset" an already-built subset and
        # have it come out looking freshly taken — exactly what would let
        # an unconditioned subset be laundered into looking conditioned.
        paired = PairedSeries(
            frame=_with_station(
                pl.DataFrame(
                    {
                        "timestamp": [1, 2],
                        "gauge_value_mm": [1.0, 2.0],
                        "era5_nearest_mm_per_h": [0.1, 0.2],
                    }
                ),
                Station("A"),
            ),
        )
        already_built = subset(paired, pl.lit(True), scale=Scale.MONTHLY)

        with pytest.raises(TypeError, match="MaskedGaugeSeries or PairedSeries"):
            subset(already_built, pl.lit(True), scale=Scale.DAILY)  # type: ignore[type-var]


class TestStationIdentityIsDerivedNeverStated:
    """Change 1 (Plan 184 phase 2 round 2): `station` is no longer a
    constructor field ANYWHERE in this module — attempting to supply one is
    a `TypeError`, and the derived property itself refuses a frame that
    does not resolve to exactly one distinct station."""

    def test_masked_gauge_series_rejects_a_station_kwarg(self) -> None:
        with pytest.raises(TypeError, match="station"):
            MaskedGaugeSeries(
                station=Station("A"),  # type: ignore[call-arg]
                frame=_with_station(
                    pl.DataFrame({"timestamp": [1], "value_mm": [1.0]}), Station("A")
                ),
            )

    def test_paired_series_rejects_a_station_kwarg(self) -> None:
        with pytest.raises(TypeError, match="station"):
            PairedSeries(
                station=Station("A"),  # type: ignore[call-arg]
                frame=_with_station(
                    pl.DataFrame(
                        {
                            "timestamp": [1],
                            "gauge_value_mm": [1.0],
                            "era5_nearest_mm_per_h": [0.1],
                        }
                    ),
                    Station("A"),
                ),
            )

    def test_masked_gauge_series_with_two_stations_in_its_frame_is_unconstructible(
        self,
    ) -> None:
        # A frame mixing two stations' rows genuinely has no single station
        # identity — construction itself must refuse it, eagerly (this type
        # is never expected to be legitimately empty or multi-station).
        mixed = pl.DataFrame(
            {
                "station": ["A", "B"],
                "timestamp": [1, 2],
                "value_mm": [1.0, 2.0],
            }
        )
        with pytest.raises(StationIdentityError, match="exactly one distinct station"):
            MaskedGaugeSeries(frame=mixed)

    def test_masked_gauge_series_with_no_station_column_is_unconstructible(
        self,
    ) -> None:
        no_station_col = pl.DataFrame({"timestamp": [1], "value_mm": [1.0]})
        with pytest.raises(StationIdentityError, match="'station' column"):
            MaskedGaugeSeries(frame=no_station_col)

    def test_a_frame_with_a_null_station_value_is_unconstructible(self) -> None:
        # A2 (Plan 184 T3 round 7): a single ALL-NULL station column passes
        # the "exactly one distinct value" count check (the one distinct
        # value is None), so `Station(str(None))` would otherwise silently
        # mint the literal station "None" — a null is not a degenerate but
        # real station identity, and must be rejected the same way an
        # absent/multi-valued column already is.
        null_station = pl.DataFrame(
            {
                "station": pl.Series("station", [None], dtype=pl.Utf8),
                "timestamp": [1],
                "value_mm": [1.0],
            }
        )
        with pytest.raises(StationIdentityError, match="null or empty"):
            MaskedGaugeSeries(frame=null_station)

    def test_a_frame_with_an_empty_string_station_value_is_unconstructible(
        self,
    ) -> None:
        blank_station = _with_station(
            pl.DataFrame({"timestamp": [1], "value_mm": [1.0]}), Station("")
        )
        with pytest.raises(StationIdentityError, match="null or empty"):
            MaskedGaugeSeries(frame=blank_station)

    def test_a_gauge_retained_subset_filtered_to_zero_rows_stays_constructible(
        self,
    ) -> None:
        # A legitimately empty subset (e.g. a predicate matching nothing)
        # must remain usable for its own n_gauge_retained == 0 check — the
        # station derivation is LAZY, not enforced at construction, on the
        # two subset types (unlike MaskedGaugeSeries/PairedSeries above).
        gauge = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame({"timestamp": [1, 2], "value_mm": [1.0, 2.0]}),
                Station("A"),
            ),
        )

        empty_subset = subset(gauge, pl.lit(False), scale=Scale.MONTHLY)  # noqa: FBT003

        assert empty_subset.n_gauge_retained == 0

    def test_reading_station_on_a_zero_row_subset_raises(self) -> None:
        gauge = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame({"timestamp": [1, 2], "value_mm": [1.0, 2.0]}),
                Station("A"),
            ),
        )
        empty_subset = subset(gauge, pl.lit(False), scale=Scale.MONTHLY)  # noqa: FBT003

        with pytest.raises(StationIdentityError, match="exactly one distinct station"):
            _ = empty_subset.station


class TestScaleConsistencyIsVerified:
    """Change 2 (Plan 184 phase 2 round 2): `scale` cannot be derived the
    way `station` is, but IS exactly checkable against the frame's own
    timestamps — `GaugeRetainedSubset`/`PairedRetainedSubset` verify it in
    `__post_init__`."""

    def test_jjas_scale_rejects_a_frame_carrying_a_non_jjas_month(self) -> None:
        gauge = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame(
                    {
                        "timestamp": [
                            datetime(2024, 7, 1),
                            datetime(2024, 1, 1),  # January — not JJAS
                        ],
                        "value_mm": [1.0, 2.0],
                    }
                ),
                Station("A"),
            ),
        )

        with pytest.raises(ScaleConsistencyError, match="JJAS"):
            subset(gauge, pl.lit(True), scale=Scale.JJAS)

    def test_djf_scale_rejects_a_frame_carrying_a_non_djf_month(self) -> None:
        paired = PairedSeries(
            frame=_with_station(
                pl.DataFrame(
                    {
                        "timestamp": [
                            datetime(2024, 12, 1),
                            datetime(2024, 7, 1),  # July — not DJF
                        ],
                        "gauge_value_mm": [1.0, 2.0],
                        "era5_nearest_mm_per_h": [0.1, 0.2],
                    }
                ),
                Station("A"),
            ),
        )

        with pytest.raises(ScaleConsistencyError, match="DJF"):
            subset(paired, pl.lit(True), scale=Scale.DJF)

    def test_jjas_scale_accepts_a_frame_whose_months_are_all_jjas(self) -> None:
        gauge = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame(
                    {
                        "timestamp": [
                            datetime(2024, 6, 1),
                            datetime(2024, 9, 30),
                        ],
                        "value_mm": [1.0, 2.0],
                    }
                ),
                Station("A"),
            ),
        )

        result = subset(gauge, pl.lit(True), scale=Scale.JJAS)

        assert result.n_gauge_retained == 2

    def test_daily_and_monthly_scales_have_no_month_restriction(self) -> None:
        # DAILY/MONTHLY are D12's categorical grain, reported over the
        # whole record — a frame spanning every calendar month is
        # legitimately DAILY/MONTHLY-consistent.
        gauge = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame(
                    {
                        "timestamp": [datetime(2024, m, 1) for m in range(1, 13)],
                        "value_mm": [1.0] * 12,
                    }
                ),
                Station("A"),
            ),
        )

        daily = subset(gauge, pl.lit(True), scale=Scale.DAILY)
        monthly = subset(gauge, pl.lit(True), scale=Scale.MONTHLY)

        assert daily.n_gauge_retained == 12
        assert monthly.n_gauge_retained == 12

    def test_an_empty_frame_is_vacuously_consistent_with_any_scale(self) -> None:
        # Deliberate decision (not an accident): an empty subset (e.g. a
        # predicate matching nothing) is NOT rejected by the scale check —
        # its own EmptySubsetError, raised downstream by ma6_estimands.py's
        # factories, is where emptiness is flagged, not this guard.
        gauge = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame({"timestamp": [datetime(2024, 7, 1)], "value_mm": [1.0]}),
                Station("A"),
            ),
        )

        result = subset(gauge, pl.lit(False), scale=Scale.DJF)  # noqa: FBT003

        assert result.n_gauge_retained == 0


class TestRetainedSubsetSchemaGuard:
    """The typing hole `subset()`'s `@overload` cannot close: direct
    construction bypasses it entirely, since both frames are just
    `pl.DataFrame` to pyright. `__post_init__` on both subset types makes
    the mismatch a runtime error instead of a silent estimand swap."""

    def test_a_paired_frame_is_rejected_by_gauge_retained_subset(self) -> None:
        paired_frame = pl.DataFrame(
            {
                "timestamp": [1, 2, 3],
                "gauge_value_mm": [1.0, 2.0, 3.0],
                "era5_nearest_mm_per_h": [0.1, 0.2, 0.3],
            }
        )

        with pytest.raises(RetainedSubsetSchemaError, match="era5_nearest_mm_per_h"):
            GaugeRetainedSubset(frame=paired_frame, scale=Scale.MONTHLY)

    def test_a_gauge_frame_is_rejected_by_paired_retained_subset(self) -> None:
        gauge_frame = pl.DataFrame(
            {"timestamp": [1, 2, 3], "value_mm": [1.0, 2.0, 3.0]}
        )

        with pytest.raises(RetainedSubsetSchemaError, match="era5_nearest_mm_per_h"):
            PairedRetainedSubset(frame=gauge_frame, scale=Scale.MONTHLY)

    def test_a_gauge_frame_missing_the_station_column_is_rejected(self) -> None:
        # Change 1's schema guard, eager at construction — distinct from
        # StationIdentityError, which the LAZY `.station` property raises
        # only when actually read.
        no_station_col = pl.DataFrame(
            {"timestamp": [1, 2, 3], "value_mm": [1.0, 2.0, 3.0]}
        )

        with pytest.raises(RetainedSubsetSchemaError, match="'station' column"):
            GaugeRetainedSubset(frame=no_station_col, scale=Scale.MONTHLY)

    def test_a_paired_frame_missing_the_station_column_is_rejected(self) -> None:
        no_station_col = pl.DataFrame(
            {
                "timestamp": [1, 2, 3],
                "gauge_value_mm": [1.0, 2.0, 3.0],
                "era5_nearest_mm_per_h": [0.1, 0.2, 0.3],
            }
        )

        with pytest.raises(RetainedSubsetSchemaError, match="'station' column"):
            PairedRetainedSubset(frame=no_station_col, scale=Scale.MONTHLY)


class TestGaugeMaskedPopulationVerifiesKeyIdentity:
    """A3 (Plan 184 T3 round 7): a consumer that looks a series up by its
    `by_station` KEY (e.g. `ma6_representativeness.compute_within_cell_
    pair`) is trusting that key, not the series' own data, unless
    `GaugeMaskedPopulation.__post_init__` verifies the two agree — fixed
    ONCE here for every such consumer, not at one call site."""

    def test_a_series_filed_under_the_wrong_station_key_is_rejected(self) -> None:
        series_for_b = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame({"timestamp": [1], "value_mm": [1.0]}), Station("B")
            ),
        )

        with pytest.raises(StationIdentityError, match="'A'"):
            GaugeMaskedPopulation(
                by_station={Station("A"): series_for_b},  # key says A, data says B
                excluded=(),
                accounting=(),
            )

    def test_a_correctly_keyed_population_is_unaffected(self) -> None:
        series_for_a = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame({"timestamp": [1], "value_mm": [1.0]}), Station("A")
            ),
        )

        population = GaugeMaskedPopulation(
            by_station={Station("A"): series_for_a}, excluded=(), accounting=()
        )

        assert population.by_station[Station("A")].station == Station("A")


def _write_series_nearest_nc(
    path: Path,
    *,
    stations: list[str],
    valid_time: list[datetime],
    values: dict[str, list[float]],
) -> None:
    vt = np.array(valid_time, dtype="datetime64[ns]")
    arr = np.array([values[s] for s in stations], dtype=np.float32)
    ds = xr.Dataset(
        {"precipitation_mm_per_h": (["station", "valid_time"], arr)},
        coords={"station": stations, "valid_time": vt},
    )
    ds["valid_time"].attrs["timezone"] = "UTC"
    ds["precipitation_mm_per_h"].attrs["units"] = "mm h-1"
    ds.to_netcdf(path, engine="h5netcdf")


class TestReadEra5NearestFrames:
    def test_non_finite_values_are_filtered_out_per_station(
        self, tmp_path: Path
    ) -> None:
        valid_time = [datetime(2024, 7, 1, h) for h in range(3)]
        path = tmp_path / "series_nearest.nc"
        _write_series_nearest_nc(
            path,
            stations=["A", "B"],
            valid_time=valid_time,
            values={
                "A": [1.0, float("nan"), 3.0],
                "B": [0.0, 0.0, 0.0],
            },
        )

        series = _read_era5_nearest_frames(path)

        assert series[Station("A")].frame.height == 2
        assert series[Station("B")].frame.height == 3
        assert series[Station("A")].station == Station("A")
        assert series[Station("B")].station == Station("B")


class TestBuildPairedPopulation:
    def test_raises_when_a_gauge_station_is_absent_from_the_era5_bundle(
        self, tmp_path: Path
    ) -> None:
        valid_time = [datetime(2024, 7, 1, h) for h in range(3)]
        path = tmp_path / "series_nearest.nc"
        _write_series_nearest_nc(
            path,
            stations=["A"],
            valid_time=valid_time,
            values={"A": [1.0, 2.0, 3.0]},
        )
        gauge_only_b = GaugeMaskedPopulation(
            by_station={
                Station("B"): MaskedGaugeSeries(
                    frame=_with_station(
                        pl.DataFrame(
                            {"timestamp": valid_time, "value_mm": [1.0, 2.0, 3.0]}
                        ).with_columns(pl.col("timestamp").cast(pl.Datetime("ms"))),
                        Station("B"),
                    ),
                )
            },
            excluded=(),
            accounting=(),
        )

        with pytest.raises(StationSetMismatchError, match="'B'"):
            build_paired_population(gauge_only_b, tmp_path)

    def test_pairs_every_retained_station_against_the_bundle(
        self, tmp_path: Path
    ) -> None:
        valid_time = [datetime(2024, 7, 1, h) for h in range(3)]
        path = tmp_path / "series_nearest.nc"
        _write_series_nearest_nc(
            path,
            stations=["A"],
            valid_time=valid_time,
            values={"A": [1.0, 2.0, 3.0]},
        )
        gauge_population = GaugeMaskedPopulation(
            by_station={
                Station("A"): MaskedGaugeSeries(
                    frame=_with_station(
                        pl.DataFrame(
                            {"timestamp": valid_time, "value_mm": [10.0, 20.0, 30.0]}
                        ).with_columns(pl.col("timestamp").cast(pl.Datetime("ms"))),
                        Station("A"),
                    ),
                )
            },
            excluded=(),
            accounting=(),
        )

        paired = build_paired_population(gauge_population, tmp_path)

        assert set(paired) == {Station("A")}
        assert isinstance(paired[Station("A")], PairedSeries)
        assert paired[Station("A")].frame.height == 3
        assert paired[Station("A")].frame["gauge_value_mm"].to_list() == [
            10.0,
            20.0,
            30.0,
        ]


_MANIFEST_BASE_KWARGS: dict[str, object] = {
    "orography_identity": "o",
    "operator_id": "NEAREST",
    "coordinate_table_sha256": "a" * 64,
    "source_sha256s_by_year": {"2024": "b" * 64},
    "orography_spec": {},
    "orography_source_record": {},
    "accumulation_diagnostic": {},
    "generated_at": datetime(2026, 1, 1, tzinfo=UTC),
}


def _publish_fake_bundle(root: Path, *, run_number: str, identity: str) -> Path:
    directory = points_root(root) / f"{run_number}-{identity}"
    directory.mkdir(parents=True)
    payload = directory / "series_nearest.nc"
    payload.write_bytes(f"payload-{identity}".encode())
    write_extraction_manifest(
        ExtractionManifest(
            extraction_identity=identity,
            payload_sha256s={"series_nearest.nc": checksum_file(payload)},
            **_MANIFEST_BASE_KWARGS,
        ),
        directory / manifest_filename(),
    )
    return directory


class TestDiscoverPrecipBundle:
    def test_raises_when_the_points_root_is_absent(self, tmp_path: Path) -> None:
        with pytest.raises(ExtractionInputAbsentError):
            discover_precip_bundle(tmp_path)

    def test_returns_the_highest_numbered_valid_candidate(self, tmp_path: Path) -> None:
        _publish_fake_bundle(tmp_path, run_number="0000", identity="ident0")
        newest = _publish_fake_bundle(tmp_path, run_number="0001", identity="ident1")

        directory, manifest = discover_precip_bundle(tmp_path)

        assert directory == newest
        assert manifest.extraction_identity == "ident1"

    def test_a_checksum_mismatch_on_the_highest_candidate_raises_hard(
        self, tmp_path: Path
    ) -> None:
        # Mirrors `extract_era5_t2m._discover_precip_bundle`: there is
        # exactly one precipitation bundle in play, so a checksum failure
        # on the highest candidate must raise, never silently fall back to
        # an older one.
        directory = _publish_fake_bundle(tmp_path, run_number="0000", identity="ident0")
        (directory / "series_nearest.nc").write_bytes(b"tampered")

        with pytest.raises(ExtractionPostConditionError):
            discover_precip_bundle(tmp_path)


if __name__ == "__main__":
    pytest.main([__file__])
