"""Plan 205 (M-A8) task T1 — the confound bound.

Seam tests: every assertion is on a VALUE actually produced, never on an
argument passed to a mock (`ma6_pairs`'s own convention, reused here)."""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.ma6_estimands import ElevationBand
from scripts.dhm_precip.ma6_pairs import MaskedGaugeSeries
from scripts.dhm_precip.ma7_intensity import StationIntensityDistribution
from scripts.dhm_precip.ma8_confound import (
    BandGroupSplitQuantileRatios,
    BandMembershipError,
    BetweenGroupContrastStatement,
    DuplicateStationError,
    EmptyGroupError,
    GroupBandCount,
    GroupElevationObservation,
    GroupElevationRange,
    GroupMembershipError,
    GroupRangesOverlapError,
    InsufficientObservationsError,
    MissingGroupClassificationError,
    StationClassification,
    WithinGroupElevationRelationship,
    ZeroVarianceError,
    band_group_split_quantile_ratios,
    between_group_contrast_statement,
    classify_stations,
    group_band_cross_tabulation,
    group_elevation_ranges,
    within_group_elevation_relationship,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS
from scripts.dhm_precip.resolution import infer_reporting_resolution
from scripts.dhm_precip.seasons import Season

# --- fixtures ---

_STATION_A1 = Station("A1")
_STATION_A2 = Station("A2")
_STATION_A3 = Station("A3")
_STATION_B1 = Station("B1")
_STATION_B2 = Station("B2")
_STATION_B3 = Station("B3")


def _on_grid_row(station: Station, value_mm: float) -> dict[str, object]:
    return {"station": str(station), "value_mm": value_mm}


def _on_grid_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def _synthetic_on_grid() -> pl.DataFrame:
    """3 Group A stations (0.01 mm-resolution values), 3 Group B stations
    (exact multiples of 0.2 mm) -- mirrors the real population's group
    structure without touching disk."""
    rows: list[dict[str, object]] = []
    for station in (_STATION_A1, _STATION_A2, _STATION_A3):
        rows += [_on_grid_row(station, v) for v in (0.13, 0.27, 0.41, 0.02)]
    for station in (_STATION_B1, _STATION_B2, _STATION_B3):
        rows += [_on_grid_row(station, v) for v in (0.2, 0.4, 0.6, 0.8)]
    return _on_grid_frame(rows)


def _classifications() -> tuple[StationClassification, ...]:
    on_grid = _synthetic_on_grid()
    station_elev_m = {
        _STATION_A1: 2490.0,
        _STATION_A2: 2860.0,
        _STATION_A3: 3700.0,
        _STATION_B1: 67.0,
        _STATION_B2: 1200.0,
        _STATION_B3: 2147.0,
    }
    return classify_stations(
        on_grid, station_elev_m=station_elev_m, params=DEFAULT_PARAMS
    )


class TestClassifyStations:
    def test_reads_group_from_infer_reporting_resolution_not_re_derived(self) -> None:
        """The structural requirement Plan 205 T1's Verify names: a
        station's group must come from `infer_reporting_resolution` itself.
        Proven by comparing `classify_stations`'s own output against a
        DIRECT call to `infer_reporting_resolution` on the SAME input -- a
        re-derivation with even slightly different logic would diverge on
        this crafted near-boundary case (0.199999999 mm, just inside
        `resolution_epsilon_mm` of a 0.2 mm multiple)."""
        on_grid = _on_grid_frame(
            [
                _on_grid_row(_STATION_A1, 0.199999999),
                _on_grid_row(_STATION_A1, 0.4),
                _on_grid_row(_STATION_B1, 0.2),
                _on_grid_row(_STATION_B1, 0.4),
            ]
        )
        station_elev_m = {_STATION_A1: 2500.0, _STATION_B1: 500.0}
        expected = infer_reporting_resolution(on_grid, DEFAULT_PARAMS)
        expected_group = {
            Station(str(row["station"])): row["group"]
            for row in expected.iter_rows(named=True)
        }
        result = classify_stations(
            on_grid, station_elev_m=station_elev_m, params=DEFAULT_PARAMS
        )
        for classification in result:
            assert classification.group == expected_group[classification.station]

    def test_reads_band_from_assign_elevation_band(self) -> None:
        result = _classifications()
        by_station = {c.station: c for c in result}
        assert by_station[_STATION_B1].band == ElevationBand.BELOW_700M
        assert by_station[_STATION_B3].band == ElevationBand.B2000_3000M
        assert by_station[_STATION_A3].band == ElevationBand.ABOVE_3000M

    def test_missing_classification_raises(self) -> None:
        on_grid = _on_grid_frame([_on_grid_row(_STATION_A1, 0.13)])
        with pytest.raises(MissingGroupClassificationError):
            classify_stations(
                on_grid,
                station_elev_m={_STATION_A1: 2500.0, _STATION_B1: 500.0},
                params=DEFAULT_PARAMS,
            )


class TestGroupElevationRange:
    def test_non_overlapping_ranges_on_the_current_population_shape(self) -> None:
        """Mirrors the real 26-station population's group structure (Group
        A 2,490-3,700 m, Group B 67-2,147 m, D1) -- the two groups'
        elevation ranges must not overlap."""
        classifications = _classifications()
        ranges = group_elevation_ranges(classifications)
        by_group = {r.group: r for r in ranges}
        group_a, group_b = by_group["A"], by_group["B"]
        assert group_a.min_elev_m == 2490.0
        assert group_a.max_elev_m == 3700.0
        assert group_b.min_elev_m == 67.0
        assert group_b.max_elev_m == 2147.0
        assert group_b.max_elev_m < group_a.min_elev_m

    def test_empty_members_refused(self) -> None:
        with pytest.raises(EmptyGroupError):
            GroupElevationRange(group="A", members=())

    def test_duplicate_station_refused(self) -> None:
        member = StationClassification(
            station=_STATION_A1,
            group="A",
            band=ElevationBand.ABOVE_3000M,
            elev_m=3000.0,
        )
        with pytest.raises(DuplicateStationError):
            GroupElevationRange(group="A", members=(member, member))

    def test_member_group_mismatch_refused(self) -> None:
        member = StationClassification(
            station=_STATION_B1, group="B", band=ElevationBand.BELOW_700M, elev_m=100.0
        )
        with pytest.raises(GroupMembershipError):
            GroupElevationRange(group="A", members=(member,))


class TestBetweenGroupContrastStatement:
    def test_non_overlapping_ranges_constructs(self) -> None:
        ranges = group_elevation_ranges(_classifications())
        statement = between_group_contrast_statement(ranges)
        assert "UNIDENTIFIED" in statement.declined_attribution

    def test_overlapping_ranges_refused(self) -> None:
        a_member = StationClassification(
            station=_STATION_A1, group="A", band=ElevationBand.B700_2000M, elev_m=1500.0
        )
        b_member = StationClassification(
            station=_STATION_B1, group="B", band=ElevationBand.B700_2000M, elev_m=1500.0
        )
        overlapping_ranges = (
            GroupElevationRange(group="A", members=(a_member,)),
            GroupElevationRange(group="B", members=(b_member,)),
        )
        with pytest.raises(GroupRangesOverlapError):
            between_group_contrast_statement(overlapping_ranges)

    def test_declined_attribution_is_not_caller_suppliable(self) -> None:
        ranges = group_elevation_ranges(_classifications())
        with pytest.raises(ValueError, match="not a caller-suppliable field"):
            BetweenGroupContrastStatement(ranges=ranges, declined_attribution="nope")

    def test_missing_a_group_refused(self) -> None:
        ranges = group_elevation_ranges(_classifications())
        (only_b,) = (r for r in ranges if r.group == "B")
        with pytest.raises(ValueError, match="exactly one"):
            between_group_contrast_statement((only_b,))


class TestGroupBandCrossTabulation:
    def test_full_grid_including_empty_cells(self) -> None:
        table = group_band_cross_tabulation(_classifications())
        # 4 bands x 2 groups = 8 cells, including zero-count ones.
        assert len(table) == 8
        cells = {(c.group, c.band): c.station_count for c in table}
        assert cells[("A", ElevationBand.BELOW_700M)] == 0
        assert cells[("A", ElevationBand.B700_2000M)] == 0
        assert cells[("B", ElevationBand.ABOVE_3000M)] == 0
        assert cells[("A", ElevationBand.ABOVE_3000M)] == 1
        assert cells[("B", ElevationBand.BELOW_700M)] == 1
        assert cells[("A", ElevationBand.B2000_3000M)] == 2
        assert cells[("B", ElevationBand.B2000_3000M)] == 1

    def test_empty_input_refused(self) -> None:
        with pytest.raises(EmptyGroupError):
            group_band_cross_tabulation(())

    def test_result_type_is_group_band_count(self) -> None:
        table = group_band_cross_tabulation(_classifications())
        assert all(isinstance(cell, GroupBandCount) for cell in table)


def _intensity(
    station: Station, values_mm: list[float], *, season: Season = Season.JJAS
) -> StationIntensityDistribution:
    base = datetime(2022, 7, 1)
    rows = [
        {"timestamp": base + timedelta(hours=i), "value_mm": v}
        for i, v in enumerate(values_mm)
    ]
    frame = pl.DataFrame(rows).with_columns(
        pl.lit(str(station)).alias("station"),
        pl.col("timestamp").cast(pl.Datetime("us")),
    )
    series = MaskedGaugeSeries(frame=frame.select("station", "timestamp", "value_mm"))
    return StationIntensityDistribution(series=series, season=season)


class TestBandGroupSplitQuantileRatios:
    def test_ratio_read_off_distribution_not_recomputed(self) -> None:
        dist_b = _intensity(_STATION_B3, [0.0] * 90 + [0.2, 0.5, 1.0, 20.0])
        dist_a = _intensity(_STATION_A1, [0.0] * 90 + [0.2, 0.5, 1.0, 5.0])
        station_elev_m = {_STATION_B3: 2147.0, _STATION_A1: 2490.0}
        station_group = {_STATION_B3: "B", _STATION_A1: "A"}
        result = band_group_split_quantile_ratios(
            ElevationBand.B2000_3000M,
            (dist_b, dist_a),
            station_elev_m=station_elev_m,
            station_group=station_group,
        )
        by_station = {m.station: m for m in result.members}
        assert by_station[_STATION_B3].q50_mm_per_h == dist_b.q50_mm_per_h
        assert by_station[_STATION_B3].q99_mm_per_h == dist_b.q99_mm_per_h
        assert by_station[_STATION_B3].ratio == pytest.approx(
            dist_b.q99_mm_per_h / dist_b.q50_mm_per_h
        )
        assert by_station[_STATION_A1].group == "A"
        assert by_station[_STATION_B3].group == "B"

    def test_wrong_band_membership_refused(self) -> None:
        dist = _intensity(_STATION_B1, [0.0] * 90 + [0.2, 0.5, 1.0, 20.0])
        with pytest.raises(BandMembershipError):
            band_group_split_quantile_ratios(
                ElevationBand.B2000_3000M,
                (dist,),
                station_elev_m={_STATION_B1: 67.0},
                station_group={_STATION_B1: "B"},
            )

    def test_unknown_group_refused(self) -> None:
        dist = _intensity(_STATION_B3, [0.0] * 90 + [0.2, 0.5, 1.0, 20.0])
        with pytest.raises(GroupMembershipError):
            band_group_split_quantile_ratios(
                ElevationBand.B2000_3000M,
                (dist,),
                station_elev_m={_STATION_B3: 2147.0},
                station_group={},
            )

    def test_result_type_is_the_ratios_aggregate(self) -> None:
        dist = _intensity(_STATION_B3, [0.0] * 90 + [0.2, 0.5, 1.0, 20.0])
        result = band_group_split_quantile_ratios(
            ElevationBand.B2000_3000M,
            (dist,),
            station_elev_m={_STATION_B3: 2147.0},
            station_group={_STATION_B3: "B"},
        )
        assert isinstance(result, BandGroupSplitQuantileRatios)


class TestWithinGroupElevationRelationship:
    def test_perfect_positive_correlation(self) -> None:
        station_group = {_STATION_B1: "B", _STATION_B2: "B", _STATION_B3: "B"}
        observations = (
            GroupElevationObservation(
                station=_STATION_B1, elev_m=100.0, value=1.0, n=500
            ),
            GroupElevationObservation(
                station=_STATION_B2, elev_m=200.0, value=2.0, n=500
            ),
            GroupElevationObservation(
                station=_STATION_B3, elev_m=300.0, value=3.0, n=500
            ),
        )
        relationship = within_group_elevation_relationship(
            "test quantity", "B", observations, station_group=station_group
        )
        assert relationship.pearson_r == pytest.approx(1.0)
        assert relationship.n_stations == 3

    def test_perfect_negative_correlation(self) -> None:
        station_group = {_STATION_B1: "B", _STATION_B2: "B", _STATION_B3: "B"}
        observations = (
            GroupElevationObservation(
                station=_STATION_B1, elev_m=100.0, value=3.0, n=500
            ),
            GroupElevationObservation(
                station=_STATION_B2, elev_m=200.0, value=2.0, n=500
            ),
            GroupElevationObservation(
                station=_STATION_B3, elev_m=300.0, value=1.0, n=500
            ),
        )
        relationship = within_group_elevation_relationship(
            "test quantity", "B", observations, station_group=station_group
        )
        assert relationship.pearson_r == pytest.approx(-1.0)

    def test_fewer_than_three_stations_refused(self) -> None:
        station_group = {_STATION_B1: "B", _STATION_B2: "B"}
        observations = (
            GroupElevationObservation(
                station=_STATION_B1, elev_m=100.0, value=1.0, n=500
            ),
            GroupElevationObservation(
                station=_STATION_B2, elev_m=200.0, value=2.0, n=500
            ),
        )
        relationship = within_group_elevation_relationship(
            "test quantity", "B", observations, station_group=station_group
        )
        with pytest.raises(InsufficientObservationsError):
            _ = relationship.pearson_r

    def test_group_mismatch_refused(self) -> None:
        station_group = {_STATION_A1: "A"}
        observations = (
            GroupElevationObservation(
                station=_STATION_A1, elev_m=2500.0, value=1.0, n=100
            ),
        )
        with pytest.raises(GroupMembershipError):
            within_group_elevation_relationship(
                "test quantity", "B", observations, station_group=station_group
            )

    def test_duplicate_station_refused(self) -> None:
        station_group = {_STATION_B1: "B"}
        obs = GroupElevationObservation(
            station=_STATION_B1, elev_m=100.0, value=1.0, n=100
        )
        with pytest.raises(DuplicateStationError):
            within_group_elevation_relationship(
                "test quantity", "B", (obs, obs), station_group=station_group
            )

    def test_zero_variance_elevation_refused(self) -> None:
        station_group = {_STATION_B1: "B", _STATION_B2: "B", _STATION_B3: "B"}
        observations = (
            GroupElevationObservation(
                station=_STATION_B1, elev_m=100.0, value=1.0, n=100
            ),
            GroupElevationObservation(
                station=_STATION_B2, elev_m=100.0, value=2.0, n=100
            ),
            GroupElevationObservation(
                station=_STATION_B3, elev_m=100.0, value=3.0, n=100
            ),
        )
        relationship = within_group_elevation_relationship(
            "test quantity", "B", observations, station_group=station_group
        )
        with pytest.raises(ZeroVarianceError):
            _ = relationship.pearson_r

    def test_empty_observations_refused(self) -> None:
        with pytest.raises(EmptyGroupError):
            within_group_elevation_relationship(
                "test quantity", "B", (), station_group={}
            )

    def test_result_type(self) -> None:
        station_group = {_STATION_B1: "B", _STATION_B2: "B", _STATION_B3: "B"}
        observations = (
            GroupElevationObservation(
                station=_STATION_B1, elev_m=100.0, value=1.0, n=100
            ),
            GroupElevationObservation(
                station=_STATION_B2, elev_m=200.0, value=2.0, n=100
            ),
            GroupElevationObservation(
                station=_STATION_B3, elev_m=300.0, value=3.0, n=100
            ),
        )
        relationship = within_group_elevation_relationship(
            "test quantity", "B", observations, station_group=station_group
        )
        assert isinstance(relationship, WithinGroupElevationRelationship)


class TestGroupElevationObservation:
    def test_negative_n_refused(self) -> None:
        with pytest.raises(ValueError, match="n must be >= 0"):
            GroupElevationObservation(
                station=_STATION_B1, elev_m=100.0, value=1.0, n=-1
            )

    def test_non_finite_elev_refused(self) -> None:
        with pytest.raises(ValueError, match="not finite"):
            GroupElevationObservation(
                station=_STATION_B1, elev_m=float("nan"), value=1.0, n=1
            )

    def test_non_finite_value_refused(self) -> None:
        with pytest.raises(ValueError, match="not finite"):
            GroupElevationObservation(
                station=_STATION_B1, elev_m=100.0, value=float("inf"), n=1
            )
