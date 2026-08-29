"""Plan 216 (M-A11) T1 — locked red-first against the spec (D6):
- a metres-valued file (units defect) must be REJECTED, not silently
  read as a ~1000x wet bias.
- a real material negative increment must be REJECTED, not clamped away.
- the nearest-cell operator finds the true nearest point on an irregular
  point cloud (never a row/col assumption).
- the D6 request payload shape (origin/type/levtype/area-as-string,
  times restricted to 00/12 UTC) is exactly what the live ECDS costing
  endpoint accepted (measured 2026-08-29).
- a wrong-year/wrong-month/wrong-init-hour/wrong-lead-axis/wrong-centre
  `--skip-retrieve` file must be REJECTED (D2/D6), never silently labelled
  "JJAS TIGGE_YEAR".
- every derived artefact gets an attribution sidecar (D6 licence terms).

No network access — every test is against synthetic `xr.Dataset` fixtures
or a fake `CdsClient`, mirroring `tests/unit/scripts/test_era5_acquire.py`.
"""

from __future__ import annotations

import inspect
import json

import numpy as np
import pytest
import xarray as xr

from scripts.dhm_precip.domain_types import (
    Station,
    StationCoordinate,
    StationCoordinateTable,
)
from scripts.dhm_precip.tigge_ifs import (
    STUDY_AREA,
    TIGGE_ACKNOWLEDGEMENT_TEXT,
    TIGGE_ATTRIBUTION_TEXT,
    TIGGE_DATASET_ID,
    TiggeConservationError,
    TiggeIdentityError,
    TiggeStepAxisError,
    TiggeUnitsError,
    assert_tigge_identity,
    assert_tp_units,
    build_tigge_request,
    deaccumulate,
    extract_station_series,
    main,
    nearest_point_index,
    write_tigge_attribution,
)


def _cf_dataset(
    *,
    steps_h: list[int],
    lat: np.ndarray,
    lon: np.ndarray,
    accum_mm: np.ndarray,  # (step, points)
    units: str = "kg m**-2",
    init: str = "2025-07-15T00:00:00",
) -> xr.Dataset:
    """A minimal single-init `(step, values)` control-forecast dataset,
    shaped like the real cfgrib-opened TIGGE file (measured 2026-08-29)."""
    tp = xr.DataArray(
        accum_mm.astype(np.float32),
        dims=("step", "values"),
        attrs={"units": units, "GRIB_units": units},
    )
    return xr.Dataset(
        {"tp": tp},
        coords={
            "step": ("step", np.array(steps_h, dtype="timedelta64[h]")),
            "latitude": ("values", lat),
            "longitude": ("values", lon),
            "time": np.datetime64(init, "ns"),
        },
    )


class TestAssertTpUnits:
    """T1 Verify: 'the unit assertion has a test that fails against a
    metres-valued file — a 1000x error is the one defect here that would
    look like a result.'"""

    def test_accepts_kg_m2(self) -> None:
        ds = _cf_dataset(
            steps_h=[0, 6],
            lat=np.array([30.0, 29.0]),
            lon=np.array([81.0, 82.0]),
            accum_mm=np.array([[0.0, 0.0], [1.0, 2.0]]),
            units="kg m**-2",
        )
        assert_tp_units(ds)  # must not raise

    def test_rejects_a_metres_valued_file(self) -> None:
        """The RED case: ERA5-Land's own convention (metres) mistakenly
        applied to a TIGGE file must be caught, not silently multiplied
        away as a plausible wet bias."""
        ds = _cf_dataset(
            steps_h=[0, 6],
            lat=np.array([30.0, 29.0]),
            lon=np.array([81.0, 82.0]),
            accum_mm=np.array([[0.0, 0.0], [0.001, 0.002]]),
            units="m",
        )
        with pytest.raises(TiggeUnitsError, match="units attribute is 'm'"):
            assert_tp_units(ds)

    def test_rejects_missing_units_attribute(self) -> None:
        ds = _cf_dataset(
            steps_h=[0, 6],
            lat=np.array([30.0]),
            lon=np.array([81.0]),
            accum_mm=np.array([[0.0], [1.0]]),
            units="",
        )
        with pytest.raises(TiggeUnitsError, match="units attribute is ''"):
            assert_tp_units(ds)


class TestDeaccumulate:
    def test_produces_correct_period_ending_increments(self) -> None:
        # 3 steps (0, 6, 12h), 2 points; continuous accumulation, no reset.
        ds = _cf_dataset(
            steps_h=[0, 6, 12],
            lat=np.array([30.0, 29.0]),
            lon=np.array([81.0, 82.0]),
            accum_mm=np.array(
                [
                    [0.0, 0.0],
                    [1.5, 2.0],
                    [4.0, 2.5],
                ]
            ),
        )
        incs = deaccumulate(ds)
        assert [(i.ending_lead_hours, i.mm.tolist()) for i in incs] == [
            (6, [1.5, 2.0]),
            (12, [2.5, 0.5]),
        ]

    def test_rejects_a_material_negative_increment(self) -> None:
        """A real accumulation-day violation (not packing noise) must be
        rejected, never silently clamped to zero."""
        ds = _cf_dataset(
            steps_h=[0, 6, 12],
            lat=np.array([30.0]),
            lon=np.array([81.0]),
            accum_mm=np.array([[0.0], [5.0], [2.0]]),  # drops by 3mm — not noise
        )
        with pytest.raises(TiggeConservationError, match="negative increment"):
            deaccumulate(ds)

    def test_clamps_packing_noise_within_tolerance(self) -> None:
        ds = _cf_dataset(
            steps_h=[0, 6],
            lat=np.array([30.0]),
            lon=np.array([81.0]),
            accum_mm=np.array([[1.0], [0.999]]),  # -0.001mm noise, within tolerance
        )
        incs = deaccumulate(ds)
        assert incs[0].mm.tolist() == [0.0]

    def test_rejects_a_non_contiguous_step_axis(self) -> None:
        ds = _cf_dataset(
            steps_h=[0, 6, 18],  # missing 12
            lat=np.array([30.0]),
            lon=np.array([81.0]),
            accum_mm=np.array([[0.0], [1.0], [3.0]]),
        )
        with pytest.raises(TiggeStepAxisError, match="contiguous"):
            deaccumulate(ds)

    def test_handles_multi_init_time_dimension(self) -> None:
        """The real full-season file has `time` as a DIMENSION (244
        inits), not a scalar — this is the shape actually returned
        (measured 2026-08-29), so it must be handled, not just the
        single-init smoke-test shape."""
        tp = xr.DataArray(
            np.array(
                [
                    [[0.0], [1.0], [3.0]],  # init 0: steps 0,6,12
                    [[0.0], [2.0], [2.0]],  # init 1: steps 0,6,12
                ],
                dtype=np.float32,
            ),
            dims=("time", "step", "values"),
            attrs={"units": "kg m**-2"},
        )
        ds = xr.Dataset(
            {"tp": tp},
            coords={
                "time": (
                    "time",
                    np.array(
                        ["2025-07-15T00:00:00", "2025-07-15T12:00:00"],
                        dtype="datetime64[ns]",
                    ),
                ),
                "step": ("step", np.array([0, 6, 12], dtype="timedelta64[h]")),
                "latitude": ("values", np.array([30.0])),
                "longitude": ("values", np.array([81.0])),
            },
        )
        incs = deaccumulate(ds)
        assert len(incs) == 4  # 2 inits x 2 increments each
        by_init = sorted({str(i.init_time_utc) for i in incs})
        assert len(by_init) == 2
        second_init_incs = [i for i in incs if i.mm.tolist() == [0.0]]
        assert len(second_init_incs) == 1  # init 1's step6->12 (2.0 - 2.0 = 0)


class TestNearestPointIndex:
    def test_picks_the_true_nearest_point_on_an_irregular_cloud(self) -> None:
        # Deliberately irregular spacing — never a row/col grid assumption.
        lat = np.array([30.0, 29.0, 28.5, 26.1])
        lon = np.array([80.1, 85.0, 82.99, 88.9])
        idx = nearest_point_index(lat=lat, lon=lon, station_lat=28.5, station_lon=83.0)
        assert idx == 2

    def test_picks_nearest_even_when_it_is_not_the_first_point(self) -> None:
        lat = np.array([30.0, 26.05])
        lon = np.array([80.0, 88.95])
        idx = nearest_point_index(lat=lat, lon=lon, station_lat=26.0, station_lon=89.0)
        assert idx == 1


class TestBuildTiggeRequest:
    """D6, measured against the live ECDS costing endpoint 2026-08-29: an
    `{n,w,s,e}` area object is REJECTED ('is not of type string'); the
    accepted shape is exactly this."""

    def test_matches_the_measured_ecds_payload_shape(self) -> None:
        payload = build_tigge_request(
            year=2025,
            months=[6, 7],
            days=[1, 2],
            times=[0, 12],
            leadtime_hours=[0, 6, 12],
            area=STUDY_AREA,
        )
        assert payload["origin"] == "ecmwf"
        assert payload["forecast_type"] == "control_forecast"
        assert payload["level_type"] == "single_level"
        assert payload["variable"] == ["total_precipitation"]
        assert payload["year"] == ["2025"]
        assert payload["month"] == ["06", "07"]
        assert payload["day"] == ["01", "02"]
        assert payload["time"] == ["00:00", "12:00"]
        assert payload["leadtime_hour"] == ["0", "6", "12"]
        assert payload["data_format"] == "grib"
        assert payload["area"] == "31/80/26/89"  # STRING, not an object

    def test_accepts_a_range_not_only_a_list(self) -> None:
        """The type contract is `Sequence[int]`, not `list[int]` — `main()`
        calls this with `days=range(1, 32)`. A signature that silently
        required a `list` would be a lie the type checker could not catch
        because `range` and `list` both satisfy structural duck typing at
        the call site until iterated."""
        payload = build_tigge_request(
            year=2025,
            months=(6,),
            days=range(1, 4),
            times=(0, 12),
            leadtime_hours=(0, 6),
            area=STUDY_AREA,
        )
        assert payload["day"] == ["01", "02", "03"]


class TestAssertTiggeIdentity:
    """D2/D6/T1 Verify — a stale or wrong `--skip-retrieve` file must never
    be silently reported as 'JJAS TIGGE_YEAR'. Every axis checked here is
    read from the opened dataset itself, never assumed from a filename."""

    def test_accepts_a_matching_jjas_2025_file(self) -> None:
        ds = _cf_dataset(
            steps_h=[0, 6, 12],
            lat=np.array([30.0]),
            lon=np.array([81.0]),
            accum_mm=np.array([[0.0], [1.0], [2.0]]),
            init="2025-07-15T00:00:00",
        )
        assert_tigge_identity(ds, expected_leadtime_hours=[0, 6, 12])  # no raise

    def test_rejects_a_wrong_year(self) -> None:
        ds = _cf_dataset(
            steps_h=[0, 6],
            lat=np.array([30.0]),
            lon=np.array([81.0]),
            accum_mm=np.array([[0.0], [1.0]]),
            init="2024-07-15T00:00:00",  # not TIGGE_YEAR
        )
        with pytest.raises(TiggeIdentityError, match="year"):
            assert_tigge_identity(ds, expected_leadtime_hours=[0, 6])

    def test_rejects_a_month_outside_jjas(self) -> None:
        ds = _cf_dataset(
            steps_h=[0, 6],
            lat=np.array([30.0]),
            lon=np.array([81.0]),
            accum_mm=np.array([[0.0], [1.0]]),
            init="2025-10-15T00:00:00",  # October, not JJAS
        )
        with pytest.raises(TiggeIdentityError, match="month"):
            assert_tigge_identity(ds, expected_leadtime_hours=[0, 6])

    def test_rejects_an_init_hour_outside_00_12_utc(self) -> None:
        ds = _cf_dataset(
            steps_h=[0, 6],
            lat=np.array([30.0]),
            lon=np.array([81.0]),
            accum_mm=np.array([[0.0], [1.0]]),
            init="2025-07-15T06:00:00",  # not 00 or 12 UTC
        )
        with pytest.raises(TiggeIdentityError, match="hour"):
            assert_tigge_identity(ds, expected_leadtime_hours=[0, 6])

    def test_rejects_a_lead_axis_that_does_not_match_the_request(self) -> None:
        ds = _cf_dataset(
            steps_h=[0, 6, 18],  # not the requested [0, 6, 12]
            lat=np.array([30.0]),
            lon=np.array([81.0]),
            accum_mm=np.array([[0.0], [1.0], [3.0]]),
            init="2025-07-15T00:00:00",
        )
        with pytest.raises(TiggeIdentityError, match="lead"):
            assert_tigge_identity(ds, expected_leadtime_hours=[0, 6, 12])

    def test_rejects_a_non_ecmwf_centre_attribute(self) -> None:
        ds = _cf_dataset(
            steps_h=[0, 6],
            lat=np.array([30.0]),
            lon=np.array([81.0]),
            accum_mm=np.array([[0.0], [1.0]]),
            init="2025-07-15T00:00:00",
        )
        ds.attrs["GRIB_centre"] = "kwbc"  # NCEP, not ECMWF
        with pytest.raises(TiggeIdentityError, match="centre"):
            assert_tigge_identity(ds, expected_leadtime_hours=[0, 6])

    def test_a_units_defect_is_still_caught_first(self) -> None:
        """`assert_tigge_identity` runs `assert_tp_units` internally — a
        metres-valued file must fail there, not pass through to the axis
        checks."""
        ds = _cf_dataset(
            steps_h=[0, 6],
            lat=np.array([30.0]),
            lon=np.array([81.0]),
            accum_mm=np.array([[0.0], [0.001]]),
            units="m",
            init="2025-07-15T00:00:00",
        )
        with pytest.raises(TiggeUnitsError):
            assert_tigge_identity(ds, expected_leadtime_hours=[0, 6])


class TestWriteTiggeAttribution:
    """D6 — 'Attribution to ECMWF and acknowledgement of TIGGE are licence
    conditions on any output.' Every derived artefact (T1's raw extraction
    AND T2's comparison CSV) gets an adjacent, tested sidecar — never
    stdout-only, which does not travel with the file."""

    def test_writes_an_adjacent_sidecar_with_the_required_fields(
        self, tmp_path: object
    ) -> None:
        output_path = tmp_path / "some_output.parquet"  # type: ignore[operator]
        sidecar = write_tigge_attribution(output_path)
        assert sidecar == output_path.with_name("some_output.parquet.attribution.json")
        record = json.loads(sidecar.read_text())
        assert record["attribution"] == TIGGE_ATTRIBUTION_TEXT
        assert record["acknowledgement"] == TIGGE_ACKNOWLEDGEMENT_TEXT
        assert record["source_dataset"] == TIGGE_DATASET_ID
        assert record["for_file"] == "some_output.parquet"
        assert "licence_note" in record

    def test_extra_fields_are_merged_in(self, tmp_path: object) -> None:
        output_path = tmp_path / "out.csv"  # type: ignore[operator]
        sidecar = write_tigge_attribution(output_path, extra={"n_rows": 42})
        record = json.loads(sidecar.read_text())
        assert record["n_rows"] == 42
        assert record["attribution"] == TIGGE_ATTRIBUTION_TEXT  # not clobbered


class TestExpectedStationsIndependence:
    """The `expected_stations` passed to `load_station_coordinates` inside
    `main()` must come from an INDEPENDENT inventory (the gauge
    population), never a second read of the very same coordinate file
    `load_station_coordinates` is about to validate — that would make the
    equality check inside it a tautology that can never fail (the same
    anti-pattern flagged in `extract_era5.py`'s prior review). This is a
    source-level lock, mirroring `TestCleanCheckoutImport` in
    `test_tigge_gauge_timing.py` — the only practical way to pin an
    ordering/data-flow property of a CLI `main()` that always touches real
    files and a real (mocked-at-the-edge) network."""

    def test_main_sources_expected_stations_from_the_gauge_population(self) -> None:
        source = inspect.getsource(main)
        assert "load_gauge_masked_population" in source
        gauge_call = source.index("load_gauge_masked_population")
        coords_call = source.index("load_station_coordinates(")
        assert gauge_call < coords_call, (
            "expected_stations must be built from the gauge population "
            "BEFORE load_station_coordinates is called with it"
        )
        # Never re-derive expected_stations from `coords_path` itself —
        # that is the exact tautology this test forbids.
        assert "expected_stations=frozenset(coords" not in source


class TestExtractStationSeries:
    def test_valid_time_is_init_plus_lead_and_gaps_are_never_filled(self) -> None:
        ds = _cf_dataset(
            steps_h=[0, 6, 12],
            lat=np.array([30.0, 26.0]),
            lon=np.array([80.0, 89.0]),
            accum_mm=np.array([[0.0, 0.0], [1.0, 2.0], [3.0, 2.0]]),
        )
        incs = deaccumulate(ds)
        coords = StationCoordinateTable(
            by_station={
                Station("Near80N30"): StationCoordinate(
                    station=Station("Near80N30"),
                    excel_col="A",
                    lat=30.0,
                    lon=80.0,
                    elev_m=500.0,
                ),
                Station("Near89S26"): StationCoordinate(
                    station=Station("Near89S26"),
                    excel_col="B",
                    lat=26.0,
                    lon=89.0,
                    elev_m=1500.0,
                ),
            }
        )
        series = extract_station_series(ds, incs, coords)
        assert series.height == 2 * 2  # 2 stations x 2 increments
        row = series.filter(
            (series["station"] == "Near80N30") & (series["ending_lead_hours"] == 6)
        ).row(0, named=True)
        assert row["tigge_mm"] == pytest.approx(1.0)
        assert (
            row["valid_time_utc"] - row["init_time_utc"]
        ).total_seconds() == 6 * 3600
