from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from shapely.geometry import box

from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.ids import BasinId, StationId
from tests.conftest import make_raw_historical_forcing, make_station_config
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeHistoricalForcingStore,
    FakeStationStore,
)

_SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "backfill_meteoswiss_history.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location(
        "backfill_meteoswiss_history_script", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["backfill_meteoswiss_history_script"] = module
    spec.loader.exec_module(module)
    return module


def _stub_pg_stores(monkeypatch) -> None:
    monkeypatch.setattr("sapphire_flow.store.basin_store.PgBasinStore", MagicMock())
    monkeypatch.setattr("sapphire_flow.store.station_store.PgStationStore", MagicMock())
    monkeypatch.setattr(
        "sapphire_flow.store.historical_forcing_store.PgHistoricalForcingStore",
        MagicMock(),
    )


class TestBackfillScriptMain:
    def test_main_returns_nonzero_without_database_url(self, mod, monkeypatch) -> None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert mod.main([]) == 1

    def test_main_dry_run_returns_zero_without_touching_db(
        self, mod, monkeypatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
        create_engine = MagicMock()
        monkeypatch.setattr(mod.sa, "create_engine", create_engine)
        assert mod.main(["--dry-run"]) == 0
        create_engine.assert_not_called()

    def test_bind_only_skips_the_chunked_backfill(self, mod, monkeypatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
        monkeypatch.setattr(mod.sa, "create_engine", MagicMock())
        monkeypatch.setattr(mod, "_run_migrations", MagicMock())
        _stub_pg_stores(monkeypatch)

        bind_stub = MagicMock(
            return_value=mod.BindingBackfillResult(
                stations_bound=3, stations_excluded=1
            )
        )
        monkeypatch.setattr(mod, "bind_meteoswiss_reanalysis_fleet", bind_stub)
        run_backfill_stub = MagicMock()
        monkeypatch.setattr(mod, "run_backfill", run_backfill_stub)

        result = mod.main(["--bind-only"])

        assert result == 0
        bind_stub.assert_called_once()
        run_backfill_stub.assert_not_called()

    def test_full_run_binds_before_it_backfills_and_advances_valid_time(
        self, mod, monkeypatch
    ) -> None:
        # MAJOR: exercise the REAL bind + REAL run_backfill against fake stores
        # and an adapter that REQUIRES the MeteoSwiss binding to already exist.
        # Asserts ordering (bind-before-backfill) as an effect, and that the
        # per-source MAX(valid_time) advances — not merely that run_backfill was
        # called.
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
        monkeypatch.setattr(mod.sa, "create_engine", MagicMock())
        monkeypatch.setattr(mod, "_run_migrations", MagicMock())

        basin = Basin(
            id=BasinId(uuid4()),
            code="B-REAL",
            name="Real basin",
            geometry=box(6.0, 46.0, 10.0, 48.0),
            area_km2=100.0,
            attributes=None,
            band_geometries=None,
            created_at=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
            network="bafu",
        )
        station = make_station_config(
            station_id=StationId(uuid4()), code="S-REAL", basin_id=basin.id
        )
        station_store = FakeStationStore()
        station_store.store_station(station)
        basin_store = FakeBasinStore()
        basin_store.store_basin(basin)
        forcing_store = FakeHistoricalForcingStore()

        monkeypatch.setattr(
            "sapphire_flow.store.basin_store.PgBasinStore",
            lambda _conn: basin_store,
        )
        monkeypatch.setattr(
            "sapphire_flow.store.station_store.PgStationStore",
            lambda _conn: station_store,
        )
        monkeypatch.setattr(
            "sapphire_flow.store.historical_forcing_store.PgHistoricalForcingStore",
            lambda _conn: forcing_store,
        )

        # A single tight TABSD window keeps the real chunked driver to one
        # (product, year) chunk. Other products publish nothing (None).
        hwm = ensure_utc(datetime(1981, 1, 3, tzinfo=UTC))

        class _RequiresBindingAdapter:
            def discover_product_boundary(self, product):  # type: ignore[no-untyped-def]
                if product is ForcingSource.METEOSWISS_TABSD:
                    return hwm
                return None

            def fetch_products(  # type: ignore[no-untyped-def]
                self, products, station_configs, start, end, parameters
            ):
                rows = []
                for cfg in station_configs:
                    bindings = station_store.fetch_reanalysis_bindings(cfg.station_id)
                    assert any(
                        b.nwp_source == "meteoswiss_open_data_reanalysis"
                        for b in bindings
                    ), "backfill ran before the binding was written"
                    rows.append(
                        make_raw_historical_forcing(
                            station_id=cfg.station_id,
                            source=products[0].value,
                            parameter=parameters[0],
                            valid_time=start,
                        )
                    )
                return rows

        monkeypatch.setattr(
            "sapphire_flow.flows.ingest_weather_history.build_production_reanalysis_adapter",
            lambda **_kwargs: _RequiresBindingAdapter(),
        )
        monkeypatch.setattr(
            "sapphire_flow.flows.ingest_weather_history._load_reanalysis_stac_config",
            MagicMock(),
        )

        # Pre-condition: no MeteoSwiss forcing yet — MAX(valid_time) is undefined.
        assert forcing_store.fetch_available_sources(station.id) == []

        result = mod.main([])

        assert result == 0
        # The binding was written (real bind), and the real backfill landed rows
        # under the meteoswiss_tabsd source: MAX(valid_time) advanced from
        # nothing to the fetched day.
        assert "meteoswiss_tabsd" in forcing_store.fetch_available_sources(station.id)
        stored = forcing_store.fetch_forcing(
            station.id,
            ForcingSource.METEOSWISS_TABSD.value,
            ensure_utc(datetime(1981, 1, 1, tzinfo=UTC)),
            ensure_utc(datetime(1981, 1, 4, tzinfo=UTC)),
        )
        assert stored, "no forcing rows landed"
        assert max(r.valid_time for r in stored) == ensure_utc(
            datetime(1981, 1, 1, tzinfo=UTC)
        )

    def test_station_batch_size_threaded_through_when_provided(
        self, mod, monkeypatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
        monkeypatch.setattr(mod.sa, "create_engine", MagicMock())
        monkeypatch.setattr(mod, "_run_migrations", MagicMock())
        _stub_pg_stores(monkeypatch)
        monkeypatch.setattr(
            mod,
            "bind_meteoswiss_reanalysis_fleet",
            MagicMock(
                return_value=mod.BindingBackfillResult(
                    stations_bound=1, stations_excluded=0
                )
            ),
        )
        monkeypatch.setattr(
            mod, "eligible_meteoswiss_configs", MagicMock(return_value=[])
        )
        run_backfill_stub = MagicMock(
            return_value=mod.BackfillResult(
                chunks_processed=0, chunks_skipped=0, rows_written=0, stations=0
            )
        )
        monkeypatch.setattr(mod, "run_backfill", run_backfill_stub)
        monkeypatch.setattr(
            "sapphire_flow.flows.ingest_weather_history.build_production_reanalysis_adapter",
            MagicMock(),
        )
        monkeypatch.setattr(
            "sapphire_flow.flows.ingest_weather_history._load_reanalysis_stac_config",
            MagicMock(),
        )

        result = mod.main(["--station-batch-size", "25"])

        assert result == 0
        _, kwargs = run_backfill_stub.call_args
        assert kwargs["station_batch_size"] == 25
