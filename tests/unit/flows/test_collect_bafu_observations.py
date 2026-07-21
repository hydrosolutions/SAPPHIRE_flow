from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003

import polars as pl
import pytest

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.exceptions import AdapterError
from sapphire_flow.types.bafu_observation import BafuObservationRow
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import PipelineHealthStatus
from tests.fakes.fake_stores import FakePipelineHealthStore


def _import_flow_module() -> object:
    try:
        from sapphire_flow.flows import collect_bafu_observations

        return collect_bafu_observations
    except ImportError as exc:  # pragma: no cover - red-first guard
        pytest.fail(
            "sapphire_flow.flows.collect_bafu_observations does not exist yet "
            f"(T3/T4 not implemented): {exc}"
        )


def _make_config(**overrides: object) -> DeploymentConfig:
    defaults: dict[str, object] = {"max_retention_days": 3650}
    defaults.update(overrides)
    return DeploymentConfig(**defaults)  # type: ignore[arg-type]


def _row(
    gauge_code: str,
    lindas_kind: str = "river",
    parameter: str = "discharge",
    value: float = 12.3,
    measurement_time: datetime | None = None,
) -> BafuObservationRow:
    ts = measurement_time or datetime(2026, 7, 21, 15, 0, tzinfo=UTC)
    return BafuObservationRow(
        gauge_code=gauge_code,
        lindas_kind=lindas_kind,  # type: ignore[arg-type]
        parameter=parameter,  # type: ignore[arg-type]
        value=value,
        measurement_time=ensure_utc(ts),
    )


class _FakeAdapter:
    def __init__(
        self,
        rows: list[BafuObservationRow] | Exception,
        raw_payload: dict[str, object] | None = None,
    ) -> None:
        self._rows = rows
        self._raw_payload = raw_payload or {"results": {"bindings": []}}
        self.calls = 0

    def fetch_all_observations_with_raw(
        self,
    ) -> tuple[list[BafuObservationRow], dict[str, object]]:
        self.calls += 1
        if isinstance(self._rows, Exception):
            raise self._rows
        return self._rows, self._raw_payload


class _NeverCalledAdapter:
    def fetch_all_observations_with_raw(
        self,
    ) -> tuple[list[BafuObservationRow], dict[str, object]]:
        raise AssertionError("adapter must not be used when the archive path is unset")


class _ClockSpy:
    def __init__(self, value: datetime) -> None:
        self._value = ensure_utc(value)
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        return self._value


class TestQuarantineGate:
    def test_blank_archive_path_is_noop(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=Path("  "))
        result = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_NeverCalledAdapter(),
            clock=_ClockSpy(datetime(2026, 7, 21, 10, 5, tzinfo=UTC)),
        )
        assert result == module._EMPTY_RESULT  # type: ignore[attr-defined]
        # No parquet/raw files anywhere under tmp_path.
        assert list(tmp_path.rglob("*")) == []

    def test_unset_archive_path_is_noop(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=None)
        result = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_NeverCalledAdapter(),
            clock=_ClockSpy(datetime(2026, 7, 21, 10, 5, tzinfo=UTC)),
        )
        assert result == module._EMPTY_RESULT  # type: ignore[attr-defined]


class TestDedup:
    def test_same_hour_retry_writes_zero_new_files(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        rows = [_row("2135"), _row("3001", lindas_kind="lake", parameter="water_level")]

        first = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(datetime(2026, 7, 21, 10, 5, 3, tzinfo=UTC)),
        )
        parquet_files_after_first = list(tmp_path.rglob("*.parquet"))
        assert len(parquet_files_after_first) == 1
        assert first.row_count == 2

        # Retry within the SAME hour, different minute/second — must resolve to
        # the same cycle_at and skip (production-default path, not a re-injected
        # literal — the two clock values differ).
        second_adapter = _FakeAdapter(rows)
        second = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=second_adapter,
            clock=_ClockSpy(datetime(2026, 7, 21, 10, 7, 41, tzinfo=UTC)),
        )
        assert list(tmp_path.rglob("*.parquet")) == parquet_files_after_first
        assert second_adapter.calls == 0  # dedup short-circuits before any fetch
        assert second.row_count == 0

    def test_next_hour_writes_a_new_snapshot(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        rows = [_row("2135")]

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(datetime(2026, 7, 21, 10, 5, tzinfo=UTC)),
        )
        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(datetime(2026, 7, 21, 11, 5, tzinfo=UTC)),
        )
        assert len(list(tmp_path.rglob("*.parquet"))) == 2


class TestRestatement:
    def test_later_hour_restatement_preserves_both_snapshots(
        self, tmp_path: Path
    ) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        ts = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter([_row("2135", value=100.0, measurement_time=ts)]),
            clock=_ClockSpy(datetime(2026, 7, 21, 10, 5, tzinfo=UTC)),
        )
        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter([_row("2135", value=101.0, measurement_time=ts)]),
            clock=_ClockSpy(datetime(2026, 7, 21, 11, 5, tzinfo=UTC)),
        )

        parquet_files = sorted(tmp_path.rglob("*.parquet"))
        assert len(parquet_files) == 2
        values = sorted(pl.read_parquet(f)["value"].to_list()[0] for f in parquet_files)
        assert values == [100.0, 101.0]  # both survive — no drop, no overwrite


class TestMultiGaugeArchive:
    def test_one_run_archives_many_distinct_gauges(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        rows = [
            _row("2135"),
            _row("2200"),
            _row("3001", lindas_kind="lake", parameter="water_level"),
        ]
        result = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(datetime(2026, 7, 21, 10, 5, tzinfo=UTC)),
        )
        assert result.row_count == 3
        assert result.gauge_count == 3

        parquet_files = list(tmp_path.rglob("*.parquet"))
        assert len(parquet_files) == 1
        df = pl.read_parquet(parquet_files[0])
        assert set(df["gauge_code"].to_list()) == {"2135", "2200", "3001"}
        assert "cycle_at" in df.columns


class TestRawArchival:
    def test_raw_json_companion_is_plain_and_readable(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        raw_payload = {"results": {"bindings": [{"fake": "payload"}]}}

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter([_row("2135")], raw_payload=raw_payload),
            clock=_ClockSpy(datetime(2026, 7, 21, 10, 5, tzinfo=UTC)),
        )
        raw_files = list(tmp_path.rglob("*.json"))
        assert len(raw_files) == 1
        assert json.loads(raw_files[0].read_text()) == raw_payload


class TestHeartbeat:
    def test_ok_status_on_clean_run_with_one_stale_many_fresh(
        self, tmp_path: Path
    ) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()
        stale_ts = datetime(2025, 5, 28, 0, 0, tzinfo=UTC)
        fresh_ts = datetime(2026, 7, 21, 15, 0, tzinfo=UTC)
        rows = [
            _row("9999", measurement_time=stale_ts),  # dead gauge, still present
            _row("2135", measurement_time=fresh_ts),
            _row("2200", measurement_time=fresh_ts),
        ]

        module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(datetime(2026, 7, 21, 15, 5, tzinfo=UTC)),
            pipeline_health_store=health_store,
        )

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.OK
        assert records[0].detail["row_count"] == 3
        assert records[0].detail["newest_measurement_time"] == fresh_ts.isoformat()

    def test_empty_response_writes_critical_and_reraises(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()

        with pytest.raises(AdapterError):
            module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
                config=config,
                adapter=_FakeAdapter([]),
                clock=_ClockSpy(datetime(2026, 7, 21, 15, 5, tzinfo=UTC)),
                pipeline_health_store=health_store,
            )

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].detail["row_count"] == 0
        assert records[0].detail["newest_measurement_time"] is None
        # No parquet was written for a non-run (an outage, not a real snapshot).
        assert list(tmp_path.rglob("*.parquet")) == []

    def test_http_error_fetch_writes_critical_before_reraising(
        self, tmp_path: Path
    ) -> None:
        # Drives a REAL BafuObservationAdapter (not a hand-fed AdapterError)
        # over an httpx.MockTransport 500 response, through the real flow, so
        # this locks the actual production path: the adapter must normalize
        # the HTTP failure to AdapterError, and the flow must write CRITICAL
        # before re-raising it.
        import httpx

        from sapphire_flow.adapters.bafu_observation import BafuObservationAdapter

        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server error")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = BafuObservationAdapter(
            endpoint="https://lindas.admin.ch/query",
            http_client=client,
            sleeper=lambda _seconds: None,
            max_retries=1,
        )

        with pytest.raises(AdapterError, match="failed with status 500"):
            module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
                config=config,
                adapter=adapter,
                clock=_ClockSpy(datetime(2026, 7, 21, 15, 5, tzinfo=UTC)),
                pipeline_health_store=health_store,
            )

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].detail["error_type"] is not None
        # No parquet was written for a non-run (an outage, not a real snapshot).
        assert list(tmp_path.rglob("*.parquet")) == []

    def test_malformed_json_shape_writes_critical_before_reraising(
        self, tmp_path: Path
    ) -> None:
        # Drives a REAL BafuObservationAdapter over an httpx.MockTransport
        # response that IS valid JSON but has the WRONG top-level shape (a
        # bare list) — `payload["results"]["bindings"]` then raises
        # TypeError, not ValueError/KeyError. This locks the T8 contract:
        # the adapter must normalize even a TypeError-shaped malformed
        # response to AdapterError, and the flow must write CRITICAL before
        # re-raising it (never let a bare TypeError skip the heartbeat).
        import httpx

        from sapphire_flow.adapters.bafu_observation import BafuObservationAdapter

        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[1, 2, 3])

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = BafuObservationAdapter(
            endpoint="https://lindas.admin.ch/query",
            http_client=client,
            sleeper=lambda _seconds: None,
            max_retries=1,
        )

        with pytest.raises(AdapterError, match="not a well-formed SPARQL"):
            module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
                config=config,
                adapter=adapter,
                clock=_ClockSpy(datetime(2026, 7, 21, 15, 5, tzinfo=UTC)),
                pipeline_health_store=health_store,
            )

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].detail["error_type"] is not None
        # No parquet was written for a non-run (an outage, not a real snapshot).
        assert list(tmp_path.rglob("*.parquet")) == []

    def test_bindings_null_writes_critical_before_reraising(
        self, tmp_path: Path
    ) -> None:
        # A well-formed envelope whose `bindings` is null: extraction
        # succeeds (bindings=None), then len(None) would raise a bare
        # TypeError OUTSIDE the try. The adapter's isinstance guard must
        # surface it as AdapterError, and the flow must write CRITICAL before
        # re-raising it (the T8 contract for a wrong-shaped bindings value).
        import httpx

        from sapphire_flow.adapters.bafu_observation import BafuObservationAdapter

        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": {"bindings": None}})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = BafuObservationAdapter(
            endpoint="https://lindas.admin.ch/query",
            http_client=client,
            sleeper=lambda _seconds: None,
            max_retries=1,
        )

        with pytest.raises(AdapterError, match="is not a list"):
            module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
                config=config,
                adapter=adapter,
                clock=_ClockSpy(datetime(2026, 7, 21, 15, 5, tzinfo=UTC)),
                pipeline_health_store=health_store,
            )

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].detail["error_type"] is not None
        assert list(tmp_path.rglob("*.parquet")) == []

    def test_all_gauges_stale_writes_critical_but_archives_and_does_not_raise(
        self, tmp_path: Path
    ) -> None:
        # A served-but-FROZEN feed: every gauge's newest measurement_time is
        # older than the staleness threshold. The collection SUCCEEDED — the
        # snapshot must still be archived and the run must NOT re-raise — but
        # the heartbeat must be CRITICAL (reason "stale_measurement_time") so
        # the watchdog alarms on a frozen graph that would otherwise report OK
        # forever.
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()
        run_at = datetime(2026, 7, 21, 15, 5, tzinfo=UTC)
        # 6h behind run_at — well past the ~3h threshold, for every gauge.
        stale_ts = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)
        rows = [
            _row("2135", measurement_time=stale_ts),
            _row("2200", measurement_time=stale_ts),
            _row(
                "3001",
                lindas_kind="lake",
                parameter="water_level",
                measurement_time=stale_ts,
            ),
        ]

        result = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter(rows),
            clock=_ClockSpy(run_at),
            pipeline_health_store=health_store,
        )

        # Did NOT raise, and the snapshot WAS archived.
        assert result.dedup_skipped is False
        assert result.row_count == 3
        assert len(list(tmp_path.rglob("*.parquet"))) == 1

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].detail["error_type"] == "stale_measurement_time"
        assert records[0].detail["row_count"] == 3
        assert (
            records[0].detail["newest_measurement_time"]
            == ensure_utc(stale_ts).isoformat()
        )

    def test_truncated_fetch_writes_critical(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)
        health_store = FakePipelineHealthStore()

        with pytest.raises(AdapterError, match="LIMIT"):
            module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
                config=config,
                adapter=_FakeAdapter(AdapterError("hit the safety LIMIT")),
                clock=_ClockSpy(datetime(2026, 7, 21, 15, 5, tzinfo=UTC)),
                pipeline_health_store=health_store,
            )

        check_type = module.PipelineCheckType.BAFU_OBSERVATION_FRESHNESS  # type: ignore[attr-defined]
        records = health_store.fetch_recent(check_type)
        assert records[0].status is PipelineHealthStatus.CRITICAL

    def test_health_store_outage_does_not_fail_the_run(self, tmp_path: Path) -> None:
        module = _import_flow_module()
        config = _make_config(bafu_observation_archive_path=tmp_path)

        class _RaisingHealthStore:
            def append_health_record(self, record: object) -> None:
                raise RuntimeError("db unavailable")

        result = module.collect_bafu_observations_flow(  # type: ignore[attr-defined]
            config=config,
            adapter=_FakeAdapter([_row("2135")]),
            clock=_ClockSpy(datetime(2026, 7, 21, 15, 5, tzinfo=UTC)),
            pipeline_health_store=_RaisingHealthStore(),
        )
        assert result.row_count == 1
