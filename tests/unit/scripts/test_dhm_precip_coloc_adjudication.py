"""Plan 182 (M-A10) — end-to-end composition: threshold ladder, D3 pairing,
D5 bootstrap, D9 verdict, over synthetic data (no real Pyramid files
needed)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import polars as pl

from scripts.dhm_precip.coloc_adjudication import adjudicate_station
from scripts.dhm_precip.coloc_verdict import Verdict
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.params import DEFAULT_PARAMS

_DHM_STATION = Station("Lukla Airport")
_PYRAMID_STATION = "AWS3 Lukla"


def _hourly_frame(
    *, station: object, years: list[int], values_by_hour: dict[int, float]
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for year in years:
        for hour in range(24):
            rows.append(
                {
                    "station": station,
                    "timestamp": datetime(year, 7, 1) + timedelta(hours=hour),
                    "value_mm": values_by_hour.get(hour, 0.0),
                }
            )
    return pl.DataFrame(rows)


class TestAdjudicateStationRefutesWhenBothInstrumentsAgree:
    def test_full_pipeline_refutes_h1_on_matching_synthetic_data(self) -> None:
        """DHM and Pyramid share the SAME true peak (hour 14) at EVERY
        threshold, and 5 season-years are provided (adequacy passes) — the
        whole pipeline should refute H1 without any gate raising."""
        years = [2019, 2020, 2021, 2022, 2023]
        # Real signal at hour 14 is well above 0.2mm; a tiny UNIFORM
        # sub-threshold noise floor sits on every hour (a common scalar —
        # ablation must not move the peak, per D7.2).
        values = {h: 0.05 for h in range(24)}
        values[14] = 5.05

        dhm_overlap = _hourly_frame(
            station=_DHM_STATION, years=years, values_by_hour=values
        )
        dhm_full_record = dhm_overlap
        pyramid = _hourly_frame(
            station=_PYRAMID_STATION,
            years=years,
            values_by_hour={h: (0.0 if h != 14 else 5.0) for h in range(24)},
        )

        result = adjudicate_station(
            dhm_station=_DHM_STATION,
            dhm_overlap_retained=dhm_overlap,
            dhm_full_record_retained=dhm_full_record,
            pyramid_retained=pyramid,
            rng=random.Random(7),
            params=DEFAULT_PARAMS,
        )

        assert result.threshold_ladder_peaks[0.0] == 14
        assert result.threshold_ladder_peaks[0.2] == 14
        assert result.pyramid_peak_hour == 14
        assert result.bootstrap.n_season_years == 5
        assert result.bootstrap.adequate_sample is True
        assert result.pairing.n_common_retained == result.pairing.n_dhm_retained
        assert result.station_verdict.verdict == Verdict.H1_REFUTED
