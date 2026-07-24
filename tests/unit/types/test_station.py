import uuid
from datetime import UTC, datetime

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import GaugingStatus
from sapphire_flow.types.ids import StationGroupId, TenantId
from sapphire_flow.types.station import StationGroup
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.conftest import make_station_config


class TestStationConfigGaugingStatus:
    def test_defaults_to_gauged(self) -> None:
        station = make_station_config()
        assert station.gauging_status is GaugingStatus.GAUGED

    def test_accepts_ungauged(self) -> None:
        station = make_station_config(gauging_status=GaugingStatus.UNGAUGED)
        assert station.gauging_status is GaugingStatus.UNGAUGED

    def test_accepts_calculated(self) -> None:
        station = make_station_config(gauging_status=GaugingStatus.CALCULATED)
        assert station.gauging_status is GaugingStatus.CALCULATED


class TestStationConfigTenant:
    """Plan 147 Slice A: canonical tenant ownership (R4 LOCKED)."""

    def test_defaults_to_the_seeded_sapphire_tenant(self) -> None:
        station = make_station_config()
        assert station.tenant_id == DEFAULT_TENANT_ID

    def test_accepts_an_explicit_tenant(self) -> None:
        other = TenantId(uuid.uuid4())
        station = make_station_config(tenant_id=other)
        assert station.tenant_id == other


class TestStationGroupTenant:
    def test_defaults_to_the_seeded_sapphire_tenant(self) -> None:
        group = StationGroup(
            id=StationGroupId(uuid.uuid4()),
            name="a-group",
            station_ids=frozenset(),
            created_at=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
        )
        assert group.tenant_id == DEFAULT_TENANT_ID
