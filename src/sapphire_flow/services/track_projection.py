"""Plan 151 T3: per-assignment forcing-track projection + dedup reducer.

Pure `services/` code — no I/O. Realizes design D2 (projection) and D5
(dedup reducer) over the locked types in `types/forcing_track.py`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.adapters.forecast_interface import ForecastInterfaceAdapter
from sapphire_flow.types.enums import EnsembleMode
from sapphire_flow.types.forcing_track import (
    AssignmentKey,
    FeatureName,
    ForcingRequired,
    ForcingTrackKey,
    FutureSteps,
    NoForcingRequired,
    ResolvedTrackRequest,
    TrackProjection,
)

if TYPE_CHECKING:
    from sapphire_flow.protocols.forecast_model import StationForecastModel
    from sapphire_flow.types.station import ModelAssignment, StationWeatherSource


def project_forcing_requirement(
    assignment: ModelAssignment,
    model: StationForecastModel,
    station_weather_source: StationWeatherSource,
) -> TrackProjection:
    """Pure per-assignment projection (D2).

    Input is the explicit join `(assignment, model, station_weather_source)`
    — a `ModelAssignment` carries no NWP source or spatial binding of its
    own (those live on `StationWeatherSource`).

    The SELECTED branch is the one keyed by `assignment.time_step`: an FI
    model may declare one future-forced branch and one past-only branch
    (D32), so `NoForcingRequired` is decided from what THIS assignment's own
    `time_step` selects — never from the model's globally max-collapsed
    `data_requirements` — even when a SIBLING branch of the same model has
    future forcing.
    """
    assignment_key = AssignmentKey((assignment.station_id, assignment.model_id))

    if isinstance(model, ForecastInterfaceAdapter):
        return _project_fi_model(
            model, assignment, station_weather_source, assignment_key
        )
    return _project_native_model(
        model, assignment, station_weather_source, assignment_key
    )


def _project_fi_model(
    model: ForecastInterfaceAdapter,
    assignment: ModelAssignment,
    station_weather_source: StationWeatherSource,
    assignment_key: AssignmentKey,
) -> TrackProjection:
    horizons = model.future_feature_horizons(assignment.time_step)
    if not horizons:
        return NoForcingRequired(assignment=assignment_key)

    modes = model.future_feature_modes(assignment.time_step)
    # T2's construction-time sweep (D4) already enforces exactly ONE
    # ensemble_mode per branch, so `modes.values()` is uniform by
    # invariant here — not a re-validation. `next(iter(...))` picks it.
    ensemble_mode = next(iter(modes.values()), EnsembleMode.SINGLE)

    key = ForcingTrackKey(
        nwp_source=station_weather_source.nwp_source,
        ensemble_mode=ensemble_mode,
        time_step=assignment.time_step,
        spatial_representation=station_weather_source.extraction_type,
        features=frozenset(horizons),
    )
    return ForcingRequired(
        key=key, assignment_horizons=horizons, assignment=assignment_key
    )


def _project_native_model(
    model: StationForecastModel,
    assignment: ModelAssignment,
    station_weather_source: StationWeatherSource,
    assignment_key: AssignmentKey,
) -> TrackProjection:
    # Non-FI (native SAP3) model: no per-time-step branches exist to select
    # from, and no per-feature horizons are declared — broadcast the single
    # scalar `forecast_horizon_steps` across every declared future feature.
    requirements = model.data_requirements
    if not requirements.future_dynamic_features:
        return NoForcingRequired(assignment=assignment_key)

    horizons = {
        FeatureName(name): FutureSteps(value=requirements.forecast_horizon_steps)
        for name in requirements.future_dynamic_features
    }
    key = ForcingTrackKey(
        nwp_source=station_weather_source.nwp_source,
        ensemble_mode=requirements.ensemble_mode,
        time_step=assignment.time_step,
        spatial_representation=station_weather_source.extraction_type,
        features=frozenset(horizons),
    )
    return ForcingRequired(
        key=key, assignment_horizons=horizons, assignment=assignment_key
    )


def resolve_tracks(required: list[ForcingRequired]) -> list[ResolvedTrackRequest]:
    """Pure, deterministic dedup reducer (D5).

    Groups `required` by `key`; emits ONE `ResolvedTrackRequest` per distinct
    key whose `fetch_horizons[f]` is the per-feature MAX `FutureSteps` across
    the group, retaining each assignment's own horizons. Output order is
    deterministic (sorted by key).
    """
    groups: dict[ForcingTrackKey, list[ForcingRequired]] = {}
    for item in required:
        horizon_features = frozenset(item.assignment_horizons)
        if horizon_features != item.key.features:
            raise ValueError(
                "ForcingRequired.assignment_horizons features "
                f"{horizon_features} do not match key.features "
                f"{item.key.features} for assignment {item.assignment!r}"
            )
        groups.setdefault(item.key, []).append(item)

    resolved = [
        ResolvedTrackRequest(
            key=key,
            fetch_horizons={
                feature: FutureSteps(
                    value=max(item.assignment_horizons[feature].value for item in group)
                )
                for feature in key.features
            },
            assignments=tuple(group),
        )
        for key, group in groups.items()
    ]
    return sorted(resolved, key=_track_sort_key)


def _track_sort_key(request: ResolvedTrackRequest) -> tuple[object, ...]:
    key = request.key
    return (
        key.nwp_source,
        key.ensemble_mode.value,
        key.time_step,
        key.spatial_representation.value,
        tuple(sorted(key.features)),
    )
