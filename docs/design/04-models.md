---
status: DRAFT
---

> **DRAFT** — This design doc has not completed the review maturity gate. Do not treat as authoritative until `status: READY`.

# Forecast Models

## Pluggable model interface

The system supports multiple model types through a common Protocol.
ML models (LSTM, transformer) are the primary implementation, but
conceptual models (HBV, GR4J via pydrology) can be wrapped to the
same interface.

## ForecastModel Protocol

```python
from typing import Protocol, runtime_checkable
from pathlib import Path

@runtime_checkable
class ForecastModel(Protocol):
    @property
    def min_lookback_hours(self) -> int:
        """Minimum hours of historical observations needed to produce a forecast."""
        ...

    def predict(
        self,
        inputs: ModelInputs,
    ) -> ForecastEnsemble: ...

    def save(self, path: Path) -> None: ...

    @classmethod
    def load(cls, path: Path) -> "ForecastModel": ...
```

```python
class ForecastType(Enum):
    SUBDAILY = "subdaily"
    DAILY = "daily"
    PENTADAL = "pentadal"
    DEKADAL = "dekadal"
    MONTHLY = "monthly"
    SEASONAL = "seasonal"

class ModelInputs(NamedTuple):
    station_id: str
    parameter_id: str                                   # primary forecast target (e.g. "water_level")
    observations: dict[str, list[tuple[datetime, float]]]
    # Keyed by parameter name. Example:
    # {"water_level": [(t1, 3.2), (t2, 3.4), ...],
    #  "temperature": [(t1, 12.0), (t2, 11.5), ...]}
    weather_forecasts: dict[str, list[tuple[datetime, float]]]
    # Keyed by parameter name. Example:
    # {"precipitation": [(t1, 5.0), (t2, 8.2), ...],
    #  "temperature":   [(t1, 14.0), (t2, 13.5), ...]}
    forecast_type: ForecastType
    metadata: dict[str, Any]                            # station context (elevation, basin area, ...)

`observations` is keyed by parameter name, allowing models to consume multiple
input parameters (e.g. both water level and temperature). Models that need only
the primary parameter access it via `inputs.observations[inputs.parameter_id]`.
The `weather_forecasts` dict follows the same convention. All parameters included
in `observations` have passed QC filtering — excluded observations (quality_flag 9)
are already removed by the forecast preparation service.

**Data availability check**: The forecast flow verifies that sufficient data
exists before calling `predict()`. It checks the time span of
`observations[parameter_id]` (the primary forecast target) against the model's
`min_lookback_hours`. See 05-flows.md for the full fallback logic.

class ForecastEnsemble(NamedTuple):
    station_id: str
    parameter_id: str
    forecast_type: ForecastType
    issued_at: datetime
    members: list[list[tuple[int, float]]]  # list of members, each a list of (lead_time_minutes, value)
```

> These types are defined in `sapphire_flow.types` and `sapphire_flow.protocols`.
> The `members` field uses lead_time in minutes (integer) for consistency with
> the database schema. `ForecastType` is a Python Enum matching the database ENUM.

Training is separate from prediction — see below.

## Model lookback requirements

Models must declare their minimum data requirements:

The `ForecastModel` Protocol (defined above) includes `min_lookback_hours` as
a required property.

The forecast flow checks data availability against `min_lookback_hours` before
calling `predict()`. If insufficient data is available:

1. Log a warning with the station ID and data gap details
2. Fall back to the station's fallback model (which should have lower data requirements)
3. If the fallback also has insufficient data, skip the station and flag it as "no forecast"

This prevents cryptic model failures during:
- Initial deployment (limited historical data)
- Stations with sparse or gapped data
- Recovery after extended outages

Fallback models (e.g. linear regression, persistence) should require minimal lookback
(1-3 days) to maximize availability.

## Forecast types

Models can produce output at different temporal resolutions:

| forecast_type | Typical horizon | Source                          |
|---------------|----------------|---------------------------------|
| subdaily      | 3-6 days       | Native model output (hourly)    |
| daily         | 15 days        | Native model output             |
| pentadal      | 1 month        | Native or aggregated from daily |
| dekadal       | 1 month        | Native or aggregated from daily |
| monthly       | 3 months       | Native model output (seasonal)  |
| seasonal      | 6 months       | Native model output (seasonal)  |

If a model natively produces pentadal/monthly/seasonal output, it stores
results directly with the appropriate `forecast_type`. Pentadal and dekadal
can also be computed on-the-fly by aggregating daily forecasts (handled by
the API layer, not the model). This allows the same daily model to serve
both daily bulletins and pentadal/dekadal bulletins (common in Central Asia).

## Training interface

```python
@runtime_checkable
class TrainableModel(Protocol):
    def train(
        self,
        training_data: TrainingDataset,
        config: ModelConfig,
    ) -> TrainResult: ...
```

Training produces a model artifact (weights file, parameters) that can
be `save()`d and later `load()`ed for operational prediction.

## Collaboration strategy

### v1.0: single-repo with Protocol boundary

All Protocols, domain types, and orchestration live in SAPPHIRE_flow:

```
SAPPHIRE_flow/
├── src/sapphire_flow/
│   ├── protocols/          # ForecastModel, TrainableModel, DataSource, etc.
│   ├── types/              # ModelInputs, ForecastEnsemble, Observation, etc.
│   ├── models/             # Model implementations (or thin wrappers)
│   └── ...                 # Everything else
```

The model collaborator develops in their own repo (e.g. `hf-forecasting`) and
implements the Protocols defined in `sapphire_flow.protocols`. For v1.0, the
collaborator installs SAPPHIRE_flow as a dependency (or just copies the Protocol
files — they're tiny). Models register via Python entry points as before.

### Future: extract shared package when needed

When a second consumer exists (the model collaborator's package in active
development), extract `protocols/` and `types/` into a standalone `sapphire-sdk`
package. This is a mechanical operation:
1. Move `protocols/` and `types/` to a new repo
2. Add a `pyproject.toml`
3. Both SAPPHIRE_flow and hf-forecasting depend on `sapphire-sdk`

The Protocol-based design makes this extraction safe — no implementation code
moves, only interfaces and value types.

### Why not extract now?

- One consumer (SAPPHIRE_flow) means zero coordination benefit
- Every Protocol change would require: bump sdk → publish → bump pin in SAPPHIRE_flow → bump pin in hf-forecasting. Three PRs instead of one.
- The collaborator is not yet actively developing against the interface
- Extraction cost is low (~30 minutes) whenever it becomes needed

### Benefits of the Protocol boundary

- **Clean contract**: Models don't know about PostgreSQL, Prefect, or FastAPI
- **Independent development**: Collaborator works at their own pace against stable Protocols
- **Independent testing**: Models are tested with synthetic data, no infra needed
- **Version pinning**: We upgrade model package versions deliberately, not accidentally
- **Name independence**: SAPPHIRE_flow doesn't care what the models package is called
- **Multiple model packages**: Future collaborators can create their own packages
- **Easy extraction**: When a shared package is needed, the boundary is already clean

### Model discovery

Models register themselves via Python entry points:

```toml
# In the models package pyproject.toml (e.g. hf-forecasting)
[project.entry-points."sapphire_flow.models"]
lstm_daily = "hf_forecasting.lstm:LSTMDailyModel"
lstm_subdaily = "hf_forecasting.lstm:LSTMSubDailyModel"
```

SAPPHIRE_flow discovers available models at startup:

```python
from importlib.metadata import entry_points

models = entry_points(group="sapphire_flow.models")
```

This means adding a new model type requires zero changes to SAPPHIRE_flow.

At startup, the system validates all registered models:
1. Load each entry point and verify it satisfies the `ForecastModel` Protocol (via `runtime_checkable`)
2. Check that every station's configured `model` has a matching entry point
3. Check that every station's configured `model_version` matches the installed package version
4. Fail fast with a clear error message if any check fails

This catches misconfiguration at deploy time, not at 3 AM during a forecast run.

## Station-level model configuration

Each station's model assignment is stored in the `station_model_config` database
table (see 02-data-model.md). This replaces TOML-based per-station configuration,
enabling runtime changes without container restarts.

### Viewing and changing model assignments

**Dashboard**: The station detail page shows the current model assignment alongside
skill scores. Forecasters can switch models via a dropdown of registered models.

**API**:
```
GET    /api/v1/stations/{id}/model-config        Current model assignment
PATCH  /api/v1/stations/{id}/model-config        Change model assignment
GET    /api/v1/admin/model-config                All station model configs
PATCH  /api/v1/admin/model-config/bulk           Bulk update (e.g. version upgrade)
```

**CLI** (initial setup):
```bash
# Import from TOML bootstrap file
docker compose exec api sapphire-flow import-model-config --file models.toml
```

### Bootstrap TOML format

Used only for initial import — not read at runtime:

```toml
[stations.ABC-001]
model = "lstm_daily"
model_version = "2026.10.1"
model_artifact = "artifacts/abc_001_lstm.pt"
fallback_model = "hbv"
fallback_artifact = "artifacts/abc_001_hbv.json"

[stations.XYZ-042]
model = "hbv"
model_artifact = "artifacts/xyz_042_hbv.json"
```

## Fallback model requirement

Every station must have at least one non-ML model configured as a fallback
(e.g. linear regression, persistence/climatology baseline, or HBV/GR4J via pydrology).
Linear regression is the recommended default fallback — powerful enough to be
useful, trivial to implement and retrain, and has no external dependencies.
This ensures the system can produce forecasts even when:
- ML model artifacts are unavailable or corrupted
- The ML model collaborator is unreachable for retraining
- An ML model produces clearly erroneous output (detected by sanity checks)

The forecast flow tries the primary model first. If it fails, it falls back
to the station's fallback model and flags the forecast accordingly.

## Model output sanity checks

After `predict()` returns and before results are stored, a sanity check validates
the output. This catches corrupted models, numerical instability, or clearly
wrong predictions before they reach the dashboard.

```python
import math

from sapphire_flow.services.forecast_prep import validate_ensemble

class SanityCheckFailure(Exception):
    pass

def validate_ensemble(ensemble: ForecastEnsemble, station_metadata: dict) -> None:
    """Raises SanityCheckFailure if output is physically implausible."""
    # 1. Non-empty ensemble
    if len(ensemble.members) == 0:
        raise SanityCheckFailure("Ensemble has 0 members")

    # 2. No empty members
    for i, member in enumerate(ensemble.members):
        if len(member) == 0:
            raise SanityCheckFailure(f"Member {i} has 0 lead-time entries")

    # 3. Consistent lead-time sequences across all members
    reference_lead_times = [lt for lt, _ in ensemble.members[0]]
    for i, member in enumerate(ensemble.members):
        member_lead_times = [lt for lt, _ in member]
        if member_lead_times != reference_lead_times:
            raise SanityCheckFailure(
                f"Member {i} lead times {member_lead_times} differ from "
                f"member 0 lead times {reference_lead_times}"
            )

    # 4. Lead times are non-negative and strictly increasing
    for i, lt in enumerate(reference_lead_times):
        if lt < 0:
            raise SanityCheckFailure(f"Negative lead time {lt} at index {i}")
        if i > 0 and lt <= reference_lead_times[i - 1]:
            raise SanityCheckFailure(
                f"Non-monotonic lead times: {reference_lead_times[i-1]} -> {lt}"
            )

    # 5. All values are finite (catches NaN and Inf)
    for i, member in enumerate(ensemble.members):
        for lead_time, value in member:
            if not math.isfinite(value):
                raise SanityCheckFailure(
                    f"Non-finite value {value} in member {i} at lead {lead_time}"
                )

    # 6. Plausibility bounds
    min_plausible = station_metadata.get("min_plausible", -50)
    max_plausible = station_metadata.get("max_plausible", 50000)
    for member in ensemble.members:
        for lead_time, value in member:
            if value < min_plausible:
                raise SanityCheckFailure(
                    f"Value {value} below minimum plausible "
                    f"{min_plausible} at lead {lead_time}"
                )
            if value > max_plausible:
                raise SanityCheckFailure(
                    f"Value {value} above maximum plausible "
                    f"{max_plausible} at lead {lead_time}"
                )

    # 7. Ensemble spread (only for multi-member ensembles)
    if len(ensemble.members) > 1:
        unique_values = {v for member in ensemble.members for _, v in member}
        if len(unique_values) == 1:
            raise SanityCheckFailure(
                "All ensemble members identical — possible model failure"
            )
```

Plausibility bounds (`min_plausible`, `max_plausible`) are configured per station
in TOML or station metadata. If sanity checks fail, the forecast flow falls back
to the station's fallback model (same as primary model failure).

This function lives in `services/forecast_prep.py` — it is a pure function
with no I/O, testable with synthetic ensembles.

## Bulk data export for training

Model collaborators and automated retraining pipelines need bulk access to
historical data. The API provides a dedicated export endpoint:

```
GET /api/v1/export/training-data?station=ABC-001&start=2000-01-01&end=2025-12-31&format=csv
GET /api/v1/export/training-data?station=ABC-001&format=parquet
```

Export includes:
- Observations (with quality flags, excluding flag=9 excluded values)
- Weather forecast history (all ensemble members)
- Rating curve versions (if applicable)
- Station metadata (location, elevation, basin area)

This endpoint requires `admin` or `forecaster` role. Large exports are
streamed to avoid memory issues.

Typical retraining cadence: annually or every 5 years depending on the model
and data availability. Retraining is managed within SAPPHIRE_flow (see
Training workflow below).

## Training workflow

Training is decoupled from operations:

1. Collaborator or hydrologist trains a model (on workstation, cloud, or server)
2. Training produces an artifact (e.g. `.pt` file for PyTorch, `.json` for conceptual)
3. Artifact is stored in a known location (filesystem or object storage)
4. Station config points to the artifact
5. Operational system loads the artifact at forecast time

Training can also be triggered as a Prefect flow for periodic retraining,
but this is optional — manual training + artifact upload is the baseline.

### Artifact safety

Model artifacts must be replaced atomically to prevent operational forecasts
from loading a partially-written file:

1. Train writes to a temporary path (e.g. `artifacts/abc_001_lstm.pt.tmp`)
2. Validate the artifact: `model.load(tmp_path)` succeeds + `model.predict()` on a validation set passes `validate_ensemble`
3. Atomically rename: `os.rename(tmp_path, final_path)` (atomic on POSIX within the same filesystem)
4. Only then update the model registry / station config

If validation fails, the temporary file is deleted and the operational
artifact is untouched. The old model continues serving forecasts.

## Rating curves and discharge

### v1.0: Water level forecasts only

Models predict water level as the primary forecast parameter. Water level is
more directly observable and avoids compounding rating curve errors into
forecasts. Flood thresholds in v1.0 are defined in water level units.

### v2.0: Discharge conversion

When a rating curve is available and has associated uncertainty:
- Model predicts water level
- System converts to discharge post-prediction using the active rating curve
- Rating curve uncertainty is propagated into the discharge ensemble spread
- Both water level and discharge forecasts are stored

**Design consideration**: Storing water-level forecasts as canonical and computing
discharge at query time (via the active rating curve) would avoid baking a specific
rating curve version into stored values. This allows retroactive recomputation when
rating curves are updated. Trade-off: query-time computation adds latency and
complexity. Decision deferred to v2.0 implementation phase.

When no rating curve exists:
- Model predicts water level directly
- Discharge is not computed

### Rating curve uncertainty

Rating curves carry significant uncertainty, especially at high flows (extrapolation
beyond measured points). The system supports uncertainty bounds on rating curves
(see 02-data-model.md `rating_curves.uncertainty`). In practice, many hydromets
do not quantify this uncertainty — the system provides estimation tools and
encourages adoption, but does not require it.

When uncertainty bounds are available, discharge ensemble spread is widened
accordingly. When unavailable, discharge conversion uses the point estimate
and the uncertainty bands reflect only the hydrological model uncertainty.

Optional (future): learn an implicit stage-discharge relationship from data.

## Review History

| Round | Date | Reviewers | Blocking | Advisory | Status |
|-------|------|-----------|----------|----------|--------|
