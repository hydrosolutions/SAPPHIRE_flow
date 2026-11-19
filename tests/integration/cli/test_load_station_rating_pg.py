from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.cli import load_station_observations as lso
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.config.qc_rules import resolve_qc_rules
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.clim_baseline_store import PgClimBaselineStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import WaterLevelGaugeType
from sapphire_flow.types.ids import StationId, TenantId
from tests.conftest import make_station_config

NOW = ensure_utc(datetime(2026, 11, 18, tzinfo=UTC))
EPOCH = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
IDENTITY = DeploymentIdentityConfig(
    writable_tenants=frozenset({"sandbox"}), global_admin=False
)
STATION_ARGS = ["--tenant", "sandbox", "--network", "research"]
DAYS = 40


def _levels_csv(base: float, *, step: float) -> str:
    start = datetime(2026, 10, 1, 6, tzinfo=UTC)
    rows = [
        f"{(start + timedelta(days=i)).isoformat()},{base + step * (i % 5):.3f}"
        for i in range(DAYS)
    ]
    return "time,value\n" + "\n".join(rows) + "\n"


def _stores(conn: sa.Connection) -> dict[str, object]:
    return {
        "identity": IDENTITY,
        "tenants": PgTenantStore(conn),
        "stations": PgStationStore(conn),
        "observations": PgObservationStore(conn),
        "audit": PgAuditLogStore(conn),
        "clock": lambda: NOW,
    }


def _load_levels(conn: sa.Connection, code: str, text: str) -> None:
    args = lso._parse_args(  # pyright: ignore[reportPrivateUsage]
        [
            "water-level", *STATION_ARGS, "--station-code", code, "--csv", "x.csv",
            "--time-column", "time", "--value-column", "value", "--apply",
            "--allow-unchecked",
        ]
    )  # fmt: skip
    assert (
        lso.run_load(
            args,
            text=text,
            baselines=PgClimBaselineStore(conn),
            qc_rules=resolve_qc_rules()[0],
            **_stores(conn),  # pyright: ignore[reportArgumentType]
        )
        == 0
    )


def _rating(conn: sa.Connection, code: str, table: str, extra: list[str]) -> int:
    args = lso._parse_args(  # pyright: ignore[reportPrivateUsage]
        [
            "rating", *STATION_ARGS, "--station-code", code, "--csv", "x.csv",
            "--level-column", "h", "--discharge-column", "q", *extra,
        ]
    )  # fmt: skip
    return lso.run_rating(
        args,
        text=table,
        ratings=PgRatingCurveStore(conn),
        **_stores(conn),  # pyright: ignore[reportArgumentType]
    )


def _derive(conn: sa.Connection, code: str, extra: list[str]) -> int:
    args = lso._parse_args(  # pyright: ignore[reportPrivateUsage]
        ["derive-discharge", *STATION_ARGS, "--station-code", code, *extra]
    )
    return lso.run_derive(
        args,
        ratings=PgRatingCurveStore(conn),
        baselines=PgClimBaselineStore(conn),
        qc_rules=resolve_qc_rules()[0],
        **_stores(conn),  # pyright: ignore[reportArgumentType]
    )


def _audit(conn: sa.Connection, station_id: StationId, event_type: str) -> int:
    return conn.execute(
        sa.text(
            "SELECT count(*) FROM audit_log WHERE target_id = :i AND event_type = :e"
        ),
        {"i": str(station_id), "e": event_type},
    ).scalar_one()


def _derived_rows(conn: sa.Connection, station_id: StationId) -> list[sa.Row[tuple]]:  # type: ignore[type-arg]
    return list(
        conn.execute(
            sa.text(
                "SELECT value, source, rating_curve_id, qc_rule_version, qc_status "
                "FROM observations WHERE station_id = :i AND parameter = 'discharge' "
                "ORDER BY timestamp"
            ),
            {"i": station_id},
        )
    )


@pytest.mark.parametrize("gauge", ["staff", "bridge"])
def test_rating_then_derive_discharge_round_trip(
    db_connection: sa.Connection,
    capsys: pytest.CaptureFixture[str],
    gauge: str,
) -> None:
    tenant = PgTenantStore(db_connection).ensure_tenant(
        tenant_id=TenantId(uuid4()), code="sandbox", name="Sandbox"
    )
    bridge = gauge == "bridge"
    station = make_station_config(
        station_id=StationId(uuid4()),
        code="R1",
        network="research",
        tenant_id=tenant.id,
        tz="UTC",
        water_level_datum_masl=1480.0,
        water_level_unit="m",
        water_level_gauge_type=(
            WaterLevelGaugeType.BRIDGE_SENSOR
            if bridge
            else WaterLevelGaugeType.STAFF_OR_PRESSURE
        ),
        water_level_sensor_masl=1490.0 if bridge else None,
    )
    PgStationStore(db_connection).store_station(station)
    # gauge readings 2.0..2.4 (stored 1482.0..1482.4 staff, 1488.0..1487.6 bridge)
    _load_levels(db_connection, "R1", _levels_csv(2.0, step=0.1))
    table = (
        "h,q\n1.0,90\n2.0,40\n3.0,10\n" if bridge else "h,q\n1.0,10\n2.0,40\n3.0,90\n"
    )

    assert _rating(db_connection, "R1", table, []) == 0  # dry run
    assert PgRatingCurveStore(db_connection).fetch_active_curve(station.id) is None
    assert _audit(db_connection, station.id, "rating_curve_stored") == 0

    assert _rating(db_connection, "R1", table, ["--apply", "--label", "SYNTHETIC"]) == 0
    curve = PgRatingCurveStore(db_connection).fetch_active_curve(station.id)
    assert curve is not None and curve.rating_type_label == "SYNTHETIC"
    assert _audit(db_connection, station.id, "rating_curve_stored") == 1
    assert _rating(db_connection, "R1", table, ["--apply"]) == 3  # needs --supersede

    capsys.readouterr()
    assert _derive(db_connection, "R1", []) == 0  # dry run
    assert _derived_rows(db_connection, station.id) == []
    assert "DRY RUN" in capsys.readouterr().out

    assert _derive(db_connection, "R1", ["--apply"]) == 0
    rows = _derived_rows(db_connection, station.id)
    assert len(rows) == DAYS
    assert {r.source for r in rows} == {"rating_curve_derived"}
    assert {r.rating_curve_id for r in rows} == {curve.id}
    assert {r.qc_rule_version for r in rows} == {"1.2"}
    # level 1482.0 is gauge reading 2.0 on a staff gauge (Q 40), 1488.0 on a bridge
    assert rows[0].value == pytest.approx(40.0)
    assert _audit(db_connection, station.id, "observations_loaded") == 2
    out = capsys.readouterr().out
    assert "daily values usable (passed or unchecked)" in out

    assert _derive(db_connection, "R1", ["--apply"]) == 0
    assert "rows written or changed: 0" in capsys.readouterr().out
    assert _derived_rows(db_connection, station.id) == rows


def test_supersede_closes_the_old_curve_in_the_database(
    db_connection: sa.Connection,
) -> None:
    tenant = PgTenantStore(db_connection).ensure_tenant(
        tenant_id=TenantId(uuid4()), code="sandbox", name="Sandbox"
    )
    station = make_station_config(
        station_id=StationId(uuid4()),
        code="R2",
        network="research",
        tenant_id=tenant.id,
        water_level_datum_masl=1480.0,
        water_level_unit="m",
        water_level_gauge_type=WaterLevelGaugeType.STAFF_OR_PRESSURE,
    )
    PgStationStore(db_connection).store_station(station)
    table = "h,q\n1.0,10\n2.0,40\n"
    first = ["--apply", "--valid-from", "2026-01-01T00:00:00+00:00"]
    assert _rating(db_connection, "R2", table, first) == 0
    assert _rating(db_connection, "R2", table, ["--apply", "--supersede"]) == 0
    store = PgRatingCurveStore(db_connection)
    curves = store.fetch_all_curves_for_station(station.id)
    assert [(c.version, c.valid_to) for c in curves] == [(1, NOW), (2, None)]
    assert _audit(db_connection, station.id, "rating_curve_stored") == 2


def test_derive_skips_failed_and_suspect_levels(
    db_connection: sa.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    tenant = PgTenantStore(db_connection).ensure_tenant(
        tenant_id=TenantId(uuid4()), code="sandbox", name="Sandbox"
    )
    station = make_station_config(
        station_id=StationId(uuid4()),
        code="R3",
        network="research",
        tenant_id=tenant.id,
        tz="UTC",
        water_level_datum_masl=1480.0,
        water_level_unit="m",
        water_level_gauge_type=WaterLevelGaugeType.STAFF_OR_PRESSURE,
    )
    PgStationStore(db_connection).store_station(station)
    _load_levels(db_connection, "R3", _levels_csv(2.0, step=0.1))
    assert (
        _rating(db_connection, "R3", "h,q\n1.0,10\n2.0,40\n3.0,90\n", ["--apply"]) == 0
    )
    db_connection.execute(
        sa.text(
            "UPDATE observations SET qc_status = :s WHERE station_id = :i "
            "AND parameter = 'water_level' AND timestamp = :t"
        ),
        [
            {
                "s": "qc_failed",
                "i": station.id,
                "t": datetime(2026, 10, 1, 6, tzinfo=UTC),
            },
            {
                "s": "qc_suspect",
                "i": station.id,
                "t": datetime(2026, 10, 2, 6, tzinfo=UTC),
            },
        ],
    )
    capsys.readouterr()
    assert _derive(db_connection, "R3", ["--apply"]) == 0
    assert "2 levels skipped: failed or suspect QC" in capsys.readouterr().out
    assert len(_derived_rows(db_connection, station.id)) == DAYS - 2
