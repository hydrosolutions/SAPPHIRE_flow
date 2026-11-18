from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.cli import create_station as cs
from sapphire_flow.cli import load_station_observations as lso
from sapphire_flow.config.deployment_identity import (
    DeploymentIdentityConfig,
    load_deployment_identity_config,
)
from sapphire_flow.config.qc_rules import resolve_qc_rules
from sapphire_flow.services.qc import Stage1QualityChecker
from sapphire_flow.services.qc_datum import obs_qc_rule_version
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.clim_baseline_store import PgClimBaselineStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.ids import StationId, TenantId

NOW = ensure_utc(datetime(2026, 11, 18, tzinfo=UTC))
IDENTITY = DeploymentIdentityConfig(
    writable_tenants=frozenset({"sandbox"}), global_admin=False
)
FIXTURES = Path(__file__).parents[2] / "fixtures" / "synthetic_station"
EPOCH = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))


def _create(conn: sa.Connection, argv: list[str]) -> None:
    code = cs.run_create(
        cs._parse_args([*argv, "--tenant", "sandbox", "--apply"]),  # pyright: ignore[reportPrivateUsage]
        identity=IDENTITY,
        tenants=PgTenantStore(conn),
        stations=PgStationStore(conn),
        audit=PgAuditLogStore(conn),
        ask=None,
        new_id=uuid4,
        clock=lambda: NOW,
    )
    assert code == 0


def _create_scenario_station(conn: sa.Connection) -> StationId:
    scenario = json.loads((FIXTURES / "scenario.json").read_text())
    _create(
        conn,
        [
            arg
            for k, v in scenario.items()
            for arg in (f"--{k.replace('_', '-')}", str(v))
        ],
    )
    station = PgStationStore(conn).fetch_station_by_code("SYN001", "synthetic")
    assert station is not None
    return station.id


def _load(
    conn: sa.Connection,
    command: str,
    text: str,
    code: str,
    network: str,
    extra: list[str],
) -> int:
    args = lso._parse_args(  # pyright: ignore[reportPrivateUsage]
        [
            command,
            "--tenant",
            "sandbox",
            "--station-code",
            code,
            "--network",
            network,
            "--csv",
            "x.csv",
            "--time-column",
            "time",
            "--value-column",
            "value",
            "--apply",
            *extra,
        ]
    )
    return lso.run_load(
        args,
        text=text,
        identity=IDENTITY,
        tenants=PgTenantStore(conn),
        stations=PgStationStore(conn),
        observations=PgObservationStore(conn),
        audit=PgAuditLogStore(conn),
        baselines=PgClimBaselineStore(conn),
        qc_rules=resolve_qc_rules()[0],
        clock=lambda: NOW,
    )


def _stored(
    conn: sa.Connection, station_id: StationId, parameter: str
) -> list[tuple[str, str | None, QcStatus]]:
    return [
        (o.timestamp.isoformat(), o.qc_rule_version, o.qc_status)
        for o in PgObservationStore(conn).fetch_observations(
            station_id, parameter, EPOCH, NOW
        )
    ]


def _audit_rows(conn: sa.Connection, station_id: StationId) -> int:
    return conn.execute(
        sa.text(
            "SELECT count(*) FROM audit_log WHERE target_id = :i "
            "AND event_type = 'observations_loaded'"
        ),
        {"i": str(station_id)},
    ).scalar_one()


def _ensure_tenant(conn: sa.Connection) -> None:
    PgTenantStore(conn).ensure_tenant(
        tenant_id=TenantId(uuid4()), code="sandbox", name="Sandbox"
    )


@pytest.mark.parametrize(
    ("name", "count", "usable", "extra"),
    [
        ("discharge_hourly.csv", 72, 3, ["--zone", "UTC"]),
        ("discharge_daily.csv", 90, 30, []),
    ],
)
class TestSyntheticDischargeScenario:
    def test_load_stores_qc_clean_rows_with_one_audit_row_per_load(
        self,
        db_connection: sa.Connection,
        capsys: pytest.CaptureFixture[str],
        name: str,
        count: int,
        usable: int,
        extra: list[str],
    ) -> None:
        _ensure_tenant(db_connection)
        station_id = _create_scenario_station(db_connection)
        text = (FIXTURES / name).read_text()

        assert (
            _load(db_connection, "discharge", text, "SYN001", "synthetic", extra) == 0
        )
        rows = _stored(db_connection, station_id, "discharge")
        assert len(rows) == count
        assert {v for _, v, _ in rows} == {obs_qc_rule_version("discharge", None)}
        assert {v for _, v, _ in rows} == {"1.2"}
        # Observed from the real run: every reading judged by some rule and passed.
        assert {s for _, _, s in rows} == {QcStatus.QC_PASSED}
        assert _audit_rows(db_connection, station_id) == 1
        out = capsys.readouterr().out
        assert "history-based check not run: no baseline yet" in out
        assert f"{usable} of 30 daily values usable (passed or unchecked)" in out

        assert (
            _load(db_connection, "discharge", text, "SYN001", "synthetic", extra) == 0
        )
        assert "rows written or changed: 0" in capsys.readouterr().out
        assert _stored(db_connection, station_id, "discharge") == rows
        assert _audit_rows(db_connection, station_id) == 2


class TestWaterLevelWithDatum:
    CODE, NETWORK = "WL001", "synthetic"

    def _series(self, base: float) -> str:
        start = datetime(2025, 1, 1, tzinfo=UTC)
        lines = [
            f"{(start + timedelta(hours=i)).isoformat()},{base + 0.01 * i:.2f}"
            for i in range(48)
        ]
        return "time,value\n" + "\n".join(lines) + "\n"

    def _make(self, conn: sa.Connection) -> StationId:
        _ensure_tenant(conn)
        _create(
            conn,
            [
                "--code",
                self.CODE,
                "--network",
                self.NETWORK,
                "--name",
                "WL",
                "--lon",
                "1",
                "--lat",
                "1",
                "--timezone",
                "UTC",
                "--parameters",
                "water_level",
                "--gateway-name",
                "wl001",
                "--gauge-type",
                "staff",
                "--datum-masl",
                "1480",
            ],
        )
        station = PgStationStore(conn).fetch_station_by_code(self.CODE, self.NETWORK)
        assert station is not None
        return station.id

    def test_gauge_heights_get_datum_version_and_pass_range_check(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = self._make(db_connection)
        code = _load(
            db_connection,
            "water-level",
            self._series(2.0),
            self.CODE,
            self.NETWORK,
            ["--zone", "UTC"],
        )
        rows = _stored(db_connection, station_id, "water_level")
        assert code == 0
        assert {v for _, v, _ in rows} == {"1.2-datum"}
        assert "1.2-datum-skip" not in {v for _, v, _ in rows}
        assert QcStatus.QC_FAILED not in {s for _, _, s in rows}

    def test_same_series_stored_without_the_shift_fails_range_check(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = self._make(db_connection)
        _load(
            db_connection,
            "water-level",
            self._series(2.0),
            self.CODE,
            self.NETWORK,
            ["--zone", "UTC", "--value-kind", "level-masl"],
        )
        rows = _stored(db_connection, station_id, "water_level")
        assert {s for _, _, s in rows} == {QcStatus.QC_FAILED}


class TestRefusedLoadIsRolledBack:
    def test_zero_rule_refusal_leaves_no_rows_and_no_audit_row(
        self, db_connection: sa.Connection
    ) -> None:
        _ensure_tenant(db_connection)
        station_id = _create_scenario_station(db_connection)
        sparse = "time,value\n2025-01-01,20.0\n2025-01-08,21.0\n"

        def attempt(extra: list[str]) -> int:
            try:
                with db_connection.begin_nested():
                    return _load(
                        db_connection, "discharge", sparse, "SYN001", "synthetic", extra
                    )
            except lso.LoadRolledBack:
                return 3

        assert attempt([]) == 3
        assert _stored(db_connection, station_id, "discharge") == []
        assert _audit_rows(db_connection, station_id) == 0
        assert attempt([]) == 3

        assert attempt(["--allow-unchecked"]) == 0
        rows = _stored(db_connection, station_id, "discharge")
        assert {s for _, _, s in rows} == {QcStatus.QC_UNCHECKED}
        assert _audit_rows(db_connection, station_id) == 1


class TestDeploymentSmokeFixtureWithItsPinnedRules:
    SMOKE = files("sapphire_flow").joinpath("data/deployment_smoke")

    def _config(self, tmp_path: Path) -> Path:
        path = tmp_path / "smoke.toml"
        path.write_text(
            self.SMOKE.joinpath("qc-rules.toml").read_text()
            + '\n[deployment]\nwritable_tenants = ["smoke"]\n'
        )
        return path

    def _run(
        self,
        conn: sa.Connection,
        config: Path,
        csv_text: str,
        extra: list[str],
    ) -> int:
        identity = load_deployment_identity_config(config)
        args = lso._parse_args(  # pyright: ignore[reportPrivateUsage]
            [
                "discharge", "--config", str(config), "--tenant", "smoke",
                "--station-code", "SYN001", "--network", "synthetic",
                "--csv", "x.csv", "--time-column", "time", "--value-column", "value",
                "--zone", "UTC", *extra,
            ]
        )  # fmt: skip
        return lso.run_load(
            args,
            text=csv_text,
            identity=identity,
            tenants=PgTenantStore(conn),
            stations=PgStationStore(conn),
            observations=PgObservationStore(conn),
            audit=PgAuditLogStore(conn),
            baselines=PgClimBaselineStore(conn),
            qc_rules=resolve_qc_rules()[0],
            clock=lambda: NOW,
        )

    def test_dry_run_then_apply_with_one_config_file(
        self,
        db_connection: sa.Connection,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        config = self._config(tmp_path)
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))
        identity = load_deployment_identity_config(config)
        PgTenantStore(db_connection).ensure_tenant(
            tenant_id=TenantId(uuid4()), code="smoke", name="Smoke"
        )
        scenario = json.loads(self.SMOKE.joinpath("scenario.json").read_text())
        created = cs.run_create(
            cs._parse_args(  # pyright: ignore[reportPrivateUsage]
                [
                    "--config",
                    str(config),
                    "--tenant",
                    "smoke",
                    "--apply",
                    *[
                        a
                        for k, v in scenario.items()
                        for a in (f"--{k.replace('_', '-')}", str(v))
                    ],
                ]
            ),  # fmt: skip
            identity=identity,
            tenants=PgTenantStore(db_connection),
            stations=PgStationStore(db_connection),
            audit=PgAuditLogStore(db_connection),
            ask=None,
            new_id=uuid4,
            clock=lambda: NOW,
        )
        assert created == 0
        station = PgStationStore(db_connection).fetch_station_by_code(
            "SYN001", "synthetic"
        )
        assert station is not None
        text = self.SMOKE.joinpath("discharge_hourly.csv").read_text()
        capsys.readouterr()

        assert self._run(db_connection, config, text, []) == 0
        assert "QC rule version: 1.2" in capsys.readouterr().out
        assert _stored(db_connection, station.id, "discharge") == []
        assert _audit_rows(db_connection, station.id) == 0

        assert self._run(db_connection, config, text, ["--apply"]) == 0
        out = capsys.readouterr().out
        rows = _stored(db_connection, station.id, "discharge")
        assert len(rows) == 72
        assert {s for _, _, s in rows} == {QcStatus.QC_PASSED}
        assert {v for _, v, _ in rows} == {"1.2"}
        assert "3 of 30 daily values usable (passed or unchecked)" in out
        assert "history-based check not run: no baseline yet" in out
        stamps = {t for t, _, _ in rows}
        assert {
            f"2025-01-02T{hour}:00:00+00:00" for hour in ("21", "22", "23")
        } <= stamps
        assert _audit_rows(db_connection, station.id) == 1

        rules = resolve_qc_rules()[0]
        assert rules.version == "deployment-smoke-hourly-1"
        stored = PgObservationStore(db_connection).fetch_observations(
            station.id, "discharge", EPOCH, NOW
        )
        judged = {
            rule_id: len(
                Stage1QualityChecker()
                .check_with_coverage(
                    stored,
                    rules,
                    overrides=[],
                    baselines=[],
                    station_networks={station.id: "synthetic"},
                    skipped_rule_ids=frozenset(
                        r.rule_id for r in rules.rules if r.rule_id != rule_id
                    ),
                )
                .judged
            )
            for rule_id in ("range_check", "rate_of_change", "spike", "gross_outlier")
        }
        assert judged == {
            "range_check": 72,
            "rate_of_change": 71,
            "spike": 70,
            "gross_outlier": 0,
        }
