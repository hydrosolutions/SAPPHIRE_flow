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
from datetime import UTC, datetime
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
    TransectStation,
    compute_transect_station_result,
    discover_and_load_station_grid_elevation_table,
    hour_of_day_equalised_mean,
    lapse_correct_to_station_degc,
)
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
    """D14 — model orography sits ABOVE the station for every high-altitude
    case, so the correction WARMS. Verified against the real published
    `station_grid_elevation.csv` values the plan's D14 amendment cites
    (2026-08-20)."""

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
        era5_grid = pl.DataFrame(
            {
                "timestamp": [datetime(2022, 1, 1, h) for h in range(24)],
                "value": [0.0] * 24,
            }
        )
        pyramid_at = pl.DataFrame(
            {
                "timestamp": [datetime(2022, 1, 1, h) for h in range(24)],
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
        different populations, not a clean lapse residual."""
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
                "timestamp": [datetime(2022, 1, 1, 0), datetime(2022, 1, 2, 0)],
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
