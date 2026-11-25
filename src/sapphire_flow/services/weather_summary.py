from __future__ import annotations

from typing import Literal

from sapphire_flow.types.operational_weather import (
    DerivedWeatherUse,
    FutureWeatherUse,
    PastWeatherUse,
    WeatherUse,
)
from sapphire_flow.types.operational_weather_v2 import (
    DerivedWeatherUseV2,
    FutureWeatherUseV2,
    PastWeatherUseV2,
    WeatherUseV2,
)
from sapphire_flow.types.weather_summary import (
    WeatherLegSummary,
    WeatherLegUseSummary,
    WeatherSourceRunSummary,
    WeatherUseSummary,
)


def _leg(
    use: WeatherUse | WeatherUseV2 | None, leg: Literal["past", "future"]
) -> WeatherLegSummary:
    if use is None:
        return WeatherLegSummary(freshness="unknown", uses=())
    fact = use.past if leg == "past" else use.future
    if isinstance(fact, (PastWeatherUse, PastWeatherUseV2)):
        source_runs = sorted(
            {
                (row.source.value, row.source_run)
                for day in (
                    fact.periods if isinstance(fact, PastWeatherUseV2) else fact.days
                )
                for row in day.samples
            }
            | {
                (row.nwp_source, row.cycle_time)
                for fill in fact.gap_fills
                for row in fill.donor_records
            }
        )
        reason = fact.refresh_outcome.reason
        snapshot_id, digest, retrieved = (
            str(fact.snapshot_id),
            fact.snapshot_sha256,
            fact.retrieved_at,
        )
    elif isinstance(fact, (FutureWeatherUse, FutureWeatherUseV2)):
        source_runs = [(fact.receipt.scope.source, fact.receipt.cycle_time)]
        reason = fact.refresh_attempt.outcome.reason
        snapshot_id, digest, retrieved = (
            None,
            fact.receipt.row_sha256,
            fact.receipt.validated_at,
        )
    else:
        return WeatherLegSummary(freshness="unused", uses=())
    return WeatherLegSummary(
        freshness=fact.freshness.value,
        uses=(
            WeatherLegUseSummary(
                freshness=fact.freshness.value,
                failure_code=reason.value if reason is not None else None,
                failure_at=use.refresh_finished_at if reason is not None else None,
                snapshot_id=snapshot_id,
                snapshot_sha256=digest,
                retrieved_at=retrieved,
                age_at_use_seconds=(use.used_at - retrieved).total_seconds(),
                sources=tuple(
                    WeatherSourceRunSummary(source=source, source_run=run)
                    for source, run in source_runs
                ),
            ),
        ),
    )


def summarize_weather_use(
    use: WeatherUse | DerivedWeatherUse | WeatherUseV2 | DerivedWeatherUseV2,
) -> WeatherUseSummary:
    facts = (
        [item.weather_use for item in use.contributors]
        if isinstance(use, (DerivedWeatherUse, DerivedWeatherUseV2))
        else [use]
    )

    def aggregate(leg: Literal["past", "future"]) -> WeatherLegSummary:
        summaries = [_leg(fact, leg) for fact in facts]
        states = {summary.freshness for summary in summaries}
        freshness: Literal["current", "stale", "unused", "unknown"] = "unused"
        for state in ("current", "unknown", "stale"):
            if state in states:
                freshness = state
        return WeatherLegSummary(
            freshness=freshness,
            uses=tuple(item for summary in summaries for item in summary.uses),
        )

    return WeatherUseSummary(
        station_id=use.station_id,
        issue_time=use.issue_time,
        past=aggregate("past"),
        future=aggregate("future"),
    )
