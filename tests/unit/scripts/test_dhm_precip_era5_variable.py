"""ERA5-Land multi-variable safety (2026-08-19).

Plan 184 D14 needs `2m_temperature` alongside `total_precipitation`. Before
this change the pipeline was single-variable in three places that would each
have destroyed data SILENTLY:

* the raw path was `era5_land_tp_raw_{window_id}.nc` — no variable — so a
  temperature fetch overwrote the precipitation archive under a filename
  claiming to be precipitation;
* the manifest's acquisition-wide immutability guard checked `dataset`, which
  is identical for both variables, so it never fired; and
* the transform stage would have deaccumulated an INSTANTANEOUS field against
  ERA5-Land's 01 UTC reset and multiplied Kelvin by the m->mm factor.

The resume guard made the first one worse rather than safer: a different
variable yields a different request identity, so the window reads as stale and
is re-downloaded straight over the existing file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pytest
import xarray as xr

from scripts.dhm_precip.acquire_era5 import _exit_code_for, build_parser, run
from scripts.dhm_precip.era5_acquire import _validate_variable
from scripts.dhm_precip.era5_errors import (
    Era5StageNotApplicableError,
    Era5StorageError,
    Era5ValidationError,
)
from scripts.dhm_precip.era5_manifest import (
    Era5ProvenanceManifest,
    OperatorProvenance,
    raw_artifact_path,
    read_manifest,
    write_manifest_atomic,
)
from scripts.dhm_precip.era5_request import (
    DEFAULT_REQUEST_SPEC,
    Era5Accumulation,
    Era5RequestSpec,
    accumulation_of,
    build_request_payload,
    expected_grid_shape,
    expected_units,
    parse_window_arg,
    variable_code,
)

if TYPE_CHECKING:
    import argparse


class TestVariableRegistry:
    def test_precipitation_is_accumulated_and_temperature_is_not(self) -> None:
        assert accumulation_of("total_precipitation") is Era5Accumulation.ACCUMULATED
        assert accumulation_of("2m_temperature") is Era5Accumulation.INSTANTANEOUS

    def test_an_unknown_variable_is_rejected_at_spec_construction(self) -> None:
        with pytest.raises(ValueError, match="unknown ERA5-Land variable"):
            Era5RequestSpec(variable="sea_surface_temperature")

    def test_the_payload_carries_the_requested_variable(self) -> None:
        spec = Era5RequestSpec(variable="2m_temperature")
        payload = build_request_payload(parse_window_arg("2021-10"), spec)
        assert payload["variable"] == ["2m_temperature"]


class TestRawPathIsKeyedByVariable:
    """The clobber that motivated this change."""

    def test_temperature_and_precipitation_do_not_share_a_raw_path(self) -> None:
        root = Path("/tmp/root")
        tp = raw_artifact_path(
            "2020-01", root, variable_code=variable_code("total_precipitation")
        )
        t2m = raw_artifact_path(
            "2020-01", root, variable_code=variable_code("2m_temperature")
        )
        assert tp != t2m
        assert "t2m" in t2m.name

    def test_the_existing_precipitation_path_is_unchanged(self) -> None:
        """Every artefact already on disk must keep its current path — this
        change must not orphan the 74 acquired precipitation windows."""
        root = Path("/tmp/root")
        assert raw_artifact_path("2020-01", root).name == "era5_land_tp_raw_2020-01.nc"


class TestTransformRefusesAnInstantaneousVariable:
    def _args(self, **over: object) -> argparse.Namespace:
        args = build_parser().parse_args(
            ["--provenance", "/nonexistent.json", "--stage", "transform"]
        )
        for k, v in over.items():
            setattr(args, k, v)
        return args

    @pytest.mark.parametrize("stage", ["transform", "all"])
    def test_temperature_cannot_reach_the_deaccumulator(self, stage: str) -> None:
        args = self._args(stage=stage, variable="2m_temperature")
        with pytest.raises(Era5StageNotApplicableError, match="INSTANTANEOUS"):
            run(args)

    def test_the_refusal_precedes_reading_the_provenance_file(self) -> None:
        """The guard must cost nothing: --provenance points at a file that does
        not exist, so if the guard fired AFTER provenance loading this would
        raise a storage error instead."""
        args = self._args(stage="transform", variable="2m_temperature")
        with pytest.raises(Era5StageNotApplicableError):
            run(args)

    def test_precipitation_is_not_refused(self) -> None:
        """The guard must not block the variable the pipeline was built for —
        this fails past the guard, on the absent provenance file."""
        args = self._args(stage="transform", variable="total_precipitation")
        with pytest.raises((Era5StorageError, OSError)):
            run(args)


class TestExitCodes:
    def test_stage_not_applicable_maps_to_7(self) -> None:
        assert _exit_code_for(Era5StageNotApplicableError("x")) == 7

    def test_storage_still_maps_to_5(self) -> None:
        assert _exit_code_for(Era5StorageError("x")) == 5


class TestCliDefaultsAreUnchanged:
    def test_variable_defaults_to_precipitation(self) -> None:
        args = build_parser().parse_args(["--provenance", "p.json"])
        assert args.variable == DEFAULT_REQUEST_SPEC.variable == "total_precipitation"

    def test_an_unknown_variable_is_rejected_by_argparse(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(
                ["--provenance", "p.json", "--variable", "not_a_variable"]
            )


class TestRawValidationIsVariableAware:
    """The fourth single-variable assumption, found by the real fetch: the raw
    schema check pinned BOTH the netCDF data-variable name ('tp') and its units
    (metres). The temperature download succeeded and was then rejected with
    "expected variable 'tp' absent; got ['t2m']".
    """

    WINDOW = "2021-10-01"  # a whole DAY: 24 hourly stamps

    def _dataset(self, name: str, units: str) -> xr.Dataset:
        n_lat, n_lon = expected_grid_shape(DEFAULT_REQUEST_SPEC.area)
        north, west, south, east = DEFAULT_REQUEST_SPEC.area
        stamps = np.array(
            [np.datetime64(f"2021-10-01T{h:02d}", "ns") for h in range(24)]
        )
        return xr.Dataset(
            {
                name: (
                    ("valid_time", "latitude", "longitude"),
                    np.zeros((stamps.size, n_lat, n_lon), dtype="float32"),
                    {"units": units},
                )
            },
            coords={
                "valid_time": stamps,
                "latitude": np.linspace(north, south, n_lat),
                "longitude": np.linspace(west, east, n_lon),
            },
        )

    def test_expected_units_differ_by_variable(self) -> None:
        assert "m" in expected_units("total_precipitation")
        assert "k" in expected_units("2m_temperature")
        assert "m" not in expected_units("2m_temperature")

    def test_a_kelvin_t2m_file_validates(self) -> None:
        spec = Era5RequestSpec(variable="2m_temperature")
        _validate_variable(
            self._dataset("t2m", "K"), window=parse_window_arg(self.WINDOW), spec=spec
        )

    def test_a_t2m_file_labelled_in_metres_is_rejected(self) -> None:
        """Units are checked, not assumed — the transform's m->mm factor is
        meaningless if the source is not what it claims to be."""
        spec = Era5RequestSpec(variable="2m_temperature")
        with pytest.raises(Era5ValidationError, match="units attribute"):
            _validate_variable(
                self._dataset("t2m", "m"),
                window=parse_window_arg(self.WINDOW),
                spec=spec,
            )

    def test_precipitation_validation_is_unchanged(self) -> None:
        _validate_variable(
            self._dataset("tp", "m"),
            window=parse_window_arg(self.WINDOW),
            spec=DEFAULT_REQUEST_SPEC,
        )

    def test_the_wrong_variable_for_the_spec_is_still_rejected(self) -> None:
        with pytest.raises(Era5ValidationError, match="expected variable 't2m'"):
            _validate_variable(
                self._dataset("tp", "m"),
                window=parse_window_arg(self.WINDOW),
                spec=Era5RequestSpec(variable="2m_temperature"),
            )


class TestTheVariableSurvivesTheDISK:
    """The guard is only as good as its persistence, and this is where it first
    failed: the domain dataclass carried `variable`, but the pydantic boundary
    model did not, so the field was written, dropped on serialisation, and read
    back as the default. The acquisition-wide guard then rejected the very data
    root it had just created — a false positive that halted a legitimate fetch.

    Testing the guard's LOGIC did not catch this. Only a round-trip does.
    """

    def _manifest(self, variable: str) -> Era5ProvenanceManifest:
        return Era5ProvenanceManifest(
            dataset="reanalysis-era5-land",
            variable=variable,
            client_package_version="1.2.3",
            operator_provenance=OperatorProvenance(
                cds_portal_url="https://cds.climate.copernicus.eu",
                dataset_landing_page_url="https://cds.climate.copernicus.eu/x",
                licence_name="Licence to use Copernicus Products",
                licence_version="1.2",
                licence_accepted_at=datetime(2026, 8, 19, tzinfo=UTC),
            ),
        )

    def test_temperature_round_trips_through_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        write_manifest_atomic(self._manifest("2m_temperature"), path)
        loaded = read_manifest(path)
        assert loaded is not None
        assert loaded.variable == "2m_temperature"

    def test_the_variable_is_actually_serialised(self, tmp_path: Path) -> None:
        """Read-back alone could pass on a default; assert the key is on disk."""
        path = tmp_path / "manifest.json"
        write_manifest_atomic(self._manifest("2m_temperature"), path)
        assert json.loads(path.read_text())["variable"] == "2m_temperature"

    def test_a_manifest_without_the_field_reads_as_precipitation(
        self, tmp_path: Path
    ) -> None:
        """Back-compat: every manifest written before the field existed IS
        precipitation, and must keep loading rather than raising."""
        path = tmp_path / "manifest.json"
        write_manifest_atomic(self._manifest("total_precipitation"), path)
        raw = json.loads(path.read_text())
        del raw["variable"]
        path.write_text(json.dumps(raw))
        loaded = read_manifest(path)
        assert loaded is not None
        assert loaded.variable == "total_precipitation"
