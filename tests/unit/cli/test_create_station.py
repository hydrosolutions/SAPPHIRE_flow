from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from sapphire_flow.cli import create_station as cs
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    AuditEventType,
    StationStatus,
    WaterLevelGaugeType,
)
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.tenant import DEFAULT_TENANT_CODE, DEFAULT_TENANT_ID
from tests.conftest import make_station_config
from tests.fakes.fake_stores import (
    FakeAuditLogStore,
    FakeStationStore,
    FakeTenantStore,
)

NOW = ensure_utc(datetime(2026, 11, 18, tzinfo=UTC))
FIXED_ID = UUID("00000000-0000-0000-0000-000000000042")
SCOPED = DeploymentIdentityConfig(
    writable_tenants=frozenset({DEFAULT_TENANT_CODE}), global_admin=False
)
ADMIN = DeploymentIdentityConfig(writable_tenants=frozenset(), global_admin=True)

BASE = [
    "--tenant", DEFAULT_TENANT_CODE, "--code", "00001", "--network", "research",
    "--name", "Trishuli", "--lon", "85.34", "--lat", "28.16",
    "--timezone", "Asia/Kathmandu", "--parameters", "water_level,discharge",
    "--gateway-name", "trishuli_00001",
]  # fmt: skip
STAFF = ["--gauge-type", "staff", "--datum-masl", "1480"]
BRIDGE = [
    "--gauge-type",
    "bridge-sensor",
    "--datum-masl",
    "1480",
    "--sensor-masl",
    "1490",
]


class Rig:
    def __init__(self) -> None:
        self.stations = FakeStationStore()
        self.tenants = FakeTenantStore()
        self.audit = FakeAuditLogStore()

    def run(
        self,
        extra: list[str],
        *,
        identity: DeploymentIdentityConfig = SCOPED,
        ask: object = None,
        base: list[str] | None = None,
    ) -> int:
        args = cs._parse_args((base or BASE) + extra)  # pyright: ignore[reportPrivateUsage]
        return cs.run_create(
            args,
            identity=identity,
            tenants=self.tenants,
            stations=self.stations,
            audit=self.audit,
            ask=ask,  # pyright: ignore[reportArgumentType]
            new_id=lambda: FIXED_ID,
            clock=lambda: NOW,
        )


@pytest.fixture
def rig() -> Rig:
    return Rig()


class TestDryRunAndApply:
    def test_dry_run_writes_neither_station_nor_audit(
        self, rig: Rig, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert rig.run(STAFF) == 0
        assert rig.stations.fetch_all_stations() == []
        assert rig.audit.entries == []
        out = capsys.readouterr().out
        assert "DRY RUN" in out and "'00001'" in out and "'research'" in out
        assert "trishuli_00001" in out

    def test_apply_stores_one_onboarding_station_and_one_audit_row(
        self, rig: Rig
    ) -> None:
        assert rig.run([*STAFF, "--apply"]) == 0
        (station,) = rig.stations.fetch_all_stations()
        assert station.station_status is StationStatus.ONBOARDING
        assert station.tenant_id == DEFAULT_TENANT_ID
        assert station.water_level_gauge_type is WaterLevelGaugeType.STAFF_OR_PRESSURE
        assert (station.water_level_datum_masl, station.water_level_unit) == (
            1480.0,
            "m",
        )
        (entry,) = rig.audit.entries
        assert entry.event_type is AuditEventType.STATION_ONBOARDED

    def test_bridge_sensor_stores_both_elevations(self, rig: Rig) -> None:
        assert rig.run([*BRIDGE, "--apply"]) == 0
        (station,) = rig.stations.fetch_all_stations()
        assert station.water_level_gauge_type is WaterLevelGaugeType.BRIDGE_SENSOR
        assert station.water_level_sensor_masl == 1490.0

    def test_staff_example_adds_reading_to_datum(
        self, rig: Rig, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rig.run(STAFF)
        assert "reading 2.0 m -> stored 1482.0 m a.s.l." in capsys.readouterr().out

    def test_bridge_example_subtracts_distance_from_sensor(
        self, rig: Rig, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rig.run(BRIDGE)
        out = capsys.readouterr().out
        assert (
            "reading 3.0 m, sensor 1490.0 -> stored 1487.0 m a.s.l., "
            "7.0 m over gauge zero 1480.0"
        ) in out


class TestRefusals:
    def test_unwritable_tenant_refused(self, rig: Rig) -> None:
        other = DeploymentIdentityConfig(
            writable_tenants=frozenset({"elsewhere"}), global_admin=False
        )
        rig.tenants.ensure_tenant(
            tenant_id=TenantId(uuid4()), code="elsewhere", name="E"
        )
        assert rig.run([*STAFF, "--apply"], identity=other) == 1
        assert rig.stations.fetch_all_stations() == []

    def test_unknown_tenant_refused(self, rig: Rig) -> None:
        assert rig.run([*STAFF], identity=ADMIN, base=_swap_tenant("nope")) == 1

    def test_existing_pair_refused_without_naming_other_tenant(self, rig: Rig) -> None:
        other = rig.tenants.ensure_tenant(
            tenant_id=TenantId(uuid4()), code="secret_org", name="S"
        )
        rig.stations.store_station(
            make_station_config(code="00001", network="research", tenant_id=other.id)
        )
        assert rig.run([*STAFF, "--apply"]) == 1
        assert len(rig.stations.fetch_all_stations()) == 1
        assert rig.audit.entries == []

    def test_existing_pair_names_owner_only_for_global_admin(self, rig: Rig) -> None:
        other = rig.tenants.ensure_tenant(
            tenant_id=TenantId(uuid4()), code="secret_org", name="S"
        )
        rig.stations.store_station(
            make_station_config(code="00001", network="research", tenant_id=other.id)
        )
        for reveal, expected in ((True, True), (False, False)):
            with pytest.raises(cs.StationExistsError) as info:
                cs.plan_station(
                    rig.stations,
                    rig.tenants,
                    _request(),
                    uuid4(),
                    reveal_owner=reveal,
                    new_id=uuid4,
                    clock=lambda: NOW,
                )
            assert ("secret_org" in str(info.value)) is expected

    @pytest.mark.parametrize(
        "override",
        [
            ["--lat", "95"],
            ["--timezone", "Mars/Olympus"],
            ["--parameters", "water_level,snow"],
            ["--gateway-name", "Bad Name"],
            ["--name", "  "],
        ],
    )
    def test_bad_inputs_refused(self, rig: Rig, override: list[str]) -> None:
        base = _override(BASE, override)
        assert rig.run([*STAFF, "--apply"], base=base) == 1
        assert rig.stations.fetch_all_stations() == []

    @pytest.mark.parametrize(
        "extra",
        [
            ["--gauge-type", "staff", "--datum-masl", "99999"],
            ["--gauge-type", "staff", "--datum-masl", "nan"],
            ["--datum-masl", "1480"],
            ["--gauge-type", "staff"],
            ["--gauge-type", "staff", "--datum-masl", "1480", "--sensor-masl", "1490"],
            ["--gauge-type", "bridge-sensor", "--datum-masl", "1480"],
            [*BRIDGE[:-1], "1470"],
            ["--gauge-type", "staff", "--datum-masl", "1480", "--datum-unknown"],
        ],
    )
    def test_gauge_combinations_refused(self, rig: Rig, extra: list[str]) -> None:
        assert rig.run([*extra, "--apply"]) == 1
        assert rig.stations.fetch_all_stations() == []

    def test_gauge_rejected_without_water_level(self, rig: Rig) -> None:
        base = _override(BASE, ["--parameters", "discharge"])
        assert rig.run(STAFF, base=base) == 1

    def test_missing_datum_non_interactive_refused(self, rig: Rig) -> None:
        assert rig.run([]) == 1

    def test_datum_unknown_allowed_with_warning_and_no_unit(self, rig: Rig) -> None:
        assert rig.run(["--datum-unknown", "--apply"]) == 0
        (station,) = rig.stations.fetch_all_stations()
        assert station.water_level_gauge_type is None
        assert station.water_level_datum_masl is None
        assert station.water_level_unit is None

    def test_discharge_only_needs_no_gauge(self, rig: Rig) -> None:
        base = _override(BASE, ["--parameters", "discharge"])
        assert rig.run(["--apply"], base=base) == 0


class TestPrompt:
    def test_prompt_path_stores_answers(self, rig: Rig) -> None:
        answers = iter(["2", "1480", "1490", "y"])
        assert rig.run(["--apply"], ask=lambda _p: next(answers)) == 0
        (station,) = rig.stations.fetch_all_stations()
        assert station.water_level_gauge_type is WaterLevelGaugeType.BRIDGE_SENSOR
        assert station.water_level_datum_masl == 1480.0
        assert station.water_level_sensor_masl == 1490.0

    def test_empty_gauge_answer_means_unknown(self, rig: Rig) -> None:
        assert rig.run(["--apply"], ask=lambda _p: "") == 0
        (station,) = rig.stations.fetch_all_stations()
        assert station.water_level_datum_masl is None

    def test_gauge_type_is_asked_first(self, rig: Rig) -> None:
        prompts: list[str] = []

        def ask(prompt: str) -> str:
            prompts.append(prompt)
            return ""

        rig.run([], ask=ask)
        assert prompts[0].startswith("Gauge type")


def _request() -> cs.StationRequest:
    return cs.parse_station_request(cs._parse_args(BASE + STAFF))  # pyright: ignore[reportPrivateUsage]


def _override(base: list[str], override: list[str]) -> list[str]:
    out = list(base)
    out[out.index(override[0]) + 1] = override[1]
    return out


def _swap_tenant(code: str) -> list[str]:
    return _override(BASE, ["--tenant", code])


class TestConfirmation:
    def test_example_shown_before_save_and_decline_writes_nothing(
        self, rig: Rig, capsys: pytest.CaptureFixture[str]
    ) -> None:
        answers = iter(["1", "1480", "n"])
        assert rig.run(["--apply"], ask=lambda _p: next(answers)) == 1
        assert "stored 1482.0" in capsys.readouterr().out
        assert rig.stations.fetch_all_stations() == []
        assert rig.audit.entries == []

    def test_scripted_flags_do_not_ask_for_confirmation(self, rig: Rig) -> None:
        def ask(_p: str) -> str:
            raise AssertionError("must not prompt")

        assert rig.run([*STAFF, "--apply"], ask=ask) == 0


class TestOwnerHiddenOnFullPath:
    def test_scoped_admin_output_never_names_other_tenant(self, rig: Rig) -> None:
        from structlog.testing import capture_logs

        other = rig.tenants.ensure_tenant(
            tenant_id=TenantId(uuid4()), code="secret_org", name="S"
        )
        rig.stations.store_station(
            make_station_config(code="00001", network="research", tenant_id=other.id)
        )
        with capture_logs() as logs:
            assert rig.run([*STAFF, "--apply"]) == 1
        text = " ".join(str(entry) for entry in logs)
        assert "already exists" in text
        assert "secret_org" not in text
