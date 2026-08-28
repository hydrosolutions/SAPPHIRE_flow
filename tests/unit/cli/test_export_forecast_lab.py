"""Plan 198 T6 — CLI export. Locks AC17: atomic write, no partial file
survives a mid-write failure."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from sapphire_flow.cli.export_forecast_lab import (
    export_forecast_lab_snapshot,
    main,
)
from sapphire_flow.services.forecast_lab.db_sources import ForecastLabStores
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ModelCombinationStrategy
from tests.conftest import make_station_config
from tests.fakes.fake_stores import (
    FakeArtifactProvenanceStore,
    FakeBasinStore,
    FakeForecastStore,
    FakeModelArtifactStore,
    FakeModelStore,
    FakeObservationStore,
    FakeStationStore,
)

if TYPE_CHECKING:
    from pathlib import Path

_EPOCH = ensure_utc(datetime(2026, 8, 21, 10, 45, 0, tzinfo=UTC))


def _stores(station_store: FakeStationStore | None = None) -> ForecastLabStores:
    return ForecastLabStores(
        station_store=station_store or FakeStationStore(),
        observation_store=FakeObservationStore(),
        forecast_store=FakeForecastStore(),
        model_store=FakeModelStore(),
        artifact_store=FakeModelArtifactStore(),
        provenance_store=FakeArtifactProvenanceStore(),
        basin_store=FakeBasinStore(),
    )


class TestSuccessfulExport:
    def test_writes_a_valid_snapshot_file(self, tmp_path: Path) -> None:
        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        output_path = tmp_path / "snapshot.json"

        snapshot = export_forecast_lab_snapshot(
            _stores(station_store),
            archive_base_path=None,
            station_codes=[],
            observation_hours=168,
            output_path=output_path,
            clock=lambda: _EPOCH,
        )

        assert output_path.exists()
        on_disk = json.loads(output_path.read_text())
        assert on_disk["snapshot_id"] == snapshot.snapshot_id
        assert on_disk["stations"][0]["station"]["code"] == "2009"
        # No leftover temp file.
        assert list(tmp_path.glob(".*.tmp")) == []


class TestUnknownStationCodeIsATotalFailure:
    def test_unknown_station_code_raises_and_writes_nothing(
        self, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "snapshot.json"

        # Bare `pytest.raises(SystemExit)` would also pass on an unrelated
        # exit (a config failure, say) that wrote nothing — match the message
        # so this locks the unknown-code path specifically.
        with pytest.raises(SystemExit, match="unknown or ineligible"):
            export_forecast_lab_snapshot(
                _stores(),
                archive_base_path=None,
                station_codes=["does-not-exist"],
                observation_hours=168,
                output_path=output_path,
                clock=lambda: _EPOCH,
            )

        assert not output_path.exists()


class TestAtomicWriteSurvivesMidWriteFailure:
    """AC17 — a failure between the temp write and the final rename must
    leave no partial file at `output_path`, and no leftover temp file."""

    def test_os_replace_failure_leaves_no_partial_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.cli import export_forecast_lab as mod

        def _raising_replace(*args: Any, **kwargs: Any) -> None:
            raise OSError("simulated disk failure")

        monkeypatch.setattr(mod.os, "replace", _raising_replace)
        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        output_path = tmp_path / "snapshot.json"

        with pytest.raises(OSError, match="simulated disk failure"):
            export_forecast_lab_snapshot(
                _stores(station_store),
                archive_base_path=None,
                station_codes=[],
                observation_hours=168,
                output_path=output_path,
                clock=lambda: _EPOCH,
            )

        assert not output_path.exists()
        assert list(tmp_path.glob(".*.tmp")) == []

    def test_pre_existing_output_is_untouched_when_the_write_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed re-export must not corrupt/truncate a previously
        successful file at the same path — the old content survives."""
        from sapphire_flow.cli import export_forecast_lab as mod

        output_path = tmp_path / "snapshot.json"
        output_path.write_text('{"previous": "content"}')

        def _raising_replace(*args: Any, **kwargs: Any) -> None:
            raise OSError("simulated disk failure")

        monkeypatch.setattr(mod.os, "replace", _raising_replace)
        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)

        with pytest.raises(OSError):
            export_forecast_lab_snapshot(
                _stores(station_store),
                archive_base_path=None,
                station_codes=[],
                observation_hours=168,
                output_path=output_path,
                clock=lambda: _EPOCH,
            )

        assert json.loads(output_path.read_text()) == {"previous": "content"}


class TestMainExitCode:
    def test_main_returns_zero_on_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.cli import export_forecast_lab as mod

        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        stores = _stores(station_store)

        class _FakeConfig:
            bafu_forecast_archive_path = None
            forecast_combination_strategy = ModelCombinationStrategy.PRIMARY

        class _FakeConn:
            def __enter__(self) -> _FakeConn:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

        class _FakeEngine:
            def begin(self) -> _FakeConn:
                return _FakeConn()

        monkeypatch.setattr(mod, "load_config", lambda: _FakeConfig())
        monkeypatch.setattr(mod, "create_engine_from_env", lambda: _FakeEngine())
        monkeypatch.setattr(mod, "_forecast_lab_stores", lambda conn: stores)

        output_path = tmp_path / "snapshot.json"
        exit_code = main(["--output", str(output_path)])

        assert exit_code == 0
        assert output_path.exists()


class TestCombinationStrategyPropagation:
    """Plan 204 T1 propagation lock (independent Codex pass, round 6) —
    `main()` must forward `config.forecast_combination_strategy` to
    `export_forecast_lab_snapshot()`, not silently rely on its `PRIMARY`
    default. Patched to POOLED (never PRIMARY) for the same reason as the
    route's propagation test."""

    def test_main_forwards_pooled_strategy_and_renders_combined_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import random
        from datetime import timedelta
        from uuid import uuid4

        import polars as pl

        from sapphire_flow.cli import export_forecast_lab as mod
        from sapphire_flow.types.ensemble import ForecastEnsemble
        from sapphire_flow.types.enums import ForecastStatus, NwpCycleSource
        from sapphire_flow.types.forecast import OperationalForecast
        from sapphire_flow.types.ids import POOLED_MODEL_ID, ForecastId, ModelId

        station = make_station_config(code="2009")
        station_store = FakeStationStore()
        station_store.store_station(station)
        forecast_store = FakeForecastStore()

        vt = ensure_utc(_EPOCH + timedelta(days=1))
        rng = random.Random(9)
        rows = [
            {"valid_time": vt, "member_id": m, "value": rng.uniform(10.0, 100.0)}
            for m in range(5)
        ]
        df = pl.DataFrame(rows).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )
        ensemble = ForecastEnsemble.from_members(
            station_id=station.id,
            issued_at=_EPOCH,
            parameter="discharge",
            units="m3/s",
            time_step=timedelta(days=1),
            values=df,
            model_id=POOLED_MODEL_ID,
        )
        forecast_store.store_forecast(
            OperationalForecast(
                id=ForecastId(uuid4()),
                station_id=station.id,
                model_id=POOLED_MODEL_ID,
                model_artifact_id=None,
                issued_at=_EPOCH,
                nwp_cycle_reference_time=_EPOCH,
                nwp_cycle_source=NwpCycleSource.PRIMARY,
                representation=ensemble.representation,
                status=ForecastStatus.RAW,
                version=1,
                warm_up_source=None,
                warm_up_state_age_hours=None,
                observation_staleness_hours=0.3,
                ensemble=ensemble,
                created_at=_EPOCH,
                updated_at=_EPOCH,
                combination_strategy="pooled",
                source_model_ids=[ModelId("nwp_regression")],
            )
        )
        stores = ForecastLabStores(
            station_store=station_store,
            observation_store=FakeObservationStore(),
            forecast_store=forecast_store,
            model_store=FakeModelStore(),
            artifact_store=FakeModelArtifactStore(),
            provenance_store=FakeArtifactProvenanceStore(),
            basin_store=FakeBasinStore(),
        )

        class _FakeConfig:
            bafu_forecast_archive_path = None
            forecast_combination_strategy = ModelCombinationStrategy.POOLED

        class _FakeConn:
            def __enter__(self) -> _FakeConn:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

        class _FakeEngine:
            def begin(self) -> _FakeConn:
                return _FakeConn()

        monkeypatch.setattr(mod, "load_config", lambda: _FakeConfig())
        monkeypatch.setattr(mod, "create_engine_from_env", lambda: _FakeEngine())
        monkeypatch.setattr(mod, "_forecast_lab_stores", lambda conn: stores)

        output_path = tmp_path / "snapshot.json"
        exit_code = main(["--output", str(output_path)])

        assert exit_code == 0
        on_disk = json.loads(output_path.read_text())
        assert on_disk["stations"][0]["combined_forecast"]["available"] is True
        assert on_disk["stations"][0]["combined_forecast"]["model_key"] == "_pooled"
