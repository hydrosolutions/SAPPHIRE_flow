"""Plan 184 (M-A6) task T2 — Pyramid `AT` loader, the D14 lapse correction,
and the hour-of-day-equalised transect check.

Guard tests (module-level intent, CLAUDE.md testing philosophy — contracts,
not internals):

- `TestLoadPyramidLvl1AtCsv` proves `AT` is retained at the real cold
  extreme (-24 degC+) and is structurally unable to pass through RR's
  precipitation range check (no `params` argument exists on the AT loader
  at all).
- `TestLapseCorrectionSign` proves the D14 correction's SIGN against the
  real published `station_grid_elevation.csv` numbers the plan cites
  (Humde +8.44, Olangchunggola +6.73, Lukla +6.52, Syangboche +4.86), plus
  a station-above-orography case where the correction must COOL.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.era5_errors import (
    ExtractionInputAbsentError,
    ExtractionPostConditionError,
)
from scripts.dhm_precip.era5_extract_manifest import (
    ExtractionManifest,
    checksum_file,
    manifest_filename,
    points_root,
    write_extraction_manifest,
)
from scripts.dhm_precip.ma6_lapse_check import (
    STANDARD_LAPSE_RATE_DEGC_PER_KM,
    GaugeToGaugeDiagnostic,
    TransectStation,
    TransectStationResult,
    _attribution_notes,
    compute_transect_station_result,
    discover_and_load_station_grid_elevation_table,
    hour_of_day_equalised_mean,
    lapse_correct_to_station_degc,
    pair_pyramid_and_era5,
    paired_gauge_diagnostic,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS
from scripts.dhm_precip.pyramid_loader import (
    AIR_TEMP_COLUMN,
    PyramidDuplicateTimestampError,
    PyramidParseFailureError,
    PyramidSchemaMismatchError,
    PyramidSourceUnreadableError,
    load_pyramid_lvl1_at_csv,
    load_pyramid_lvl1_csv,
)

if TYPE_CHECKING:
    from pathlib import Path

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


def _publish_fake_elevation_bundle(
    root: Path, *, run_number: str, identity: str
) -> Path:
    directory = points_root(root) / f"{run_number}-{identity}"
    directory.mkdir(parents=True)
    payload = directory / "station_grid_elevation.csv"
    payload.write_text(
        "station,station_elev_m,orography_elev_m\nHumde Airport,3401.0,4700.18\n"
    )
    write_extraction_manifest(
        ExtractionManifest(
            extraction_identity=identity,
            payload_sha256s={"station_grid_elevation.csv": checksum_file(payload)},
            **_MANIFEST_BASE_KWARGS,
        ),
        directory / manifest_filename(),
    )
    return directory


_STATION = Station("AWS4 Kala Patthar")

_HEADER = "year;month;day;hour;AT;RR;AP;RH;WS;WD"


def _row(
    year: int, month: int, day: int, hour: int | str, at: str, rr: str = ""
) -> str:
    return f"{year};{month};{day};{hour};{at};{rr};;;;"


def _write_pyramid_csv(path: Path, rows: list[str], *, header: str = _HEADER) -> None:
    path.write_bytes(("\r".join([header, *rows]) + "\r\n").encode("utf-8"))


class TestExpectedColumnName:
    def test_air_temp_column_constant(self) -> None:
        assert AIR_TEMP_COLUMN == "AT"


class TestLoadPyramidLvl1AtCsv:
    def test_loads_station_timestamp_and_value_degc(self, tmp_path: Path) -> None:
        path = tmp_path / "AWS4_Z5600_Lvl1.csv"
        _write_pyramid_csv(
            path, [_row(2021, 7, 1, 0, "-10.2"), _row(2021, 7, 1, 1, "-9.8")]
        )
        result = load_pyramid_lvl1_at_csv(path, station=_STATION)
        assert result.retained.height == 2
        assert result.n_raw == 2
        assert result.n_retained == 2
        assert result.retained["station"].to_list() == [str(_STATION), str(_STATION)]
        assert result.retained["value_degc"].to_list() == [-10.2, -9.8]

    def test_timestamp_parsing_is_shared_with_rr(self, tmp_path: Path) -> None:
        """Same four-integer-column timestamp construction the RR loader
        uses (module docstring: "sharing ONLY its file/timestamp
        parsing")."""
        path = tmp_path / "AWS4_Z5600_Lvl1.csv"
        _write_pyramid_csv(
            path, [_row(2021, 7, 1, 23, "-1.0"), _row(2021, 7, 2, 0, "-1.5")]
        )
        result = load_pyramid_lvl1_at_csv(path, station=_STATION)
        assert result.retained["timestamp"].to_list() == [
            datetime(2021, 7, 1, 23),
            datetime(2021, 7, 2, 0),
        ]

    def test_a_minus_24_degc_reading_is_retained(self, tmp_path: Path) -> None:
        """The measured real AWS4 minimum (-24.79 degC). This is the direct
        proof that `AT` does NOT pass through RR's `[0.0, 200.0]` mm
        physical-range check, which would have deleted it (D14's binding
        rule)."""
        path = tmp_path / "AWS4_Z5600_Lvl1.csv"
        _write_pyramid_csv(path, [_row(2021, 1, 15, 3, "-24.79")])
        result = load_pyramid_lvl1_at_csv(path, station=_STATION)
        assert result.n_retained == 1
        assert result.retained["value_degc"].to_list() == [-24.79]

    def test_every_sub_zero_reading_across_a_realistic_cold_night_is_retained(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "AWS4_Z5600_Lvl1.csv"
        cold_values = [-24.79, -20.0, -15.5, -5.0, -0.5, -0.01]
        rows = [_row(2021, 1, 15, hour, str(v)) for hour, v in enumerate(cold_values)]
        _write_pyramid_csv(path, rows)
        result = load_pyramid_lvl1_at_csv(path, station=_STATION)
        assert result.n_retained == len(cold_values)
        assert sorted(result.retained["value_degc"].to_list()) == sorted(cold_values)

    def test_loader_has_no_params_argument(self) -> None:
        """Structural guard: there is no parameter through which
        `DhmPrecipParams`' precipitation range bounds could reach this
        loader at all."""
        params = inspect.signature(load_pyramid_lvl1_at_csv).parameters
        assert "params" not in params

    def test_nan_value_is_excluded_and_counted(self, tmp_path: Path) -> None:
        path = tmp_path / "nan.csv"
        _write_pyramid_csv(
            path, [_row(2021, 7, 1, 0, "nan"), _row(2021, 7, 1, 1, "-1.0")]
        )
        result = load_pyramid_lvl1_at_csv(path, station=_STATION)
        assert result.n_raw == 2
        assert result.n_nonfinite == 1
        assert result.retained.height == 1
        assert result.retained["value_degc"].to_list() == [-1.0]

    def test_empty_at_field_is_missing_not_a_parse_failure(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "AWS4_Z5600_Lvl1.csv"
        _write_pyramid_csv(
            path,
            [_row(2021, 7, 1, 0, ""), _row(2021, 7, 1, 1, "-3.0")],
        )
        result = load_pyramid_lvl1_at_csv(path, station=_STATION)
        assert result.n_raw == 2
        assert result.n_retained == 1
        assert result.retained["value_degc"].to_list() == [-3.0]

    def test_missing_at_column_raises_schema_mismatch(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.csv"
        _write_pyramid_csv(
            path,
            ["2021;7;1;0;;;;;;"],
            header="year;month;day;hour;OTHER;RR;AP;RH;WS;WD",
        )
        with pytest.raises(PyramidSchemaMismatchError, match="AT"):
            load_pyramid_lvl1_at_csv(path, station=_STATION)

    def test_missing_time_column_raises_schema_mismatch(self, tmp_path: Path) -> None:
        path = tmp_path / "bad_time.csv"
        _write_pyramid_csv(
            path, ["2021;7;1;;-1.0;;;;"], header="year;month;day;AT;RR;AP;RH;WS;WD"
        )
        with pytest.raises(PyramidSchemaMismatchError, match="hour"):
            load_pyramid_lvl1_at_csv(path, station=_STATION)

    def test_non_numeric_value_raises_parse_failure(self, tmp_path: Path) -> None:
        path = tmp_path / "bad_value.csv"
        _write_pyramid_csv(path, [_row(2021, 7, 1, 0, "not_a_number")])
        with pytest.raises(PyramidParseFailureError):
            load_pyramid_lvl1_at_csv(path, station=_STATION)

    def test_duplicate_timestamp_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "dup.csv"
        _write_pyramid_csv(
            path, [_row(2021, 7, 1, 0, "-1.0"), _row(2021, 7, 1, 0, "-2.0")]
        )
        with pytest.raises(PyramidDuplicateTimestampError):
            load_pyramid_lvl1_at_csv(path, station=_STATION)

    def test_missing_file_raises_unreadable(self, tmp_path: Path) -> None:
        with pytest.raises(PyramidSourceUnreadableError):
            load_pyramid_lvl1_at_csv(tmp_path / "missing.csv", station=_STATION)

    def test_rr_loader_is_unaffected_by_at_support(self, tmp_path: Path) -> None:
        """The existing RR path must keep behaving exactly as before —
        the refactor that factored out shared parsing must not have
        changed RR's own retention semantics."""
        path = tmp_path / "AWS4_Z5600_Lvl1.csv"
        _write_pyramid_csv(
            path,
            [
                _row(2021, 7, 1, 0, "-10.0", rr="0.2"),
                _row(2021, 7, 1, 1, "-9.0", rr="0"),
            ],
        )
        result = load_pyramid_lvl1_csv(path, station=_STATION)
        assert result.retained["value_mm"].to_list() == [0.2, 0.0]


class TestLapseCorrectionSign:
    """D14 — model orography sits ABOVE the station for MOST high-altitude
    cases (verified against the real published `station_grid_elevation.csv`
    values the plan's D14 amendment cites, 2026-08-20), so the correction
    usually WARMS — this is NOT universal: AWS4 Kala Patthar sits ~19 m
    ABOVE its own cell's orography, the exception `test_cools_when_the_
    station_is_above_orography` covers below."""

    @pytest.mark.parametrize(
        ("orography_elev_m", "station_elev_m", "expected_correction"),
        [
            (4700.1767578125, 3401.0, 8.44),  # Humde
            (4154.630859375, 3119.0, 6.73),  # Olangchunggola
            (3863.830322265625, 2860.0, 6.52),  # Lukla Airport
            (4447.3779296875, 3700.0, 4.86),  # Syangboche Airport
        ],
    )
    def test_matches_the_published_elevation_table(
        self, orography_elev_m: float, station_elev_m: float, expected_correction: float
    ) -> None:
        corrected = lapse_correct_to_station_degc(
            0.0, orography_elev_m=orography_elev_m, station_elev_m=station_elev_m
        )
        assert corrected == pytest.approx(expected_correction, abs=0.005)

    def test_warms_when_orography_is_above_the_station(self) -> None:
        raw = 0.0
        corrected = lapse_correct_to_station_degc(
            raw, orography_elev_m=4700.0, station_elev_m=3400.0
        )
        assert corrected > raw

    def test_cools_when_the_station_is_above_orography(self) -> None:
        """Ghorepani: station 2,742 m, orography 2,298.997 m — the station
        sits ABOVE its grid cell's model orography, so correcting DOWN to
        (up to, here) the station means COOLING."""
        raw = 10.0
        corrected = lapse_correct_to_station_degc(
            raw, orography_elev_m=2298.997314453125, station_elev_m=2742.0
        )
        assert corrected < raw
        assert corrected == pytest.approx(10.0 - 2.8795, abs=0.005)

    def test_zero_elevation_difference_is_a_no_op(self) -> None:
        assert lapse_correct_to_station_degc(
            5.0, orography_elev_m=3000.0, station_elev_m=3000.0
        ) == pytest.approx(5.0)

    def test_standard_rate_constant(self) -> None:
        assert STANDARD_LAPSE_RATE_DEGC_PER_KM == 6.5

    def test_rate_is_a_parameter_never_hardcoded_only_inline(self) -> None:
        """D14 forbids fitting the rate — but the function must still
        ACCEPT a rate parameter (used only for the 5.0-9.8 sensitivity
        band elsewhere in the track, T5's job), never hardcode 6.5 where a
        caller cannot override it for that sensitivity."""
        default = lapse_correct_to_station_degc(
            0.0, orography_elev_m=4700.0, station_elev_m=3400.0
        )
        custom = lapse_correct_to_station_degc(
            0.0, orography_elev_m=4700.0, station_elev_m=3400.0, rate_degc_per_km=9.8
        )
        assert custom != default
        assert custom == pytest.approx(1.3 * 9.8)


class TestHourOfDayEqualisedMean:
    def test_equal_hourly_counts_matches_naive_mean(self) -> None:
        frame = pl.DataFrame(
            {
                "timestamp": [
                    datetime(2023, 8, day, hour) for day in (1, 2) for hour in range(24)
                ],
                "value": [10.0] * 48,
            }
        )
        assert hour_of_day_equalised_mean(frame) == pytest.approx(10.0)

    def test_unequal_hourly_counts_differs_from_naive_mean(self) -> None:
        """Reproduces the plan's own AWS3-August-2023 shape: hour 0 is
        sampled many times at a high value, hour 12 sampled once at a low
        value — a naive mean is dragged toward the densely-sampled hour;
        the equalised mean weighs both hours the same."""
        timestamps = [datetime(2023, 8, 1, 0, minute) for minute in range(0, 60, 5)]
        values = [20.0] * len(timestamps)
        timestamps.append(datetime(2023, 8, 1, 12, 0))
        values.append(0.0)
        frame = pl.DataFrame({"timestamp": timestamps, "value": values})

        naive_mean = sum(values) / len(values)
        equalised = hour_of_day_equalised_mean(frame)

        assert equalised == pytest.approx(10.0)  # mean of {20.0, 0.0}
        assert equalised != pytest.approx(naive_mean)

    def test_whole_hour_relabelling_is_invariant(self) -> None:
        """D14 amendment — a whole-hour relabelling (the period-ending
        assumption's residual uncertainty) moves the equalised mean by
        exactly 0.000 degC: the same 24 hourly means are averaged, only
        their labels change."""
        import random as _random

        rng = _random.Random(0)
        timestamps = [
            datetime(2022, 7, 1, hour, 0) for hour in range(24) for _ in range(3)
        ]
        values = [rng.uniform(-5, 15) for _ in timestamps]
        frame = pl.DataFrame({"timestamp": timestamps, "value": values})
        shifted = pl.DataFrame(
            {
                "timestamp": [ts.replace(hour=(ts.hour + 1) % 24) for ts in timestamps],
                "value": values,
            }
        )
        assert hour_of_day_equalised_mean(frame) == pytest.approx(
            hour_of_day_equalised_mean(shifted), abs=1e-9
        )

    def test_empty_frame_raises(self) -> None:
        frame = pl.DataFrame({"timestamp": [], "value": []}).with_columns(
            pl.col("timestamp").cast(pl.Datetime)
        )
        with pytest.raises(ValueError, match="empty"):
            hour_of_day_equalised_mean(frame)


class TestComputeTransectStationResult:
    """The pure core — no I/O, injected frames."""

    _STATION = TransectStation(
        pyramid_station=Station("AWS3 Lukla"),
        csv_filename="AWS3_Z2660_Lvl1.csv",
        lat=27.70,
        lon=86.72,
        elevation_m=2660.0,
    )

    def test_discrepancy_is_corrected_minus_pyramid(self) -> None:
        """ERA5 (UTC) and Pyramid (NPT) timestamps are 6h apart labels for
        ROUNDED-ALIGNED hours (Finding A, Plan 184 T2 round-2 review; the
        true offset is 5h45m, so +6h is an approximation, never the same
        physical hour) —
        NOT identically-labelled, so this test exercises the real clock
        reconciliation `pair_pyramid_and_era5` performs, rather than
        relying on a coincidental label match."""
        npt_offset = timedelta(hours=DEFAULT_PARAMS.coloc_dhm_utc_to_npt_hour_offset)
        era5_grid = pl.DataFrame(
            {
                "timestamp": [datetime(2022, 1, 1, h) for h in range(24)],
                "value": [0.0] * 24,
            }
        )
        pyramid_at = pl.DataFrame(
            {
                "timestamp": [datetime(2022, 1, 1, h) + npt_offset for h in range(24)],
                "value": [1.0] * 24,
            }
        )
        result = compute_transect_station_result(
            station=self._STATION,
            era5_grid=era5_grid,
            orography_elev_m=3863.830322265625,
            pyramid_at=pyramid_at,
        )
        expected_correction = 6.5 * (3863.830322265625 - 2660.0) / 1000.0
        assert result.lapse_correction_degc == pytest.approx(expected_correction)
        assert result.era5_corrected_hour_equalised_degc == pytest.approx(
            expected_correction
        )
        assert result.pyramid_hour_equalised_degc == pytest.approx(1.0)
        assert result.discrepancy_degc == pytest.approx(expected_correction - 1.0)
        assert result.n_era5_hours == 24
        assert result.n_pyramid_retained == 24
        assert result.n_paired == 24

    def test_era5_is_paired_to_pyramids_own_timestamps_before_averaging(self) -> None:
        """Finding 1, Plan 184 T2 review — D2: hour-of-day equalisation
        alone equalises HOUR exposure, not SEASONAL/DATE exposure. ERA5
        has full "coverage" across two disjoint dates: 2022-01-01/02 (value
        0.0) and 2022-07-01/02 (value 100.0, a season Pyramid never
        retained here). If ERA5 were averaged over its OWN full population
        (all 4 hours, naive mean 50.0) rather than paired to the 2 hours
        Pyramid actually retained (0.0), the check would compare two
        different populations, not a clean lapse residual. ERA5/Pyramid
        timestamps carry the same 6h UTC->NPT clock offset as `test_
        discrepancy_is_corrected_minus_pyramid` above (Finding A, Plan 184
        T2 round-2 review) — the retained-population logic under test is
        orthogonal to the clock reconciliation, but the fixture must still
        represent physically-real pairs."""
        npt_offset = timedelta(hours=DEFAULT_PARAMS.coloc_dhm_utc_to_npt_hour_offset)
        era5_grid = pl.DataFrame(
            {
                "timestamp": [
                    datetime(2022, 1, 1, 0),
                    datetime(2022, 1, 2, 0),
                    datetime(2022, 7, 1, 0),
                    datetime(2022, 7, 2, 0),
                ],
                "value": [0.0, 0.0, 100.0, 100.0],
            }
        )
        pyramid_at = pl.DataFrame(
            {
                "timestamp": [
                    datetime(2022, 1, 1, 0) + npt_offset,
                    datetime(2022, 1, 2, 0) + npt_offset,
                ],
                "value": [5.0, 5.0],
            }
        )
        result = compute_transect_station_result(
            station=self._STATION,
            era5_grid=era5_grid,
            # orography == station elevation => zero lapse correction,
            # keeping era5_raw == era5_corrected for a simpler assertion.
            orography_elev_m=self._STATION.elevation_m,
            pyramid_at=pyramid_at,
        )
        assert result.n_paired == 2
        assert result.era5_raw_hour_equalised_degc == pytest.approx(0.0)
        assert result.era5_corrected_hour_equalised_degc == pytest.approx(0.0)
        assert result.pyramid_hour_equalised_degc == pytest.approx(5.0)
        assert result.discrepancy_degc == pytest.approx(-5.0)
        # Context counts stay honest about each side's OWN population,
        # distinct from the paired population the means are computed over.
        assert result.n_era5_hours == 4
        assert result.n_pyramid_retained == 2

    def test_result_never_asserts_pass_fail(self, tmp_path: Path) -> None:
        """D14: the check reports a QUANTIFIED discrepancy, never a
        verdict — there is no boolean/enum field on the result type, AND
        the RENDERED REPORT TEXT never emits one either (Finding 3, Plan
        184 T2 review: a field-name-only check cannot catch `_write_report`
        emitting literal "PASS"/"FAIL" text into the table or notes, so it
        would stay green even if it did)."""
        from dataclasses import fields

        from scripts.dhm_precip.ma6_lapse_check import (
            TransectStationResult,
            _write_report,
        )

        field_names = {f.name for f in fields(TransectStationResult)}
        assert not any(
            "pass" in name or "fail" in name or "verdict" in name
            for name in field_names
        )

        result = TransectStationResult(
            pyramid_station=Station("AWS3 Lukla"),
            station_elevation_m=2660.0,
            orography_elev_m=3863.830322265625,
            lapse_correction_degc=7.82,
            n_era5_hours=24,
            n_pyramid_retained=24,
            n_paired=24,
            era5_raw_hour_equalised_degc=4.08,
            era5_corrected_hour_equalised_degc=11.91,
            pyramid_hour_equalised_degc=10.15,
            discrepancy_degc=1.76,
        )
        report_path = tmp_path / "ma6_lapse_check.md"
        _write_report(report_path, (result,))
        rendered = report_path.read_text().upper()
        assert "PASS" not in rendered
        assert "FAIL" not in rendered
        assert "VERDICT" not in rendered


class TestPairPyramidAndEra5ClockReconciliation:
    """Finding A, Plan 184 T2 round-2 review — ERA5 (UTC) and Pyramid (NPT
    wall-clock, unconverted — `pyramid_loader` module docstring) are NOT
    on the same clock. A join on raw timestamp labels pairs each side's
    DIFFERENT physical hour: this test's ERA5/Pyramid timestamps are
    chosen so raw-label joining and clock-reconciled joining produce
    DIFFERENT, both non-empty, results — a silent regression to raw-label
    joining changes the numbers here rather than merely emptying the
    join (which a crash would make too easy to notice)."""

    def test_reconciles_utc_and_npt_labels_before_joining(self) -> None:
        # ERA5 (UTC) at 00:00 and 06:00; Pyramid (NPT) at 06:00 and 12:00.
        # With the declared +6h UTC->NPT offset (params.coloc_dhm_utc_to_
        # npt_hour_offset), ERA5 00:00 UTC is ROUNDED-ALIGNED to Pyramid's
        # 06:00 NPT reading, and ERA5 06:00 UTC to Pyramid's 12:00 NPT.
        # NOT the same physical hour: NPT is UTC+5h45m, so the +6h shift
        # carries up to 15 min of residual misalignment — part of D2's
        # declared +/-1.75h (params.coloc_alignment_uncertainty_hours).
        era5_grid = pl.DataFrame(
            {
                "timestamp": [datetime(2022, 1, 1, 0), datetime(2022, 1, 1, 6)],
                "value": [100.0, 200.0],
            }
        )
        pyramid_at = pl.DataFrame(
            {
                "timestamp": [datetime(2022, 1, 1, 6), datetime(2022, 1, 1, 12)],
                "value": [5.0, 10.0],
            }
        )
        paired = pair_pyramid_and_era5(era5_grid=era5_grid, pyramid_at=pyramid_at)

        # Raw-label joining would find exactly ONE overlapping label
        # (06:00 on both sides) and wrongly pair ERA5's 06:00-UTC reading
        # (200.0) with Pyramid's 06:00-NPT reading (5.0) — a real,
        # non-crashing, WRONG pairing (~6h apart physically). The
        # clock-reconciled join must instead find BOTH physically-aligned
        # pairs and must NOT contain that wrong pairing.
        ordered = paired.sort("timestamp")
        assert paired.height == 2
        assert ordered["era5_value_degc"].to_list() == [100.0, 200.0]
        assert ordered["pyramid_value_degc"].to_list() == [5.0, 10.0]
        wrong_pairing = paired.filter(
            (pl.col("era5_value_degc") == 200.0) & (pl.col("pyramid_value_degc") == 5.0)
        )
        assert wrong_pairing.height == 0

    def test_offset_is_a_parameter_not_a_hardcoded_literal(self) -> None:
        """The offset must be an explicit, overridable parameter (never a
        literal baked into the join) — this test uses a DIFFERENT offset
        (+3h) than the repository default (+6h) and confirms the join
        follows the passed offset, not a hardcoded one."""
        era5_grid = pl.DataFrame(
            {"timestamp": [datetime(2022, 1, 1, 0)], "value": [42.0]}
        )
        pyramid_at = pl.DataFrame(
            {"timestamp": [datetime(2022, 1, 1, 3)], "value": [7.0]}
        )
        paired = pair_pyramid_and_era5(
            era5_grid=era5_grid, pyramid_at=pyramid_at, utc_to_npt_hour_offset=3
        )
        assert paired.height == 1
        assert paired["era5_value_degc"].to_list() == [42.0]


class TestPairedGaugeDiagnostic:
    """Finding B, Plan 184 T2 round-2 review — the AWS1/AWS4 gauge-to-gauge
    diagnostic must be computed on the timestamps BOTH gauges retained,
    never on each gauge's own independently-sized population (the review's
    35,019-vs-24,369-hours finding: the D2 population error relocated from
    the gauge-vs-ERA5 axis to the gauge-vs-gauge axis)."""

    def test_computes_means_over_the_common_retained_population_only(self) -> None:
        # frame_a retains hours 0,1,2; frame_b retains hours 1,2,3 — only
        # hours 1 and 2 are common. frame_a's own-population value at hour
        # 0 (10.0) and frame_b's own-population value at hour 3 (300.0)
        # must NOT enter either mean — an independent-population
        # computation (the round-2 bug) WOULD pull them in.
        frame_a = pl.DataFrame(
            {
                "timestamp": [datetime(2022, 1, 1, h) for h in (0, 1, 2)],
                "value": [10.0, 20.0, 30.0],
            }
        )
        frame_b = pl.DataFrame(
            {
                "timestamp": [datetime(2022, 1, 1, h) for h in (1, 2, 3)],
                "value": [1.0, 2.0, 300.0],
            }
        )
        diagnostic = paired_gauge_diagnostic(frame_a, frame_b)

        assert diagnostic is not None
        assert diagnostic.n_common_retained == 2
        assert diagnostic.mean_a_degc == pytest.approx(25.0)  # mean(20.0, 30.0)
        assert diagnostic.mean_b_degc == pytest.approx(1.5)  # mean(1.0, 2.0)

    def test_returns_none_when_there_is_no_common_population(self) -> None:
        frame_a = pl.DataFrame({"timestamp": [datetime(2022, 1, 1, 0)], "value": [1.0]})
        frame_b = pl.DataFrame({"timestamp": [datetime(2022, 1, 1, 1)], "value": [2.0]})
        assert paired_gauge_diagnostic(frame_a, frame_b) is None


class TestAttributionNotesUsesTheCommonRetainedDiagnostic:
    """Finding B, Plan 184 T2 round-2 review — `_attribution_notes` must
    render the AWS1/AWS4 rate FROM the passed `aws1_aws4_diagnostic`
    (the common-retained population), never by re-deriving it from each
    result's own `pyramid_hour_equalised_degc` (each station's own,
    differently-sized, ERA5-paired population)."""

    def _shared_cell_result(
        self, *, station_name: str, station_elevation_m: float
    ) -> TransectStationResult:
        return TransectStationResult(
            pyramid_station=Station(station_name),
            station_elevation_m=station_elevation_m,
            orography_elev_m=5581.1,
            lapse_correction_degc=0.0,
            n_era5_hours=100,
            n_pyramid_retained=100,
            n_paired=100,
            era5_raw_hour_equalised_degc=0.0,
            era5_corrected_hour_equalised_degc=0.0,
            # Deliberately implausible sentinel values — if the note fell
            # back to these (the round-2 bug) rather than the diagnostic,
            # the assertions below on the rendered rate would fail.
            pyramid_hour_equalised_degc=-999.0,
            discrepancy_degc=0.0,
        )

    def test_note_reports_the_diagnostics_own_rate_and_count(self) -> None:
        aws1 = self._shared_cell_result(
            station_name="AWS1 Pyramid", station_elevation_m=5035.0
        )
        aws4 = self._shared_cell_result(
            station_name="AWS4 Kala Patthar", station_elevation_m=5600.0
        )
        diagnostic = GaugeToGaugeDiagnostic(
            n_common_retained=24357, mean_a_degc=-1.8243, mean_b_degc=-4.7705
        )
        text = "\n".join(
            _attribution_notes((aws1, aws4), aws1_aws4_diagnostic=diagnostic)
        )
        assert "24357" in text
        assert "+2.946" in text  # -1.8243 - (-4.7705)
        assert "+5.215" in text  # 2.9462 degC / 0.565 km
        assert "-999.0" not in text

    def test_note_is_omitted_when_there_is_no_diagnostic(self) -> None:
        aws1 = self._shared_cell_result(
            station_name="AWS1 Pyramid", station_elevation_m=5035.0
        )
        aws4 = self._shared_cell_result(
            station_name="AWS4 Kala Patthar", station_elevation_m=5600.0
        )
        text = "\n".join(_attribution_notes((aws1, aws4), aws1_aws4_diagnostic=None))
        assert "OBSERVED rate from two gauges" not in text
        assert "share exactly the same ERA5-Land grid cell" not in text


class TestDiscoverAndLoadStationGridElevationTable:
    """D6/D14 item 2 — the DHM-gauge-station half of the lapse input,
    discovered by the SAME highest-valid-`NNNN`-with-checksum-reconciled
    convention `ma6_pairs.discover_precip_bundle` already applies, here to
    a THIRD payload (`station_grid_elevation.csv`)."""

    def test_raises_when_the_points_root_is_absent(self, tmp_path: Path) -> None:
        with pytest.raises(ExtractionInputAbsentError):
            discover_and_load_station_grid_elevation_table(tmp_path)

    def test_returns_the_highest_numbered_valid_candidates_table(
        self, tmp_path: Path
    ) -> None:
        _publish_fake_elevation_bundle(tmp_path, run_number="0000", identity="ident0")
        _publish_fake_elevation_bundle(tmp_path, run_number="0001", identity="ident1")

        table, identity = discover_and_load_station_grid_elevation_table(tmp_path)

        assert identity == "ident1"
        assert table["station"].to_list() == ["Humde Airport"]

    def test_a_checksum_mismatch_on_the_highest_candidate_raises_hard(
        self, tmp_path: Path
    ) -> None:
        directory = _publish_fake_elevation_bundle(
            tmp_path, run_number="0000", identity="ident0"
        )
        (directory / "station_grid_elevation.csv").write_bytes(b"tampered")

        with pytest.raises(ExtractionPostConditionError):
            discover_and_load_station_grid_elevation_table(tmp_path)
