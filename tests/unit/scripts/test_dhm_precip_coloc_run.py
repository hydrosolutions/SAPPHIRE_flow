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
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip.coloc_pairs import COLOCATED_PAIRS
from scripts.dhm_precip.coloc_run import (
    DhmRetainedWindows,
    run_coloc_adjudication,
)
from scripts.dhm_precip.coloc_verdict import IndeterminateReason, Verdict
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.params import DEFAULT_PARAMS

if TYPE_CHECKING:
    from pathlib import Path

_HOUR_OFFSET = DEFAULT_PARAMS.coloc_dhm_utc_to_npt_hour_offset
_DAYS = range(1, 7)

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
    lines = ["TIMESTAMP,RR"]
    for utc_ts in _july_hours(years):
        npt_ts = utc_ts + timedelta(hours=_HOUR_OFFSET)
        peak = (
            pre_split_peak_hour
            if pre_split_peak_hour is not None and npt_ts.year < split_year
            else npt_peak_hour
        )
        lines.append(
            f"{npt_ts.isoformat(sep=' ')},{5.0 if npt_ts.hour == peak else 0.0}"
        )
    path.write_text("\n".join(lines) + "\n")


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
