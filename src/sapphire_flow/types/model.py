from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import polars as pl  # noqa: TC002

if TYPE_CHECKING:
    from datetime import timedelta
    from uuid import UUID

    import xarray as xr

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import (
        ArtifactScope,
        ModelArtifactStatus,
        SpatialRepresentation,
    )
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, StationId

ModelParams = dict[str, Any]
ModelArtifact = Any

PROVENANCE_SUFFIX = "_provenance"


def forcing_provenance_columns(forcing: pl.DataFrame) -> list[str]:
    return [c for c in forcing.columns if c.endswith(PROVENANCE_SUFFIX)]


def parameter_columns(forcing: pl.DataFrame) -> list[str]:
    return [
        c
        for c in forcing.columns
        if c not in ("timestamp", "station_id") and not c.endswith(PROVENANCE_SUFFIX)
    ]


def validate_forcing_provenance(forcing: pl.DataFrame) -> None:
    param_cols = parameter_columns(forcing)
    expected = {f"{p}{PROVENANCE_SUFFIX}" for p in param_cols}
    actual = set(forcing_provenance_columns(forcing))
    if missing := expected - actual:
        raise ValueError(f"Missing provenance columns: {missing}")
    if extra := actual - expected:
        raise ValueError(f"Orphaned provenance columns: {extra}")


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelInputs:
    station_id: StationId
    forcing: pl.DataFrame | xr.Dataset
    observations: pl.DataFrame
    static_attributes: pl.DataFrame | None
    issue_time: UtcDatetime
    forecast_horizon_steps: int
    time_step: timedelta
    warm_up_steps: int | None


@dataclass(frozen=True, kw_only=True, slots=True)
class TrainingData:
    forcing: pl.DataFrame
    observations: pl.DataFrame
    targets: pl.DataFrame
    static_attributes: pl.DataFrame | None
    time_step: timedelta
    val_start: UtcDatetime | None


@dataclass(frozen=True, kw_only=True, slots=True)
class GroupTrainingData:
    group_id: StationGroupId
    station_data: dict[StationId, TrainingData]
    time_step: timedelta
    val_start: UtcDatetime | None


@dataclass(frozen=True, kw_only=True, slots=True)
class StationInputData:
    past_targets: pl.DataFrame
    past_dynamic: pl.DataFrame
    future_dynamic: pl.DataFrame
    static: pl.DataFrame | None


@dataclass(frozen=True, kw_only=True, slots=True)
class GroupModelInputs:
    group_id: StationGroupId
    station_ids: tuple[StationId, ...]
    past_targets: pl.DataFrame
    past_dynamic: pl.DataFrame
    future_dynamic: pl.DataFrame
    static: pl.DataFrame | None
    issue_time: UtcDatetime
    forecast_horizon_steps: int
    time_step: timedelta

    def for_station(self, station_id: StationId) -> StationInputData:
        if station_id not in self.station_ids:
            msg = f"Station {station_id} not in group {self.group_id}"
            raise ValueError(msg)
        sid_str = str(station_id)

        def _filter(df: pl.DataFrame) -> pl.DataFrame:
            return df.filter(pl.col("station_id") == sid_str).drop("station_id")

        # Edge case: if self.static is not None but filtering yields zero rows
        # (station exists in station_ids but has no row in stacked static DF),
        # return None rather than an empty DataFrame.
        static_filtered: pl.DataFrame | None = None
        if self.static is not None:
            sf = _filter(self.static)
            static_filtered = sf if not sf.is_empty() else None

        return StationInputData(
            past_targets=_filter(self.past_targets),
            past_dynamic=_filter(self.past_dynamic),
            future_dynamic=_filter(self.future_dynamic),
            static=static_filtered,
        )


def _reorder_station_id_first(df: pl.DataFrame) -> pl.DataFrame:
    cols = ["station_id"] + [c for c in df.columns if c != "station_id"]
    return df.select(cols)


def stack_model_inputs(
    group_id: StationGroupId,
    inputs: dict[StationId, ModelInputs],
    issue_time: UtcDatetime,
) -> GroupModelInputs:
    """Stack per-station ModelInputs into a single GroupModelInputs.

    Adds a ``station_id`` (Utf8) column as the first column of each DataFrame.
    Splits ``ModelInputs.forcing`` into past_dynamic (≤ issue_time) and
    future_dynamic (> issue_time) based on the timestamp column.
    Maps ``ModelInputs.observations`` → past_targets.

    Boundary semantics: the row at exactly ``issue_time`` is included in
    ``past_dynamic`` (the last known state), not in ``future_dynamic``.
    """
    if not inputs:
        raise ValueError("Cannot stack empty inputs dict")

    station_ids = tuple(inputs.keys())
    first = next(iter(inputs.values()))

    for sid, inp in inputs.items():
        if inp.issue_time != first.issue_time:
            raise ValueError(
                f"Inconsistent issue_time: station {sid} has {inp.issue_time}, "
                f"expected {first.issue_time}"
            )
        if inp.forecast_horizon_steps != first.forecast_horizon_steps:
            raise ValueError(
                f"Inconsistent forecast_horizon_steps: station {sid} has "
                f"{inp.forecast_horizon_steps}, expected {first.forecast_horizon_steps}"
            )
        if inp.time_step != first.time_step:
            raise ValueError(
                f"Inconsistent time_step: station {sid} has {inp.time_step}, "
                f"expected {first.time_step}"
            )

    past_targets_parts: list[pl.DataFrame] = []
    past_dynamic_parts: list[pl.DataFrame] = []
    future_dynamic_parts: list[pl.DataFrame] = []
    static_parts: list[pl.DataFrame] = []

    for sid, inp in inputs.items():
        if isinstance(inp.forcing, pl.DataFrame):
            forcing = inp.forcing
        else:
            raise TypeError(
                f"GroupModelInputs stacking requires pl.DataFrame forcing, "
                f"got {type(inp.forcing).__name__} for station {sid}"
            )

        sid_col = pl.lit(str(sid)).alias("station_id")

        past = forcing.filter(pl.col("timestamp") <= issue_time)
        future = forcing.filter(pl.col("timestamp") > issue_time)

        past_dynamic_parts.append(past.with_columns(sid_col))
        future_dynamic_parts.append(future.with_columns(sid_col))

        past_targets_parts.append(inp.observations.with_columns(sid_col))

        if inp.static_attributes is not None:
            static_parts.append(inp.static_attributes.with_columns(sid_col))

    return GroupModelInputs(
        group_id=group_id,
        station_ids=station_ids,
        past_targets=_reorder_station_id_first(pl.concat(past_targets_parts)),
        past_dynamic=_reorder_station_id_first(pl.concat(past_dynamic_parts)),
        future_dynamic=_reorder_station_id_first(pl.concat(future_dynamic_parts)),
        static=(
            _reorder_station_id_first(pl.concat(static_parts))
            if static_parts
            else None
        ),
        issue_time=first.issue_time,
        forecast_horizon_steps=first.forecast_horizon_steps,
        time_step=first.time_step,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelRecord:
    id: ModelId
    display_name: str
    artifact_scope: ArtifactScope
    description: str
    created_at: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelDataRequirements:
    target_parameters: frozenset[str]
    past_dynamic_features: frozenset[str]
    future_dynamic_features: frozenset[str]
    static_features: frozenset[str]
    supported_time_steps: frozenset[timedelta]
    lookback_steps: int
    spatial_input_type: SpatialRepresentation


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelRegistryEntry:
    id: ModelId
    display_name: str
    description: str
    artifact_scope: ArtifactScope
    data_requirements: ModelDataRequirements
    registered_at: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelArtifactRecord:
    id: ArtifactId
    model_id: ModelId
    station_id: StationId | None
    group_id: StationGroupId | None
    status: ModelArtifactStatus
    artifact_path: str
    training_period_start: UtcDatetime
    training_period_end: UtcDatetime
    trained_at: UtcDatetime
    promoted_at: UtcDatetime | None
    promoted_by: UUID | None
    superseded_at: UtcDatetime | None
    created_at: UtcDatetime
