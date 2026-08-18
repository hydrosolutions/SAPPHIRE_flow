"""Plan 182 (M-A10) — the runner: composes both co-located pairs, both
windows, and the EXACT two-station synthesis, with a real Pyramid CSV
loader over synthetic tmp-path fixture files (no real Zenodo data
needed) and a dependency-injected DHM provider (no real workbook needed).
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
from scripts.dhm_precip.coloc_verdict import Verdict
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.params import DEFAULT_PARAMS

if TYPE_CHECKING:
    from pathlib import Path

_HOUR_OFFSET = DEFAULT_PARAMS.coloc_dhm_utc_to_npt_hour_offset
_YEARS = [2020, 2021, 2022, 2023, 2024]
_DAYS = range(1, 21)


def _july_timestamps(years: list[int], days: range) -> list[datetime]:
    return [
        datetime(year, 7, day, hour)
        for year in years
        for day in days
        for hour in range(24)
    ]


def _dhm_frame(station: Station, *, real_utc_peak_hour: int) -> pl.DataFrame:
    timestamps = _july_timestamps(_YEARS, _DAYS)
    return pl.DataFrame(
        [
            {
                "station": station,
                "timestamp": ts,
                "value_mm": 5.0 if ts.hour == real_utc_peak_hour else 0.0,
            }
            for ts in timestamps
        ]
    )


def _write_pyramid_csv(path: Path, *, real_npt_peak_hour: int) -> None:
    dhm_timestamps = _july_timestamps(_YEARS, _DAYS)
    npt_timestamps = [ts + timedelta(hours=_HOUR_OFFSET) for ts in dhm_timestamps]
    lines = ["TIMESTAMP,RR"]
    lines += [
        f"{ts.isoformat(sep=' ')},{5.0 if ts.hour == real_npt_peak_hour else 0.0}"
        for ts in npt_timestamps
    ]
    path.write_text("\n".join(lines) + "\n")


def _provider_for(peak_hours_utc: dict[Station, int]):
    def provider(station: Station) -> DhmRetainedWindows:
        frame = _dhm_frame(station, real_utc_peak_hour=peak_hours_utc[station])
        return DhmRetainedWindows(overlap=frame, full_record=frame)

    return provider


class TestRunColocAdjudication:
    def test_both_pairs_agree_synthesis_refutes_h1(self, tmp_path: Path) -> None:
        """Both DHM stations peak (UTC) at an hour that converts to the
        SAME NPT hour their Pyramid partner peaks at — both stations
        REFUTE H1, and the exact two-station synthesis must agree."""
        for pair in COLOCATED_PAIRS:
            _write_pyramid_csv(
                tmp_path / pair.pyramid_csv_filename, real_npt_peak_hour=14
            )

        provider = _provider_for(
            {pair.dhm_station: 8 for pair in COLOCATED_PAIRS}  # UTC 8 -> NPT 14
        )

        report = run_coloc_adjudication(
            dhm_retained=provider,
            pyramid_dir=tmp_path,
            rng=random.Random(1),
            params=DEFAULT_PARAMS,
        )

        assert set(report.pair_adjudications) == {
            pair.dhm_station for pair in COLOCATED_PAIRS
        }
        for pair in COLOCATED_PAIRS:
            adj = report.pair_adjudications[pair.dhm_station]
            assert adj.station_verdict.verdict == Verdict.H1_REFUTED
            # "full profiles or per-window results", not just a bare peak:
            assert adj.threshold_ladder_profiles[0.0].height > 0
            assert adj.dhm_full_record_profile.height > 0
        assert report.synthesis.verdict == Verdict.H1_REFUTED

    def test_pairs_disagree_synthesis_is_indeterminate(self, tmp_path: Path) -> None:
        """One station refutes, the other's DHM/Pyramid peaks are
        antipodal (matched-resolution disagreement) — the exact
        two-station synthesis must report INDETERMINATE, never average."""
        lukla_pair = next(
            p for p in COLOCATED_PAIRS if p.dhm_station == Station("Lukla Airport")
        )
        syangboche_pair = next(
            p for p in COLOCATED_PAIRS if p.dhm_station == Station("Syangboche Airport")
        )

        _write_pyramid_csv(
            tmp_path / lukla_pair.pyramid_csv_filename, real_npt_peak_hour=14
        )
        _write_pyramid_csv(
            tmp_path / syangboche_pair.pyramid_csv_filename, real_npt_peak_hour=23
        )

        provider = _provider_for(
            {
                lukla_pair.dhm_station: 8,  # UTC 8 -> NPT 14, agrees
                syangboche_pair.dhm_station: 2,  # UTC 2 -> NPT 8, disagrees w/ 23
            }
        )

        report = run_coloc_adjudication(
            dhm_retained=provider,
            pyramid_dir=tmp_path,
            rng=random.Random(2),
            params=DEFAULT_PARAMS,
        )

        assert (
            report.pair_adjudications[lukla_pair.dhm_station].station_verdict.verdict
            == Verdict.H1_REFUTED
        )
        assert (
            report.pair_adjudications[
                syangboche_pair.dhm_station
            ].station_verdict.verdict
            == Verdict.INDETERMINATE
        )
        assert report.synthesis.verdict == Verdict.INDETERMINATE
