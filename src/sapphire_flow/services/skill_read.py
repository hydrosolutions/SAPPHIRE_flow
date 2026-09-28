"""Plan 402 T2 — the read path for `GET /api/v1/stations/{id}/skill`.

Row selection (plan `Endpoint contract`): the models of the station's active
assignments (station and group, Plan 329) via `fetch_active_model_assignments`
— used only to list models; for each model, the artifact the station's
forecast actually uses, through the ID-only
`ModelArtifactStore.fetch_active_artifact_id_for_station` (T2, no bytes
loaded); skill rows for that model, parameter `discharge` only, kept only
when `model_artifact_id` equals that artifact; dropped when `eval_period_end`
is after the injected request time (D5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sapphire_flow.services.forecast_lab.db_sources import (
    fetch_active_model_assignments,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.protocols.stores import ModelArtifactStore, SkillStore
    from sapphire_flow.services.forecast_lab.db_sources import ForecastLabStores
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import FlowRegime, ForcingType, SkillSource
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationId

EvaluatedOn = Literal[
    "training_period", "outside_training_period", "overlaps_training_period"
]

_DISCHARGE = "discharge"


@dataclass(frozen=True, kw_only=True, slots=True)
class StationSkillRow:
    model_id: ModelId
    model_artifact_id: ArtifactId
    generation_id: UUID | None
    skill_source: SkillSource
    forcing_type: ForcingType | None
    computation_version: int
    time_step_seconds: int
    phase_offset_seconds: int | None
    eval_period_start: UtcDatetime
    eval_period_end: UtcDatetime
    training_period_start: UtcDatetime
    training_period_end: UtcDatetime
    evaluated_on: EvaluatedOn
    lead_time_hours: int
    season: str | None
    flow_regime: FlowRegime | None
    metric: str
    score: float
    sample_size: int


def evaluated_on(
    *,
    eval_start: UtcDatetime,
    eval_end: UtcDatetime,
    training_start: UtcDatetime,
    training_end: UtcDatetime,
) -> EvaluatedOn:
    """Closed intervals (plan `Endpoint contract`): an eval window starting
    exactly at `training_period_end` is `overlaps_training_period`, not
    `outside_training_period` — a shared instant is not "no shared instant"."""
    if training_start <= eval_start and eval_end <= training_end:
        return "training_period"
    if eval_end < training_start or eval_start > training_end:
        return "outside_training_period"
    return "overlaps_training_period"


_NullFirst = tuple[int, "str | int"]


def _null_first(value: str | int | None) -> _NullFirst:
    return (0, "") if value is None else (1, value)


_SortKey = tuple[
    str, str, _NullFirst, int, _NullFirst, int, _NullFirst, _NullFirst, str
]


def _sort_key(row: StationSkillRow) -> _SortKey:
    forcing = row.forcing_type.value if row.forcing_type is not None else None
    flow_regime = row.flow_regime.value if row.flow_regime is not None else None
    return (
        str(row.model_id),
        row.skill_source.value,
        _null_first(forcing),
        row.time_step_seconds,
        _null_first(row.phase_offset_seconds),
        row.lead_time_hours,
        _null_first(row.season),
        _null_first(flow_regime),
        row.metric,
    )


def forecast_lab_stores_from_dict(stores: dict[str, object]) -> ForecastLabStores:
    """Builds the `ForecastLabStores` bundle `fetch_active_model_assignments`
    needs from the `api/deps.py::get_stores` dict — mirrors
    `api/routes/forecast_lab.py::_forecast_lab_stores` (kept separate: that
    one is module-private)."""
    from sapphire_flow.services.forecast_lab.db_sources import ForecastLabStores

    return ForecastLabStores(
        station_store=stores["station_store"],  # type: ignore[arg-type]
        observation_store=stores["obs_store"],  # type: ignore[arg-type]
        forecast_store=stores["forecast_store"],  # type: ignore[arg-type]
        model_store=stores["model_store"],  # type: ignore[arg-type]
        artifact_store=stores["artifact_store"],  # type: ignore[arg-type]
        provenance_store=stores["provenance_store"],  # type: ignore[arg-type]
        basin_store=stores["basin_store"],  # type: ignore[arg-type]
        group_store=stores["group_store"],  # type: ignore[arg-type]
    )


def fetch_station_skill_rows(
    stores: ForecastLabStores,
    artifact_store: ModelArtifactStore,
    skill_store: SkillStore,
    *,
    station_id: StationId,
    now: UtcDatetime,
    model_id: ModelId | None = None,
) -> list[StationSkillRow]:
    assignments = fetch_active_model_assignments(stores, station_id)
    # De-duplicate while keeping first-seen order — `fetch_active_model_assignments`
    # already picks one assignment per model (station wins ties).
    model_ids = list(dict.fromkeys(a.model_id for a in assignments))
    if model_id is not None:
        model_ids = [mid for mid in model_ids if mid == model_id]

    rows: list[StationSkillRow] = []
    for mid in model_ids:
        artifact_id = artifact_store.fetch_active_artifact_id_for_station(
            station_id, mid
        )
        if artifact_id is None:
            continue
        record = artifact_store.fetch_artifact_record(artifact_id)
        if record is None:
            continue
        scores = skill_store.fetch_latest_scores(station_id, mid, parameter=_DISCHARGE)
        for score in scores:
            if score.model_artifact_id != artifact_id:
                continue
            if score.eval_period_end > now:
                continue
            rows.append(
                StationSkillRow(
                    model_id=mid,
                    model_artifact_id=artifact_id,
                    generation_id=score.generation_id,
                    skill_source=score.skill_source,
                    forcing_type=score.forcing_type,
                    computation_version=score.computation_version,
                    time_step_seconds=score.time_step_seconds,
                    phase_offset_seconds=score.phase_offset_seconds,
                    eval_period_start=score.eval_period_start,
                    eval_period_end=score.eval_period_end,
                    training_period_start=record.training_period_start,
                    training_period_end=record.training_period_end,
                    evaluated_on=evaluated_on(
                        eval_start=score.eval_period_start,
                        eval_end=score.eval_period_end,
                        training_start=record.training_period_start,
                        training_end=record.training_period_end,
                    ),
                    lead_time_hours=score.lead_time_hours,
                    season=score.season,
                    flow_regime=score.flow_regime,
                    metric=score.metric,
                    score=score.score,
                    sample_size=score.sample_size,
                )
            )
    rows.sort(key=_sort_key)
    return rows
