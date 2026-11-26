from __future__ import annotations

import dataclasses
import os
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from sapphire_flow.cli import ingest_pause as cli
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.services.write_principal import TENANT_REFUSAL_FOR_CLI
from sapphire_flow.types.enums import AuditEventType
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.tenant import DEFAULT_TENANT_CODE
from tests.conftest import make_station_config
from tests.fakes.fake_stores import (
    FakeAuditLogStore,
    FakeStationStore,
    FakeTenantStore,
)
from tests.unit.services.test_station_readiness import NOW

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.types.station import StationConfig

CODE = "TEST-001"
BASE = [
    "--tenant", DEFAULT_TENANT_CODE, "--network", "bafu", "--code", CODE,
    "--operator", "bea", "--reason", "gauge destroyed",
]  # fmt: skip


def _world(
    *, paused: bool = False, tenant_id: TenantId | None = None
) -> tuple[FakeStationStore, StationConfig]:
    station = make_station_config(code=CODE, network="bafu")
    station = dataclasses.replace(station, ingest_paused=paused)
    if tenant_id is not None:
        station = dataclasses.replace(station, tenant_id=tenant_id)
    stations = FakeStationStore()
    stations.store_station(station)
    return stations, station


def _run(
    stations: FakeStationStore,
    argv: list[str],
    audit: FakeAuditLogStore,
) -> int:
    args = cli._build_parser().parse_args(argv)  # pyright: ignore[reportPrivateUsage]
    return cli.run_ingest_pause(
        args,
        identity=DeploymentIdentityConfig(
            writable_tenants=frozenset({DEFAULT_TENANT_CODE}), global_admin=False
        ),
        tenants=FakeTenantStore(),
        stations=stations,
        audit=audit,
        clock=lambda: NOW,
    )


def _paused(stations: FakeStationStore, station: StationConfig) -> bool:
    current = stations.fetch_station(station.id)
    assert current is not None
    return current.ingest_paused


class TestParser:
    @pytest.mark.parametrize("extra", [[], ["--pause", "--resume"]])
    def test_exactly_one_of_pause_resume(self, extra: list[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            cli._build_parser().parse_args([*BASE, *extra])  # pyright: ignore[reportPrivateUsage]
        assert exc.value.code == 2


class TestDryRun:
    def test_writes_nothing(self, capsys: pytest.CaptureFixture[str]) -> None:
        stations, station = _world()
        audit = FakeAuditLogStore()
        assert _run(stations, [*BASE, "--pause"], audit) == 0
        assert not _paused(stations, station)
        assert audit.entries == []
        assert "NO CHANGE" not in capsys.readouterr().out

    def test_no_change_when_already_in_state(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        stations, _ = _world(paused=True)
        assert _run(stations, [*BASE, "--pause"], FakeAuditLogStore()) == 0
        assert "NO CHANGE" in capsys.readouterr().out

    def test_wrong_tenant_exits_3_without_writing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        stations, station = _world(tenant_id=TenantId(uuid4()))
        audit = FakeAuditLogStore()
        assert _run(stations, [*BASE, "--pause"], audit) == 3
        assert TENANT_REFUSAL_FOR_CLI in capsys.readouterr().out
        assert not _paused(stations, station)
        assert audit.entries == []


class TestApply:
    def test_pause_writes_flag_and_one_audit_row(self) -> None:
        stations, station = _world()
        audit = FakeAuditLogStore()
        assert _run(stations, [*BASE, "--pause", "--apply"], audit) == 0
        assert _paused(stations, station)
        (entry,) = audit.entries
        assert entry.event_type is AuditEventType.STATION_INGEST_PAUSE_CHANGED
        assert entry.detail["from"] is False
        assert entry.detail["to"] is True
        assert entry.detail["reason"] == "gauge destroyed"
        assert entry.detail["operator"] == "bea"

    def test_resume(self) -> None:
        stations, station = _world(paused=True)
        audit = FakeAuditLogStore()
        assert _run(stations, [*BASE, "--resume", "--apply"], audit) == 0
        assert not _paused(stations, station)
        (entry,) = audit.entries
        assert entry.detail["from"] is True
        assert entry.detail["to"] is False

    def test_already_in_state_is_no_change_and_no_audit_row(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        stations, station = _world(paused=True)
        audit = FakeAuditLogStore()
        assert _run(stations, [*BASE, "--pause", "--apply"], audit) == 0
        assert _paused(stations, station)
        assert audit.entries == []
        assert "NO CHANGE" in capsys.readouterr().out

    def test_allowed_tenant_other_stations_exits_3_with_one_rejection_row(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        stations, station = _world(tenant_id=TenantId(uuid4()))
        audit = FakeAuditLogStore()
        assert _run(stations, [*BASE, "--pause", "--apply"], audit) == 3
        assert TENANT_REFUSAL_FOR_CLI in capsys.readouterr().out
        assert not _paused(stations, station)
        (entry,) = audit.entries
        assert entry.detail["outcome"] == "rejected_tenant_mismatch"
        assert "from" not in entry.detail

    def test_unknown_station_exits_2(self) -> None:
        stations, _ = _world()
        argv = [*BASE, "--pause", "--apply"]
        argv[argv.index(CODE)] = "nope"
        assert _run(stations, argv, FakeAuditLogStore()) == 2


class TestInputChecks:
    @pytest.mark.parametrize("apply", [[], ["--apply"]])
    @pytest.mark.parametrize("blank", ["", "   "])
    @pytest.mark.parametrize("flag", ["--reason", "--operator"])
    def test_blank_reason_or_operator_exits_2(
        self, flag: str, blank: str, apply: list[str]
    ) -> None:
        stations, station = _world()
        audit = FakeAuditLogStore()
        argv = [*BASE, "--pause", *apply]
        argv[argv.index(flag) + 1] = blank
        assert _run(stations, argv, audit) == 2
        assert not _paused(stations, station)
        assert audit.entries == []


class TestMain:
    def test_main_sets_config_env_without_leaking(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        config = tmp_path / "config.toml"
        config.write_text("[other]\n")  # no [deployment]: main stops at the identity
        monkeypatch.setenv("SAPPHIRE_CONFIG", "/placeholder")
        assert cli.main([*BASE, "--pause", "--config", str(config)]) == 2
        assert os.environ["SAPPHIRE_CONFIG"] == str(config)
