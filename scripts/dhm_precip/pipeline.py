"""The shared computation pipeline — every family (Phase 2) run once, over
`(RAW, ON_GRID)`, producing every table the runner writes and every value the
evaluator compares against `expectations.toml`. `run.py` and `evaluate.py`
both call this; the extraction ids below are the one place that knows how a
manifest `id` maps onto a computed table.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip import (
    normalise,
    stats_axis,
    stats_climatology,
    stats_coherence,
    stats_defects,
    stats_inventory,
    stats_precision,
)
from scripts.dhm_precip.domain_types import (
    AxisStatus,
    LongFrameInventory,
    Station,
    StationCoordinateTable,
    TableDeclaration,
    View,
)
from scripts.dhm_precip.numeric import as_float, as_int

if TYPE_CHECKING:
    from scripts.dhm_precip.params import DhmPrecipParams

ExpectationValue = float | int | str | tuple[float, float] | None
"""`None` means "not computable for this run" (e.g. an insufficient-data
edge case) — NOT the same as a NaN float, which does not survive a JSON
round-trip (pydantic serialises float('nan') as `null`, which its own float
field then rejects on read). `None` is the one value the evaluator already
treats as "the runner produced no value for this expectation id" (evaluate.py)."""


@dataclass(frozen=True, kw_only=True, slots=True)
class ComputedTables:
    inventory: pl.DataFrame
    hourly_simultaneity: pl.DataFrame
    station_span: pl.DataFrame
    elevation_overlap: pl.DataFrame
    row_counts: pl.DataFrame
    off_grid_obs: pl.DataFrame
    off_grid_minutes: pl.DataFrame
    off_grid_attribution: pl.DataFrame
    group_membership: pl.DataFrame
    wet_hour_fraction: pl.DataFrame
    intensity_quantiles: pl.DataFrame
    subthreshold_mass: pl.DataFrame
    sentinel_counts: pl.DataFrame
    stuck_high_runs: pl.DataFrame
    zero_runs: pl.DataFrame
    daily_totals: pl.DataFrame
    annual_totals: pl.DataFrame
    monthly_climatology: pl.DataFrame
    djf_share: pl.DataFrame
    year_completeness: pl.DataFrame
    missingness_ratio: pl.DataFrame
    pairwise_distances: pl.DataFrame
    nearest_neighbour: pl.DataFrame
    diurnal_profiles: pl.DataFrame
    geometry_summary: pl.DataFrame
    coherence_summary: pl.DataFrame
    modal_intensity: pl.DataFrame
    interannual_stability: pl.DataFrame
    loo_tail_prediction: pl.DataFrame
    normalised_axis: pl.DataFrame
    normalisation_provenance: pl.DataFrame


def _normalised_axis_and_provenance(
    raw: pl.DataFrame,
    on_grid: pl.DataFrame,
    stations: StationCoordinateTable,
    params: DhmPrecipParams,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Plan 172 (M-A2) — the canonical gap-explicit hourly axis plus its
    provenance record. `live_stations` is D6b's already-validated D12
    population (never the workbook's raw 37-column set). D5's row-identity
    conservation is asserted here in code — a violation halts the run."""
    live_stations = frozenset(Station(s) for s in stations.stations)
    normalised = normalise.normalise_hourly_axis(on_grid, live_stations)
    normalise.assert_row_identity_conservation(on_grid, normalised, live_stations)
    provenance = normalise.build_provenance(raw, params)

    normalised_tagged = normalised.with_columns(
        pl.lit(View.ON_GRID.value).alias("view"),
        pl.lit(AxisStatus.NORMALIZED.value).alias("axis_status"),
    )
    provenance_tagged = pl.DataFrame(
        {
            "off_grid_source_timestamp_rows": [
                provenance.off_grid_source_timestamp_rows
            ],
            "off_grid_non_null_observations": [
                provenance.off_grid_non_null_observations
            ],
            "period_ending": [provenance.period_ending],
            "period_ending_source": [provenance.period_ending_source],
        }
    ).with_columns(
        pl.lit(View.ON_GRID.value).alias("view"),
        pl.lit(AxisStatus.NORMALIZED.value).alias("axis_status"),
    )
    return normalised_tagged, provenance_tagged


def compute_all(
    raw: pl.DataFrame,
    on_grid: pl.DataFrame,
    inventory: LongFrameInventory,
    stations: StationCoordinateTable,
    params: DhmPrecipParams,
) -> ComputedTables:
    normalised_axis, normalisation_provenance = _normalised_axis_and_provenance(
        raw, on_grid, stations, params
    )
    group_membership = stats_precision.infer_group_membership(on_grid, params)

    frequency_correlations = stats_coherence.frequency_correlations(
        on_grid, stations, params
    )
    pairwise_distances = stats_coherence.pairwise_distances(stations)
    diurnal_profiles = stats_coherence.diurnal_profiles(on_grid, params)
    diurnal_correlations = stats_coherence.diurnal_profile_correlations(
        diurnal_profiles, stations.stations, params
    )
    intensity_quantiles = stats_precision.per_station_intensity_quantiles(
        on_grid, params
    )
    loo_input = intensity_quantiles.select("station", "q0.5", "q0.99").drop_nulls()
    loo_result = stats_precision.leave_one_out_tail_prediction_error(
        dict(zip(loo_input["station"], loo_input["q0.5"], strict=True)),
        dict(zip(loo_input["station"], loo_input["q0.99"], strict=True)),
    )
    loo_tail_prediction = pl.DataFrame(
        {
            "statistic": list(loo_result.keys()),
            "value": list(loo_result.values()),
        }
    ).with_columns(
        pl.lit(View.ON_GRID.value).alias("view"),
        pl.lit(AxisStatus.RAW_PROVISIONAL.value).alias("axis_status"),
    )

    # D7/major-6: geometry (coordinate-only) and correlation (data-derived)
    # statistics get their own tables with distinct, internally-consistent
    # `axis_status` values — a single table cannot honestly carry both
    # `AXIS_INDEPENDENT` and `RAW_PROVISIONAL` rows tagged uniformly as one
    # or the other.
    geometry_summary = pl.DataFrame(
        {
            "statistic": ["median_nn_distance_km", "pairs_within_25km_count"],
            "value": [
                as_float(
                    stats_coherence.nearest_neighbour_distances(pairwise_distances)[
                        "nearest_neighbour_km"
                    ].median()
                ),
                float(
                    stats_coherence.pair_count_within(
                        pairwise_distances, params.distance_bin_edges_km[0]
                    )
                ),
            ],
        }
    ).with_columns(
        pl.lit(View.ON_GRID.value).alias("view"),
        pl.lit(AxisStatus.AXIS_INDEPENDENT.value).alias("axis_status"),
    )
    coherence_summary = pl.DataFrame(
        {
            "statistic": [
                "hourly_r_within_25km",
                "hourly_r_beyond_200km",
                "hourly_r_undistanced",
                "3h_r_undistanced",
                "daily_r_undistanced",
                "daily_r_within_25km",
                "daily_r_beyond_200km",
                "diurnal_median_r",
            ],
            "value": [
                stats_coherence.distance_stratified_median_r(
                    frequency_correlations["hourly"],
                    pairwise_distances,
                    max_km=params.distance_bin_edges_km[0],
                ),
                stats_coherence.distance_stratified_median_r(
                    frequency_correlations["hourly"],
                    pairwise_distances,
                    min_km=params.distance_bin_edges_km[2],
                ),
                stats_coherence.undistanced_median_r(frequency_correlations["hourly"]),
                stats_coherence.undistanced_median_r(frequency_correlations["3h"]),
                stats_coherence.undistanced_median_r(frequency_correlations["daily"]),
                stats_coherence.distance_stratified_median_r(
                    frequency_correlations["daily"],
                    pairwise_distances,
                    max_km=params.distance_bin_edges_km[0],
                ),
                stats_coherence.distance_stratified_median_r(
                    frequency_correlations["daily"],
                    pairwise_distances,
                    min_km=params.distance_bin_edges_km[2],
                ),
                stats_coherence.undistanced_median_r(diurnal_correlations),
            ],
        }
    ).with_columns(
        pl.lit(View.ON_GRID.value).alias("view"),
        pl.lit(AxisStatus.RAW_PROVISIONAL.value).alias("axis_status"),
    )

    return ComputedTables(
        inventory=stats_inventory.usable_station_inventory(inventory),
        hourly_simultaneity=stats_inventory.hourly_reporting_simultaneity(on_grid),
        station_span=stats_inventory.station_span_and_coverage(on_grid),
        elevation_overlap=stats_inventory.group_elevation_overlap(
            on_grid, stations, params
        ),
        row_counts=stats_axis.row_count_diagnostics(raw, params),
        off_grid_obs=stats_axis.off_grid_observation_diagnostics(raw, params),
        off_grid_minutes=stats_axis.off_grid_minute_distribution(raw, params),
        off_grid_attribution=stats_axis.per_station_off_grid_attribution(raw, params),
        group_membership=group_membership,
        wet_hour_fraction=stats_precision.per_station_wet_hour_fraction(
            on_grid, params
        ),
        intensity_quantiles=intensity_quantiles,
        subthreshold_mass=stats_precision.per_station_subthreshold_mass_fraction(
            on_grid, params
        ),
        sentinel_counts=stats_defects.sentinel_counts(on_grid, params),
        stuck_high_runs=stats_defects.stuck_high_candidate_runs(on_grid, params),
        zero_runs=stats_defects.candidate_zero_runs(on_grid, params),
        daily_totals=stats_defects.daily_totals(on_grid, params),
        annual_totals=stats_defects.annual_totals(on_grid, params),
        monthly_climatology=stats_climatology.monthly_climatology(on_grid),
        djf_share=stats_climatology.djf_share_of_total(on_grid, params),
        year_completeness=stats_climatology.per_year_totals_with_completeness(
            on_grid, params
        ),
        missingness_ratio=stats_climatology.wet_biased_missingness_ratio(
            on_grid, stations, params
        ),
        pairwise_distances=pairwise_distances,
        nearest_neighbour=stats_coherence.nearest_neighbour_distances(
            pairwise_distances
        ),
        diurnal_profiles=diurnal_profiles,
        geometry_summary=geometry_summary,
        coherence_summary=coherence_summary,
        modal_intensity=stats_precision.per_station_modal_intensity(on_grid, params),
        interannual_stability=stats_coherence.interannual_diurnal_stability(
            on_grid, params
        ),
        loo_tail_prediction=loo_tail_prediction,
        normalised_axis=normalised_axis,
        normalisation_provenance=normalisation_provenance,
    )


def _range(frame: pl.DataFrame, column: str) -> tuple[float, float]:
    return (as_float(frame[column].min()), as_float(frame[column].max()))


def extract_values(tables: ComputedTables) -> dict[str, ExpectationValue]:
    """The one place a manifest `id` is mapped onto a computed value."""
    values: dict[str, ExpectationValue] = {}

    values["inv_usable_station_count"] = as_int(tables.inventory["is_usable"].sum())
    values["inv_empty_column_count"] = as_int((~tables.inventory["is_usable"]).sum())
    median_reporting = tables.hourly_simultaneity["reporting_station_count"].median()
    values["inv_median_stations_reporting_per_hour"] = round(
        as_float(median_reporting if median_reporting is not None else 0)
    )

    a_row = tables.elevation_overlap.filter(pl.col("group") == "A")
    b_row = tables.elevation_overlap.filter(pl.col("group") == "B")
    values["inv_group_a_elevation_range"] = (
        as_float(a_row["elev_min_m"][0]),
        as_float(a_row["elev_max_m"][0]),
    )
    values["inv_group_b_elevation_range"] = (
        as_float(b_row["elev_min_m"][0]),
        as_float(b_row["elev_max_m"][0]),
    )
    values["inv_group_elevation_gap"] = as_float(
        a_row["group_a_min_minus_group_b_max_m"][0]
    )

    values["axis_total_rows"] = as_int(tables.row_counts["total_rows"][0])
    values["axis_clean_hourly_slots"] = as_int(
        tables.row_counts["clean_hourly_slot_count"][0]
    )
    values["axis_off_grid_row_count"] = as_int(
        tables.row_counts["off_grid_row_count"][0]
    )
    values["axis_off_grid_observation_fraction"] = as_float(
        tables.off_grid_obs["off_grid_observation_fraction"][0]
    )
    values["axis_duplicate_row_count"] = as_int(
        tables.row_counts["duplicate_timestamp_count"][0]
    )
    values["axis_monotonic"] = (
        "true" if tables.row_counts["timestamp_monotonic"][0] else "false"
    )

    wet_by_group = tables.wet_hour_fraction.join(tables.group_membership, on="station")
    values["prec_group_a_wet_hour_fraction"] = _range(
        wet_by_group.filter(pl.col("group") == "A"), "wet_hour_fraction"
    )
    values["prec_group_b_wet_hour_fraction"] = _range(
        wet_by_group.filter(pl.col("group") == "B"), "wet_hour_fraction"
    )

    q_col = "q0.999"
    q_by_group = tables.intensity_quantiles.join(tables.group_membership, on="station")
    values["prec_group_a_q999"] = _range(
        q_by_group.filter(pl.col("group") == "A"), q_col
    )
    values["prec_group_b_q999"] = _range(
        q_by_group.filter(pl.col("group") == "B"), q_col
    )

    ratios = (
        tables.intensity_quantiles.select(
            "station", (pl.col("q0.99") / pl.col("q0.5")).alias("shape_ratio")
        )
        .drop_nulls()
        .filter(pl.col("shape_ratio").is_finite())
    )
    values["prec_shape_ratio_range"] = _range(ratios, "shape_ratio")
    ratio_std = as_float(ratios["shape_ratio"].std(ddof=0))
    ratio_mean = as_float(ratios["shape_ratio"].mean())
    values["prec_shape_ratio_cv_q99"] = ratio_std / ratio_mean

    sub_by_group = tables.subthreshold_mass.join(tables.group_membership, on="station")
    values["prec_group_a_subthreshold_mass_fraction"] = _range(
        sub_by_group.filter(pl.col("group") == "A"), "subthreshold_mass_fraction"
    )

    lukla = tables.sentinel_counts.filter(pl.col("station") == "Lukla Airport")
    values["defect_lukla_sentinel_count"] = (
        as_int(lukla["sentinel_count"][0]) if lukla.height else 0
    )

    sindhuli = tables.stuck_high_runs.filter(pl.col("station") == "Sindhuli Madhi")
    if sindhuli.height:
        longest = sindhuli.sort("run_length_hours", descending=True).head(1)
        values["defect_sindhuli_stuck_high_duration_hours"] = as_int(
            longest["run_length_hours"][0]
        )
        values["defect_sindhuli_stuck_high_total_mm"] = as_float(
            longest["run_total_mm"][0]
        )
    else:
        values["defect_sindhuli_stuck_high_duration_hours"] = 0
        values["defect_sindhuli_stuck_high_total_mm"] = 0.0

    aiselukhark = tables.zero_runs.filter(pl.col("station") == "Aiselukhark")
    values["defect_zero_run_aiselukhark_days"] = (
        as_float(aiselukhark["run_length_days"].max()) if aiselukhark.height else 0.0
    )

    for year, key in (
        (2023, "defect_khumaltar_2023_total_mm"),
        (2024, "defect_khumaltar_2024_total_mm"),
    ):
        row = tables.annual_totals.filter(
            (pl.col("station") == "Khumaltar") & (pl.col("year") == year)
        )
        values[key] = as_float(row["annual_total_mm"][0]) if row.height else None

    flood_day = dt.date(2021, 10, 19)
    for station, key in (
        ("Tarahara", "defect_extreme_tarahara_daily_mm"),
        ("Kanyam Tea Estate", "defect_extreme_kanyam_daily_mm"),
    ):
        row = tables.daily_totals.filter(
            (pl.col("station") == station) & (pl.col("day") == flood_day)
        )
        values[key] = as_float(row["daily_total_mm"][0]) if row.height else None

    humde_djf = tables.djf_share.filter(pl.col("station") == "Humde Airport")
    values["clim_djf_share_humde"] = (
        as_float(humde_djf["djf_share_of_annual_total"][0])
        if humde_djf.height
        else None
    )

    missingness_col = tables.missingness_ratio["wet_biased_missingness_ratio"]
    missingness_all = missingness_col.drop_nulls()
    missingness = missingness_all.filter(missingness_all.is_finite())
    values["clim_missingness_ratio_median"] = (
        as_float(missingness.median()) if missingness.len() else None
    )
    values["clim_missingness_ratio_max"] = (
        as_float(missingness.max()) if missingness.len() else None
    )

    geometry = dict(
        zip(
            tables.geometry_summary["statistic"].to_list(),
            tables.geometry_summary["value"].to_list(),
            strict=True,
        )
    )
    summary = dict(
        zip(
            tables.coherence_summary["statistic"].to_list(),
            tables.coherence_summary["value"].to_list(),
            strict=True,
        )
    )

    def _nan_safe(key: str) -> float | None:
        value = summary[key]
        return None if value != value else value  # NaN != NaN

    values["coh_median_nn_distance_km"] = round(geometry["median_nn_distance_km"])
    values["coh_pairs_within_25km_count"] = int(geometry["pairs_within_25km_count"])
    values["coh_hourly_r_within_25km"] = _nan_safe("hourly_r_within_25km")
    values["coh_hourly_r_beyond_200km"] = _nan_safe("hourly_r_beyond_200km")
    values["coh_hourly_r_undistanced"] = _nan_safe("hourly_r_undistanced")
    values["coh_3h_r_undistanced"] = _nan_safe("3h_r_undistanced")
    values["coh_daily_r_undistanced"] = _nan_safe("daily_r_undistanced")
    values["coh_daily_r_within_25km"] = _nan_safe("daily_r_within_25km")
    values["coh_daily_r_beyond_200km"] = _nan_safe("daily_r_beyond_200km")
    values["coh_diurnal_median_r"] = _nan_safe("diurnal_median_r")

    a_modal = tables.modal_intensity.join(tables.group_membership, on="station").filter(
        pl.col("group") == "A"
    )
    values["prec_group_a_modal_intensity"] = (
        _range(a_modal, "modal_value_mm") if a_modal.height else None
    )

    loo = dict(
        zip(
            tables.loo_tail_prediction["statistic"].to_list(),
            tables.loo_tail_prediction["value"].to_list(),
            strict=True,
        )
    )

    def _loo_safe(key: str) -> float | None:
        value = loo.get(key)
        return None if value is None or value != value else value

    values["prec_loo_median_abs_error"] = _loo_safe("median_abs_error")
    values["prec_loo_min_error"] = _loo_safe("min_error")
    values["prec_loo_max_error"] = _loo_safe("max_error")
    values["prec_loo_within_25pct_fraction"] = _loo_safe("within_25pct_fraction")

    return values


def table_declarations(tables: ComputedTables) -> tuple[TableDeclaration, ...]:
    declarations: list[TableDeclaration] = []
    for field_name in ComputedTables.__dataclass_fields__:
        frame: pl.DataFrame = getattr(tables, field_name)
        if "view" not in frame.columns or "axis_status" not in frame.columns:
            continue
        pairs = frame.select("view", "axis_status").unique().rows()
        declarations.append(
            TableDeclaration(
                name=field_name,
                view_axis_pairs=tuple(
                    sorted((View(v), AxisStatus(a)) for v, a in pairs)
                ),
            )
        )
    return tuple(declarations)
