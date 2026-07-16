from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.ids import StationId
from tests.conftest import make_raw_historical_forcing, make_station_config
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeHistoricalForcingStore,
    FakeStationStore,
)

_SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "validate_forcing_reference.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location(
        "validate_forcing_reference_script", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_forcing_reference_script"] = module
    spec.loader.exec_module(module)
    return module


def _stub_pg_stores(monkeypatch, station_store, basin_store, forcing_store) -> None:
    monkeypatch.setattr(
        "sapphire_flow.store.basin_store.PgBasinStore", lambda _conn: basin_store
    )
    monkeypatch.setattr(
        "sapphire_flow.store.station_store.PgStationStore",
        lambda _conn: station_store,
    )
    monkeypatch.setattr(
        "sapphire_flow.store.historical_forcing_store.PgHistoricalForcingStore",
        lambda _conn: forcing_store,
    )


class TestValidateScriptMain:
    def test_main_returns_nonzero_without_database_url(self, mod, monkeypatch) -> None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert mod.main([]) == 1

    def test_skip_live_tail_runs_reference_comparison_only(
        self, mod, monkeypatch, capsys
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
        monkeypatch.setattr(mod.sa, "create_engine", MagicMock())

        station = make_station_config(code="BASIN-1")
        station_store = FakeStationStore()
        station_store.store_station(station)
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()
        forcing_store.store_forcing(
            [
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.METEOSWISS_RHIRESD.value,
                    parameter="precipitation",
                    valid_time=datetime(2000, 6, 1, tzinfo=UTC),
                    value=10.0,
                ),
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.CAMELS_CH.value,
                    parameter="precipitation",
                    valid_time=datetime(2000, 6, 1, tzinfo=UTC),
                    value=10.0,
                ),
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.METEOSWISS_TABSD.value,
                    parameter="temperature",
                    valid_time=datetime(2000, 6, 1, tzinfo=UTC),
                    value=5.0,
                ),
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.CAMELS_CH.value,
                    parameter="temperature",
                    valid_time=datetime(2000, 6, 1, tzinfo=UTC),
                    value=5.0,
                ),
            ]
        )
        _stub_pg_stores(monkeypatch, station_store, basin_store, forcing_store)

        result = mod.main(["--skip-live-tail"])

        assert result == 0
        out = capsys.readouterr().out
        assert "BASIN-1" in out
        assert "All basins PASS." in out

    def test_full_run_includes_live_tail_residual(
        self, mod, monkeypatch, capsys
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
        monkeypatch.setattr(mod.sa, "create_engine", MagicMock())

        station = make_station_config(station_id=StationId(uuid4()), code="BASIN-2")
        station_store = FakeStationStore()
        station_store.store_station(station)
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()
        _stub_pg_stores(monkeypatch, station_store, basin_store, forcing_store)

        window_start = date(2026, 5, 15)
        window_end = date(2026, 5, 16)

        class _FakeAdapter:
            def discover_product_availability_range(self, product):  # type: ignore[no-untyped-def]
                if product is ForcingSource.METEOSWISS_RHIRESD:
                    return (date(1981, 1, 1), window_end)
                if product is ForcingSource.METEOSWISS_RPRELIMD:
                    return (window_start, date(2026, 7, 1))
                return None

            def fetch_products(  # type: ignore[no-untyped-def]
                self, products, station_configs, start, end, parameters
            ):
                (product,) = products
                return [
                    make_raw_historical_forcing(
                        station_id=station.id,
                        source=product.value,
                        parameter="precipitation",
                        valid_time=start,
                        value=5.0,
                    )
                ]

        monkeypatch.setattr(
            "sapphire_flow.flows.ingest_weather_history.build_production_reanalysis_adapter",
            lambda **_kwargs: _FakeAdapter(),
        )
        monkeypatch.setattr(
            "sapphire_flow.flows.ingest_weather_history._load_reanalysis_stac_config",
            MagicMock(),
        )

        result = mod.main([])

        assert result == 0
        out = capsys.readouterr().out
        assert "Live-tail residual" in out
        assert "Paired samples:     1" in out
