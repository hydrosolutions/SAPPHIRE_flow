from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import polars as pl
import structlog

from sapphire_flow.types.enums import ArtifactScope, SpatialRepresentation
from sapphire_flow.types.model import ModelDataRequirements

if TYPE_CHECKING:
    import random

    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.model import (
        ModelArtifact,
        ModelParams,
        StationModelInputs,
        StationTrainingData,
    )

log = structlog.get_logger(__name__)

_QUANTILE_LEVELS = [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95]
_MIN_TRAIN_ROWS = 365


@dataclass(frozen=True, kw_only=True, slots=True)
class ClimatologyArtifact:
    quantiles: (
        pl.DataFrame
    )  # [day_of_year (Int32), quantile (Float64), value (Float64), parameter (Utf8)]


class ClimatologyFallbackModel:
    artifact_scope: ArtifactScope = ArtifactScope.STATION

    def __init__(
        self,
        target_parameters: frozenset[str] = frozenset({"discharge"}),
    ) -> None:
        self.data_requirements = ModelDataRequirements(
            target_parameters=target_parameters,
            past_dynamic_features=frozenset(),
            future_dynamic_features=frozenset(),
            static_features=frozenset(),
            supported_time_steps=frozenset({timedelta(hours=24)}),
            lookback_steps=1,
            forecast_horizon_steps=5,
            spatial_input_type=SpatialRepresentation.POINT,
        )

    def train(
        self,
        data: StationTrainingData,
        params: ModelParams,
        rng: random.Random,
    ) -> ModelArtifact:
        targets = data.past_targets.sort("timestamp")

        if len(targets) < _MIN_TRAIN_ROWS:
            raise ValueError(
                f"Insufficient training data: need at least {_MIN_TRAIN_ROWS} rows, "
                f"got {len(targets)}"
            )

        parts: list[pl.DataFrame] = []

        for param in self.data_requirements.target_parameters:
            with_doy = targets.select(
                [
                    pl.col("timestamp")
                    .dt.ordinal_day()
                    .alias("day_of_year")
                    .cast(pl.Int32),
                    pl.col(param).alias("value"),
                ]
            )

            agg = (
                with_doy.group_by("day_of_year")
                .agg(
                    [
                        pl.col("value")
                        .quantile(q, interpolation="linear")
                        .alias(f"q_{q}")
                        for q in _QUANTILE_LEVELS
                    ]
                )
                .sort("day_of_year")
            )

            melted = (
                agg.unpivot(
                    index="day_of_year",
                    on=[f"q_{q}" for q in _QUANTILE_LEVELS],
                    variable_name="_q_label",
                    value_name="value",
                )
                .with_columns(
                    pl.col("_q_label")
                    .str.strip_prefix("q_")
                    .cast(pl.Float64)
                    .alias("quantile")
                )
                .drop("_q_label")
                .with_columns(pl.lit(param).alias("parameter"))
            )

            parts.append(melted)

        quantiles_df = pl.concat(parts).select(
            ["day_of_year", "quantile", "value", "parameter"]
        )

        log.debug(
            "model.training_completed",
            n_rows=len(targets),
            n_parameters=len(self.data_requirements.target_parameters),
        )

        return ClimatologyArtifact(quantiles=quantiles_df)

    def predict(
        self,
        artifact: ModelArtifact,
        inputs: StationModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[dict[str, ForecastEnsemble], bytes | None]:
        from sapphire_flow.types.ensemble import ForecastEnsemble

        art: ClimatologyArtifact = artifact
        horizon = inputs.forecast_horizon_steps
        results: dict[str, ForecastEnsemble] = {}

        for param in self.data_requirements.target_parameters:
            param_quantiles = art.quantiles.filter(pl.col("parameter") == param)

            rows: list[dict[str, Any]] = []
            for step in range(horizon):
                valid_time = inputs.issue_time + (step + 1) * inputs.time_step
                doy = valid_time.timetuple().tm_yday

                doy_rows = param_quantiles.filter(pl.col("day_of_year") == doy)

                for row in doy_rows.iter_rows(named=True):
                    rows.append(
                        {
                            "valid_time": valid_time,
                            "quantile": row["quantile"],
                            "value": row["value"],
                        }
                    )

            values_df = pl.DataFrame(rows).with_columns(
                pl.col("quantile").cast(pl.Float64),
                pl.col("value").cast(pl.Float64),
            )

            ensemble = ForecastEnsemble.from_quantiles(
                station_id=inputs.station_id,
                issued_at=inputs.issue_time,
                parameter=param,
                units="m3/s",
                time_step=inputs.time_step,
                values=values_df,
            )
            results[param] = ensemble

        return results, None

    def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
        art: ClimatologyArtifact = artifact
        buf = io.BytesIO()
        art.quantiles.write_ipc(buf)
        return buf.getvalue()

    def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
        df = pl.read_ipc(io.BytesIO(raw))
        return ClimatologyArtifact(quantiles=df)
