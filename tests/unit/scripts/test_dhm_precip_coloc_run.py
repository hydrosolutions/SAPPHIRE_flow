"""Plan 182 (M-A10) — the runner: composes both co-located pairs, both
windows, and the EXACT two-station synthesis, with a real Pyramid CSV
loader over synthetic tmp-path fixture files (no real Zenodo data
needed) and a dependency-injected DHM provider (no real workbook needed).

**Every test here drives the pipeline through the REAL `COLOCATED_PAIRS`
bounds** (D11): DHM 2020-2025, Pyramid Lukla 2005-2023 / Namche 2002-2023,
and each pair's real overlap window. The previous versions of these tests
fed a synthetic 2020-2024 frame for BOTH windows, bypassing the registry
entirely — that is how a green suite coexisted with a deliverable that
could only ever return INDETERMINATE.
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip import coloc_run
from scripts.dhm_precip.coloc_pairs import COLOCATED_PAIRS
from scripts.dhm_precip.coloc_run import (
    DhmRetainedWindows,
    _production_dhm_retained_provider,
    _write_report,
    run_coloc_adjudication,
)
from scripts.dhm_precip.coloc_verdict import IndeterminateReason, Verdict
from scripts.dhm_precip.domain_types import LongFrameInventory, Station
from scripts.dhm_precip.params import DEFAULT_PARAMS

if TYPE_CHECKING:
    import pytest

_HOUR_OFFSET = DEFAULT_PARAMS.coloc_dhm_utc_to_npt_hour_offset
_DAYS = range(1, 7)

_HOUR_ROW = re.compile(r"^\| \d+ \|")

_LUKLA = Station("Lukla Airport")
_SYANGBOCHE = Station("Syangboche Airport")


def _july_hours(years: list[int], days: range = _DAYS) -> list[datetime]:
    return [
        datetime(year, 7, day, hour)
        for year in years
        for day in days
        for hour in range(24)
    ]


def _real_bounds_provider(peak_hours_utc: dict[Station, int]):
    """DHM's REAL full-record span per the registry (2020-2025) and each
    pair's REAL overlap window — never a synthetic uniform frame."""

    def provider(station: Station) -> DhmRetainedWindows:
        pair = next(p for p in COLOCATED_PAIRS if p.dhm_station == station)
        peak = peak_hours_utc[station]

        def frame(years: list[int]) -> pl.DataFrame:
            return pl.DataFrame(
                [
                    {
                        "station": str(station),
                        "timestamp": ts,
                        "value_mm": 5.0 if ts.hour == peak else 0.0,
                    }
                    for ts in _july_hours(years)
                ]
            )

        return DhmRetainedWindows(
            overlap=frame(
                list(range(pair.overlap_start_year, pair.overlap_end_year + 1))
            ),
            full_record=frame(list(range(pair.dhm_start_year, pair.dhm_end_year + 1))),
        )

    return provider


def _write_pyramid_csv(
    path: Path,
    *,
    years: list[int],
    npt_peak_hour: int,
    pre_split_peak_hour: int | None = None,
    split_year: int = 2020,
) -> None:
    """Pyramid's REAL per-station span, NPT wall-clock, on the timestamps a
    DHM UTC frame maps onto under the D2 offset (so the overlap window is
    genuinely pairable). `pre_split_peak_hour` shifts the PYRAMID phase
    before `split_year` — D12's stationarity check is Pyramid's, not
    DHM's."""
    # The REAL Zenodo Lvl1 shape: semicolon-delimited, CR-only line endings,
    # `year;month;day;hour;AT;RR;AP;RH;WS;WD` (no TIMESTAMP column).
    lines = ["year;month;day;hour;AT;RR;AP;RH;WS;WD"]
    for utc_ts in _july_hours(years):
        npt_ts = utc_ts + timedelta(hours=_HOUR_OFFSET)
        peak = (
            pre_split_peak_hour
            if pre_split_peak_hour is not None and npt_ts.year < split_year
            else npt_peak_hour
        )
        rr = 5.0 if npt_ts.hour == peak else 0.0
        lines.append(
            f"{npt_ts.year};{npt_ts.month};{npt_ts.day};{npt_ts.hour};;{rr};;;;"
        )
    path.write_bytes(("\r".join(lines) + "\r\n").encode("utf-8"))


def _write_agreeing_pyramid_files(tmp_path: Path, *, npt_peak_hour: int = 14) -> None:
    for pair in COLOCATED_PAIRS:
        _write_pyramid_csv(
            tmp_path / pair.pyramid_csv_filename,
            years=list(range(pair.pyramid_start_year, pair.pyramid_end_year + 1)),
            npt_peak_hour=npt_peak_hour,
        )


def _agreeing_report(tmp_path: Path, *, seed: int):
    _write_agreeing_pyramid_files(tmp_path)
    return run_coloc_adjudication(
        dhm_retained=_real_bounds_provider(
            {pair.dhm_station: 8 for pair in COLOCATED_PAIRS}  # UTC 8 -> NPT 14
        ),
        pyramid_dir=tmp_path,
        rng=random.Random(seed),
        params=DEFAULT_PARAMS,
    )


class TestVerdictIsReachableThroughTheRealRegistryBounds:
    """D11 — 'A test must drive the pipeline through the REAL registry
    bounds and prove a decisive verdict is reachable. A suite that cannot
    fail on "the deliverable is unreachable" is not testing the
    deliverable.'"""

    def test_real_bounds_yield_a_decisive_verdict(self, tmp_path: Path) -> None:
        report = _agreeing_report(tmp_path, seed=17)

        assert set(report.pair_adjudications) == {
            pair.dhm_station for pair in COLOCATED_PAIRS
        }
        for pair in COLOCATED_PAIRS:
            verdict = report.pair_adjudications[pair.dhm_station].station_verdict
            assert verdict.verdict is not Verdict.INDETERMINATE, (
                f"{pair.dhm_station}: {verdict.reason} — a decisive verdict "
                "must be REACHABLE against the real registry bounds"
            )
            assert verdict.verdict == Verdict.H1_REFUTED
        assert report.synthesis.verdict == Verdict.H1_REFUTED

    def test_each_window_reports_its_own_pyramid_retention(
        self, tmp_path: Path
    ) -> None:
        """The Pyramid file spans the whole record; each window's retention
        must be that window's retained JJAS hours, not the file's."""
        report = _agreeing_report(tmp_path, seed=18)

        hours_per_season = len(_DAYS) * 24
        for pair in COLOCATED_PAIRS:
            adj = report.pair_adjudications[pair.dhm_station]
            pyramid_seasons = pair.pyramid_end_year - pair.pyramid_start_year + 1
            overlap_seasons = pair.overlap_end_year - pair.overlap_start_year + 1
            assert adj.full_record.n_pyramid_retained == (
                pyramid_seasons * hours_per_season
            )
            assert adj.overlap.n_pyramid_retained == overlap_seasons * hours_per_season


class TestStationarityIsCheckedOnPyramidNotDhm:
    """D12 — 'The pre-2020 vs 2020+ split is PYRAMID's.' DHM has no
    pre-2020 data at all, so a DHM-side split can never see a Pyramid phase
    shift, and a non-contemporaneous full-record comparison would be
    licensed on a record that is demonstrably not stationary."""

    def test_a_pyramid_phase_shift_across_2020_is_detected(
        self, tmp_path: Path
    ) -> None:
        for pair in COLOCATED_PAIRS:
            _write_pyramid_csv(
                tmp_path / pair.pyramid_csv_filename,
                years=list(range(pair.pyramid_start_year, pair.pyramid_end_year + 1)),
                npt_peak_hour=14,
                pre_split_peak_hour=2,  # 12h circular shift before 2020
            )

        report = run_coloc_adjudication(
            dhm_retained=_real_bounds_provider(
                {pair.dhm_station: 8 for pair in COLOCATED_PAIRS}
            ),  # DHM is perfectly stable
            pyramid_dir=tmp_path,
            rng=random.Random(19),
            params=DEFAULT_PARAMS,
        )

        for pair in COLOCATED_PAIRS:
            adj = report.pair_adjudications[pair.dhm_station]
            assert adj.station_verdict.verdict == Verdict.INDETERMINATE
            assert (
                adj.station_verdict.reason == IndeterminateReason.ADEQUACY_NONSTATIONARY
            )
            # The DHM-side split is VACUOUS here by construction — DHM is
            # stable, so only the Pyramid check can see the shift.
            assert adj.dhm_stationarity.peak_diff_hours == 0.0
            assert adj.pyramid_stationarity.peak_diff_hours == 12.0


class TestSynthesisOverBothPairs:
    def test_pairs_disagree_synthesis_is_indeterminate(self, tmp_path: Path) -> None:
        """One station refutes, the other's DHM/Pyramid peaks disagree by
        more than the gate-1 threshold — the exact two-station synthesis
        must report INDETERMINATE, never average."""
        for pair in COLOCATED_PAIRS:
            _write_pyramid_csv(
                tmp_path / pair.pyramid_csv_filename,
                years=list(range(pair.pyramid_start_year, pair.pyramid_end_year + 1)),
                npt_peak_hour=14 if pair.dhm_station == _LUKLA else 23,
            )

        report = run_coloc_adjudication(
            dhm_retained=_real_bounds_provider(
                {
                    _LUKLA: 8,  # UTC 8 -> NPT 14, agrees
                    _SYANGBOCHE: 2,  # UTC 2 -> NPT 8, 9h from 23
                }
            ),
            pyramid_dir=tmp_path,
            rng=random.Random(2),
            params=DEFAULT_PARAMS,
        )

        assert (
            report.pair_adjudications[_LUKLA].station_verdict.verdict
            == Verdict.H1_REFUTED
        )
        syangboche = report.pair_adjudications[_SYANGBOCHE].station_verdict
        assert syangboche.verdict == Verdict.INDETERMINATE
        assert syangboche.reason == IndeterminateReason.MATCHED_RESOLUTION_DISAGREEMENT
        assert report.synthesis.verdict == Verdict.INDETERMINATE
        assert report.synthesis.reason == IndeterminateReason.STATION_DISAGREEMENT


# --- The Exit deliverable: the written report ----------------------------


def _h1_supported_provider():
    """D7 — the ablation moves the DHM peak INTO agreement with Pyramid:
    a sub-0.2 mm drizzle sits on every NPT-02 hour (mean 0.19), while real
    rain both instruments can see falls at NPT 14 on one day in three
    (mean 0.167). At all values the peak is 02; zeroing below 0.2 mm moves
    it to 14, where Pyramid peaks — 12 h of movement, toward."""

    def provider(station: Station) -> DhmRetainedWindows:
        pair = next(p for p in COLOCATED_PAIRS if p.dhm_station == station)

        def value(ts: datetime) -> float:
            if ts.hour == 20:  # NPT 02 — resolution-level drizzle
                return 0.19
            if ts.hour == 8 and ts.day % 3 == 1:  # NPT 14 — real rain
                return 0.5
            return 0.0

        def frame(years: list[int]) -> pl.DataFrame:
            return pl.DataFrame(
                [
                    {"station": str(station), "timestamp": ts, "value_mm": value(ts)}
                    for ts in _july_hours(years)
                ]
            )

        return DhmRetainedWindows(
            overlap=frame(
                list(range(pair.overlap_start_year, pair.overlap_end_year + 1))
            ),
            full_record=frame(list(range(pair.dhm_start_year, pair.dhm_end_year + 1))),
        )

    return provider


class TestWrittenReportCarriesEveryExitDeliverable:
    """Plan 182 Exit — the runner must be able to PRODUCE the deliverables,
    not just the verdict scalars. Peak-hour integers alone are not the
    milestone: the profile tables (with hourly `n`), each side's retention
    per window, D2's alignment uncertainty, D8's micro-climate/wind
    alternative, D7.3's drizzle confound and Pyramid's CC BY 4.0
    attribution are all named in Exit."""

    def _report_text(self, tmp_path: Path) -> str:
        report = _agreeing_report(tmp_path, seed=41)
        out = tmp_path / "coloc_adjudication.md"
        _write_report(out, report)
        return out.read_text()

    def test_full_profile_tables_for_both_networks_in_both_windows(
        self, tmp_path: Path
    ) -> None:
        text = self._report_text(tmp_path)
        for pair in COLOCATED_PAIRS:
            for window in ("Full record", "Overlap"):
                for rung in DEFAULT_PARAMS.coloc_threshold_ladder_mm:
                    assert (
                        f"DHM normalised diurnal profile — {window} — "
                        f"{rung} mm rung" in text
                    ), (pair.dhm_station, window, rung)
                assert f"Pyramid normalised diurnal profile — {window}" in text, (
                    pair.dhm_station,
                    window,
                )

        # 2 pairs x 2 windows x (3 ladder rungs + 1 Pyramid) tables, each
        # with one row per hour of day.
        hour_rows = [line for line in text.splitlines() if _HOUR_ROW.match(line)]
        assert (
            len(hour_rows)
            == 2 * 2 * (len(DEFAULT_PARAMS.coloc_threshold_ladder_mm) + 1) * 24
        )

    def test_every_profile_table_carries_its_hourly_n(self, tmp_path: Path) -> None:
        """D4 — 'Report the retained-hour count beside every statistic.'"""
        text = self._report_text(tmp_path)
        headers = [line for line in text.splitlines() if line.startswith("| hour")]
        assert headers
        for header in headers:
            assert "| n |" in header

    def test_each_side_retention_is_reported_per_window(self, tmp_path: Path) -> None:
        text = self._report_text(tmp_path)
        assert text.count("n DHM retained (this window)") == 4
        assert text.count("n Pyramid retained (this window)") == 4

    def test_alignment_uncertainty_and_the_phase_precision_limit(
        self, tmp_path: Path
    ) -> None:
        text = self._report_text(tmp_path)
        assert "1.75" in text
        assert "no phase result finer than" in text.lower()

    def test_the_d8_microclimate_and_wind_alternative_is_adjudicated_against(
        self, tmp_path: Path
    ) -> None:
        text = self._report_text(tmp_path).lower()
        assert "micro-climat" in text
        assert "wind" in text

    def test_the_d7_3_drizzle_confound_is_stated(self, tmp_path: Path) -> None:
        text = self._report_text(tmp_path).lower()
        assert "drizzle" in text

    def test_pyramid_attribution_is_present(self, tmp_path: Path) -> None:
        text = self._report_text(tmp_path)
        assert "CC BY 4.0" in text
        assert "Salerno" in text

    def test_no_magnitude_totals_are_reported(self, tmp_path: Path) -> None:
        """D1 — 'No mm totals are compared or reported.' The profile tables
        carry the NORMALISED value and `n` only."""
        text = self._report_text(tmp_path)
        assert "mean_value_mm" not in text
        assert "mm/h" not in text

    def test_affected_claims_are_listed_only_when_h1_is_supported(
        self, tmp_path: Path
    ) -> None:
        _write_agreeing_pyramid_files(tmp_path)
        supported = run_coloc_adjudication(
            dhm_retained=_h1_supported_provider(),
            pyramid_dir=tmp_path,
            rng=random.Random(43),
            params=DEFAULT_PARAMS,
        )
        assert supported.synthesis.verdict == Verdict.H1_SUPPORTED

        out = tmp_path / "supported.md"
        _write_report(out, supported)
        text = out.read_text()
        assert "Affected claims" in text
        assert "M-A7" in text
        assert "M-A1" in text

        assert "Affected claims" not in self._report_text(tmp_path)


# --- The production seam: the mask join, against real loader dtypes ------


_MS_FIXTURE_STATIONS = (_LUKLA, _SYANGBOCHE)
_MS_FIXTURE_HOURS = [datetime(2020, 7, 1) + timedelta(hours=h) for h in range(10 * 24)]
_SENTINEL_TIMESTAMP = datetime(2020, 7, 3, 5)


def _millisecond_long_frame() -> pl.DataFrame:
    """What `pl.read_excel` actually hands `load_long_frame` from the pinned
    workbook: a `timestamp` column at MILLISECOND precision. Values vary
    hour to hour so neither `frozen_sensor` instance fires — the only
    masked key is the Lukla sentinel, which `range_check` rejects."""
    rows = [
        {
            "source_row_index": index,
            "station": str(station),
            "timestamp": ts,
            "value_mm": (
                DEFAULT_PARAMS.sentinel_value
                if station == _LUKLA and ts == _SENTINEL_TIMESTAMP
                else 0.1 * (index % 7 + 1)
            ),
        }
        for station in _MS_FIXTURE_STATIONS
        for index, ts in enumerate(_MS_FIXTURE_HOURS)
    ]
    return pl.DataFrame(
        rows,
        schema={
            "source_row_index": pl.Int64,
            "station": pl.Utf8,
            "timestamp": pl.Datetime("ms"),
            "value_mm": pl.Float64,
        },
    )


def _patch_workbook_io(
    monkeypatch: pytest.MonkeyPatch, long_frame: pl.DataFrame
) -> None:
    """Mocks ONLY the file-I/O boundary (CLAUDE.md: mock at external
    boundaries). Everything after it — `on_grid_view`, `normalise_hourly_axis`,
    `iter_observations_by_station`, `qc_mask.iter_station_results` and the
    mask anti-join — is the real production composition."""
    inventory = LongFrameInventory(
        all_columns=tuple(str(station) for station in _MS_FIXTURE_STATIONS),
        empty_columns=(),
        total_rows=len(_MS_FIXTURE_HOURS),
    )
    monkeypatch.setattr(coloc_run, "resolve_source_path", lambda: Path("workbook.xlsx"))
    monkeypatch.setattr(coloc_run, "resolve_coords_path", lambda: Path("coords.csv"))
    monkeypatch.setattr(
        coloc_run,
        "load_long_frame",
        lambda _path, *, expected_sha256: (long_frame, inventory),
    )
    monkeypatch.setattr(
        coloc_run,
        "load_station_coordinates",
        lambda _path, *, expected_stations: None,
    )


class TestProductionProviderJoinsTheMaskAgainstTheLoadersOwnDtype:
    """The seam every other test in this file bypasses by injecting a
    `DhmRetainedProvider`. `_production_dhm_retained_provider` is the only
    place a frame the runner CONSTRUCTS (the QC mask) meets a frame the
    WORKBOOK produced, and `pl.read_excel` yields `Datetime('ms')` — a mask
    frame pinned to `Datetime('us')` makes the anti-join raise
    `SchemaError`, so `main()` can never write a report against real data
    while the suite stays green."""

    def test_mask_join_survives_a_millisecond_precision_workbook_timestamp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_workbook_io(monkeypatch, _millisecond_long_frame())

        provider = _production_dhm_retained_provider(DEFAULT_PARAMS)
        retained = provider(_LUKLA).full_record

        timestamps = set(retained.get_column("timestamp").to_list())
        # The anti-join ran and dropped exactly the masked key — not a
        # no-op that would also pass a mismatched-dtype join if polars ever
        # started coercing silently.
        assert _SENTINEL_TIMESTAMP not in timestamps
        assert datetime(2020, 7, 3, 6) in timestamps
        assert timestamps == set(_MS_FIXTURE_HOURS) - {_SENTINEL_TIMESTAMP}

    def test_every_colocated_station_gets_its_retained_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_workbook_io(monkeypatch, _millisecond_long_frame())

        provider = _production_dhm_retained_provider(DEFAULT_PARAMS)

        assert provider(_SYANGBOCHE).full_record.height == len(_MS_FIXTURE_HOURS)
        assert provider(_LUKLA).full_record.height == len(_MS_FIXTURE_HOURS) - 1
