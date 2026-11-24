from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa

from sapphire_flow.config.deployment import load_config
from sapphire_flow.services.bafu_threshold_import import import_one
from sapphire_flow.services.threshold_import import ThresholdImportMode
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId
from tests.conftest import make_station_config

NOW = ensure_utc(datetime(2026, 11, 24, tzinfo=UTC))


class TestImportOneTransaction:
    def test_audit_failure_rolls_back_thresholds(
        self, db_engine: sa.Engine, tmp_path: Path
    ) -> None:
        station = make_station_config(station_id=StationId(uuid4()), code="2150")
        with db_engine.begin() as conn:
            PgStationStore(conn).store_station(station)
        html = Path("tests/fixtures/bafu_thresholds/2150.html").read_text()
        config = load_config("config.toml")
        config.enable_observation_alerts = True
        try:
            with httpx.Client(
                transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html))
            ) as client:
                kwargs = dict(
                    station=station,
                    tenant_id=station.tenant_id,
                    config=config,
                    engine=db_engine,
                    client=client,
                    mode=ThresholdImportMode.APPLY,
                    report_dir=tmp_path,
                    clock=lambda: NOW,
                )
                with (
                    patch(
                        "sapphire_flow.services.bafu_threshold_import.PgAuditLogStore.append_entry",
                        side_effect=RuntimeError("audit failed"),
                    ),
                    pytest.raises(RuntimeError, match="audit failed"),
                ):
                    import_one(**kwargs)
                with db_engine.connect() as conn:
                    assert PgStationStore(conn).fetch_thresholds(station.id) == []
                result = import_one(**kwargs)
                assert result["status"] == "imported"
                with db_engine.connect() as conn:
                    assert len(PgStationStore(conn).fetch_thresholds(station.id)) == 4
                assert import_one(**kwargs)["status"] == "unchanged"
        finally:
            with db_engine.begin() as conn:
                conn.execute(
                    sa.text("DELETE FROM station_thresholds WHERE station_id=:id"),
                    {"id": station.id},
                )
                conn.execute(
                    sa.text("DELETE FROM stations WHERE id=:id"), {"id": station.id}
                )
