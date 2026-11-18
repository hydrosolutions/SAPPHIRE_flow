from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sapphire_flow.cli import load_station_observations as lso
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.config.qc_rules import resolve_qc_rules
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    AuditEventType,
    ObservationSource,
    WaterLevelGaugeType,
)
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.observation import RawObservation
from sapphire_flow.types.tenant import DEFAULT_TENANT_CODE
from tests.conftest import make_station_config
from tests.fakes.fake_stores import (
    FakeAuditLogStore,
    FakeClimBaselineStore,
    FakeObservationStore,
    FakeRatingCurveStore,
    FakeStationStore,
    FakeTenantStore,
)

NOW = ensure_utc(datetime(2026, 11, 18, tzinfo=UTC))
EPOCH = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
SCOPED = DeploymentIdentityConfig(
    writable_tenants=frozenset({DEFAULT_TENANT_CODE}), global_admin=False
)
TABLE = "h,q\n1.0,10\n2.0,40\n3.0,90\n"
STATION_ARGS = [
    "--tenant", DEFAULT_TENANT_CODE, "--station-code", "00001", "--network", "research",
]  # fmt: skip
RATING_ARGS = [
    "rating", *STATION_ARGS, "--csv", "x.csv", "--level-column", "h",
    "--discharge-column", "q",
]  # fmt: skip
MASL_TABLE = "h,q\n1481,10\n1482,40\n"


def _station(**kw: object):  # noqa: ANN202
    return make_station_config(
        code="00001",
        network="research",
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
        self.ratings = FakeRatingCurveStore()
        self.audit = FakeAuditLogStore()

    @property
    def station_id(self):  # noqa: ANN202
        return self.stations.fetch_all_stations()[0].id

    def levels(self, values: list[float]) -> None:
        self.observations.store_raw_observations(
            [
                RawObservation(
                    station_id=self.station_id,
                    timestamp=ensure_utc(datetime(2026, 11, 1, hour, tzinfo=UTC)),
                    parameter="water_level",
                    value=v,
                    source=ObservationSource.MEASURED,
                )
                for hour, v in enumerate(values)
            ]
        )

    def discharge(self):  # noqa: ANN202
        return self.observations.fetch_observations(
            self.station_id, "discharge", EPOCH, NOW
        )

    def rating(self, extra: list[str] | None = None, text: str = TABLE) -> int:
        args = lso._parse_args([*RATING_ARGS, *(extra or [])])  # pyright: ignore[reportPrivateUsage]
        return lso.run_rating(
            args,
            text=text,
            identity=SCOPED,
            tenants=self.tenants,
            stations=self.stations,
            observations=self.observations,
            ratings=self.ratings,
            audit=self.audit,
            clock=lambda: NOW,
        )

    def derive(self, extra: list[str] | None = None) -> int:
        args = lso._parse_args(["derive-discharge", *STATION_ARGS, *(extra or [])])  # pyright: ignore[reportPrivateUsage]
        return lso.run_derive(
            args,
            identity=SCOPED,
            tenants=self.tenants,
            stations=self.stations,
            observations=self.observations,
            ratings=self.ratings,
            audit=self.audit,
            baselines=FakeClimBaselineStore(),
            qc_rules=resolve_qc_rules()[0],
            clock=lambda: NOW,
        )

    def active(self):  # noqa: ANN202
        return self.ratings.fetch_active_curve(self.station_id)


class TestRatingCommand:
    def test_dry_run_writes_nothing_and_compares_ranges(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rig = Rig(_station())
        rig.levels([1481.5, 1482.5])
        assert rig.rating() == 0
        assert rig.active() is None
        assert rig.audit.entries == []
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "curve stage range: 1481 to 1483 m a.s.l." in out
        assert "stored water level range: 1481.5 to 1482.5" in out
        assert "do not overlap" not in out

    def test_apply_stores_curve_and_one_audit_row(self) -> None:
        rig = Rig(_station())
        assert rig.rating(["--apply", "--label", "SYNTHETIC"]) == 0
        curve = rig.active()
        assert curve is not None
        assert (curve.version, curve.rating_type_label) == (1, "SYNTHETIC")
        (entry,) = rig.audit.entries
        assert entry.event_type is AuditEventType.RATING_CURVE_STORED
        assert entry.detail is not None
        assert entry.detail["curve_id"] == str(curve.id)

    def test_no_overlap_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        rig = Rig(_station())
        rig.levels([1.0, 2.0])
        assert rig.rating() == 0
        assert "do not overlap" in capsys.readouterr().out

    def test_existing_active_curve_is_refused_without_supersede(self) -> None:
        rig = Rig(_station())
        assert rig.rating(["--apply"]) == 0
        first = rig.active()
        assert rig.rating(["--apply"]) == 3
        assert rig.active() == first
        assert len(rig.audit.entries) == 1

    def test_supersede_closes_old_curve_and_bumps_version(self) -> None:
        rig = Rig(_station())
        assert rig.rating(["--apply", "--valid-from", "2026-01-01T00:00:00+00:00"]) == 0
        old = rig.active()
        assert old is not None
        assert rig.rating(["--apply", "--supersede"]) == 0
        new = rig.active()
        assert new is not None
        assert new.id != old.id
        assert new.version == old.version + 1
        closed = [
            c
            for c in rig.ratings.fetch_all_curves_for_station(rig.station_id)
            if c.id == old.id
        ]
        assert closed[0].valid_to == NOW
        assert len(rig.audit.entries) == 2

    def test_supersede_before_active_start_is_a_usage_error(self) -> None:
        rig = Rig(_station())
        assert rig.rating(["--apply"]) == 0
        assert rig.rating(["--supersede", "--valid-from", "2026-01-01T00:00:00Z"]) == 2

    @pytest.mark.parametrize(
        "text",
        ["h,q\n1,2\n", "h,q\n1,20\n2,10\n", "h,q\n1,2\n1,3\n", "x,y\n1,2\n", ""],
    )
    def test_invalid_table_exits_2_and_writes_nothing(self, text: str) -> None:
        rig = Rig(_station())
        assert rig.rating(["--apply"], text=text) == 2
        assert rig.active() is None
        assert rig.audit.entries == []

    def test_log_linear_with_zero_discharge_exits_2(self) -> None:
        rig = Rig(_station())
        assert rig.rating(["--interpolation", "log_linear"], "h,q\n1,0\n2,5\n") == 2

    def test_gauge_reading_without_datum_is_refused(self) -> None:
        rig = Rig(make_station_config(code="00001", network="research"))
        assert rig.rating(["--apply"]) == 3
        assert rig.audit.entries == []

    def test_level_masl_works_without_datum(self) -> None:
        rig = Rig(make_station_config(code="00001", network="research"))
        assert rig.rating(["--level-kind", "level-masl", "--apply"], MASL_TABLE) == 0

    def test_unknown_station_exits_2(self) -> None:
        assert Rig().rating() == 2

    def test_non_writable_tenant_is_refused_and_audited(self) -> None:
        rig = Rig(_station(tenant_id=TenantId(uuid4())))
        assert rig.rating(["--apply"]) == 3
        (entry,) = rig.audit.entries
        assert entry.detail is not None
        assert entry.detail["outcome"] == "rejected_tenant_mismatch"
        assert rig.active() is None


def _setup_derive(values: list[float]) -> Rig:
    rig = Rig(_station())
    rig.levels(values)
    assert rig.rating(["--apply"]) == 0
    return rig


class TestDeriveCommand:
    def test_dry_run_writes_nothing(self, capsys: pytest.CaptureFixture[str]) -> None:
        rig = _setup_derive([1481.5, 1482.5])
        assert rig.derive() == 0
        assert rig.discharge() == []
        assert len(rig.audit.entries) == 1  # the rating row only
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "QC rule version: 1.2\n" in out

    def test_apply_writes_derived_rows_one_audit_row_and_qc_version(self) -> None:
        rig = _setup_derive([1481.5, 1482.5, 1490.0])
        assert rig.derive(["--apply", "--allow-unchecked"]) == 0
        curve = rig.active()
        assert curve is not None
        rows = rig.discharge()
        assert sorted(o.value for o in rows if o.value is not None) == [25.0, 65.0]
        assert {o.source for o in rows} == {ObservationSource.RATING_CURVE_DERIVED}
        assert {o.rating_curve_id for o in rows} == {curve.id}
        assert {o.qc_rule_version for o in rows} == {"1.2"}
        entry = rig.audit.entries[-1]
        assert entry.event_type is AuditEventType.OBSERVATIONS_LOADED
        assert entry.detail is not None
        assert entry.detail["rows"] == 2
        assert entry.detail["skipped_outside_curve"] == 1
        assert len(rig.audit.entries) == 2

    def test_rerun_is_idempotent(self, capsys: pytest.CaptureFixture[str]) -> None:
        rig = _setup_derive([1481.5, 1482.5])
        assert rig.derive(["--apply", "--allow-unchecked"]) == 0
        capsys.readouterr()
        assert rig.derive(["--apply", "--allow-unchecked"]) == 0
        assert "rows written or changed: 0" in capsys.readouterr().out

    def test_all_levels_outside_curve_is_refused(self) -> None:
        rig = _setup_derive([1500.0, 1501.0])
        assert rig.derive(["--apply"]) == 3
        assert len(rig.audit.entries) == 1

    def test_no_active_curve_exits_2(self) -> None:
        rig = Rig(_station())
        rig.levels([1481.5])
        assert rig.derive() == 2

    def test_no_water_level_exits_2(self) -> None:
        rig = Rig(_station())
        assert rig.rating(["--apply"]) == 0
        assert rig.derive() == 2

    def test_unknown_curve_id_exits_2(self) -> None:
        rig = _setup_derive([1481.5])
        assert rig.derive(["--curve-id", str(uuid4())]) == 2

    def test_window_selects_readings(self) -> None:
        rig = _setup_derive([1481.5, 1482.5, 1482.0])
        window = ["--start", "2026-11-01T01:00:00+00:00"]
        window += ["--end", "2026-11-01T02:00:00+00:00"]
        assert rig.derive(["--apply", "--allow-unchecked", *window]) == 0
        assert [o.value for o in rig.discharge()] == [65.0]

    def test_non_writable_tenant_is_refused(self) -> None:
        rig = Rig(_station(tenant_id=TenantId(uuid4())))
        assert rig.derive() == 3

    def test_station_without_datum_derives_with_a_level_masl_curve(self) -> None:
        rig = Rig(make_station_config(code="00001", network="research"))
        rig.levels([1481.5])
        assert rig.rating(["--level-kind", "level-masl", "--apply"], MASL_TABLE) == 0
        assert rig.derive(["--apply", "--allow-unchecked"]) == 0
        assert [o.value for o in rig.discharge()] == [25.0]
