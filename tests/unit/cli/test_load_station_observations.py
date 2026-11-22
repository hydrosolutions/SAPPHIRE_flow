from __future__ import annotations

import argparse
import copy
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sapphire_flow.cli import load_station_observations as lso
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.config.qc_rules import resolve_qc_rules
from sapphire_flow.services.station_observation_qc import Stage1QcReport
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AuditEventType, WaterLevelGaugeType
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.tenant import DEFAULT_TENANT_CODE
from tests.conftest import make_station_config
from tests.fakes.fake_stores import (
    FakeAuditLogStore,
    FakeClimBaselineStore,
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
        before = copy.deepcopy((self.observations.__dict__, self.audit.__dict__))
        try:
            return self._run_load(args, text)
        except lso.LoadRolledBack:
            # What the real transaction does on rollback.
            self.observations.__dict__.update(before[0])
            self.audit.__dict__.update(before[1])
            return lso.EXIT_REFUSED

    def _run_load(self, args: object, text: str) -> int:
        return lso.run_load(
            args,  # pyright: ignore[reportArgumentType]
            text=text,
            identity=SCOPED,
            tenants=self.tenants,
            stations=self.stations,
            observations=self.observations,
            audit=self.audit,
            baselines=FakeClimBaselineStore(),
            qc_rules=resolve_qc_rules()[0],
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
        assert "2 of 30 daily values usable (passed or unchecked)" in out
        assert "history-based check not run: no baseline yet" in out
        assert "QC rule version: 1.2-datum" in out

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

    def test_tenant_mismatch_message_does_not_reveal_foreign_ids(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        owner = TenantId(uuid4())
        station = _station(tenant_id=owner)
        Rig(station).run("water-level", ["--apply"])
        out = capsys.readouterr().out
        assert "--tenant" in out
        assert str(owner) not in out
        assert str(station.id) not in out

    def test_unknown_station_and_bad_csv_exit_2(self) -> None:
        assert Rig().run("water-level", []) == 2
        assert Rig(_station()).run("water-level", [], text="t,x\n1,2\n") == 2

    def test_absolute_looking_values_warn_and_strict_fails(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        text = "t,v\n2026-11-01 06:00,1481.0\n"
        allow = ["--allow-unchecked"]
        assert Rig(_station()).run("water-level", allow, text=text) == 0
        assert "level-masl" in capsys.readouterr().out
        assert Rig(_station()).run("water-level", ["--strict"], text=text) == 2

    def test_level_masl_kind_stores_values_as_is(self) -> None:
        station = _station()
        rig = Rig(station)
        text = "t,v\n2026-11-01 06:00,1481.0\n"
        assert (
            rig.run(
                "water-level",
                ["--value-kind", "level-masl", "--apply", "--allow-unchecked"],
                text,
            )
            == 0
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
        assert "0 of 5 daily values usable" in capsys.readouterr().out


class TestQcOnLoad:
    def test_apply_records_qc_status_and_rule_version(self) -> None:
        station = _station()
        rig = Rig(station)
        assert rig.run("water-level", ["--apply"]) == 0
        stored = rig.observations.fetch_observations(
            station.id, "water_level", ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)), NOW
        )
        assert {o.qc_rule_version for o in stored} == {"1.2-datum"}
        assert all(o.qc_status.value != "raw" for o in stored)

    def test_repeating_an_applied_load_says_there_is_nothing_new_to_check(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rig = Rig(_station())
        assert rig.run("water-level", ["--apply"]) == 0
        first = capsys.readouterr().out
        assert "QC: 0 passed" not in first
        assert rig.run("water-level", ["--apply"]) == 0
        second = capsys.readouterr().out
        assert "rows written or changed: 0" in second
        assert "no new readings to check" in second
        assert "0 passed" not in second

    def test_a_stale_check_is_not_reported_as_nothing_to_check(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report = Stage1QcReport(
            counts={
                key: 0
                for key in ("passed", "failed", "suspect", "unchecked", "unjudged")
            },
            rule_version="1.2-datum",
            zero_rule_groups=(),
            stale=True,
            baseline_ran=True,
            statuses={},
        )
        lso._print_qc(report, 0, argparse.Namespace(lookback_days=30))
        out = capsys.readouterr().out
        assert "no new readings to check" not in out
        assert "0 passed" in out

    def test_no_datum_is_refused_unless_allowed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rig = Rig(make_station_config(code="00001", network="research"))
        args = ["--value-kind", "level-masl", "--apply"]
        assert rig.run("water-level", args) == 3
        assert "has no datum" in capsys.readouterr().out
        assert rig.run("water-level", [*args, "--allow-unchecked"]) == 0

    def test_refused_apply_saves_nothing_and_keeps_refusing_until_allowed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        station = _station()
        rig = Rig(station)
        sparse = "t,v\n2026-11-01 06:00,2.0\n2026-11-08 06:00,2.5\n"
        window = (ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)), NOW)

        for _ in range(2):
            assert rig.run("water-level", ["--apply"], text=sparse) == 3
            out = capsys.readouterr().out
            assert "no quality rule applies" in out
            assert "NOTHING WAS SAVED" in out
            assert rig.audit.entries == []
            assert (
                rig.observations.fetch_observations(station.id, "water_level", *window)
                == []
            )

        assert rig.run("water-level", ["--apply", "--allow-unchecked"], sparse) == 0
        stored = rig.observations.fetch_observations(station.id, "water_level", *window)
        assert {o.qc_status.value for o in stored} == {"qc_unchecked"}
        assert len(rig.audit.entries) == 1

    def test_dry_run_refusal_does_not_claim_a_rollback(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rig = Rig(_station())
        sparse = "t,v\n2026-11-01 06:00,2.0\n2026-11-08 06:00,2.5\n"
        assert rig.run("water-level", [], text=sparse) == 3
        assert "NOTHING WAS SAVED" not in capsys.readouterr().out
