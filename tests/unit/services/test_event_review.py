from __future__ import annotations

from uuid import uuid4

import pytest

from sapphire_flow.services.event_review import read_review_options
from sapphire_flow.types.event_review import (
    ReviewCatalogLimitError,
    ReviewOptionsQuery,
    ReviewPolicy,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.unit.api.test_event_review import Catalog, source


class TestReadReviewOptions:
    def test_refuses_protected_hindcast_metadata_from_store(self) -> None:
        query = ReviewOptionsQuery(
            station_id=StationId(uuid4()),
            tenant_id=DEFAULT_TENANT_ID,
            policy=ReviewPolicy.PUBLISHED_ONLY,
        )
        with pytest.raises(ValueError, match="Protected hindcast metadata"):
            read_review_options(Catalog((source(),)), query)

    def test_source_limit_is_not_silent_truncation(self) -> None:
        query = ReviewOptionsQuery(
            station_id=StationId(uuid4()),
            tenant_id=DEFAULT_TENANT_ID,
            policy=ReviewPolicy.ORDINARY,
        )
        with pytest.raises(ReviewCatalogLimitError, match="source limit"):
            read_review_options(Catalog((source(),) * 101), query)
