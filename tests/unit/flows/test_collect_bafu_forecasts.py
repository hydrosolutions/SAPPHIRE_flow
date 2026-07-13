from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003

import polars as pl
import pytest

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.exceptions import AdapterError
from sapphire_flow.flows.collect_bafu_forecasts import (
    _EMPTY_RESULT,
    collect_bafu_forecasts_flow,
)
from sapphire_flow.types.bafu_forecast import (
    BafuForecastRow,
    BafuForecastStation,
    BafuStationInventory,
    BafuVariantFetch,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import PipelineCheckType, PipelineHealthStatus
from tests.fakes.fake_stores import FakePipelineHealthStore

_PRODUCED_AT = ensure_utc(datetime(2026, 7, 10, 9, 43, 8, tzinfo=UTC))
_ISSUED_AT = ensure_utc(datetime(2026, 7, 10, 5, 0, tzinfo=UTC))


def _make_config(**overrides: object) -> DeploymentConfig:
    defaults: dict[str, object] = {"max_retention_days": 3650}
    defaults.update(overrides)
    return DeploymentConfig(**defaults)  # type: ignore[arg-type]


def _river_station(key: str = "2135") -> BafuForecastStation:
    return BafuForecastStation(
        key=key,
        label=f"River {key}",
        icon="river",
        metric="discharge_ms",
        unit="m³/s",
        plot_path=f"/web/hydro/hydro_sensor_pq_forecast/{key}/plots",
    )


def _lake_station(key: str = "3001") -> BafuForecastStation:
    return BafuForecastStation(
        key=key,
        label=f"Lake {key}",
        icon="lake",
        metric="masl",
        unit="m ü.M.",
        plot_path=f"/web/hydro/hydro_sensor_pq_forecast/{key}/plots",
    )


def _fetch(
    station_key: str,
    variant: str,
    *,
    issued_at: object = _ISSUED_AT,
    metric: str = "discharge_ms",
    n_rows: int = 3,
) -> BafuVariantFetch:
    rows = [
        BafuForecastRow(
            station_key=station_key,
            metric=metric,  # type: ignore[arg-type]
            unit="m³/s",
            issued_at=issued_at,  # type: ignore[arg-type]
            produced_at=_PRODUCED_AT,
            valid_time=ensure_utc(datetime(2026, 7, 10, 6 + i, 0, tzinfo=UTC)),
            trace_name="Median",
            point_index=i,
            value=100.0 + i,
        )
        for i in range(n_rows)
    ]
    return BafuVariantFetch(
        station_key=station_key,
        variant=variant,  # type: ignore[arg-type]
        metric=metric,
        issued_at=issued_at,  # type: ignore[arg-type]
        rows=rows,
        raw_payload={"station_key": station_key, "variant": variant},
    )


class _FakeAdapter:
    def __init__(
        self,
        inventory: BafuStationInventory,
        variant_results: dict[tuple[str, str], BafuVariantFetch | None | Exception],
    ) -> None:
        self._inventory = inventory
        self._variant_results = variant_results
        self.calls: list[tuple[str, str]] = []
        self.inventory_calls = 0

    def fetch_station_inventory(self) -> BafuStationInventory:
        self.inventory_calls += 1
        return self._inventory

    def fetch_variant_forecast(
        self, station_key: str, variant: str, produced_at: object
    ) -> BafuVariantFetch | None:
        self.calls.append((station_key, variant))
        result = self._variant_results.get((station_key, variant))
        if isinstance(result, Exception):
            raise result
        return result


class _RaisingAdapter:
    def fetch_station_inventory(self) -> BafuStationInventory:
        raise AdapterError("GeoJSON unreachable")

    def fetch_variant_forecast(
        self, station_key: str, variant: str, produced_at: object
    ) -> BafuVariantFetch | None:
        raise AssertionError("must not be called after inventory failure")


class _NeverCalledAdapter:
    def fetch_station_inventory(self) -> BafuStationInventory:
        raise AssertionError("adapter must not be used when the archive path is unset")

    def fetch_variant_forecast(
        self, station_key: str, variant: str, produced_at: object
    ) -> BafuVariantFetch | None:
        raise AssertionError("adapter must not be used when the archive path is unset")


class _SleepSpy:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class _ClockSpy:
    def __init__(self, value: object) -> None:
        self._value = value
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        return self._value


class TestQuarantineGate:
    def test_disabled_returns_empty_result_when_archive_path_unset(self) -> None:
        config = _make_config(bafu_forecast_archive_path=None)
        result = collect_bafu_forecasts_flow(
            config=config,
            adapter=_NeverCalledAdapter(),
            clock=_ClockSpy(_PRODUCED_AT),
            sleeper=_SleepSpy(),
        )
        assert result.stations_seen == 0
        assert result.variants_fetched == 0
        assert result.rows_archived == 0


class TestCollection:
    def test_archives_new_forecasts_raw_and_parsed(self, tmp_path: Path) -> None:
        river = _river_station("2135")
        lake = _lake_station("3001")
        inventory = BafuStationInventory(
            stations=[river, lake], produced_at=_PRODUCED_AT
        )
        variant_results: dict[tuple[str, str], BafuVariantFetch | None | Exception] = {
            ("2135", "q_forecast"): _fetch("2135", "q_forecast", n_rows=3),
            ("3001", "q_forecast"): _fetch("3001", "q_forecast", n_rows=2),
            ("3001", "p_forecast"): _fetch(
                "3001", "p_forecast", metric="masl", n_rows=4
            ),
        }
        adapter = _FakeAdapter(inventory, variant_results)
        config = _make_config(bafu_forecast_archive_path=tmp_path)

        result = collect_bafu_forecasts_flow(
            config=config,
            adapter=adapter,
            clock=_ClockSpy(_PRODUCED_AT),
            sleeper=_SleepSpy(),
        )

        assert result.stations_seen == 2
        assert result.variants_fetched == 3
        assert result.variants_absent == 0
        assert result.variants_failed == 0
        assert result.rows_archived == 3 + 2 + 4

        # River station never attempts p_forecast (politeness).
        assert ("2135", "p_forecast") not in adapter.calls
        assert ("3001", "p_forecast") in adapter.calls

        stamp = _ISSUED_AT.strftime("%Y%m%dT%H%M%SZ")
        raw_q_2135 = tmp_path / "raw" / f"2135_q_forecast_{stamp}.json"
        assert raw_q_2135.exists()
        assert json.loads(raw_q_2135.read_text()) == {
            "station_key": "2135",
            "variant": "q_forecast",
        }

        parsed_q_2135 = tmp_path / "parsed" / f"2135_q_forecast_{stamp}.parquet"
        assert parsed_q_2135.exists()
        df = pl.read_parquet(parsed_q_2135)
        assert len(df) == 3
        assert set(df.columns) == {
            "station_key",
            "metric",
            "unit",
            "issued_at",
            "produced_at",
            "valid_time",
            "trace_name",
            "point_index",
            "value",
        }
        assert df["station_key"].to_list() == ["2135"] * 3
        assert df["point_index"].to_list() == [0, 1, 2]

    def test_dedup_skips_rewrite_on_same_issued_at(self, tmp_path: Path) -> None:
        station = _river_station("2135")
        inventory = BafuStationInventory(stations=[station], produced_at=_PRODUCED_AT)
        variant_results: dict[tuple[str, str], BafuVariantFetch | None | Exception] = {
            ("2135", "q_forecast"): _fetch("2135", "q_forecast", n_rows=3),
        }
        adapter = _FakeAdapter(inventory, variant_results)
        config = _make_config(bafu_forecast_archive_path=tmp_path)

        first = collect_bafu_forecasts_flow(
            config=config,
            adapter=adapter,
            clock=_ClockSpy(_PRODUCED_AT),
            sleeper=_SleepSpy(),
        )
        assert first.variants_fetched == 1
        assert first.variants_skipped_dedup == 0

        raw_path = (
            tmp_path
            / "raw"
            / f"2135_q_forecast_{_ISSUED_AT.strftime('%Y%m%dT%H%M%SZ')}.json"
        )
        mtime_after_first = raw_path.stat().st_mtime_ns

        # Second run — the adapter is still called (issued_at is only known
        # after fetching), but the archive must not be re-written.
        second = collect_bafu_forecasts_flow(
            config=config,
            adapter=adapter,
            clock=_ClockSpy(_PRODUCED_AT),
            sleeper=_SleepSpy(),
        )
        assert second.variants_fetched == 0
        assert second.variants_skipped_dedup == 1
        assert second.rows_archived == 0
        assert raw_path.stat().st_mtime_ns == mtime_after_first

    def test_variant_absent_is_not_counted_as_failure(self, tmp_path: Path) -> None:
        station = _lake_station("3001")
        inventory = BafuStationInventory(stations=[station], produced_at=_PRODUCED_AT)
        variant_results: dict[tuple[str, str], BafuVariantFetch | None | Exception] = {
            ("3001", "q_forecast"): None,  # 404 — absent, not an error
            ("3001", "p_forecast"): _fetch(
                "3001", "p_forecast", metric="masl", n_rows=1
            ),
        }
        adapter = _FakeAdapter(inventory, variant_results)
        config = _make_config(bafu_forecast_archive_path=tmp_path)

        result = collect_bafu_forecasts_flow(
            config=config,
            adapter=adapter,
            clock=_ClockSpy(_PRODUCED_AT),
            sleeper=_SleepSpy(),
        )
        assert result.variants_absent == 1
        assert result.variants_failed == 0
        assert result.variants_fetched == 1

    def test_single_station_failure_does_not_abort_run(self, tmp_path: Path) -> None:
        failing = _river_station("1111")
        healthy = _river_station("2135")
        inventory = BafuStationInventory(
            stations=[failing, healthy], produced_at=_PRODUCED_AT
        )
        variant_results: dict[tuple[str, str], BafuVariantFetch | None | Exception] = {
            ("1111", "q_forecast"): AdapterError("boom"),
            ("2135", "q_forecast"): _fetch("2135", "q_forecast", n_rows=1),
        }
        adapter = _FakeAdapter(inventory, variant_results)
        config = _make_config(bafu_forecast_archive_path=tmp_path)

        result = collect_bafu_forecasts_flow(
            config=config,
            adapter=adapter,
            clock=_ClockSpy(_PRODUCED_AT),
            sleeper=_SleepSpy(),
        )
        assert result.variants_failed == 1
        assert result.variants_fetched == 1
        assert result.stations_seen == 2

    def test_total_failure_on_inventory_fetch_raises(self, tmp_path: Path) -> None:
        config = _make_config(bafu_forecast_archive_path=tmp_path)
        with pytest.raises(AdapterError, match="GeoJSON unreachable"):
            collect_bafu_forecasts_flow(
                config=config,
                adapter=_RaisingAdapter(),
                clock=_ClockSpy(_PRODUCED_AT),
                sleeper=_SleepSpy(),
            )

    def test_sleeper_called_between_but_not_before_first_station(
        self, tmp_path: Path
    ) -> None:
        stations = [
            _river_station("1000"),
            _river_station("2000"),
            _river_station("3000"),
        ]
        inventory = BafuStationInventory(stations=stations, produced_at=_PRODUCED_AT)
        variant_results: dict[tuple[str, str], BafuVariantFetch | None | Exception] = {
            (s.key, "q_forecast"): _fetch(s.key, "q_forecast", n_rows=1)
            for s in stations
        }
        adapter = _FakeAdapter(inventory, variant_results)
        sleeper = _SleepSpy()
        config = _make_config(bafu_forecast_archive_path=tmp_path)

        collect_bafu_forecasts_flow(
            config=config,
            adapter=adapter,
            clock=_ClockSpy(_PRODUCED_AT),
            sleeper=sleeper,
        )
        assert len(sleeper.calls) == len(stations) - 1

    def test_missing_icon_station_is_skipped(self, tmp_path: Path) -> None:
        missing = BafuForecastStation(
            key="9999",
            label="No data 9999",
            icon="missing",
            metric="discharge_ms",
            unit="m³/s",
            plot_path="/web/hydro/hydro_sensor_pq_forecast/9999/plots",
        )
        healthy = _river_station("2135")
        inventory = BafuStationInventory(
            stations=[missing, healthy], produced_at=_PRODUCED_AT
        )
        variant_results: dict[tuple[str, str], BafuVariantFetch | None | Exception] = {
            ("2135", "q_forecast"): _fetch("2135", "q_forecast", n_rows=1),
        }
        adapter = _FakeAdapter(inventory, variant_results)
        config = _make_config(bafu_forecast_archive_path=tmp_path)

        result = collect_bafu_forecasts_flow(
            config=config,
            adapter=adapter,
            clock=_ClockSpy(_PRODUCED_AT),
            sleeper=_SleepSpy(),
        )
        # The missing station triggers no fetch at all.
        assert all(key != "9999" for key, _ in adapter.calls)
        assert result.variants_fetched == 1

    def test_blank_archive_path_is_noop(self, tmp_path: Path) -> None:
        # An empty/whitespace path must NOT resolve to Path("") == cwd and
        # bypass the quarantine gate — the flow no-ops like an unset path.
        config = _make_config(bafu_forecast_archive_path=Path("  "))
        adapter = _FakeAdapter(
            BafuStationInventory(
                stations=[_river_station("2135")], produced_at=_PRODUCED_AT
            ),
            {("2135", "q_forecast"): _fetch("2135", "q_forecast")},
        )
        result = collect_bafu_forecasts_flow(
            config=config,
            adapter=adapter,
            clock=_ClockSpy(_PRODUCED_AT),
            sleeper=_SleepSpy(),
        )
        assert result == _EMPTY_RESULT
        assert adapter.inventory_calls == 0  # never even fetched

    def test_clock_is_injected_and_used(self, tmp_path: Path) -> None:
        station = _river_station("2135")
        inventory = BafuStationInventory(stations=[station], produced_at=_PRODUCED_AT)
        variant_results: dict[tuple[str, str], BafuVariantFetch | None | Exception] = {
            ("2135", "q_forecast"): _fetch("2135", "q_forecast", n_rows=1),
        }
        adapter = _FakeAdapter(inventory, variant_results)
        config = _make_config(bafu_forecast_archive_path=tmp_path)
        clock = _ClockSpy(_PRODUCED_AT)

        collect_bafu_forecasts_flow(
            config=config, adapter=adapter, clock=clock, sleeper=_SleepSpy()
        )
        assert clock.calls == 1


class _RaisingHealthStore:
    def append_health_record(self, record: object) -> None:
        raise RuntimeError("db unavailable")


class TestDisposeConn:
    def test_closes_connection_then_disposes_engine(self) -> None:
        from sapphire_flow.flows.collect_bafu_forecasts import _dispose_conn

        calls: list[str] = []

        class _Engine:
            def dispose(self) -> None:
                calls.append("dispose")

        class _Conn:
            engine = _Engine()

            def close(self) -> None:
                calls.append("close")

        _dispose_conn(_Conn())
        assert calls == ["close", "dispose"]  # connection returned, then pool freed

    def test_none_is_noop(self) -> None:
        from sapphire_flow.flows.collect_bafu_forecasts import _dispose_conn

        _dispose_conn(None)  # must not raise (injected-adapter/test path)

    def test_close_error_is_swallowed(self) -> None:
        from sapphire_flow.flows.collect_bafu_forecasts import _dispose_conn

        class _Conn:
            def close(self) -> None:
                raise RuntimeError("boom")

        _dispose_conn(_Conn())  # best-effort — never propagates


class TestBuildHealthStoreBestEffort:
    def test_returns_none_none_on_failure_so_collection_proceeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A health-store outage (missing DATABASE_URL / DB down) must NEVER abort
        # the forward-only collection run — it returns (None, None) and the run
        # proceeds with no heartbeat, rather than raising before any fetch.
        from sapphire_flow.flows.collect_bafu_forecasts import (
            _build_health_store_best_effort,
        )

        monkeypatch.delenv("DATABASE_URL", raising=False)
        conn, store = _build_health_store_best_effort()
        assert conn is None
        assert store is None


class TestBafuHealthHeartbeat:
    def test_ok_status_and_detail_counts_on_clean_run(self, tmp_path: Path) -> None:
        river = _river_station("2135")
        lake = _lake_station("3001")
        inventory = BafuStationInventory(
            stations=[river, lake], produced_at=_PRODUCED_AT
        )
        variant_results: dict[tuple[str, str], BafuVariantFetch | None | Exception] = {
            ("2135", "q_forecast"): _fetch("2135", "q_forecast", n_rows=3),
            ("3001", "q_forecast"): _fetch("3001", "q_forecast", n_rows=2),
            ("3001", "p_forecast"): _fetch(
                "3001", "p_forecast", metric="masl", n_rows=4
            ),
        }
        adapter = _FakeAdapter(inventory, variant_results)
        config = _make_config(bafu_forecast_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()

        result = collect_bafu_forecasts_flow(
            config=config,
            adapter=adapter,
            clock=_ClockSpy(_PRODUCED_AT),
            sleeper=_SleepSpy(),
            pipeline_health_store=health_store,
        )

        records = health_store.fetch_recent(PipelineCheckType.BAFU_FORECAST_FRESHNESS)
        assert len(records) == 1
        record = records[0]
        assert record.check_type is PipelineCheckType.BAFU_FORECAST_FRESHNESS
        assert record.status is PipelineHealthStatus.OK
        assert record.subject == "bafu_forecast_collector"
        assert record.cycle_time is None
        assert record.checked_at == _PRODUCED_AT
        assert record.created_at == _PRODUCED_AT
        assert record.detail == {
            "stations_seen": result.stations_seen,
            "variants_fetched": result.variants_fetched,
            "variants_absent": result.variants_absent,
            "variants_skipped_dedup": result.variants_skipped_dedup,
            "variants_failed": result.variants_failed,
            "rows_archived": result.rows_archived,
        }
        assert record.detail == {
            "stations_seen": 2,
            "variants_fetched": 3,
            "variants_absent": 0,
            "variants_skipped_dedup": 0,
            "variants_failed": 0,
            "rows_archived": 9,
        }

    def test_warning_status_when_variants_failed(self, tmp_path: Path) -> None:
        failing = _river_station("1111")
        healthy = _river_station("2135")
        inventory = BafuStationInventory(
            stations=[failing, healthy], produced_at=_PRODUCED_AT
        )
        variant_results: dict[tuple[str, str], BafuVariantFetch | None | Exception] = {
            ("1111", "q_forecast"): AdapterError("boom"),
            ("2135", "q_forecast"): _fetch("2135", "q_forecast", n_rows=1),
        }
        adapter = _FakeAdapter(inventory, variant_results)
        config = _make_config(bafu_forecast_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()

        collect_bafu_forecasts_flow(
            config=config,
            adapter=adapter,
            clock=_ClockSpy(_PRODUCED_AT),
            sleeper=_SleepSpy(),
            pipeline_health_store=health_store,
        )

        records = health_store.fetch_recent(PipelineCheckType.BAFU_FORECAST_FRESHNESS)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.WARNING
        assert records[0].detail["variants_failed"] == 1

    def test_health_write_failure_does_not_fail_the_collection_run(
        self, tmp_path: Path
    ) -> None:
        station = _river_station("2135")
        inventory = BafuStationInventory(stations=[station], produced_at=_PRODUCED_AT)
        variant_results: dict[tuple[str, str], BafuVariantFetch | None | Exception] = {
            ("2135", "q_forecast"): _fetch("2135", "q_forecast", n_rows=1),
        }
        adapter = _FakeAdapter(inventory, variant_results)
        config = _make_config(bafu_forecast_archive_path=tmp_path)

        result = collect_bafu_forecasts_flow(
            config=config,
            adapter=adapter,
            clock=_ClockSpy(_PRODUCED_AT),
            sleeper=_SleepSpy(),
            pipeline_health_store=_RaisingHealthStore(),
        )

        # The archive/collection outcome is unaffected by the health-write
        # failure — best-effort, never fatal to the run.
        assert result.variants_fetched == 1
        assert result.rows_archived == 1

    def test_no_health_write_when_store_not_provided(self, tmp_path: Path) -> None:
        # Default (no store injected, no DATABASE_URL) must not raise —
        # _append_bafu_health_record no-ops on pipeline_health_store=None.
        station = _river_station("2135")
        inventory = BafuStationInventory(stations=[station], produced_at=_PRODUCED_AT)
        variant_results: dict[tuple[str, str], BafuVariantFetch | None | Exception] = {
            ("2135", "q_forecast"): _fetch("2135", "q_forecast", n_rows=1),
        }
        adapter = _FakeAdapter(inventory, variant_results)
        config = _make_config(bafu_forecast_archive_path=tmp_path)

        result = collect_bafu_forecasts_flow(
            config=config,
            adapter=adapter,
            clock=_ClockSpy(_PRODUCED_AT),
            sleeper=_SleepSpy(),
        )
        assert result.variants_fetched == 1


class TestPipelineCheckTypeEnum:
    def test_bafu_forecast_freshness_round_trips(self) -> None:
        assert (
            PipelineCheckType("bafu_forecast_freshness")
            is PipelineCheckType.BAFU_FORECAST_FRESHNESS
        )
        assert PipelineCheckType.BAFU_FORECAST_FRESHNESS.value == (
            "bafu_forecast_freshness"
        )
