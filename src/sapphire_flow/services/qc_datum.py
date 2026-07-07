from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from sapphire_flow.types.domain import QcFlag
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.ids import ObservationId
    from sapphire_flow.types.observation import Observation

OBS_DATUM_DEPENDENT_RULES = frozenset({"range_check", "gross_outlier"})
FORECAST_DATUM_DEPENDENT_RULES = frozenset(
    {"range_check", "negative_value", "climatology_outlier"}
)
DATUM_QC_RULE_VERSION = "1.1-datum"
DATUM_SKIP_QC_RULE_VERSION = "1.1-datum-skip"
SUPPORTED_WATER_LEVEL_UNITS = frozenset({"m", "m a.s.l."})


def obs_qc_rule_version(parameter: str, datum: float | None) -> str:
    if parameter != "water_level":
        return "1.0"
    return DATUM_QC_RULE_VERSION if datum is not None else DATUM_SKIP_QC_RULE_VERSION


def obs_skipped_rules(parameter: str, datum: float | None) -> frozenset[str]:
    if parameter == "water_level" and datum is None:
        return OBS_DATUM_DEPENDENT_RULES
    return frozenset()


def forecast_skipped_rules(parameter: str, datum: float | None) -> frozenset[str]:
    if parameter == "water_level" and datum is None:
        return FORECAST_DATUM_DEPENDENT_RULES
    return frozenset()


def shift_observations_for_water_level_datum(
    observations: list[Observation],
    *,
    parameter: str,
    datum: float | None,
) -> list[Observation]:
    if parameter != "water_level" or datum is None:
        return observations
    return [
        replace(obs, value=obs.value - datum) if obs.value is not None else obs
        for obs in observations
    ]


def shift_ensemble_for_water_level_datum(
    ensemble: ForecastEnsemble,
    *,
    datum: float | None,
) -> ForecastEnsemble:
    if ensemble.parameter != "water_level" or datum is None:
        return ensemble
    return replace(
        ensemble, values=ensemble.values.with_columns(pl.col("value") - datum)
    )


def add_observation_datum_details(
    flags: dict[ObservationId, list[QcFlag]],
    *,
    raw_observations: list[Observation],
    shifted_observations: list[Observation],
    parameter: str,
    datum: float | None,
) -> dict[ObservationId, list[QcFlag]]:
    if parameter != "water_level" or datum is None:
        return flags
    raw_by_id = {obs.id: obs for obs in raw_observations}
    shifted_by_id = {obs.id: obs for obs in shifted_observations}
    return {
        obs_id: [
            _add_flag_detail(
                flag,
                raw_value=raw_by_id[obs_id].value,
                relative_value=shifted_by_id[obs_id].value,
                datum=datum,
            )
            for flag in obs_flags
        ]
        for obs_id, obs_flags in flags.items()
    }


def add_forecast_datum_details(
    flags: list[QcFlag],
    *,
    raw_ensemble: ForecastEnsemble,
    shifted_ensemble: ForecastEnsemble,
    datum: float | None,
) -> list[QcFlag]:
    if raw_ensemble.parameter != "water_level" or datum is None:
        return flags
    raw_min = raw_ensemble.values["value"].min()
    shifted_min = shifted_ensemble.values["value"].min()
    raw_median = raw_ensemble.values["value"].median()
    shifted_median = shifted_ensemble.values["value"].median()
    suffix = (
        f"raw_min={raw_min}, relative_min={shifted_min}, "
        f"raw_median={raw_median}, relative_median={shifted_median}, "
        f"datum_masl={datum}"
    )
    return [
        replace(flag, detail=f"{flag.detail}; {suffix}" if flag.detail else suffix)
        for flag in flags
    ]


def _add_flag_detail(
    flag: QcFlag,
    *,
    raw_value: float | None,
    relative_value: float | None,
    datum: float,
) -> QcFlag:
    suffix = (
        f"raw_value={raw_value}, relative_value={relative_value}, datum_masl={datum}"
    )
    return replace(flag, detail=f"{flag.detail}; {suffix}" if flag.detail else suffix)
