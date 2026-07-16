from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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

    def test_full_run_binds_then_backfills(self, mod, monkeypatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
        monkeypatch.setattr(mod.sa, "create_engine", MagicMock())
        monkeypatch.setattr(mod, "_run_migrations", MagicMock())
        _stub_pg_stores(monkeypatch)

        bind_stub = MagicMock(
            return_value=mod.BindingBackfillResult(
                stations_bound=2, stations_excluded=0
            )
        )
        monkeypatch.setattr(mod, "bind_meteoswiss_reanalysis_fleet", bind_stub)
        eligible_stub = MagicMock(return_value=[])
        monkeypatch.setattr(mod, "eligible_meteoswiss_configs", eligible_stub)
        run_backfill_stub = MagicMock(
            return_value=mod.BackfillResult(
                chunks_processed=5, chunks_skipped=2, rows_written=100, stations=2
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

        result = mod.main([])

        assert result == 0
        bind_stub.assert_called_once()
        run_backfill_stub.assert_called_once()

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
