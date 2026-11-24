from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from sapphire_flow.adapters.bafu_thresholds import parse_threshold_page
from sapphire_flow.config.deployment import load_config
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.threshold_import import (
    ThresholdImportMode,
    bafu_thresholds,
    import_station_thresholds,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import StationThreshold
from sapphire_flow.types.enums import ThresholdSource
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.station import StationConfig
from sapphire_flow.types.threshold_import import ThresholdImportStatus
from tests.conftest import make_station_config
from tests.fakes.fake_stores import FakeStationStore

TestData = tuple[StationConfig, list[StationThreshold], FakeStationStore]


class TestImportStationThresholds:
    @pytest.fixture
    def setup(self) -> TestData:
        station = make_station_config(code="2150", network="bafu")
        snapshot = parse_threshold_page(
            Path("tests/fixtures/bafu_thresholds/2150.html").read_text(),
            station_code="2150",
            retrieved_at=ensure_utc(datetime(2026, 11, 24, tzinfo=UTC)),
        )
        thresholds = bafu_thresholds(
            station, snapshot, load_config("config.toml").get_danger_level_definitions()
        )
        store = FakeStationStore()
        store.store_station(station)
        return station, thresholds, store

    def test_preview_does_not_write_and_apply_is_idempotent(
        self, setup: TestData
    ) -> None:
        station, thresholds, store = setup
        kwargs = dict(
            station=station,
            tenant_id=station.tenant_id,
            thresholds=thresholds,
            store=store,
        )
        assert (
            import_station_thresholds(**kwargs, mode=ThresholdImportMode.PREVIEW)
            is ThresholdImportStatus.PREVIEW
        )
        assert store.fetch_thresholds(station.id) == []
        assert (
            import_station_thresholds(**kwargs, mode=ThresholdImportMode.APPLY)
            is ThresholdImportStatus.IMPORTED
        )
        assert (
            import_station_thresholds(**kwargs, mode=ThresholdImportMode.APPLY)
            is ThresholdImportStatus.UNCHANGED
        )
        assert len(store.fetch_thresholds(station.id)) == 4

    @pytest.mark.parametrize(
        "change", [{"value": 999.0}, {"source": ThresholdSource.INFERRED}]
    )
    def test_conflict_preserves_all_rows(
        self, setup: TestData, change: dict[str, object]
    ) -> None:
        station, thresholds, store = setup
        old = replace(thresholds[0], **change)
        store.store_thresholds([old])
        assert (
            import_station_thresholds(
                station=station,
                tenant_id=station.tenant_id,
                thresholds=thresholds,
                store=store,
                mode=ThresholdImportMode.APPLY,
            )
            is ThresholdImportStatus.CONFLICT
        )
        assert store.fetch_thresholds(station.id) == [old]

    def test_cross_tenant_rejected(self, setup: TestData) -> None:
        station, thresholds, store = setup
        with pytest.raises(ConfigurationError, match="outside"):
            import_station_thresholds(
                station=station,
                tenant_id=TenantId(uuid4()),
                thresholds=thresholds,
                store=store,
                mode=ThresholdImportMode.APPLY,
            )
        assert store.fetch_thresholds(station.id) == []
