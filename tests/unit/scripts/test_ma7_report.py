"""Plan 193 (M-A7) task T4 — the report and Exit.

Seam tests: every assertion is on a VALUE actually produced (a rendered
line, a bootstrap outcome, a raised exception), never on an argument passed
to a mock (`ma6_pairs`'s own "Seam tests" convention, reused here).

Bootstrap resample counts are overridden down from the production default
(2000) via `dataclasses.replace(DEFAULT_PARAMS, ...)` — the mechanics under
test (seeding, refusal rendering, adequacy labelling) do not depend on the
resample count, and a small count keeps this suite fast.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.ma6_estimands import ElevationBand
from scripts.dhm_precip.ma6_pairs import GaugeMaskedPopulation, MaskedGaugeSeries
from scripts.dhm_precip.ma7_run import (
    REPORT_SEED,
    BootstrapRefusal,
    Ma7Inputs,
    MissingStationMetadataError,
    _write_report,
    run_ma7_report,
)
from scripts.dhm_precip.ma7_transfer import DECLINED_ATTRIBUTION, ResolutionGroupLabel
from scripts.dhm_precip.params import DEFAULT_PARAMS
from scripts.dhm_precip.seasons import Season

_STATION_A = Station("Alpha")  # < 700 m
_STATION_B = Station("Beta")  # 700-2000 m
_STATION_OLANG = Station("Olangchunggola")  # >= 3000 m, D7

_ELEV_M = {_STATION_A: 500.0, _STATION_B: 1500.0, _STATION_OLANG: 3200.0}
_GROUPS: dict[Station, ResolutionGroupLabel] = {
    _STATION_A: "A",
    _STATION_B: "A",
    _STATION_OLANG: "B",
}

_SEASON_REFERENCE_DAY = {
    Season.MAM: (4, 15),
    Season.JJAS: (7, 15),
    Season.ON: (10, 15),
    Season.DJF: (1, 15),
}

_TEST_PARAMS = replace(DEFAULT_PARAMS, ma7_bootstrap_resamples=50)


def _series(station: Station, rows: list[dict[str, object]]) -> MaskedGaugeSeries:
    frame = pl.DataFrame(rows).with_columns(
        pl.lit(str(station)).alias("station"),
        pl.col("timestamp").cast(pl.Datetime("us")),
    )
    return MaskedGaugeSeries(frame=frame.select("station", "timestamp", "value_mm"))


def _diurnal_series(
    station: Station,
    *,
    years: range,
    peak_hour: int,
    seasons: tuple[Season, ...] = (Season.MAM, Season.JJAS, Season.ON, Season.DJF),
) -> MaskedGaugeSeries:
    """One representative day per requested `seasons`, per year — a
    deterministic triangular diurnal shape peaking at `peak_hour`, wet
    (>= 0.2 mm/h) near the peak and dry away from it."""
    rows: list[dict[str, object]] = []
    for year in years:
        for season in seasons:
            month, day = _SEASON_REFERENCE_DAY[season]
            base = datetime(year, month, day)
            for hour in range(24):
                dist = min(abs(hour - peak_hour), 24 - abs(hour - peak_hour))
                value = round(max(0.0, 1.0 * (6 - dist)), 3)
                rows.append(
                    {"timestamp": base + timedelta(hours=hour), "value_mm": value}
                )
    return _series(station, rows)


def _inputs(
    *,
    series_by_station: dict[Station, MaskedGaugeSeries],
    elev_m: dict[Station, float] | None = None,
    groups: dict[Station, ResolutionGroupLabel] | None = None,
) -> Ma7Inputs:
    masked = GaugeMaskedPopulation(
        by_station=series_by_station, excluded=(), accounting=()
    )
    return Ma7Inputs(
        masked=masked,
        station_elev_m=elev_m if elev_m is not None else dict(_ELEV_M),
        station_resolution_group=groups if groups is not None else dict(_GROUPS),
        provenance_lines=("synthetic fixture — no production source",),
        params=_TEST_PARAMS,
    )


def _standard_inputs(*, years: range = range(2018, 2024)) -> Ma7Inputs:
    return _inputs(
        series_by_station={
            _STATION_A: _diurnal_series(_STATION_A, years=years, peak_hour=21),
            _STATION_B: _diurnal_series(_STATION_B, years=years, peak_hour=14),
            _STATION_OLANG: _diurnal_series(_STATION_OLANG, years=years, peak_hour=3),
        }
    )


def _clock() -> datetime:
    return datetime(2026, 8, 27, 12, 0, 0)


def _bimodal_by_year_series(
    station: Station, *, years: range, peak_a: int, peak_b: int
) -> MaskedGaugeSeries:
    """Alternating peak hour by year, EQUAL magnitude — so which hour
    argmaxes after a season-year bootstrap resample genuinely depends on
    which years the draw happened to pick, unlike a fixture whose every
    year shares one identical shape (where any resample composition
    reproduces the same peak regardless of the RNG)."""
    rows: list[dict[str, object]] = []
    for i, year in enumerate(years):
        peak_hour = peak_a if i % 2 == 0 else peak_b
        month, day = _SEASON_REFERENCE_DAY[Season.JJAS]
        base = datetime(year, month, day)
        for hour in range(24):
            dist = min(abs(hour - peak_hour), 24 - abs(hour - peak_hour))
            value = round(max(0.0, 1.0 * (6 - dist)), 3)
            rows.append({"timestamp": base + timedelta(hours=hour), "value_mm": value})
    return _series(station, rows)


class TestMa7InputsValidation:
    def test_missing_elevation_rejected(self) -> None:
        with pytest.raises(MissingStationMetadataError, match="elevation"):
            _inputs(
                series_by_station={
                    _STATION_A: _diurnal_series(
                        _STATION_A, years=range(2020, 2023), peak_hour=21
                    )
                },
                elev_m={},
                groups={_STATION_A: "A"},
            )

    def test_missing_resolution_group_rejected(self) -> None:
        with pytest.raises(MissingStationMetadataError, match="resolution group"):
            _inputs(
                series_by_station={
                    _STATION_A: _diurnal_series(
                        _STATION_A, years=range(2020, 2023), peak_hour=21
                    )
                },
                elev_m={_STATION_A: 500.0},
                groups={},
            )


class TestRunMa7ReportComposition:
    def test_every_station_and_season_has_a_profile_and_intensity(self) -> None:
        report = run_ma7_report(
            inputs=_standard_inputs(),
            rng=random.Random(REPORT_SEED),
            seed=REPORT_SEED,
            clock=_clock,
        )
        for station in (_STATION_A, _STATION_B, _STATION_OLANG):
            for season in (Season.MAM, Season.JJAS, Season.ON, Season.DJF):
                assert (station, season) in report.station_profiles
                assert (station, season) in report.station_intensities

    def test_every_populated_band_has_a_band_profile_and_intensity(self) -> None:
        report = run_ma7_report(
            inputs=_standard_inputs(),
            rng=random.Random(REPORT_SEED),
            seed=REPORT_SEED,
            clock=_clock,
        )
        for season in (Season.MAM, Season.JJAS, Season.ON, Season.DJF):
            assert (ElevationBand.BELOW_700M, season) in report.band_profiles
            assert (ElevationBand.B700_2000M, season) in report.band_profiles
            assert (ElevationBand.ABOVE_3000M, season) in report.band_profiles
            # No station in this fixture falls in 2,000-3,000 m.
            assert (ElevationBand.B2000_3000M, season) not in report.band_profiles

    def test_transferability_is_jjas_only(self) -> None:
        report = run_ma7_report(
            inputs=_standard_inputs(),
            rng=random.Random(REPORT_SEED),
            seed=REPORT_SEED,
            clock=_clock,
        )
        assert report.transferability.season == Season.JJAS
        assert set(report.transferability.by_elevation_band) == {
            ElevationBand.BELOW_700M,
            ElevationBand.B700_2000M,
            ElevationBand.ABOVE_3000M,
        }
        assert set(report.transferability.by_resolution_group) == {"A", "B"}


def _rng_sensitive_inputs() -> Ma7Inputs:
    """Every member station carries YEAR-VARYING data (`_bimodal_by_year_
    series`), unlike `_standard_inputs()` where every year shares one
    identical shape. With identical-shaped years, the bootstrap's argmax/
    quantile is invariant to WHICH years a resample draws, so a test built
    on `_standard_inputs()` cannot actually distinguish a correctly-seeded
    bootstrap from one that silently ignores its `rng` argument — proven by
    mutation testing an `rng=random.Random()` bypass, which that fixture did
    not catch. This fixture's per-year variation makes the resampled
    outcome genuinely depend on which years were drawn, so P1's tests
    below are sensitive to the injected RNG actually being used."""
    return _inputs(
        series_by_station={
            _STATION_A: _bimodal_by_year_series(
                _STATION_A, years=range(2018, 2024), peak_a=21, peak_b=9
            ),
            _STATION_B: _bimodal_by_year_series(
                _STATION_B, years=range(2018, 2024), peak_a=14, peak_b=2
            ),
            _STATION_OLANG: _bimodal_by_year_series(
                _STATION_OLANG, years=range(2018, 2024), peak_a=3, peak_b=15
            ),
        }
    )


class TestP1SeedReproducibility:
    def test_two_runs_with_the_same_seed_render_byte_identical_reports(
        self, tmp_path: Path
    ) -> None:
        inputs = _rng_sensitive_inputs()
        report_1 = run_ma7_report(
            inputs=inputs,
            rng=random.Random(REPORT_SEED),
            seed=REPORT_SEED,
            clock=_clock,
        )
        report_2 = run_ma7_report(
            inputs=inputs,
            rng=random.Random(REPORT_SEED),
            seed=REPORT_SEED,
            clock=_clock,
        )
        path_1 = tmp_path / "one.md"
        path_2 = tmp_path / "two.md"
        _write_report(path_1, report_1)
        _write_report(path_2, report_2)
        assert path_1.read_text() == path_2.read_text()

        # The rendered report only ever shows DERIVED aggregates (a spread,
        # a CI), which can coincidentally agree across independently-seeded
        # runs on some fixtures even when the RNG itself was NOT reused
        # (measured by mutation: swapping the injected `rng` for a fresh
        # `random.Random()` per call left this bimodal fixture's rendered
        # `spread_hours` unchanged in >98% of draws, since either peak
        # value alone already spans the full 12h gap). Asserting on the RAW
        # resampled-hours tuple is the sensitive proxy that actually proves
        # the SAME rng instance produced both runs.
        bootstrap_1 = report_1.station_profiles[_STATION_A, Season.JJAS].bootstrap
        bootstrap_2 = report_2.station_profiles[_STATION_A, Season.JJAS].bootstrap
        assert not isinstance(bootstrap_1, BootstrapRefusal)
        assert not isinstance(bootstrap_2, BootstrapRefusal)
        assert bootstrap_1.resampled_peak_hours == bootstrap_2.resampled_peak_hours

    def test_different_seeds_produce_different_resampled_spreads(self) -> None:
        inputs = _rng_sensitive_inputs()
        report_1 = run_ma7_report(
            inputs=inputs,
            rng=random.Random(REPORT_SEED),
            seed=REPORT_SEED,
            clock=_clock,
        )
        report_2 = run_ma7_report(
            inputs=inputs,
            rng=random.Random(REPORT_SEED + 1),
            seed=REPORT_SEED + 1,
            clock=_clock,
        )
        bootstrap_1 = report_1.station_profiles[_STATION_A, Season.JJAS].bootstrap
        bootstrap_2 = report_2.station_profiles[_STATION_A, Season.JJAS].bootstrap
        assert not isinstance(bootstrap_1, BootstrapRefusal)
        assert not isinstance(bootstrap_2, BootstrapRefusal)
        assert bootstrap_1.resampled_peak_hours != bootstrap_2.resampled_peak_hours

    def test_report_header_prints_the_seed(self, tmp_path: Path) -> None:
        report = run_ma7_report(
            inputs=_standard_inputs(),
            rng=random.Random(REPORT_SEED),
            seed=REPORT_SEED,
            clock=_clock,
        )
        path = tmp_path / "report.md"
        _write_report(path, report)
        text = path.read_text()
        assert f"`{REPORT_SEED}`" in text


class TestP4InadequateLabelledNeverSuppressed:
    def test_below_bar_station_still_appears_with_its_count_and_marker(
        self, tmp_path: Path
    ) -> None:
        inputs = _inputs(
            series_by_station={
                _STATION_A: _diurnal_series(
                    _STATION_A, years=range(2022, 2024), peak_hour=21
                ),  # 2 season-years, below the 5-season bar
                _STATION_B: _diurnal_series(
                    _STATION_B, years=range(2018, 2024), peak_hour=14
                ),
                _STATION_OLANG: _diurnal_series(
                    _STATION_OLANG, years=range(2018, 2024), peak_hour=3
                ),
            }
        )
        report = run_ma7_report(
            inputs=inputs,
            rng=random.Random(REPORT_SEED),
            seed=REPORT_SEED,
            clock=_clock,
        )
        bootstrap = report.station_profiles[_STATION_A, Season.JJAS].bootstrap
        assert not isinstance(bootstrap, BootstrapRefusal)
        assert bootstrap.n_season_years == 2
        assert bootstrap.adequate_sample is False

        path = tmp_path / "report.md"
        _write_report(path, report)
        assert "NO (< 5 season-years)" in path.read_text()

    def test_below_bar_station_quantile_ci_still_shows_its_count_and_marker(
        self, tmp_path: Path
    ) -> None:
        """Defect 2 regression: the intensity q50/q99 CI must be LABELLED
        with its `n_season_years` and adequacy marker (T4 P4), exactly as
        the peak-hour path already is — never rendered as if ordinary."""
        inputs = _inputs(
            series_by_station={
                _STATION_A: _diurnal_series(
                    _STATION_A, years=range(2022, 2024), peak_hour=21
                ),  # 2 season-years, below the 5-season bar
                _STATION_B: _diurnal_series(
                    _STATION_B, years=range(2018, 2024), peak_hour=14
                ),
                _STATION_OLANG: _diurnal_series(
                    _STATION_OLANG, years=range(2018, 2024), peak_hour=3
                ),
            }
        )
        report = run_ma7_report(
            inputs=inputs,
            rng=random.Random(REPORT_SEED),
            seed=REPORT_SEED,
            clock=_clock,
        )
        bootstrap = report.station_intensities[_STATION_A, Season.JJAS].q50_bootstrap
        assert not isinstance(bootstrap, BootstrapRefusal)
        assert bootstrap.n_season_years == 2
        assert bootstrap.adequate_sample is False

        path = tmp_path / "report.md"
        _write_report(path, report)
        text = path.read_text()
        # Not filtered — the row still appears — and labelled, not
        # rendered as if it were an ordinary (adequate) CI.
        assert "95% CI (n=2 season-years, NO (< 5 season-years))" in text


class TestP5RefusalRenders:
    def test_zero_season_years_becomes_a_refusal_not_a_crash(
        self, tmp_path: Path
    ) -> None:
        # Alpha carries no DJF data at all -> zero season-years for DJF.
        inputs = _inputs(
            series_by_station={
                _STATION_A: _diurnal_series(
                    _STATION_A,
                    years=range(2018, 2024),
                    peak_hour=21,
                    seasons=(Season.MAM, Season.JJAS, Season.ON),
                ),
                _STATION_B: _diurnal_series(
                    _STATION_B, years=range(2018, 2024), peak_hour=14
                ),
                _STATION_OLANG: _diurnal_series(
                    _STATION_OLANG, years=range(2018, 2024), peak_hour=3
                ),
            }
        )
        report = run_ma7_report(
            inputs=inputs,
            rng=random.Random(REPORT_SEED),
            seed=REPORT_SEED,
            clock=_clock,
        )
        bootstrap = report.station_profiles[_STATION_A, Season.DJF].bootstrap
        assert isinstance(bootstrap, BootstrapRefusal)
        assert "zero season-years" in bootstrap.reason

        path = tmp_path / "report.md"
        _write_report(path, report)
        text = path.read_text()
        assert "refused — zero season-years" in text
        # A refusal must never render as a blank cell or a literal "0.0".
        refusal_lines = [line for line in text.splitlines() if "refused —" in line]
        assert refusal_lines
        for line in refusal_lines:
            assert "| 0.0 |" not in line


class TestP3SpreadIsNotAConfidenceInterval:
    def test_peak_hour_spread_text_never_says_ci(self, tmp_path: Path) -> None:
        report = run_ma7_report(
            inputs=_standard_inputs(),
            rng=random.Random(REPORT_SEED),
            seed=REPORT_SEED,
            clock=_clock,
        )
        path = tmp_path / "report.md"
        _write_report(path, report)
        text = path.read_text()
        assert "resampled peak hours spanned a" in text
        assert "arc over" in text
        # Every occurrence of "CI" in the report belongs to a quantile line,
        # never a peak-hour spread line.
        for line in text.splitlines():
            if "resampled peak hours spanned" in line:
                assert "CI" not in line
                assert "confidence interval" not in line.lower()

    def test_quantile_bootstrap_is_labelled_a_ci(self, tmp_path: Path) -> None:
        report = run_ma7_report(
            inputs=_standard_inputs(),
            rng=random.Random(REPORT_SEED),
            seed=REPORT_SEED,
            clock=_clock,
        )
        path = tmp_path / "report.md"
        _write_report(path, report)
        text = path.read_text()
        assert "95% CI" in text


class TestD6DeclinedAttributionCarriedAsData:
    def test_report_carries_the_exact_pinned_refusal_text(self, tmp_path: Path) -> None:
        report = run_ma7_report(
            inputs=_standard_inputs(),
            rng=random.Random(REPORT_SEED),
            seed=REPORT_SEED,
            clock=_clock,
        )
        assert report.transferability.declined_attribution == DECLINED_ATTRIBUTION
        path = tmp_path / "report.md"
        _write_report(path, report)
        assert DECLINED_ATTRIBUTION in path.read_text()


class TestD7OlangchunggolaStatusReportedNotAdjudicated:
    def test_report_carries_the_open_status_and_hour_3_exposure(
        self, tmp_path: Path
    ) -> None:
        report = run_ma7_report(
            inputs=_standard_inputs(),
            rng=random.Random(REPORT_SEED),
            seed=REPORT_SEED,
            clock=_clock,
        )
        path = tmp_path / "report.md"
        _write_report(path, report)
        text = path.read_text()
        assert "Olangchunggola's 03 UTC peak is REPORTED, not adjudicated" in text
        assert "hour 03 UTC" in text


class TestRunnerWritesOnlyUnderOut:
    def test_write_text_is_called_from_exactly_one_place(self) -> None:
        """Structural guard for 'the runner writes ONLY under --out': the
        module contains exactly one `.write_text(` call site — inside
        `_write_report`, whose `path` argument always derives from
        `main()`'s `--out`. A second call site anywhere in the module would
        be a second, unreviewed place data could land — this fails loud on
        that, rather than string-matching a filename that also (correctly)
        appears in this module's own docstrings as prose."""
        import scripts.dhm_precip.ma7_run as ma7_run_module

        source = Path(ma7_run_module.__file__).read_text()
        assert source.count(".write_text(") == 1

    def test_write_report_touches_only_the_given_path(self, tmp_path: Path) -> None:
        report = run_ma7_report(
            inputs=_standard_inputs(),
            rng=random.Random(REPORT_SEED),
            seed=REPORT_SEED,
            clock=_clock,
        )
        before = set(tmp_path.iterdir())
        out_path = tmp_path / "ma7_report.md"
        _write_report(out_path, report)
        after = set(tmp_path.iterdir())
        assert after - before == {out_path}
