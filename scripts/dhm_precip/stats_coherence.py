"""Task 2f — coherence, diurnal and geometry. Single owner of all coherence
computation (D12). Consumes 1a's validated `stations` — no I/O (D4).

Pairwise distances and nearest-neighbour distribution are `AXIS_INDEPENDENT`
(coordinate-only, unaffected by M-A2); correlation, distance-stratification
and diurnal profiles are `RAW_PROVISIONAL`. No ERA5-Land grid assignment
(out of scope, D12).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip.domain_types import (
    AxisStatus,
    Station,
    StationCoordinateTable,
    View,
)
from scripts.dhm_precip.numeric import as_float

if TYPE_CHECKING:
    from scripts.dhm_precip.params import DhmPrecipParams

_EARTH_RADIUS_KM = 6371.0088


def _tag_independent(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(
        pl.lit(View.ON_GRID.value).alias("view"),
        pl.lit(AxisStatus.AXIS_INDEPENDENT.value).alias("axis_status"),
    )


def _tag_provisional(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(
        pl.lit(View.ON_GRID.value).alias("view"),
        pl.lit(AxisStatus.RAW_PROVISIONAL.value).alias("axis_status"),
    )


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def pairwise_distances(stations: StationCoordinateTable) -> pl.DataFrame:
    """`AXIS_INDEPENDENT` — great-circle distance for every unordered station pair."""
    items = list(stations.by_station.items())
    rows = [
        {
            "station_a": name_a,
            "station_b": name_b,
            "distance_km": _haversine_km(
                coord_a.lat, coord_a.lon, coord_b.lat, coord_b.lon
            ),
        }
        for (name_a, coord_a), (name_b, coord_b) in itertools.combinations(items, 2)
    ]
    return _tag_independent(pl.DataFrame(rows))


def nearest_neighbour_distances(pairwise: pl.DataFrame) -> pl.DataFrame:
    """`AXIS_INDEPENDENT` — each station's nearest-neighbour distance."""
    long = pl.concat(
        [
            pairwise.select(
                pl.col("station_a").alias("station"), pl.col("distance_km")
            ),
            pairwise.select(
                pl.col("station_b").alias("station"), pl.col("distance_km")
            ),
        ]
    )
    nn = long.group_by("station").agg(
        pl.col("distance_km").min().alias("nearest_neighbour_km")
    )
    return _tag_independent(nn)


def pair_count_within(pairwise: pl.DataFrame, max_km: float) -> int:
    return pairwise.filter(pl.col("distance_km") < max_km).height


@dataclass(frozen=True, kw_only=True, slots=True)
class _Bucketed:
    hourly: pl.DataFrame
    three_hourly: pl.DataFrame
    daily: pl.DataFrame


def _jjas(on_grid: pl.DataFrame, params: DhmPrecipParams) -> pl.DataFrame:
    return on_grid.filter(pl.col("timestamp").dt.month().is_in(params.jjas_months))


def _bucketed_frequencies(on_grid: pl.DataFrame, params: DhmPrecipParams) -> _Bucketed:
    """Rule 3 (aggregation validity): a bucket total is formed only from
    hours actually present (`hours_present`), and incomplete buckets are
    DROPPED (never treated as complete, never silently summed to 0.0 for an
    all-null bucket — Polars' `.sum()` over an all-null group returns 0.0,
    not null, so without this filter a station with NO data that bucket
    would enter the correlation as "recorded zero precipitation")."""
    jjas = _jjas(on_grid, params)
    hourly = jjas.select("station", "timestamp", "value_mm")

    three_hourly_raw = (
        jjas.with_columns(
            (pl.col("timestamp").dt.hour() // 3 * 3).alias("_bucket_hour"),
            pl.col("timestamp").dt.truncate("1d").alias("_day"),
        )
        .group_by(["station", "_day", "_bucket_hour"])
        .agg(
            pl.col("value_mm").sum().alias("value_mm"),
            pl.col("value_mm").is_not_null().sum().alias("_hours_present"),
        )
    )
    three_hourly = three_hourly_raw.filter(
        pl.col("_hours_present") >= params.three_hourly_completeness_min_hours
    ).select("station", "_day", "_bucket_hour", "value_mm")

    daily_raw = jjas.group_by(
        ["station", pl.col("timestamp").dt.date().alias("_day")]
    ).agg(
        pl.col("value_mm").sum().alias("value_mm"),
        pl.col("value_mm").is_not_null().sum().alias("_hours_present"),
    )
    daily = daily_raw.filter(
        pl.col("_hours_present") >= params.daily_completeness_min_hours
    ).select("station", "_day", "value_mm")

    return _Bucketed(hourly=hourly, three_hourly=three_hourly, daily=daily)


def _wide(frame: pl.DataFrame, index_cols: list[str]) -> pl.DataFrame:
    return frame.pivot(on="station", index=index_cols, values="value_mm")


def _pairwise_correlations(
    wide: pl.DataFrame, stations: tuple[Station, ...], min_samples: int
) -> dict[tuple[Station, Station], float]:
    """`min_samples` is the caller's own threshold — NOT always
    `params.min_paired_samples`: a 24-point diurnal profile and a
    JJAS-long hourly series are different populations with different
    minimums (see `diurnal_profile_correlations`)."""
    results: dict[tuple[Station, Station], float] = {}
    for a, b in itertools.combinations(stations, 2):
        if a not in wide.columns or b not in wide.columns:
            continue
        pair = wide.select(a, b).drop_nulls()
        if pair.height < min_samples:
            continue
        r = pair.select(pl.corr(a, b)).item()
        if r is not None:
            results[(a, b)] = as_float(r)
    return results


def frequency_correlations(
    on_grid: pl.DataFrame, stations: StationCoordinateTable, params: DhmPrecipParams
) -> dict[str, dict[tuple[Station, Station], float]]:
    """Per-pair Pearson r at hourly/3-hourly/daily resolution, JJAS,
    `pairwise_missing_policy = pairwise_complete`, `min_paired_samples`."""
    bucketed = _bucketed_frequencies(on_grid, params)
    station_names = stations.stations
    return {
        "hourly": _pairwise_correlations(
            _wide(bucketed.hourly, ["timestamp"]),
            station_names,
            params.min_paired_samples,
        ),
        "3h": _pairwise_correlations(
            _wide(bucketed.three_hourly, ["_day", "_bucket_hour"]),
            station_names,
            params.min_paired_samples,
        ),
        "daily": _pairwise_correlations(
            _wide(bucketed.daily, ["_day"]), station_names, params.min_paired_samples
        ),
    }


def undistanced_median_r(correlations: dict[tuple[Station, Station], float]) -> float:
    values = list(correlations.values())
    return as_float(pl.Series(values).median()) if values else float("nan")


def distance_stratified_median_r(
    correlations: dict[tuple[Station, Station], float],
    pairwise: pl.DataFrame,
    *,
    max_km: float | None = None,
    min_km: float | None = None,
) -> float:
    distance_lookup: dict[tuple[Station, Station], float] = {
        (row["station_a"], row["station_b"]): as_float(row["distance_km"])
        for row in pairwise.iter_rows(named=True)
    }
    selected: list[float] = []
    for (a, b), r in correlations.items():
        distance = distance_lookup.get((a, b), distance_lookup.get((b, a)))
        if distance is None:
            continue
        if max_km is not None and not (distance < max_km):
            continue
        if min_km is not None and not (distance > min_km):
            continue
        selected.append(r)
    return as_float(pl.Series(selected).median()) if selected else float("nan")


def diurnal_profiles(on_grid: pl.DataFrame, params: DhmPrecipParams) -> pl.DataFrame:
    """`RAW_PROVISIONAL` — per-station mean value by hour-of-day, JJAS."""
    jjas = _jjas(on_grid, params).filter(pl.col("value_mm").is_not_null())
    profile = (
        jjas.with_columns(pl.col("timestamp").dt.hour().alias("hour"))
        .group_by(["station", "hour"])
        .agg(pl.col("value_mm").mean().alias("mean_value_mm"))
    )
    return _tag_provisional(profile)


def interannual_diurnal_stability(
    on_grid: pl.DataFrame, params: DhmPrecipParams
) -> pl.DataFrame:
    """`RAW_PROVISIONAL` — Task 2f's "interannual stability" deliverable: for
    each station, the median Pearson r between every pair of that station's
    own per-year JJAS diurnal profiles (24-hour mean vectors). A high value
    means the station's diurnal shape repeats from year to year. This has no
    vision-quoted number to gate against (D8: no statistic is invented to be
    reproduced) — it is computed and stored as a Task 2f table, not an
    expectation."""
    jjas = _jjas(on_grid, params).filter(pl.col("value_mm").is_not_null())
    per_year_profile = (
        jjas.with_columns(
            pl.col("timestamp").dt.hour().alias("hour"),
            pl.col("timestamp").dt.year().cast(pl.Utf8).alias("year"),
        )
        .group_by(["station", "year", "hour"])
        .agg(pl.col("value_mm").mean().alias("mean_value_mm"))
    )
    rows: list[dict[str, object]] = []
    for station in sorted(per_year_profile["station"].unique().to_list()):
        wide = (
            per_year_profile.filter(pl.col("station") == station)
            .pivot(on="year", index="hour", values="mean_value_mm")
            .sort("hour")
        )
        years = [c for c in wide.columns if c != "hour"]
        pair_rs: list[float] = []
        for year_a, year_b in itertools.combinations(years, 2):
            pair = wide.select(year_a, year_b).drop_nulls()
            if pair.height < params.min_diurnal_paired_hours:
                continue
            r = pair.select(pl.corr(year_a, year_b)).item()
            if r is not None:
                pair_rs.append(as_float(r))
        rows.append(
            {
                "station": station,
                "interannual_diurnal_stability_median_r": (
                    as_float(pl.Series(pair_rs).median()) if pair_rs else None
                ),
                "n_year_pairs": len(pair_rs),
            }
        )
    return _tag_provisional(pl.DataFrame(rows))


def diurnal_profile_correlations(
    profiles: pl.DataFrame, stations: tuple[Station, ...], params: DhmPrecipParams
) -> dict[tuple[Station, Station], float]:
    """Between-station Pearson r of the 24-hour mean profile vectors (a
    profile-shape comparison, not the Rule-2 quantile-vector trap).

    Uses `params.min_diurnal_paired_hours`, NOT `params.min_paired_samples` —
    a 24-point-per-station profile is a different population from the
    JJAS-long hourly/3h/daily series `min_paired_samples` was set for; reusing
    that threshold here would reject every pair (24 < 100) and silently
    produce no correlations at all.
    """
    wide = profiles.pivot(on="station", index="hour", values="mean_value_mm").sort(
        "hour"
    )
    return _pairwise_correlations(wide, stations, params.min_diurnal_paired_hours)
