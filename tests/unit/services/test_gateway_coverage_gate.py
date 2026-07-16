"""Plan 082 Phase 3: Gateway coverage manifest + training-readiness gate."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.gateway_coverage import (
    GatewayCoverageKey,
    GatewayCoverageManifest,
    GatewayCoverageSpan,
    assert_returned_span_covers_request,
    build_coverage_manifest,
    coverage_spans_window,
    parse_coverage_manifest_row,
)
from sapphire_flow.types.datetime import ensure_utc


def _dt(*args: int) -> object:
    return datetime(*args, tzinfo=UTC)


_BASIN_ROW: dict[str, object] = {
    "gateway_hru_name": "hru_dhm_west_v001",
    "name": "g_15013",
    "dataset": "era5_land",
    "variable": "precipitation",
    "spatial_type": "basin_average",
    "start": _dt(2015, 1, 1),
    "end": _dt(2025, 1, 1),
}

_BAND_ROW: dict[str, object] = {
    "gateway_hru_name": "hru_dhm_west_bands_v001",
    "name": "g_test01_band_1",
    "dataset": "jsnow_reanalysis",
    "variable": "snow_depth",
    "spatial_type": "elevation_band",
    "band_id": 1,
    "start": _dt(2018, 1, 1),
    "end": _dt(2025, 1, 1),
}


class TestGatewayCoverageManifest:
    def test_well_formed_row_round_trips_with_exact_key_and_span(self) -> None:
        key, span = parse_coverage_manifest_row(_BASIN_ROW)

        assert key == GatewayCoverageKey(
            gateway_hru_name="hru_dhm_west_v001",
            name="g_15013",
            dataset="era5_land",
            variable="precipitation",
            band_id=None,
        )
        assert span.start == ensure_utc(_dt(2015, 1, 1))
        assert span.end == ensure_utc(_dt(2025, 1, 1))

    def test_band_row_round_trips_with_band_id_in_key(self) -> None:
        key, _span = parse_coverage_manifest_row(_BAND_ROW)
        assert key.band_id == 1

    @pytest.mark.parametrize("missing_field", ["band_id", "variable"])
    def test_missing_required_field_is_rejected_at_construction(
        self, missing_field: str
    ) -> None:
        row = dict(_BAND_ROW) if missing_field == "band_id" else dict(_BASIN_ROW)
        row.pop(missing_field, None)

        with pytest.raises(ConfigurationError, match=missing_field):
            parse_coverage_manifest_row(row)

    def test_no_inference_from_data_only_row_with_no_declared_span(self) -> None:
        """A fixture carrying DATA-shaped fields (e.g. a row count) but no
        declared start/end must NOT silently produce a coverage span — the
        model has no constructor path that infers from non-empty data."""
        row = {
            "gateway_hru_name": "hru_dhm_west_v001",
            "name": "g_15013",
            "dataset": "era5_land",
            "variable": "precipitation",
            "spatial_type": "basin_average",
            "row_count": 999999,  # irrelevant: not a recognized span field
        }
        with pytest.raises(ConfigurationError, match="start"):
            parse_coverage_manifest_row(row)

    def test_manifest_member_agnostic_key_has_no_member_field(self) -> None:
        key, _ = parse_coverage_manifest_row(_BASIN_ROW)
        assert not hasattr(key, "member_id")


class TestGatewayCoverageGate:
    def _manifest(self) -> GatewayCoverageManifest:
        return build_coverage_manifest([_BASIN_ROW, _BAND_ROW])

    def _key(self, **overrides: object) -> GatewayCoverageKey:
        base = {
            "gateway_hru_name": "hru_dhm_west_v001",
            "name": "g_15013",
            "dataset": "era5_land",
            "variable": "precipitation",
            "band_id": None,
        }
        base.update(overrides)
        return GatewayCoverageKey(**base)  # type: ignore[arg-type]

    def test_window_inside_covered_span_is_true(self) -> None:
        manifest = self._manifest()
        window = GatewayCoverageSpan(
            start=ensure_utc(_dt(2016, 1, 1)), end=ensure_utc(_dt(2020, 1, 1))
        )
        assert coverage_spans_window(manifest, window, [self._key()]) is True

    def test_window_one_day_past_covered_span_is_false(self) -> None:
        manifest = self._manifest()
        window = GatewayCoverageSpan(
            start=ensure_utc(_dt(2015, 1, 1)),
            end=ensure_utc(datetime(2025, 1, 2, tzinfo=UTC)),
        )
        assert coverage_spans_window(manifest, window, [self._key()]) is False

    def test_required_key_missing_from_manifest_is_refused(self) -> None:
        manifest = self._manifest()
        window = GatewayCoverageSpan(
            start=ensure_utc(_dt(2016, 1, 1)), end=ensure_utc(_dt(2020, 1, 1))
        )
        unknown_key = self._key(variable="temperature")
        assert coverage_spans_window(manifest, window, [unknown_key]) is False

    def test_all_required_keys_must_be_covered(self) -> None:
        manifest = self._manifest()
        window = GatewayCoverageSpan(
            start=ensure_utc(_dt(2019, 1, 1)), end=ensure_utc(_dt(2020, 1, 1))
        )
        covered_key = self._key()
        uncovered_key = self._key(variable="swe")
        assert (
            coverage_spans_window(manifest, window, [covered_key, uncovered_key])
            is False
        )


class TestAssertReturnedSpanCoversRequest:
    def test_returned_span_covering_request_does_not_raise(self) -> None:
        requested = GatewayCoverageSpan(
            start=ensure_utc(_dt(2020, 1, 1)), end=ensure_utc(_dt(2020, 2, 1))
        )
        returned = GatewayCoverageSpan(
            start=ensure_utc(_dt(2020, 1, 1)), end=ensure_utc(_dt(2020, 2, 1))
        )
        assert_returned_span_covers_request(requested, returned)

    def test_returned_span_short_of_request_raises(self) -> None:
        requested = GatewayCoverageSpan(
            start=ensure_utc(_dt(2020, 1, 1)), end=ensure_utc(_dt(2020, 2, 1))
        )
        returned = GatewayCoverageSpan(
            start=ensure_utc(_dt(2020, 1, 1)), end=ensure_utc(_dt(2020, 1, 15))
        )
        with pytest.raises(ConfigurationError, match="shorter"):
            assert_returned_span_covers_request(requested, returned)
