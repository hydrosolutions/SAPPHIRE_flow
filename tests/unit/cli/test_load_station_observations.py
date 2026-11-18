from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sapphire_flow.cli import load_station_observations as lso
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AuditEventType, WaterLevelGaugeType
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.tenant import DEFAULT_TENANT_CODE
from tests.conftest import make_station_config
from tests.fakes.fake_stores import (
    FakeAuditLogStore,
    FakeObservationStore,
    FakeStationStore,
    FakeTenantStore,
)

NOW = ensure_utc(datetime(2026, 11, 18, tzinfo=UTC))
SCOPED = DeploymentIdentityConfig(
    writable_tenants=frozenset({DEFAULT_TENANT_CODE}), global_admin=False
)
CSV = "t,v\n2026-11-01 06:00,2.0\n2026-11-02 06:00,2.5\n"
ARGS = [
    "--tenant", DEFAULT_TENANT_CODE, "--station-code", "00001",
    "--network", "research", "--csv", "x.csv", "--time-column", "t",
    "--value-column", "v", "--utc-offset-minutes", "345",
]  # fmt: skip


def _station(**kw: object):  # noqa: ANN202
    return make_station_config(
        code="00001",
        network="research",
        tz="Asia/Kathmandu",
        water_level_datum_masl=1480.0,
        water_level_unit="m",
        water_level_gauge_type=WaterLevelGaugeType.STAFF_OR_PRESSURE,
        **kw,  # pyright: ignore[reportArgumentType]
    )


class Rig:
    def __init__(self, station: object | None = None) -> None:
        self.stations = FakeStationStore()
        if station is not None:
            self.stations.store_station(station)  # pyright: ignore[reportArgumentType]
        self.tenants = FakeTenantStore()
        self.observations = FakeObservationStore()
        self.audit = FakeAuditLogStore()

    def run(self, command: str, extra: list[str], text: str = CSV) -> int:
        args = lso._parse_args([command, *ARGS, *extra])  # pyright: ignore[reportPrivateUsage]
        return lso.run_load(
            args,
            text=text,
            identity=SCOPED,
            tenants=self.tenants,
            stations=self.stations,
            observations=self.observations,
            audit=self.audit,
            clock=lambda: NOW,
        )


class TestArgs:
    def test_discharge_kind_is_fixed(self) -> None:
        args = lso._parse_args(["discharge", *ARGS])  # pyright: ignore[reportPrivateUsage]
        assert args.kind.value == "discharge_m3s"

    def test_offset_and_zone_are_exclusive(self) -> None:
        with pytest.raises(SystemExit):
            lso._parse_args(["water-level", *ARGS, "--zone", "UTC"])  # pyright: ignore[reportPrivateUsage]

    def test_unknown_zone_is_rejected(self) -> None:
        with pytest.raises(SystemExit):
            lso._parse_args(["water-level", *ARGS[:-2], "--zone", "Mars/Base"])  # pyright: ignore[reportPrivateUsage]


class TestRun:
    def test_dry_run_writes_nothing_and_echoes_example(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rig = Rig(_station())
        assert rig.run("water-level", []) == 0
        assert rig.audit.entries == []
        assert (
            rig.observations.fetch_observations(
                rig.stations.fetch_all_stations()[0].id,
                "water_level",
                ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
                NOW,
            )
            == []
        )
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "reading 2 m -> stored 1482 m a.s.l. (staff gauge, datum 1480.0)" in out
        assert "2 of 30 days have a value (QC not run yet)" in out

    def test_apply_writes_rows_and_exactly_one_audit_row(self) -> None:
        station = _station()
        rig = Rig(station)
        assert rig.run("water-level", ["--apply"]) == 0
        stored = rig.observations.fetch_observations(
            station.id,
            "water_level",
            ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
            NOW,
        )
        assert sorted(o.value for o in stored if o.value is not None) == [
            1482.0,
            1482.5,
        ]
        (entry,) = rig.audit.entries
        assert entry.event_type is AuditEventType.OBSERVATIONS_LOADED
        assert entry.detail is not None and entry.detail["rows"] == 2

    def test_discharge_loads_without_datum(self) -> None:
        station = make_station_config(code="00001", network="research")
        rig = Rig(station)
        assert rig.run("discharge", ["--apply"]) == 0
        (entry,) = rig.audit.entries
        assert entry.detail is not None and entry.detail["parameter"] == "discharge"

    def test_gauge_reading_without_datum_is_refused_with_exit_3(self) -> None:
        rig = Rig(make_station_config(code="00001", network="research"))
        assert rig.run("water-level", ["--apply"]) == 3
        assert rig.audit.entries == []

    def test_non_writable_tenant_is_refused_and_audited_not_written(self) -> None:
        station = _station(tenant_id=TenantId(uuid4()))
        rig = Rig(station)
        assert rig.run("water-level", ["--apply"]) == 3
        (entry,) = rig.audit.entries
        assert entry.detail is not None
        assert entry.detail["outcome"] == "rejected_tenant_mismatch"

    def test_unknown_station_and_bad_csv_exit_2(self) -> None:
        assert Rig().run("water-level", []) == 2
        assert Rig(_station()).run("water-level", [], text="t,x\n1,2\n") == 2

    def test_absolute_looking_values_warn_and_strict_fails(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        text = "t,v\n2026-11-01 06:00,1481.0\n"
        assert Rig(_station()).run("water-level", [], text=text) == 0
        assert "level-masl" in capsys.readouterr().out
        assert Rig(_station()).run("water-level", ["--strict"], text=text) == 2

    def test_level_masl_kind_stores_values_as_is(self) -> None:
        station = _station()
        rig = Rig(station)
        text = "t,v\n2026-11-01 06:00,1481.0\n"
        assert (
            rig.run("water-level", ["--value-kind", "level-masl", "--apply"], text) == 0
        )
        (obs,) = rig.observations.fetch_observations(
            station.id, "water_level", ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)), NOW
        )
        assert obs.value == 1481.0

    def test_lookback_count_respects_as_of_and_window(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        Rig(_station()).run(
            "water-level", ["--as-of", "2026-11-10", "--lookback-days", "5"]
        )
        assert "0 of 5 days have a value" in capsys.readouterr().out
