from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.hindcast_inputs import (
    aggregate_historical_periods,
    source_fetch_bounds,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.skill import SkillEvaluationContext
from sapphire_flow.types.time_grid import PeriodLabel, TimeGrid

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forecast import HindcastForecast
    from sapphire_flow.types.hindcast_inputs import HistoricalSourcePeriod
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.observation import Observation

_ZERO = timedelta(0)


def evaluation_context(
    hindcasts: Sequence[HindcastForecast],
    reference_displacement: timedelta = _ZERO,
    declared_sources: tuple[HistoricalSourcePeriod, ...] = (),
) -> SkillEvaluationContext | None:
    if not hindcasts:
        raise ValueError("evaluation context requires hindcasts")
    periods = {item.ensemble.period_definition for item in hindcasts}
    policies = {item.input_availability_policy for item in hindcasts}
    if len(periods) != 1 or len(policies) != 1:
        raise ConfigurationError("mixed hindcast period or availability contracts")
    period = next(iter(periods))
    policy = next(iter(policies))
    if period is None:
        if policy is not None or reference_displacement:
            raise ConfigurationError(
                "legacy hindcasts have no declared reference period"
            )
        return None
    if policy is None:
        raise ConfigurationError("declared hindcasts require input availability policy")
    catalog: dict[tuple[StationId, str, str], HistoricalSourcePeriod] = {}
    for source in (
        *declared_sources,
        *(s for h in hindcasts for s in h.source_periods),
    ):
        if source.station_id != hindcasts[0].station_id:
            continue
        if source.key in catalog and catalog[source.key] != source:
            raise ConfigurationError("conflicting evaluation source declarations")
        catalog[source.key] = source
    return SkillEvaluationContext(
        forecast_period=period,
        reference_period=replace(
            period,
            grid=TimeGrid(
                step=period.grid.step,
                phase=(period.grid.phase + reference_displacement) % period.grid.step,
            ),
        ),
        reference_displacement=reference_displacement,
        input_availability_policy=policy,
        source_periods=tuple(
            sorted(
                catalog.values(),
                key=lambda item: (str(item.station_id), item.source, item.parameter),
            )
        ),
    )


def verification_sources(
    hindcasts: Sequence[HindcastForecast],
    declared_sources: tuple[HistoricalSourcePeriod, ...] = (),
) -> tuple[HistoricalSourcePeriod, ...]:
    if not hindcasts:
        raise ValueError("verification source selection requires hindcasts")
    first = hindcasts[0]
    if any(
        (item.station_id, item.ensemble.parameter)
        != (first.station_id, first.ensemble.parameter)
        for item in hindcasts
    ):
        raise ConfigurationError("verification requires one station and parameter")
    sources: dict[tuple[StationId, str, str], HistoricalSourcePeriod] = {}
    for item in (*declared_sources, *(s for h in hindcasts for s in h.source_periods)):
        if (
            item.station_id != first.station_id
            or item.parameter != first.ensemble.parameter
        ):
            continue
        if item.key in sources and sources[item.key] != item:
            raise ConfigurationError("conflicting verification source declarations")
        sources[item.key] = item
    if not sources:
        raise ConfigurationError("declared skill requires observation source periods")
    return tuple(sorted(sources.values(), key=lambda item: item.source))


def declared_observation_fetch_bounds(
    hindcasts: Sequence[HindcastForecast],
    context: SkillEvaluationContext,
    sources: tuple[HistoricalSourcePeriod, ...],
) -> tuple[UtcDatetime, UtcDatetime]:
    period = context.forecast_period
    labels = [
        value
        for item in hindcasts
        for value in item.ensemble.values["valid_time"].to_list()
    ]
    if not labels:
        raise ValueError("hindcasts carry no valid times")
    label_offset = period.grid.step if period.label is PeriodLabel.END else _ZERO
    start = ensure_utc(min(labels) - label_offset + context.reference_displacement)
    end = ensure_utc(
        max(labels) - label_offset + context.reference_displacement + period.grid.step
    )
    return source_fetch_bounds(
        start,
        end,
        tuple(replace(item, input_support_displacement=_ZERO) for item in sources),
    )


def declared_observation_lookup(
    observations: Sequence[Observation],
    *,
    sources: tuple[HistoricalSourcePeriod, ...],
    context: SkillEvaluationContext,
    now: UtcDatetime,
) -> dict[UtcDatetime, float]:
    reference = context.reference_period
    catalog = {item.key: item for item in sources}
    if len(catalog) != len(sources):
        raise ConfigurationError("duplicate verification source declarations")
    if len({(item.station_id, item.parameter) for item in sources}) != 1:
        raise ConfigurationError("verification requires one station and parameter")
    rows: dict[tuple[StationId, str, str], list[Observation]] = {}
    for item in observations:
        if item.qc_status is not QcStatus.QC_PASSED:
            continue
        key = (item.station_id, item.source.value, item.parameter)
        if key not in catalog:
            raise ConfigurationError(
                "verification observation has no source declaration"
            )
        rows.setdefault(key, []).append(item)
    result: dict[UtcDatetime, float] = {}
    forecast_offset = (
        context.forecast_period.grid.step
        if context.forecast_period.label is PeriodLabel.END
        else _ZERO
    )
    reference_offset = (
        reference.grid.step if reference.label is PeriodLabel.END else _ZERO
    )
    for key, values in rows.items():
        reduced = aggregate_historical_periods(
            pl.DataFrame(
                {
                    "timestamp": [item.timestamp for item in values],
                    "value": [item.value for item in values],
                }
            ),
            source=catalog[key],
            target=reference,
            availability_policy=context.input_availability_policy,
        )
        for label, value in reduced.select("timestamp", "value").iter_rows():
            physical_start = label - reference_offset
            if value is None or physical_start + reference.grid.step > now:
                continue
            forecast_label = ensure_utc(
                physical_start - context.reference_displacement + forecast_offset
            )
            if forecast_label in result:
                raise ConfigurationError("overlapping verification source periods")
            result[forecast_label] = float(value)
    return result
