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
_HORIZON = 5


@dataclass(frozen=True, kw_only=True, slots=True)
class LinearRegressionArtifact:
    coefficients: np.ndarray  # (n_features,) — one set for all steps (autoregressive)
    intercepts: np.ndarray  # (1,) — scalar intercept
    residuals: np.ndarray  # (n_train_samples,) — one-step residuals
    n_steps: int


def _extract_discharge(past_targets: pl.DataFrame) -> np.ndarray:
    sorted_df = past_targets.sort("timestamp")
    tail = sorted_df.tail(_LOOKBACK)
    if len(tail) < _LOOKBACK:
        raise ValueError(
            f"Insufficient lookback: need {_LOOKBACK} rows, got {len(tail)}"
        )
    return tail["discharge"].to_numpy()


class LinearRegressionDaily:
    artifact_scope: ArtifactScope = ArtifactScope.STATION
    data_requirements: ModelDataRequirements = ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset(),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=24)}),
        lookback_steps=_LOOKBACK,
        forecast_horizon_steps=_HORIZON,
        spatial_input_type=SpatialRepresentation.POINT,
    )

    def train(
        self,
        data: StationTrainingData,
        params: ModelParams,
        rng: random.Random,
    ) -> ModelArtifact:
        discharge = data.past_targets.sort("timestamp")["discharge"].to_numpy()

        n_total = len(discharge)
        min_samples = _LOOKBACK + 1

        if n_total < min_samples:
            raise ValueError(
                f"Not enough training rows: need at least {min_samples} "
                f"(lookback={_LOOKBACK} + 1), got {n_total}"
            )

        x_rows = [discharge[i - _LOOKBACK : i] for i in range(_LOOKBACK, n_total)]
        y_vals = discharge[_LOOKBACK:]

        x_mat = np.stack(x_rows)  # (n_samples, _LOOKBACK)
        y_vec = y_vals  # (n_samples,)

        alpha = float(params.get("alpha", 1.0))
        seed = rng.randint(0, 2**31)

        ridge = Ridge(alpha=alpha, random_state=seed)
        ridge.fit(x_mat, y_vec)

        predictions = ridge.predict(x_mat)
        residuals = y_vec - predictions  # (n_samples,)

        # Compute autoregressive rollout to determine n_steps from declared horizon.
        # The artifact supports multi-step prediction via rollout at predict time.
        n_steps = self.data_requirements.forecast_horizon_steps

        log.debug(
            "model.training_completed",
            n_samples=len(x_rows),
            n_features=x_mat.shape[1],
            n_steps=n_steps,
        )

        return LinearRegressionArtifact(
            coefficients=ridge.coef_,  # (n_features,)
            intercepts=np.array([ridge.intercept_]),  # (1,)
            residuals=residuals,  # (n_samples,)
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

        lags = _extract_discharge(inputs.data.past_targets)  # (_LOOKBACK,)

        # Deterministic autoregressive rollout
        det = np.zeros(horizon)
        current_lags = lags.copy()
        for step in range(horizon):
            val = float(current_lags @ art.coefficients + art.intercepts[0])
            det[step] = val
            current_lags = np.roll(current_lags, -1)
            current_lags[-1] = val

        # Bootstrap ensemble: sample residuals and build member rollouts
        n_residuals = len(art.residuals)
        seed = rng.randint(0, 2**31)
        np_rng = np.random.default_rng(seed)

        members = np.zeros((_N_MEMBERS, horizon))
        for m in range(_N_MEMBERS):
            member_lags = lags.copy()
            for step in range(horizon):
                noise_idx = np_rng.integers(0, n_residuals)
                val = float(
                    member_lags @ art.coefficients
                    + art.intercepts[0]
                    + art.residuals[noise_idx]
                )
                val = max(0.0, val)
                members[m, step] = val
                member_lags = np.roll(member_lags, -1)
                member_lags[-1] = val

        valid_times = [
            inputs.issue_time + (i + 1) * inputs.time_step for i in range(horizon)
        ]

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
        data = np.load(io.BytesIO(raw), allow_pickle=False)
        expected = {"coefficients", "intercepts", "residuals", "n_steps"}
        missing = expected - set(data.files)
        if missing:
            raise ValueError(f"artifact missing keys: {missing}")
        n_steps = int(data["n_steps"][0])
        if n_steps <= 0:
            raise ValueError(f"n_steps must be positive, got {n_steps}")
        coef = data["coefficients"]
        if coef.ndim != 1 or coef.shape[0] != _LOOKBACK:
            raise ValueError(
                f"coefficients must be 1D with shape ({_LOOKBACK},), got {coef.shape}"
            )
        if data["intercepts"].shape != (1,):
            raise ValueError(f"intercepts.shape={data['intercepts'].shape} != (1,)")
        res = data["residuals"]
        if res.ndim != 1 or res.shape[0] == 0:
            raise ValueError(
                f"residuals must be non-empty 1D array, got shape {res.shape}"
            )
        for name in ("coefficients", "intercepts", "residuals"):
            if not np.all(np.isfinite(data[name])):
                raise ValueError(f"{name} contains non-finite values (NaN or Inf)")
        return LinearRegressionArtifact(
            coefficients=data["coefficients"],
            intercepts=data["intercepts"],
            residuals=data["residuals"],
            n_steps=n_steps,
        )
