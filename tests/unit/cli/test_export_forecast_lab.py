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

        with pytest.raises(SystemExit):
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
