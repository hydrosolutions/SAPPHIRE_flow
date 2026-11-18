from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.types.event_review import (
    MAX_REVIEW_SOURCES,
    ReviewAvailability,
    ReviewCatalogLimitError,
    ReviewKind,
    ReviewOptions,
    ReviewPolicy,
)

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import EventReviewStore
    from sapphire_flow.types.event_review import ReviewOptionsQuery


def read_review_options(
    store: EventReviewStore, query: ReviewOptionsQuery
) -> ReviewOptions:
    sources = store.fetch_options(query)
    if len(sources) > MAX_REVIEW_SOURCES:
        raise ReviewCatalogLimitError("Review catalog exceeds its source limit")
    if query.policy is ReviewPolicy.PUBLISHED_ONLY and any(
        source.kind is ReviewKind.HINDCAST for source in sources
    ):
        raise ValueError("Protected hindcast metadata returned by review store")
    kinds = {source.kind for source in sources}
    return ReviewOptions(
        station_id=query.station_id,
        sources=sources,
        operational_availability=ReviewAvailability.AVAILABLE
        if ReviewKind.OPERATIONAL in kinds
        else ReviewAvailability.ABSENT,
        hindcast_availability=(
            ReviewAvailability.UNSUPPORTED
            if query.policy is ReviewPolicy.PUBLISHED_ONLY
            else ReviewAvailability.AVAILABLE
            if ReviewKind.HINDCAST in kinds
            else ReviewAvailability.ABSENT
        ),
    )
