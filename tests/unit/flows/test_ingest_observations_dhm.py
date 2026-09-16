from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import pytest

from sapphire_flow.adapters.dhm import DhmAdapter
from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.config.dhm import parse_dhm_config
from sapphire_flow.flows.ingest_observations import (
    _fetch_configured_dhm,
    _run_qc_task,
    ingest_observations_flow,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import (
    ObservationSource,
    PipelineCheckType,
    PipelineHealthStatus,
    QcStatus,
    StationKind,
)
from sapphire_flow.types.observation import HydroScraperBatchResult, RawObservation
from tests.fakes.fake_adapters import FakeStationDataSource
from tests.fakes.fake_stores import (
    FakeClimBaselineStore,
    FakeObservationStore,
    FakePipelineHealthStore,
    FakeStationStore,
)
from tests.unit.adapters.test_dhm import NOW, reading, settings, station

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import ClimBaseline
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationConfig

RULES = QcRuleSet(
    version="dhm-test",
    rules=(
        QcRuleParams(
            rule_id="rate_of_change",
            rule_version="test",
            parameter="water_level",
            time_step=timedelta(minutes=10),
            thresholds={"max_rate": 0.5},
        ),
    ),
)


def observation(
    config: StationConfig,
    minutes_ago: int,
    value: float = 2.0,
    parameter: str = "water_level",
) -> RawObservation:
    return RawObservation(
        station_id=config.id,
        parameter=parameter,
        value=value,
        timestamp=ensure_utc(NOW - timedelta(minutes=minutes_ago)),
        source=ObservationSource.MEASURED,
    )


def stations(*configs: StationConfig) -> FakeStationStore:
    result = FakeStationStore()
    for config in configs:
        result.store_station(config)
    return result


class TestDhmIngest:
    def test_injected_adapter_uses_water_level_cursor(self) -> None:
        config = station()
        store = FakeObservationStore()
        level = observation(config, 360)
        store.store_raw_observations(
            [level, observation(config, 5, parameter="discharge")]
        )

        class CapturingAdapter(FakeStationDataSource):
            captured: dict[StationId, UtcDatetime] = {}

            def fetch_observations_batch(
                self,
                station_configs: list[StationConfig],
                since: dict[StationId, UtcDatetime],
            ) -> HydroScraperBatchResult:
                self.captured = since
                return super().fetch_observations_batch(station_configs, since)

        adapter = CapturingAdapter([])
        ingest_observations_flow(
            station_store=stations(config),
            obs_store=store,
            baseline_store=FakeClimBaselineStore(),
            adapter=adapter,
            qc_rules=RULES,
            clock=lambda: NOW,
        )
        assert adapter.captured[config.id] == level.timestamp

    def test_six_hour_recovery_qcs_old_rows_with_preceding_context(self) -> None:
        config = station()
        store = FakeObservationStore()
        [context_id] = store.store_raw_observations([observation(config, 370)])
        store.update_qc(context_id, QcStatus.QC_PASSED, [])
        store.store_raw_observations([observation(config, 370, parameter="discharge")])
        [inside_id, outside_id] = store.store_raw_observations(
            [
                observation(config, 420),
                observation(config, 600),
            ]
        )
        rows = [
            reading(4.0, waterLevelOn=observation(config, m).timestamp.isoformat())
            for m in range(360, -1, -10)
        ]
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"results": rows})
            )
        ) as client:
            adapter = DhmAdapter(
                config=parse_dhm_config(settings()),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            )
            result = ingest_observations_flow(
                station_store=stations(config),
                obs_store=store,
                baseline_store=FakeClimBaselineStore(),
                adapter=adapter,
                qc_rules=RULES,
                clock=lambda: NOW,
            )
        recovered = sorted(
            [
                o
                for o in store.observations()
                if o.parameter == "water_level"
                and o.timestamp >= observation(config, 360).timestamp
            ],
            key=lambda o: o.timestamp,
        )
        assert len(recovered) == 37
        assert all(o.qc_status is not QcStatus.RAW for o in recovered)
        assert recovered[0].qc_status is QcStatus.QC_SUSPECT
        assert all(o.value == 4.0 for o in recovered)
        assert result.stations_failed == 0
        assert (
            next(o for o in store.observations() if o.id == context_id).qc_flags == []
        )
        assert (
            next(o for o in store.observations() if o.id == inside_id).qc_status
            is not QcStatus.RAW
        )
        assert (
            next(o for o in store.observations() if o.id == outside_id).qc_status
            is QcStatus.RAW
        )

    @pytest.mark.parametrize("include_unbound", [False, True])
    def test_skipped_stations_do_not_poison_health_but_unbound_gauges_fail(
        self, include_unbound: bool
    ) -> None:
        valid = station()
        configs = [
            valid,
            replace(station("WEATHER"), station_kind=StationKind.WEATHER),
            replace(station("FOREIGN"), network="bafu"),
        ]
        if include_unbound:
            configs.append(station("UNBOUND"))
        health = FakePipelineHealthStore()
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "results": [
                        reading(
                            waterLevelOn=observation(valid, 5).timestamp.isoformat()
                        )
                    ]
                },
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            adapter = DhmAdapter(
                config=parse_dhm_config(settings()),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            )
            result = ingest_observations_flow(
                station_store=stations(*configs),
                obs_store=FakeObservationStore(),
                baseline_store=FakeClimBaselineStore(),
                adapter=adapter,
                qc_rules=RULES,
                clock=lambda: NOW,
                pipeline_health_store=health,
            )
            assert not client.is_closed
        assert result.observations_stored == 1
        assert result.stations_failed == int(include_unbound)
        assert len(requests) == 1
        [record] = health.fetch_recent(PipelineCheckType.OBSERVATION_INGEST_FETCH)
        assert record.status is (
            PipelineHealthStatus.WARNING if include_unbound else PipelineHealthStatus.OK
        )
        assert record.detail["failure_counts_by_cause"] == (
            {"configuration_error": 1} if include_unbound else {}
        )

    def test_repeated_polls_advance_the_level_cursor(self) -> None:
        config = station()
        store = FakeObservationStore()
        requested_starts: list[datetime] = []
        rows = [observation(config, 20), observation(config, 10)]

        def respond(request: httpx.Request) -> httpx.Response:
            requested_starts.append(
                datetime.fromisoformat(request.url.params["water_level_on__gt"])
            )
            row = rows[len(requested_starts) - 1]
            return httpx.Response(
                200,
                json={
                    "results": [
                        reading(row.value, waterLevelOn=row.timestamp.isoformat())
                    ]
                },
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            adapter = DhmAdapter(
                config=parse_dhm_config(settings()),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            )
            for _ in range(2):
                ingest_observations_flow(
                    station_store=stations(config),
                    obs_store=store,
                    baseline_store=FakeClimBaselineStore(),
                    adapter=adapter,
                    qc_rules=RULES,
                    clock=lambda: NOW,
                )
        assert requested_starts[1] == rows[0].timestamp - timedelta(seconds=1)
        assert (
            store.fetch_latest_timestamp(config.id, "water_level") == rows[1].timestamp
        )

    def test_supervised_recovery_stores_genuine_later_data_before_normal_poll(
        self,
    ) -> None:
        config = station()
        store = FakeObservationStore()
        old = observation(config, 50)
        bad_time = observation(config, 40).timestamp
        store.store_raw_observations([old])
        newest = observation(config, 20)

        def respond(request: httpx.Request) -> httpx.Response:
            lower = datetime.fromisoformat(request.url.params["water_level_on__gt"])
            rows = [reading(newest.value, waterLevelOn=newest.timestamp.isoformat())]
            if lower <= bad_time:
                rows.insert(0, reading("rejected", waterLevelOn=bad_time.isoformat()))
            return httpx.Response(200, json={"results": rows})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            adapter = DhmAdapter(
                config=parse_dhm_config(settings()),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            )
            for _ in range(2):
                result = ingest_observations_flow(
                    station_store=stations(config),
                    obs_store=store,
                    baseline_store=FakeClimBaselineStore(),
                    adapter=adapter,
                    qc_rules=RULES,
                    clock=lambda: NOW,
                )
                assert result.stations_failed == 1
                assert (
                    store.fetch_latest_timestamp(config.id, "water_level")
                    == old.timestamp
                )
            # Explicit operator-approved gap, including the request's 1 s padding.
            resumed = adapter.fetch_observations_batch(
                [config], {config.id: observation(config, 30).timestamp}
            )
            assert resumed.failed == ()
            store.store_raw_observations(resumed.observations)
            _run_qc_task.fn(
                store,
                FakeClimBaselineStore(),
                config.id,
                "water_level",
                qc_rules=RULES,
                now=NOW,
                fetched_times=tuple(o.timestamp for o in resumed.observations),
            )
            newest = observation(config, 10)
            result = ingest_observations_flow(
                station_store=stations(config),
                obs_store=store,
                baseline_store=FakeClimBaselineStore(),
                adapter=adapter,
                qc_rules=RULES,
                clock=lambda: NOW,
            )
        assert result.stations_failed == 0
        assert (
            store.fetch_latest_timestamp(config.id, "water_level") == newest.timestamp
        )
        assert {o.timestamp for o in store.observations()} == {
            old.timestamp,
            observation(config, 20).timestamp,
            newest.timestamp,
        }

    def test_qc_failure_is_reported_after_storage(self) -> None:
        config = station()
        store = FakeObservationStore()

        class BrokenBaselines(FakeClimBaselineStore):
            def fetch_baselines(
                self, station_id: StationId, parameter: str
            ) -> list[ClimBaseline]:
                raise ValueError("baseline unavailable")

        result = ingest_observations_flow(
            station_store=stations(config),
            obs_store=store,
            baseline_store=BrokenBaselines(),
            adapter=FakeStationDataSource([observation(config, 5)]),
            qc_rules=RULES,
            clock=lambda: NOW,
        )
        assert result.stations_failed == 1
        assert store.observations()[0].qc_status is QcStatus.RAW

    def test_qc_exclusive_end_includes_latest_even_beyond_recent_window(self) -> None:
        config = station()
        store = FakeObservationStore()
        rows = [observation(config, -110), observation(config, -120)]
        store.store_raw_observations(rows)
        _run_qc_task.fn(
            store,
            FakeClimBaselineStore(),
            config.id,
            "water_level",
            qc_rules=RULES,
            now=NOW,
            fetched_times=tuple(o.timestamp for o in rows),
        )
        assert all(o.qc_status is not QcStatus.RAW for o in store.observations())

    @pytest.mark.parametrize("failure_stage", ["construction", "fetch"])
    def test_flow_owned_client_closes_on_unexpected_failure(
        self, monkeypatch: pytest.MonkeyPatch, failure_stage: str
    ) -> None:
        config = station()

        def respond(request: httpx.Request) -> httpx.Response:
            raise RuntimeError("test fetch failure")

        client = httpx.Client(transport=httpx.MockTransport(respond))
        monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
        monkeypatch.setattr(
            "sapphire_flow.flows.ingest_observations._fetch_observations_task",
            lambda adapter, configs, since: adapter.fetch_observations_batch(
                configs, since
            ),
        )
        if failure_stage == "construction":

            def fail_construction(**kwargs: object) -> DhmAdapter:
                raise RuntimeError("test construction failure")

            monkeypatch.setattr(
                "sapphire_flow.adapters.dhm.DhmAdapter", fail_construction
            )
        with pytest.raises(RuntimeError, match=f"test {failure_stage} failure"):
            _fetch_configured_dhm(
                parse_dhm_config(settings()),
                [config],
                {config.id: observation(config, 60).timestamp},
                NOW,
            )
        assert client.is_closed

    @pytest.mark.parametrize("station_count", [0, 1])
    def test_production_setup_selects_dhm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, station_count: int
    ) -> None:
        config = station()
        config_file = tmp_path / "config.toml"
        config_file.write_text("""[adapters.river_stations]
type = "dhm"
endpoint = "https://dhm.example/api/v1/"
[[adapters.river_stations.bindings]]
network = "dhm"
station_code = "DHM-1"
api_station_id = 168
level_reference = "unknown"
""")
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_file))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "results": [
                        reading(waterLevelOn=(NOW - timedelta(minutes=5)).isoformat())
                    ]
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(respond))
        original_client = httpx.Client
        clients_created: list[httpx.Client] = []

        def make_client(*args: object, **kwargs: object) -> httpx.Client:
            if "base_url" in kwargs:
                return original_client(*args, **kwargs)
            clients_created.append(client)
            return client

        monkeypatch.setattr(httpx, "Client", make_client)
        result = ingest_observations_flow(
            station_store=stations(config) if station_count else FakeStationStore(),
            obs_store=FakeObservationStore(),
            baseline_store=FakeClimBaselineStore(),
            qc_rules=RULES,
            clock=lambda: NOW,
            deployment_config=DeploymentConfig(max_retention_days=600),
        )
        assert result.observations_stored == station_count
        assert [r.method for r in requests] == ["GET"] * station_count
        assert len(clients_created) == station_count
        assert client.is_closed == bool(station_count)
        client.close()
