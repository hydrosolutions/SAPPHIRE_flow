from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from sapphire_flow.api.network_overview_cache import etag_matches, overview_etag
from sapphire_flow.api.network_overview_schemas import NetworkOverviewResponse
from sapphire_flow.api.security import Principal
from sapphire_flow.types.enums import AccessTokenRole, ForecastDataUse
from sapphire_flow.types.ids import AccessTokenId, TenantId


class TestOverviewValidators:
    @pytest.mark.parametrize(
        "header,expected",
        [
            (None, False),
            ("*", True),
            ('W/"abc"', True),
            ('"abc"', True),
            ('"other", W/"abc"', True),
            ('W/"other"', False),
            ("abc", False),
            ('*, "abc"', False),
        ],
    )
    def test_weak_matching(self, header: str | None, expected: bool) -> None:
        assert etag_matches(header, 'W/"abc"') is expected

    def test_assembly_time_is_not_semantic_but_token_is(self) -> None:
        principal = Principal(
            token_id=AccessTokenId(UUID(int=1)),
            role=AccessTokenRole.REVIEWER,
            tenant_id=TenantId(UUID(int=2)),
            station_ids=frozenset(),
        )
        body = NetworkOverviewResponse(
            generated_at=datetime(2026, 11, 10, 0, 1, tzinfo=UTC),
            hourly_end=datetime(2026, 11, 10, tzinfo=UTC),
            network="dhm",
            station_count=0,
            items=(),
        )
        tag = overview_etag(body, principal, (ForecastDataUse.STANDARD,))
        later = body.model_copy(
            update={"generated_at": datetime(2026, 11, 10, 0, 2, tzinfo=UTC)}
        )
        assert overview_etag(later, principal, (ForecastDataUse.STANDARD,)) == tag
        assert (
            overview_etag(
                body,
                replace(principal, token_id=AccessTokenId(UUID(int=3))),
                (ForecastDataUse.STANDARD,),
            )
            != tag
        )
