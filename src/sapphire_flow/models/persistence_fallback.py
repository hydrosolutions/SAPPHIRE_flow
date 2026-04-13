from __future__ import annotations

import json
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


@dataclass(frozen=True, kw_only=True, slots=True)
class PersistenceArtifact:
    target_parameters: frozenset[str]


class PersistenceFallbackModel:
    artifact_scope: ArtifactScope = ArtifactScope.STATION

    def __init__(
        self,
        target_parameters: frozenset[str] = frozenset({"discharge"}),
        spread_pct_per_step: float = 0.05,
    ) -> None:
        self.spread_pct_per_step = spread_pct_per_step
        self.data_requirements = ModelDataRequirements(
            target_parameters=target_parameters,
            past_dynamic_features=frozenset(),
            future_dynamic_features=frozenset(),
            static_features=frozenset(),
            supported_time_steps=frozenset({timedelta(hours=24)}),
            lookback_steps=1,
            spatial_input_type=SpatialRepresentation.POINT,
        )

    def train(
        self,
        data: StationTrainingData,
        params: ModelParams,
        rng: random.Random,
    ) -> ModelArtifact:
        log.debug("model.training_completed", model="persistence_fallback")
        return PersistenceArtifact(
            target_parameters=self.data_requirements.target_parameters
        )

    def predict(
        self,
        artifact: ModelArtifact,
        inputs: StationModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[dict[str, ForecastEnsemble], bytes | None]:
        from sapphire_flow.types.ensemble import ForecastEnsemble

        art: PersistenceArtifact = artifact
        past_targets = inputs.data.past_targets.sort("timestamp")
        horizon = inputs.forecast_horizon_steps

        ensembles: dict[str, ForecastEnsemble] = {}

        for param in art.target_parameters:
            last_value = past_targets[param][-1]

            rows: list[dict[str, Any]] = []
            for step in range(horizon):
                valid_time = inputs.issue_time + (step + 1) * inputs.time_step
                spread = abs(last_value) * self.spread_pct_per_step * (step + 1)
                for q in _QUANTILE_LEVELS:
                    rows.append(
                        {
                            "valid_time": valid_time,
                            "quantile": q,
                            "value": last_value + spread * (2 * q - 1),
                        }
                    )

            values_df = pl.DataFrame(rows).with_columns(
                pl.col("quantile").cast(pl.Float64),
                pl.col("value").cast(pl.Float64),
            )

            ensembles[param] = ForecastEnsemble.from_quantiles(
                station_id=inputs.station_id,
                issued_at=inputs.issue_time,
                parameter=param,
                units="m3/s",
                time_step=inputs.time_step,
                values=values_df,
            )

        return ensembles, None

    def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
        art: PersistenceArtifact = artifact
        payload = {"target_parameters": sorted(art.target_parameters)}
        return json.dumps(payload).encode()

    def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
        payload = json.loads(raw.decode())
        return PersistenceArtifact(
            target_parameters=frozenset(payload["target_parameters"])
        )
