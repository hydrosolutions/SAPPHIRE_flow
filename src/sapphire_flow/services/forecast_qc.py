from __future__ import annotations

from datetime import datetime  # noqa: TCH003  # runtime use: isinstance narrowing
from typing import TYPE_CHECKING, cast

import polars as pl

from sapphire_flow.services._qc_helpers import merge_thresholds
from sapphire_flow.types.domain import (
    ClimBaseline,
    ForecastQcRuleParams,
    ForecastQcRuleSet,
    QcFlag,
    StationForecastQcOverride,
)
from sapphire_flow.types.enums import EnsembleRepresentation, QcStatus

if TYPE_CHECKING:
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.ids import StationId

_RULE_VERSION = "1.0"


def _apply_negative_value(
    ensemble: ForecastEnsemble,
    thresholds: dict[str, float],
    rule: ForecastQcRuleParams,
) -> QcFlag | None:
    value_min = thresholds["value_min"]
    min_val = ensemble.values["value"].min()
    if min_val is not None:
        # polars .min() is typed PythonLiteral; the "value" column is float64
        min_val_f = cast("float", min_val)
        if min_val_f < value_min:
            return QcFlag(
                rule_id=rule.rule_id,
                rule_version=_RULE_VERSION,
                status=QcStatus.QC_FAILED,
                detail=f"min value {min_val_f} below {value_min}",
            )
    return None


def _apply_range_check(
    ensemble: ForecastEnsemble,
    thresholds: dict[str, float],
    rule: ForecastQcRuleParams,
) -> QcFlag | None:
    v_min = thresholds.get("value_min")
    v_max = thresholds.get("value_max")
    medians = ensemble.values.group_by("valid_time").agg(pl.col("value").median())
    for row in medians.iter_rows(named=True):
        med = row["value"]
        if med is None:
            continue
        if (v_min is not None and med < v_min) or (v_max is not None and med > v_max):
            return QcFlag(
                rule_id=rule.rule_id,
                rule_version=_RULE_VERSION,
                status=QcStatus.QC_FAILED,
                detail=f"median {med} outside [{v_min}, {v_max}]",
            )
    return None


def _apply_flat_ensemble(
    ensemble: ForecastEnsemble,
    thresholds: dict[str, float],
    rule: ForecastQcRuleParams,
) -> QcFlag | None:
    tolerance = thresholds["tolerance"]
    std = ensemble.values["value"].std()
    if std is not None:
        # polars .std() is typed PythonLiteral; the "value" column is float64
        std_f = cast("float", std)
        if std_f < tolerance:
            return QcFlag(
                rule_id=rule.rule_id,
                rule_version=_RULE_VERSION,
                status=QcStatus.QC_SUSPECT,
                detail=f"ensemble std {std_f:.6f} below tolerance {tolerance}",
            )
    return None


def _apply_ensemble_spread(
    ensemble: ForecastEnsemble,
    thresholds: dict[str, float],
    baseline_index: dict[tuple[StationId, str, int], ClimBaseline],
    rule: ForecastQcRuleParams,
) -> QcFlag | None:
    first_vt = ensemble.values["valid_time"].min()
    if first_vt is None:
        return None
    # "valid_time" is a datetime column; polars .min() is typed PythonLiteral
    assert isinstance(first_vt, datetime)
    doy = first_vt.timetuple().tm_yday
    key = (ensemble.station_id, ensemble.parameter, doy)
    baseline = baseline_index.get(key)
    if baseline is None:
        return None

    clim_std = baseline.rolling_std
    if clim_std == 0.0:
        return None

    iqr_per_step = (
        ensemble.values.group_by("valid_time")
        .agg(
            [
                pl.col("value").quantile(0.75).alias("q75"),
                pl.col("value").quantile(0.25).alias("q25"),
            ]
        )
        .with_columns((pl.col("q75") - pl.col("q25")).alias("iqr"))
    )
    mean_iqr = iqr_per_step["iqr"].mean()
    if mean_iqr is None:
        return None

    min_spread_ratio = thresholds["min_spread_ratio"]
    max_spread_ratio = thresholds["max_spread_ratio"]
    # polars .mean() is typed PythonLiteral; the "iqr" column is float64
    ratio = cast("float", mean_iqr) / clim_std

    if ratio < min_spread_ratio or ratio > max_spread_ratio:
        return QcFlag(
            rule_id=rule.rule_id,
            rule_version=_RULE_VERSION,
            status=QcStatus.QC_SUSPECT,
            detail=(
                f"ensemble spread ratio {ratio:.4f} outside "
                f"[{min_spread_ratio}, {max_spread_ratio}]"
            ),
        )
    return None


def _apply_climatology_outlier(
    ensemble: ForecastEnsemble,
    thresholds: dict[str, float],
    baseline_index: dict[tuple[StationId, str, int], ClimBaseline],
    rule: ForecastQcRuleParams,
) -> QcFlag | None:
    first_vt = ensemble.values["valid_time"].min()
    if first_vt is None:
        return None
    # "valid_time" is a datetime column; polars .min() is typed PythonLiteral
    assert isinstance(first_vt, datetime)
    doy = first_vt.timetuple().tm_yday
    key = (ensemble.station_id, ensemble.parameter, doy)
    baseline = baseline_index.get(key)
    if baseline is None:
        return None

    medians = ensemble.values.group_by("valid_time").agg(pl.col("value").median())
    overall_median = medians["value"].median()
    if overall_median is None:
        return None

    k_sigma = thresholds["k_sigma"]
    # polars .median() is typed PythonLiteral; the "value" column is float64
    overall_median_f = cast("float", overall_median)
    if abs(overall_median_f - baseline.rolling_mean) > k_sigma * baseline.rolling_std:
        return QcFlag(
            rule_id=rule.rule_id,
            rule_version=_RULE_VERSION,
            status=QcStatus.QC_SUSPECT,
            detail=(
                f"ensemble median {overall_median_f:.4f} deviates from baseline "
                f"mean {baseline.rolling_mean:.4f} by >{k_sigma}σ "
                f"(std={baseline.rolling_std:.4f})"
            ),
        )
    return None


def _apply_temporal_consistency(
    ensemble: ForecastEnsemble,
    thresholds: dict[str, float],
    rule: ForecastQcRuleParams,
) -> QcFlag | None:
    max_rate = thresholds["max_rate"]
    medians = (
        ensemble.values.group_by("valid_time")
        .agg(pl.col("value").median())
        .sort("valid_time")
    )
    values = medians["value"].to_list()
    for i in range(1, len(values)):
        if values[i] is None or values[i - 1] is None:
            continue
        diff = abs(values[i] - values[i - 1])
        if diff > max_rate:
            return QcFlag(
                rule_id=rule.rule_id,
                rule_version=_RULE_VERSION,
                status=QcStatus.QC_SUSPECT,
                detail=f"temporal jump {diff:.4f} exceeds max_rate {max_rate}",
            )
    return None


def _apply_quantile_crossing(
    ensemble: ForecastEnsemble,
    rule: ForecastQcRuleParams,
) -> QcFlag | None:
    if ensemble.representation != EnsembleRepresentation.QUANTILES:
        return None

    for vt, group in ensemble.values.group_by("valid_time"):
        ordered = group.sort("quantile")
        vals = ordered["value"].to_list()
        for i in range(1, len(vals)):
            if (
                vals[i] is not None
                and vals[i - 1] is not None
                and vals[i] < vals[i - 1]
            ):
                return QcFlag(
                    rule_id=rule.rule_id,
                    rule_version=_RULE_VERSION,
                    status=QcStatus.QC_FAILED,
                    detail=f"quantile crossing at valid_time {vt}",
                )
    return None


class ForecastOutputQualityChecker:
    def check(
        self,
        ensemble: ForecastEnsemble,
        rule_set: ForecastQcRuleSet,
        overrides: list[StationForecastQcOverride],
        baselines: list[ClimBaseline],
    ) -> list[QcFlag]:
        flags: list[QcFlag] = []
        rules = rule_set.rules_for(ensemble.parameter, ensemble.time_step)

        baseline_index: dict[tuple[StationId, str, int], ClimBaseline] = {
            (b.station_id, b.parameter, b.day_of_year): b for b in baselines
        }

        for rule in rules:
            thresholds = merge_thresholds(
                rule.thresholds,
                overrides,
                ensemble.station_id,
                rule.rule_id,
                rule.parameter,
                rule.time_step,
            )

            flag: QcFlag | None = None
            match rule.rule_id:
                case "negative_value":
                    flag = _apply_negative_value(ensemble, thresholds, rule)
                case "range_check":
                    flag = _apply_range_check(ensemble, thresholds, rule)
                case "flat_ensemble":
                    flag = _apply_flat_ensemble(ensemble, thresholds, rule)
                case "ensemble_spread":
                    flag = _apply_ensemble_spread(
                        ensemble, thresholds, baseline_index, rule
                    )
                case "climatology_outlier":
                    flag = _apply_climatology_outlier(
                        ensemble, thresholds, baseline_index, rule
                    )
                case "temporal_consistency":
                    flag = _apply_temporal_consistency(ensemble, thresholds, rule)
                case "quantile_crossing":
                    flag = _apply_quantile_crossing(ensemble, rule)

            if flag is not None:
                flags.append(flag)

        return flags
