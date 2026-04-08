from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import structlog
from sklearn.linear_model import Ridge

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

_N_MEMBERS = 50
_LOOKBACK = 7
_PAST_FEATURES = ("precipitation", "temperature")
_FUTURE_FEATURES = ("precipitation", "temperature")


@dataclass(frozen=True, kw_only=True, slots=True)
class LinearRegressionArtifact:
    coefficients: np.ndarray  # (n_steps, n_features)
    intercepts: np.ndarray  # (n_steps,)
    residuals: np.ndarray  # (n_train_samples, n_steps)
    n_steps: int


def _build_feature_vector(
    past_dynamic: pl.DataFrame,
    future_dynamic: pl.DataFrame,
    horizon: int,
) -> np.ndarray | None:
    past_arr = (
        past_dynamic.tail(_LOOKBACK).select(list(_PAST_FEATURES)).to_numpy()
    )  # (_LOOKBACK, 2)
    if past_arr.shape[0] < _LOOKBACK:
        return None  # insufficient lookback data
    future_arr = (
        future_dynamic.head(horizon).select(list(_FUTURE_FEATURES)).to_numpy()
    )  # (horizon, 2)
    if future_arr.shape[0] < horizon:
        return None  # insufficient future data
    return np.concatenate([past_arr.ravel(), future_arr.ravel()])


class LinearRegressionDaily:
    artifact_scope: ArtifactScope = ArtifactScope.STATION
    data_requirements: ModelDataRequirements = ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(_PAST_FEATURES),
        future_dynamic_features=frozenset(_FUTURE_FEATURES),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=24)}),
        lookback_steps=_LOOKBACK,
        spatial_input_type=SpatialRepresentation.POINT,
    )

    def train(
        self,
        data: StationTrainingData,
        params: ModelParams,
        rng: random.Random,
    ) -> ModelArtifact:
        targets = data.past_targets.sort("timestamp")
        past_dyn = data.past_dynamic.sort("timestamp")

        future_ts = data.future_dynamic.sort("timestamp")
        n_steps = future_ts["timestamp"].n_unique()
        if n_steps == 0:
            # Training data has no future split — default to lookback_steps
            n_steps = _LOOKBACK

        joined = past_dyn.join(
            targets.select(["timestamp", "discharge"]),
            on="timestamp",
            how="inner",
        ).sort("timestamp")

        n_total = len(joined)
        min_samples = _LOOKBACK + n_steps

        if n_total < min_samples:
            raise ValueError(
                f"Not enough training rows: need {min_samples} "
                f"(lookback={_LOOKBACK} + horizon={n_steps}), got {n_total}"
            )

        precip = joined["precipitation"].to_numpy()
        temp = joined["temperature"].to_numpy()
        discharge = joined["discharge"].to_numpy()

        x_rows: list[np.ndarray] = []
        y_rows: list[np.ndarray] = []

        for i in range(_LOOKBACK, n_total - n_steps):
            past_window = np.column_stack(
                [
                    precip[i - _LOOKBACK : i],
                    temp[i - _LOOKBACK : i],
                ]
            )
            future_window = np.column_stack(
                [
                    precip[i : i + n_steps],
                    temp[i : i + n_steps],
                ]
            )
            x_rows.append(np.concatenate([past_window.ravel(), future_window.ravel()]))
            y_rows.append(discharge[i : i + n_steps])

        if not x_rows:
            raise ValueError("No valid training samples could be constructed.")

        x_mat = np.stack(x_rows)  # (n_samples, n_features)
        y_mat = np.stack(y_rows)  # (n_samples, n_steps)

        alpha = float(params.get("alpha", 1.0))
        seed = rng.randint(0, 2**31)

        coefficients = np.zeros((n_steps, x_mat.shape[1]))
        intercepts = np.zeros(n_steps)
        predictions = np.zeros_like(y_mat)

        for s in range(n_steps):
            ridge = Ridge(alpha=alpha, random_state=seed)
            ridge.fit(x_mat, y_mat[:, s])
            coefficients[s] = ridge.coef_
            intercepts[s] = ridge.intercept_
            predictions[:, s] = ridge.predict(x_mat)

        residuals = y_mat - predictions  # (n_samples, n_steps)

        log.debug(
            "model.training_completed",
            n_samples=len(x_rows),
            n_steps=n_steps,
            n_features=x_mat.shape[1],
        )

        return LinearRegressionArtifact(
            coefficients=coefficients,
            intercepts=intercepts,
            residuals=residuals,
            n_steps=n_steps,
        )

    def predict(
        self,
        artifact: ModelArtifact,
        inputs: StationModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[dict[str, ForecastEnsemble], bytes | None]:
        from sapphire_flow.types.ensemble import ForecastEnsemble

        art: LinearRegressionArtifact = artifact
        horizon = inputs.forecast_horizon_steps

        if horizon > art.n_steps:
            raise ValueError(
                f"forecast_horizon_steps={horizon} exceeds "
                f"trained horizon n_steps={art.n_steps}"
            )

        past_dyn = inputs.data.past_dynamic.sort("timestamp")
        future_dyn = inputs.data.future_dynamic.sort("timestamp").head(horizon)

        x_vec = _build_feature_vector(past_dyn, future_dyn, horizon)
        if x_vec is None:
            raise ValueError(
                f"Insufficient data: need {_LOOKBACK} lookback rows and "
                f"{horizon} future rows"
            )

        coef = art.coefficients[:horizon]  # (horizon, n_features)
        intercept = art.intercepts[:horizon]  # (horizon,)
        det = x_vec @ coef.T + intercept  # (horizon,)

        n_residuals = art.residuals.shape[0]
        seed = rng.randint(0, 2**31)
        np_rng = np.random.default_rng(seed)
        idx = np_rng.integers(0, n_residuals, size=_N_MEMBERS)
        sampled = art.residuals[idx, :horizon]  # (_N_MEMBERS, horizon)
        members = np.clip(det[None, :] + sampled, 0.0, None)  # (_N_MEMBERS, horizon)

        valid_times = future_dyn["timestamp"].to_list()

        rows: list[dict] = [
            {"valid_time": vt, "member_id": m_idx, "value": float(members[m_idx, step])}
            for m_idx in range(_N_MEMBERS)
            for step, vt in enumerate(valid_times)
        ]

        values_df = pl.DataFrame(rows).with_columns(
            pl.col("member_id").cast(pl.Int32),
            pl.col("value").cast(pl.Float64),
        )

        ensemble = ForecastEnsemble.from_members(
            station_id=inputs.station_id,
            issued_at=inputs.issue_time,
            parameter="discharge",
            units="m3/s",
            time_step=inputs.time_step,
            values=values_df,
        )

        return {"discharge": ensemble}, None

    def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
        art: LinearRegressionArtifact = artifact
        buf = io.BytesIO()
        np.savez_compressed(
            buf,
            coefficients=art.coefficients,
            intercepts=art.intercepts,
            residuals=art.residuals,
            n_steps=np.array([art.n_steps], dtype=np.int64),
        )
        return buf.getvalue()

    def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
        data = np.load(io.BytesIO(raw))
        return LinearRegressionArtifact(
            coefficients=data["coefficients"],
            intercepts=data["intercepts"],
            residuals=data["residuals"],
            n_steps=int(data["n_steps"][0]),
        )
