from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sapphire_flow.cli import station_readiness as cli
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
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


def _run(argv: list[str], *, tenant_id: object = DEFAULT_TENANT_ID) -> int:
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
        model=None,
        scope=None,
        clock=lambda: NOW,
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
        assert set(data) == {"station", "checks", "failed", "passed"}
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
