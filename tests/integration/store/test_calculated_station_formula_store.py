from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from sapphire_flow.store.calculated_station_formula_store import PgFormulaStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.calculated_station import ComponentWeight
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import GaugingStatus, StationStatus
from sapphire_flow.types.ids import FormulaId, StationId
from tests.conftest import make_station_config

_NOW = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))


def _seed_station(
    conn: sa.Connection,
    *,
    code: str,
    gauging: GaugingStatus = GaugingStatus.GAUGED,
    status: StationStatus = StationStatus.OPERATIONAL,
) -> StationId:
    station = make_station_config(
        station_id=StationId(uuid.uuid4()),
        code=code,
        network="dhm",
        gauging_status=gauging,
        station_status=status,
    )
    PgStationStore(conn).store_station(station)
    return station.id


def _weight(
    calc: StationId,
    comp: StationId,
    *,
    weight: float = 0.6,
    parameter: str = "discharge",
    effective_from: datetime = _NOW,
    effective_to: datetime | None = None,
) -> ComponentWeight:
    return ComponentWeight(
        id=FormulaId(uuid.uuid4()),
        calculated_station_id=calc,
        component_station_id=comp,
        parameter=parameter,
        weight=weight,
        effective_from=effective_from,
        effective_to=effective_to,
        created_at=_NOW,
    )


class TestStoreAndFetch:
    def test_round_trip_current_formula(self, db_connection: sa.Connection) -> None:
        calc = _seed_station(
            db_connection, code="CALC-1", gauging=GaugingStatus.CALCULATED
        )
        comp = _seed_station(db_connection, code="COMP-1")
        store = PgFormulaStore(db_connection)

        store.store_formula([_weight(calc, comp, weight=0.6)])

        rows = store.fetch_current_formula(calc, "discharge")
        assert len(rows) == 1
        assert rows[0].component_station_id == comp
        assert rows[0].weight == pytest.approx(0.6)
        assert rows[0].effective_to is None

    def test_close_formula_clears_current(self, db_connection: sa.Connection) -> None:
        calc = _seed_station(
            db_connection, code="CALC-2", gauging=GaugingStatus.CALCULATED
        )
        comp = _seed_station(db_connection, code="COMP-2")
        store = PgFormulaStore(db_connection)
        store.store_formula([_weight(calc, comp)])

        closed = store.close_formula(calc, "discharge", _NOW + timedelta(days=30))
        assert closed == 1
        assert store.fetch_current_formula(calc, "discharge") == []


class TestPartialUnique:
    def test_two_current_rows_same_triple_rejected(
        self, db_connection: sa.Connection
    ) -> None:
        calc = _seed_station(
            db_connection, code="CALC-3", gauging=GaugingStatus.CALCULATED
        )
        comp = _seed_station(db_connection, code="COMP-3")
        store = PgFormulaStore(db_connection)
        store.store_formula([_weight(calc, comp, weight=0.5)])

        with pytest.raises(sa.exc.IntegrityError, match="uq_csf_current"):
            store.store_formula([_weight(calc, comp, weight=0.7)])


class TestEligibilityTrigger:
    def test_rejects_non_calculated_target(self, db_connection: sa.Connection) -> None:
        # target is GAUGED, not CALCULATED
        calc = _seed_station(db_connection, code="CALC-4")  # gauged by default
        comp = _seed_station(db_connection, code="COMP-4")
        store = PgFormulaStore(db_connection)
        with pytest.raises(sa.exc.DBAPIError, match="gauging_status=calculated"):
            store.store_formula([_weight(calc, comp)])

    def test_rejects_ungauged_component(self, db_connection: sa.Connection) -> None:
        calc = _seed_station(
            db_connection, code="CALC-5", gauging=GaugingStatus.CALCULATED
        )
        # component is CALCULATED (not gauged)
        comp = _seed_station(
            db_connection, code="COMP-5", gauging=GaugingStatus.CALCULATED
        )
        store = PgFormulaStore(db_connection)
        with pytest.raises(sa.exc.DBAPIError, match="gauged\\+operational"):
            store.store_formula([_weight(calc, comp)])

    def test_rejects_suspended_component(self, db_connection: sa.Connection) -> None:
        calc = _seed_station(
            db_connection, code="CALC-6", gauging=GaugingStatus.CALCULATED
        )
        comp = _seed_station(
            db_connection, code="COMP-6", status=StationStatus.SUSPENDED
        )
        store = PgFormulaStore(db_connection)
        with pytest.raises(sa.exc.DBAPIError, match="gauged\\+operational"):
            store.store_formula([_weight(calc, comp)])

    def test_closure_only_update_exempt_after_suspend(
        self, db_connection: sa.Connection
    ) -> None:
        # Store while component is operational, then suspend it, then close the formula:
        # the closure-only UPDATE must be exempt from the eligibility check.
        calc = _seed_station(
            db_connection, code="CALC-7", gauging=GaugingStatus.CALCULATED
        )
        comp = _seed_station(db_connection, code="COMP-7")
        store = PgFormulaStore(db_connection)
        store.store_formula([_weight(calc, comp)])

        db_connection.execute(
            sa.text("UPDATE stations SET station_status = 'suspended' WHERE id = :id"),
            {"id": comp},
        )

        closed = store.close_formula(calc, "discharge", _NOW + timedelta(days=30))
        assert closed == 1
        assert store.fetch_current_formula(calc, "discharge") == []


class TestFetchFormulaAt:
    def test_deterministic_valid_at_time(self, db_connection: sa.Connection) -> None:
        calc = _seed_station(
            db_connection, code="CALC-8", gauging=GaugingStatus.CALCULATED
        )
        comp = _seed_station(db_connection, code="COMP-8")
        store = PgFormulaStore(db_connection)
        # old version, then closed; new version current
        store.store_formula(
            [
                _weight(
                    calc,
                    comp,
                    weight=0.5,
                    effective_from=_NOW,
                    effective_to=_NOW + timedelta(days=10),
                )
            ]
        )
        store.store_formula(
            [
                _weight(
                    calc,
                    comp,
                    weight=0.7,
                    effective_from=_NOW + timedelta(days=10),
                    effective_to=None,
                )
            ]
        )

        at_old = store.fetch_formula_at(calc, "discharge", _NOW + timedelta(days=5))
        assert [w.weight for w in at_old] == pytest.approx([0.5])

        at_new = store.fetch_formula_at(calc, "discharge", _NOW + timedelta(days=20))
        assert [w.weight for w in at_new] == pytest.approx([0.7])

    def test_tie_on_effective_from_broken_by_created_at(
        self, db_connection: sa.Connection
    ) -> None:
        # Two rows for the same component share effective_from and both cover `at`
        # (a closed row + a current row — the partial-unique index only bars two
        # *current* rows). The stable tie-breaker (created_at, id) must pick one
        # deterministically: the greater created_at wins.
        calc = _seed_station(
            db_connection, code="CALC-TIE", gauging=GaugingStatus.CALCULATED
        )
        comp = _seed_station(db_connection, code="COMP-TIE")
        store = PgFormulaStore(db_connection)
        older = ComponentWeight(
            id=FormulaId(uuid.uuid4()),
            calculated_station_id=calc,
            component_station_id=comp,
            parameter="discharge",
            weight=0.5,
            effective_from=_NOW,
            effective_to=_NOW + timedelta(days=30),  # closed
            created_at=_NOW,
        )
        newer = ComponentWeight(
            id=FormulaId(uuid.uuid4()),
            calculated_station_id=calc,
            component_station_id=comp,
            parameter="discharge",
            weight=0.9,
            effective_from=_NOW,  # same effective_from
            effective_to=None,  # current
            created_at=_NOW + timedelta(hours=1),  # later create → wins
        )
        store.store_formula([older])
        store.store_formula([newer])

        at = store.fetch_formula_at(calc, "discharge", _NOW + timedelta(days=5))
        assert [w.weight for w in at] == pytest.approx([0.9])
