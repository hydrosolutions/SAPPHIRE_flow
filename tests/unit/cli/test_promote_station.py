from __future__ import annotations

import dataclasses
import os
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from sapphire_flow.cli import promote_station as cli
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.services.station_readiness import ReadinessStores
from sapphire_flow.types.enums import StationStatus
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.tenant import DEFAULT_TENANT_CODE
from tests.fakes.fake_stores import FakeAuditLogStore, FakeTenantStore
from tests.unit.services.test_station_promotion import HRU, _ready
from tests.unit.services.test_station_readiness import (
    NOW,
    _model,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.unit.services.test_station_readiness import World

CODE = "TEST-001"
BASE = [
    "--tenant", DEFAULT_TENANT_CODE, "--network", "research", "--code", CODE,
    "--operator", "bea",
]  # fmt: skip


def _identity(allowed: bool) -> DeploymentIdentityConfig:
    return DeploymentIdentityConfig(
        writable_tenants=frozenset({DEFAULT_TENANT_CODE}),
        global_admin=False,
        promotion_allowed=allowed,
    )


def _run(
    world: World, argv: list[str], *, allowed: bool, audit: FakeAuditLogStore
) -> int:
    args = cli._build_parser().parse_args(argv)  # pyright: ignore[reportPrivateUsage]
    return cli.run_promote(
        args,
        identity=_identity(allowed),
        tenants=FakeTenantStore(),
        stores=ReadinessStores(
            stations=world.stations,
            basins=world.basins,
            observations=world.observations,
            forcing=world.forcing,
            ratings=world.ratings,
            groups=world.groups,
            polygons=world.polygons,
        ),
        audit=audit,
        resolve_model=lambda _m: _model(),  # type: ignore[return-value]
        scope=(HRU, HRU),
        clock=lambda: NOW,
    )


def _status(world: World) -> StationStatus:
    station = world.stations.fetch_station_by_code(world.station.code, "research")
    assert station is not None
    return station.station_status


class TestParser:
    def test_operator_is_required(self) -> None:
        with pytest.raises(SystemExit):
            cli._build_parser().parse_args(  # pyright: ignore[reportPrivateUsage]
                ["--tenant", "t", "--network", "n", "--code", "c"]
            )

    @pytest.mark.parametrize("flag", ["--dry-run", "--confirm-shared-host", "--model"])
    def test_has_no_dry_run_confirm_or_model_flag(self, flag: str) -> None:
        with pytest.raises(SystemExit):
            cli._build_parser().parse_args([*BASE, flag, "x"])  # pyright: ignore[reportPrivateUsage]


class TestRun:
    def test_dry_run_writes_nothing_even_when_not_allowed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        world, audit = _ready(), FakeAuditLogStore()
        assert _run(world, BASE, allowed=False, audit=audit) == 0
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "--apply would be refused" in out
        assert _status(world) is StationStatus.ONBOARDING
        assert audit.entries == []

    def test_apply_refused_without_promotion_allowed(self) -> None:
        world, audit = _ready(), FakeAuditLogStore()
        assert _run(world, [*BASE, "--apply"], allowed=False, audit=audit) == 3
        assert _status(world) is StationStatus.ONBOARDING
        assert audit.entries == []

    def test_apply_promotes_when_allowed(self) -> None:
        world, audit = _ready(), FakeAuditLogStore()
        assert _run(world, [*BASE, "--apply"], allowed=True, audit=audit) == 0
        assert _status(world) is StationStatus.OPERATIONAL
        assert len(audit.entries) == 1

    def test_blocking_condition_exits_3_and_lists_it(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        world, audit = _ready(), FakeAuditLogStore()
        world.polygons.rows.clear()
        assert _run(world, [*BASE, "--apply"], allowed=True, audit=audit) == 3
        assert "FAIL  5. weather_bindings" in capsys.readouterr().out
        assert audit.entries == []

    def test_dry_run_and_apply_agree_on_a_blocked_station(self) -> None:
        world = _ready()
        world.polygons.rows.clear()
        for argv in (BASE, [*BASE, "--apply"]):
            assert _run(world, argv, allowed=True, audit=FakeAuditLogStore()) == 3

    def test_already_operational_exits_0(self) -> None:
        world, audit = _ready(), FakeAuditLogStore()
        _run(world, [*BASE, "--apply"], allowed=True, audit=audit)
        assert _run(world, [*BASE, "--apply"], allowed=True, audit=audit) == 0
        assert len(audit.entries) == 1

    def test_unknown_station_exits_2(self) -> None:
        argv = [*BASE[:5], "nope", *BASE[6:]]
        assert _run(_ready(), argv, allowed=True, audit=FakeAuditLogStore()) == 2

    def test_other_tenant_exits_3(self) -> None:
        world = _ready()
        world.stations.update_station(
            dataclasses.replace(world.station, tenant_id=TenantId(uuid4()))
        )
        assert _run(world, BASE, allowed=True, audit=FakeAuditLogStore()) == 3


class TestMain:
    def test_main_sets_config_env_without_leaking(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        config = tmp_path / "config.toml"
        config.write_text("[other]\n")  # no [deployment]: main stops at the identity
        monkeypatch.setenv("SAPPHIRE_CONFIG", "/placeholder")
        assert cli.main([*BASE, "--config", str(config)]) == 2
        assert os.environ["SAPPHIRE_CONFIG"] == str(config)
