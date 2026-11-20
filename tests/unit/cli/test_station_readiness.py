from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sapphire_flow.cli import station_readiness as cli
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.station_readiness import ReadinessStores
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.station_readiness import (
    CheckStatus,
    ReadinessCheck,
    StationReadinessReport,
)
from sapphire_flow.types.tenant import DEFAULT_TENANT_CODE, DEFAULT_TENANT_ID
from tests.conftest import make_station_config
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeHistoricalForcingStore,
    FakeObservationStore,
    FakeRatingCurveStore,
    FakeStationGroupStore,
    FakeStationStore,
    FakeTenantStore,
)
from tests.unit.services.test_station_readiness import FakePolygons

if TYPE_CHECKING:
    import pytest

NOW = ensure_utc(datetime(2026, 11, 18, tzinfo=UTC))
IDENTITY = DeploymentIdentityConfig(
    writable_tenants=frozenset({DEFAULT_TENANT_CODE}), global_admin=False
)


def _report() -> StationReadinessReport:
    return StationReadinessReport(
        station_id=StationId(uuid4()),
        code="00001",
        network="research",
        checks=(
            ReadinessCheck(name="datum", status=CheckStatus.PASS, detail="d=1"),
            ReadinessCheck(name="basin", status=CheckStatus.FAIL, detail="none"),
            ReadinessCheck(
                name="statics", status=CheckStatus.NOT_APPLICABLE, detail="no model"
            ),
        ),
    )


def _run(
    argv: list[str],
    *,
    tenant_id: object = DEFAULT_TENANT_ID,
    model: object | None = None,
    gateway: object | None = None,
) -> int:
    stations = FakeStationStore()
    stations.store_station(
        make_station_config(code="00001", network="research", tenant_id=tenant_id)  # type: ignore[arg-type]
    )
    tenants = FakeTenantStore()
    args = cli._build_parser().parse_args(argv)  # pyright: ignore[reportPrivateUsage]
    return cli.run_readiness(
        args,
        identity=IDENTITY,
        tenants=tenants,
        stores=ReadinessStores(
            stations=stations,
            basins=FakeBasinStore(),
            observations=FakeObservationStore(),
            forcing=FakeHistoricalForcingStore(),
            ratings=FakeRatingCurveStore(),
            groups=FakeStationGroupStore(),
            polygons=FakePolygons(),
        ),
        model=model,  # type: ignore[arg-type]
        scope=None,
        clock=lambda: NOW,
        gateway=gateway,  # type: ignore[arg-type]
    )


BASE = ["--tenant", DEFAULT_TENANT_CODE, "--network", "research", "--code", "00001"]


class TestRender:
    def test_text_has_status_lines_and_last_line_counts(self) -> None:
        lines = cli.render_text(_report()).splitlines()
        assert "PASS  datum  d=1" in lines
        assert "NOT_APPLICABLE  statics  no model" in lines
        assert lines[-1] == "1 checks failed, 1 passed"

    def test_json_has_the_stable_shape(self) -> None:
        data = json.loads(cli.render_json(_report()))
        assert set(data) == {
            "station",
            "checks",
            "supplementary",
            "failed",
            "passed",
        }
        assert data["checks"][1] == {
            "name": "basin",
            "status": "FAIL",
            "detail": "none",
        }
        assert (data["failed"], data["passed"]) == (1, 1)


class TestRun:
    def test_reports_with_exit_zero_even_when_checks_fail(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _run(BASE) == 0
        assert capsys.readouterr().out.splitlines()[-1].endswith(" passed")

    def test_json_flag_prints_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _run([*BASE, "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["station"]["code"] == "00001"

    def test_unknown_station_exits_2(self) -> None:
        assert _run([*BASE[:-1], "99999"]) == 2

    def test_station_of_another_tenant_exits_2(self) -> None:
        from sapphire_flow.types.ids import TenantId

        assert _run(BASE, tenant_id=TenantId(uuid4())) == 2


class TestOptionalSections:
    def _with_extra(self) -> StationReadinessReport:
        base = _report()
        return dataclasses.replace(
            base,
            supplementary=(
                ReadinessCheck(
                    name="gateway_data", status=CheckStatus.UNKNOWN, detail="down"
                ),
            ),
        )

    def test_text_shows_extra_section_before_an_unchanged_last_line(self) -> None:
        lines = cli.render_text(self._with_extra()).splitlines()
        assert "UNKNOWN  gateway_data  down" in lines
        assert lines.index("UNKNOWN  gateway_data  down") < len(lines) - 1
        assert lines[-1] == "1 checks failed, 1 passed"

    def test_json_lists_extra_section_outside_the_counts(self) -> None:
        data = json.loads(cli.render_json(self._with_extra()))
        assert data["supplementary"][0]["status"] == "UNKNOWN"
        assert (data["failed"], data["passed"]) == (1, 1)

    def test_gateway_is_not_called_without_the_flag(self) -> None:
        def boom() -> object:
            raise AssertionError("gateway must stay off by default")

        assert _run(BASE, gateway=boom) == 0

    def test_gateway_flag_adds_the_section(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def no_key() -> object:
            raise ConfigurationError("no key")

        assert _run([*BASE, "--check-gateway"], gateway=no_key) == 0
        assert "UNKNOWN  gateway_data" in capsys.readouterr().out

    def test_training_window_arguments_must_come_together(self) -> None:
        assert _run([*BASE, "--start", "2026-01-01"]) == 2

    def test_training_window_needs_a_model(self) -> None:
        argv = [*BASE, "--start", "2026-01-01", "--end", "2026-02-01"]
        assert _run([*argv, "--time-step-hours", "24"]) == 2

    def test_unsupported_time_step_is_a_usage_error(self) -> None:
        argv = [*BASE, "--start", "2026-01-01", "--end", "2026-02-01"]
        assert (
            _run([*argv, "--time-step-hours", "6"], model=FakeStationForecastModel())
            == 2
        )

    def test_training_section_is_added_with_a_model(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = [*BASE, "--start", "2026-01-01", "--end", "2026-02-01"]
        code = _run(
            [*argv, "--time-step-hours", "24"], model=FakeStationForecastModel()
        )
        assert code == 0
        assert "FAIL  training_samples" in capsys.readouterr().out
