"""Plan 193 (M-A7) task T3 — transferability and elevation/resolution
stratification.

Seam tests: every assertion is on a VALUE actually produced (a station
count, an error field, a raised exception), never on an argument passed to
a mock (`ma6_pairs`'s own "Seam tests" convention, reused here)."""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.ma6_estimands import ElevationBand
from scripts.dhm_precip.ma6_pairs import MaskedGaugeSeries
from scripts.dhm_precip.ma7_intensity import (
    BandIntensityDistribution,
    StationIntensityDistribution,
)
from scripts.dhm_precip.ma7_transfer import (
    DECLINED_ATTRIBUTION,
    DuplicateGroupError,
    MismatchedStationPopulationError,
    ResolutionGroupIntensityDistribution,
    ResolutionGroupLabel,
    ResolutionGroupMembershipError,
    TailPredictionError,
    build_resolution_groups,
    compare_transferability,
    elevation_band_prediction_error,
    resolution_group_prediction_error,
)
from scripts.dhm_precip.seasons import Season

_STATION_A = Station("Alpha")
_STATION_B = Station("Beta")
_STATION_C = Station("Gamma")
_STATION_D = Station("Delta")


def _series(station: Station, rows: list[dict[str, object]]) -> MaskedGaugeSeries:
    frame = pl.DataFrame(rows).with_columns(
        pl.lit(str(station)).alias("station"),
        pl.col("timestamp").cast(pl.Datetime("us")),
    )
    return MaskedGaugeSeries(frame=frame.select("station", "timestamp", "value_mm"))


def _jjas_rows(year: int, values: list[float]) -> list[dict[str, object]]:
    base = datetime(year, 7, 1)
    return [
        {"timestamp": base + timedelta(hours=i), "value_mm": v}
        for i, v in enumerate(values)
    ]


def _dist(
    station: Station, values: list[float], *, year: int = 2022
) -> StationIntensityDistribution:
    return StationIntensityDistribution(
        series=_series(station, _jjas_rows(year, values)), season=Season.JJAS
    )


# Wet-hour values (all >= 0.2 mm/h wet floor) chosen so q50/q99 are stable
# and each station's q99/q50 ratio is distinct.
_WET_A = [float(v) for v in range(1, 101)]  # q50=50.5, q99~99.01
_WET_B = [float(v) * 2 for v in range(1, 101)]  # same shape, doubled scale
_WET_C = [float(v) * 0.5 for v in range(1, 101)]  # same shape, halved scale
_ELEV = {_STATION_A: 500.0, _STATION_B: 600.0, _STATION_C: 650.0}
_RES_GROUP: dict[Station, ResolutionGroupLabel] = {
    _STATION_A: "A",
    _STATION_B: "A",
    _STATION_C: "B",
}


def _band(
    members: tuple[StationIntensityDistribution, ...],
) -> BandIntensityDistribution:
    return BandIntensityDistribution(
        band=ElevationBand.BELOW_700M, members=members, station_elev_m=_ELEV
    )


class TestResolutionGroupIntensityDistribution:
    def test_station_count_and_season(self) -> None:
        members = (_dist(_STATION_A, _WET_A), _dist(_STATION_B, _WET_B))
        group = ResolutionGroupIntensityDistribution(
            group="A", members=members, station_resolution_group=_RES_GROUP
        )
        assert group.station_count == 2
        assert group.season is Season.JJAS

    def test_zero_members_rejected(self) -> None:
        with pytest.raises(ValueError, match="zero member stations"):
            ResolutionGroupIntensityDistribution(
                group="A", members=(), station_resolution_group=_RES_GROUP
            )

    def test_member_with_wrong_declared_group_rejected(self) -> None:
        members = (_dist(_STATION_C, _WET_C),)  # Gamma is classified "B"
        with pytest.raises(ResolutionGroupMembershipError):
            ResolutionGroupIntensityDistribution(
                group="A", members=members, station_resolution_group=_RES_GROUP
            )

    def test_member_missing_from_mapping_rejected(self) -> None:
        members = (_dist(_STATION_D, _WET_A),)  # Delta has no known group
        with pytest.raises(ResolutionGroupMembershipError):
            ResolutionGroupIntensityDistribution(
                group="A", members=members, station_resolution_group=_RES_GROUP
            )

    def test_duplicate_station_rejected(self) -> None:
        member = _dist(_STATION_A, _WET_A)
        with pytest.raises(Exception, match="repeated station"):
            ResolutionGroupIntensityDistribution(
                group="A", members=(member, member), station_resolution_group=_RES_GROUP
            )


class TestBuildResolutionGroups:
    def test_partitions_by_label_sorted(self) -> None:
        members = (
            _dist(_STATION_A, _WET_A),
            _dist(_STATION_B, _WET_B),
            _dist(_STATION_C, _WET_C),
        )
        groups = build_resolution_groups(members, _RES_GROUP)
        labels = [g.group for g in groups]
        assert labels == ["A", "B"]
        assert groups[0].station_count == 2
        assert groups[1].station_count == 1

    def test_unknown_station_raises(self) -> None:
        members = (_dist(_STATION_D, _WET_A),)
        with pytest.raises(ResolutionGroupMembershipError):
            build_resolution_groups(members, _RES_GROUP)


class TestTailPredictionErrorInvariants:
    def test_negative_n_stations_used_rejected(self) -> None:
        with pytest.raises(ValueError, match="n_stations_used"):
            TailPredictionError(
                median_abs_error=0.0,
                min_error=0.0,
                max_error=0.0,
                within_25pct_fraction=1.0,
                n_stations_used=-1,
                station_count=2,
            )

    def test_n_stations_used_exceeding_station_count_rejected(self) -> None:
        with pytest.raises(ValueError, match="exceeds"):
            TailPredictionError(
                median_abs_error=0.0,
                min_error=0.0,
                max_error=0.0,
                within_25pct_fraction=1.0,
                n_stations_used=5,
                station_count=2,
            )

    def test_within_25pct_fraction_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="within_25pct_fraction"):
            TailPredictionError(
                median_abs_error=0.0,
                min_error=0.0,
                max_error=0.0,
                within_25pct_fraction=1.5,
                n_stations_used=2,
                station_count=2,
            )

    def test_min_exceeding_max_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_error"):
            TailPredictionError(
                median_abs_error=0.0,
                min_error=1.0,
                max_error=-1.0,
                within_25pct_fraction=1.0,
                n_stations_used=2,
                station_count=2,
            )

    def test_nan_error_fields_are_accepted(self) -> None:
        # The degenerate "no comparison possible" state — a single-station
        # group — must not crash the type; it is a legitimate result.
        result = TailPredictionError(
            median_abs_error=float("nan"),
            min_error=float("nan"),
            max_error=float("nan"),
            within_25pct_fraction=float("nan"),
            n_stations_used=1,
            station_count=1,
        )
        assert result.n_stations_used == 1


class TestElevationBandPredictionError:
    def test_is_the_pinned_prediction_error_shape(self) -> None:
        band = _band((_dist(_STATION_A, _WET_A), _dist(_STATION_B, _WET_B)))
        result = elevation_band_prediction_error(band)
        assert isinstance(result, TailPredictionError)
        assert result.station_count == 2
        assert result.n_stations_used == 2

    def test_value_changes_when_the_underlying_distributions_change(self) -> None:
        # The plan's own required regression: the reported field IS a
        # prediction error, and its value moves when the input data moves.
        band_matched = _band((_dist(_STATION_A, _WET_A), _dist(_STATION_B, _WET_B)))
        # Same shape (A) but a wildly divergent tail for the second member.
        divergent_b = [float(v) for v in range(1, 100)] + [10_000.0]
        band_divergent = _band(
            (_dist(_STATION_A, _WET_A), _dist(_STATION_B, divergent_b))
        )
        result_matched = elevation_band_prediction_error(band_matched)
        result_divergent = elevation_band_prediction_error(band_divergent)
        assert result_matched.median_abs_error != pytest.approx(
            result_divergent.median_abs_error
        )
        assert result_divergent.max_error > result_matched.max_error

    def test_perfectly_proportional_stations_give_near_zero_error(self) -> None:
        # A/B/C share one shape at three different scales — the pooled
        # ratio predicts every held-out station's q99 almost exactly.
        band = _band(
            (
                _dist(_STATION_A, _WET_A),
                _dist(_STATION_B, _WET_B),
                _dist(_STATION_C, _WET_C),
            )
        )
        result = elevation_band_prediction_error(band)
        assert result.median_abs_error == pytest.approx(0.0, abs=1e-6)
        assert result.within_25pct_fraction == pytest.approx(1.0)

    def test_deterministic_same_inputs_same_output(self) -> None:
        band = _band((_dist(_STATION_A, _WET_A), _dist(_STATION_B, _WET_B)))
        r1 = elevation_band_prediction_error(band)
        r2 = elevation_band_prediction_error(band)
        assert r1 == r2


class TestResolutionGroupPredictionError:
    def test_value_changes_when_the_underlying_distributions_change(self) -> None:
        group_matched = ResolutionGroupIntensityDistribution(
            group="A",
            members=(_dist(_STATION_A, _WET_A), _dist(_STATION_B, _WET_B)),
            station_resolution_group=_RES_GROUP,
        )
        divergent_b = [float(v) for v in range(1, 100)] + [10_000.0]
        group_divergent = ResolutionGroupIntensityDistribution(
            group="A",
            members=(_dist(_STATION_A, _WET_A), _dist(_STATION_B, divergent_b)),
            station_resolution_group=_RES_GROUP,
        )
        result_matched = resolution_group_prediction_error(group_matched)
        result_divergent = resolution_group_prediction_error(group_divergent)
        assert result_matched.median_abs_error != pytest.approx(
            result_divergent.median_abs_error
        )


class TestCompareTransferability:
    def _bands(self) -> tuple[BandIntensityDistribution, ...]:
        return (_band((_dist(_STATION_A, _WET_A), _dist(_STATION_B, _WET_B))),)

    def _groups_matching(self) -> tuple[ResolutionGroupIntensityDistribution, ...]:
        # Both bands' members land in resolution group "A" together so the
        # group has >= 2 members and produces a non-degenerate (non-nan)
        # result — a singleton group's all-nan TailPredictionError would
        # break equality comparisons (nan != nan) in the determinism test.
        return build_resolution_groups(
            (_dist(_STATION_A, _WET_A), _dist(_STATION_B, _WET_B)),
            {_STATION_A: "A", _STATION_B: "A"},
        )

    def test_carries_the_pinned_declined_attribution(self) -> None:
        comparison = compare_transferability(
            by_band=self._bands(), by_resolution_group=self._groups_matching()
        )
        assert comparison.declined_attribution == DECLINED_ATTRIBUTION

    def test_declined_attribution_cannot_be_overridden(self) -> None:
        comparison = compare_transferability(
            by_band=self._bands(), by_resolution_group=self._groups_matching()
        )
        from dataclasses import replace

        with pytest.raises(ValueError, match="not the pinned D6 refusal text"):
            replace(comparison, declined_attribution="something else")

    def test_both_cuts_present_side_by_side(self) -> None:
        comparison = compare_transferability(
            by_band=self._bands(), by_resolution_group=self._groups_matching()
        )
        assert ElevationBand.BELOW_700M in comparison.by_elevation_band
        assert set(comparison.by_resolution_group) == {"A"}
        assert comparison.season is Season.JJAS

    def test_both_resolution_groups_reported_when_present(self) -> None:
        # A/B in resolution group A, C in resolution group B — all three in
        # the same elevation band — exercises both sides of D6's cut.
        band = _band(
            (
                _dist(_STATION_A, _WET_A),
                _dist(_STATION_B, _WET_B),
                _dist(_STATION_C, _WET_C),
            )
        )
        groups = build_resolution_groups(
            (
                _dist(_STATION_A, _WET_A),
                _dist(_STATION_B, _WET_B),
                _dist(_STATION_C, _WET_C),
            ),
            {_STATION_A: "A", _STATION_B: "A", _STATION_C: "B"},
        )
        comparison = compare_transferability(
            by_band=(band,), by_resolution_group=groups
        )
        assert set(comparison.by_resolution_group) == {"A", "B"}
        assert comparison.by_resolution_group["A"].station_count == 2
        assert comparison.by_resolution_group["B"].station_count == 1

    def test_mismatched_station_population_rejected(self) -> None:
        # Bands cover {A, B}; resolution groups cover only {A}.
        mismatched_groups = build_resolution_groups(
            (_dist(_STATION_A, _WET_A),), {_STATION_A: "A"}
        )
        with pytest.raises(MismatchedStationPopulationError):
            compare_transferability(
                by_band=self._bands(), by_resolution_group=mismatched_groups
            )

    def test_duplicate_band_rejected(self) -> None:
        band = _band((_dist(_STATION_A, _WET_A),))
        with pytest.raises(DuplicateGroupError):
            compare_transferability(
                by_band=(band, band), by_resolution_group=self._groups_matching()
            )

    def test_empty_by_band_rejected(self) -> None:
        with pytest.raises(ValueError, match="zero elevation bands"):
            compare_transferability(
                by_band=(), by_resolution_group=self._groups_matching()
            )

    def test_deterministic_same_inputs_same_output(self) -> None:
        c1 = compare_transferability(
            by_band=self._bands(), by_resolution_group=self._groups_matching()
        )
        c2 = compare_transferability(
            by_band=self._bands(), by_resolution_group=self._groups_matching()
        )
        assert c1.by_elevation_band == c2.by_elevation_band
        assert c1.by_resolution_group == c2.by_resolution_group
