"""Plan 262 T3a: the operator route for group CREATION — the capability that had
no non-test caller.

These exercise the pure planning/apply functions against fakes. The script's own
`main` is thin wiring over them; what needs locking is the behaviour: dry run
writes nothing, re-runs are no-ops, unknown codes never reach a write, and
station status is never touched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationGroupId, StationId
from sapphire_flow.types.station import StationGroup
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from scripts.create_station_group import apply_station_group, plan_station_group

_NOW = ensure_utc(datetime(2026, 9, 11, 12, tzinfo=UTC))


def _clock():  # noqa: ANN202
    return _NOW


class _FakeStation:
    def __init__(self, station_id: StationId) -> None:
        self.id = station_id


class _FakeStationLookup:
    """Resolves only the codes it was given, so an unknown code is a real miss."""

    def __init__(self, by_code: dict[str, StationId]) -> None:
        self._by_code = by_code
        self.status_writes = 0

    def fetch_station_by_code(self, code: str, network: str) -> object | None:
        del network
        station_id = self._by_code.get(code)
        return _FakeStation(station_id) if station_id is not None else None

    def update_station_status(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.status_writes += 1


class _FakeGroupStore:
    def __init__(self, existing: StationGroup | None = None) -> None:
        self._groups: dict[str, StationGroup] = {}
        if existing is not None:
            self._groups[existing.name] = existing
        self.stored: list[StationGroup] = []
        self.added: list[tuple[StationGroupId, StationId]] = []

    def fetch_group_by_name(self, tenant_id, name: str) -> StationGroup | None:  # noqa: ANN001
        del tenant_id
        return self._groups.get(name)

    def store_group(self, group: StationGroup) -> None:
        self._groups[group.name] = group
        self.stored.append(group)

    def add_station_to_group(
        self, group_id: StationGroupId, station_id: StationId
    ) -> None:
        self.added.append((group_id, station_id))


class _FakeAuditLog:
    def __init__(self) -> None:
        self.entries: list[object] = []

    def append_entry(self, entry: object) -> None:
        self.entries.append(entry)


class TestPlanning:
    def test_a_new_group_plans_every_code_as_a_member(self) -> None:
        a, b = StationId(uuid4()), StationId(uuid4())
        plan = plan_station_group(
            name="swiss-cmal-small-pilot",
            station_codes=["2009", "2091"],
            network="bafu",
            tenant_id=DEFAULT_TENANT_ID,
            station_store=_FakeStationLookup({"2009": a, "2091": b}),
            group_store=_FakeGroupStore(),
        )

        assert plan.group_exists is False
        assert [code for code, _ in plan.members_to_add] == ["2009", "2091"]
        assert plan.unresolved_codes == ()
        assert plan.is_noop is False

    def test_an_unknown_code_is_reported_not_silently_dropped(self) -> None:
        """A group quietly created with fewer members than asked for is worse than
        a refusal — the all-or-nothing group forecast would then run on a subset.
        """
        plan = plan_station_group(
            name="g",
            station_codes=["2009", "9999"],
            network="bafu",
            tenant_id=DEFAULT_TENANT_ID,
            station_store=_FakeStationLookup({"2009": StationId(uuid4())}),
            group_store=_FakeGroupStore(),
        )

        assert plan.unresolved_codes == ("9999",)

    def test_an_existing_member_is_not_planned_again(self) -> None:
        a, b = StationId(uuid4()), StationId(uuid4())
        existing = StationGroup(
            id=StationGroupId(uuid4()),
            name="g",
            station_ids=frozenset({a}),
            created_at=_NOW,
        )

        plan = plan_station_group(
            name="g",
            station_codes=["2009", "2091"],
            network="bafu",
            tenant_id=DEFAULT_TENANT_ID,
            station_store=_FakeStationLookup({"2009": a, "2091": b}),
            group_store=_FakeGroupStore(existing),
        )

        assert plan.group_exists is True
        assert plan.group_id == existing.id
        assert plan.already_members == ("2009",)
        assert [code for code, _ in plan.members_to_add] == ["2091"]

    def test_a_fully_populated_group_plans_nothing(self) -> None:
        a = StationId(uuid4())
        existing = StationGroup(
            id=StationGroupId(uuid4()),
            name="g",
            station_ids=frozenset({a}),
            created_at=_NOW,
        )

        plan = plan_station_group(
            name="g",
            station_codes=["2009"],
            network="bafu",
            tenant_id=DEFAULT_TENANT_ID,
            station_store=_FakeStationLookup({"2009": a}),
            group_store=_FakeGroupStore(existing),
        )

        assert plan.is_noop is True


class TestApplying:
    def test_creates_the_group_then_adds_every_member(self) -> None:
        a, b = StationId(uuid4()), StationId(uuid4())
        store = _FakeGroupStore()
        plan = plan_station_group(
            name="swiss-cmal-small-pilot",
            station_codes=["2009", "2091"],
            network="bafu",
            tenant_id=DEFAULT_TENANT_ID,
            station_store=_FakeStationLookup({"2009": a, "2091": b}),
            group_store=store,
        )

        apply_station_group(plan, group_store=store, clock=_clock)

        assert [g.name for g in store.stored] == ["swiss-cmal-small-pilot"]
        assert store.stored[0].created_at == _NOW
        assert {sid for _, sid in store.added} == {a, b}

    def test_an_existing_group_is_reused_not_recreated(self) -> None:
        """Idempotence is what makes re-running safe after a partial failure."""
        a, b = StationId(uuid4()), StationId(uuid4())
        existing = StationGroup(
            id=StationGroupId(uuid4()),
            name="g",
            station_ids=frozenset({a}),
            created_at=_NOW,
        )
        store = _FakeGroupStore(existing)
        plan = plan_station_group(
            name="g",
            station_codes=["2009", "2091"],
            network="bafu",
            tenant_id=DEFAULT_TENANT_ID,
            station_store=_FakeStationLookup({"2009": a, "2091": b}),
            group_store=store,
        )

        apply_station_group(plan, group_store=store, clock=_clock)

        assert store.stored == [], "must not re-store an existing group"
        assert [sid for _, sid in store.added] == [b]

    def test_it_refuses_to_write_when_a_code_is_unresolved(self) -> None:
        store = _FakeGroupStore()
        plan = plan_station_group(
            name="g",
            station_codes=["2009", "9999"],
            network="bafu",
            tenant_id=DEFAULT_TENANT_ID,
            station_store=_FakeStationLookup({"2009": StationId(uuid4())}),
            group_store=store,
        )

        with pytest.raises(ValueError, match="9999"):
            apply_station_group(plan, group_store=store, clock=_clock)

        assert store.stored == []
        assert store.added == []

    def test_it_records_one_audit_entry_naming_the_group(self) -> None:
        a = StationId(uuid4())
        store = _FakeGroupStore()
        audit = _FakeAuditLog()
        plan = plan_station_group(
            name="swiss-cmal-small-pilot",
            station_codes=["2009"],
            network="bafu",
            tenant_id=DEFAULT_TENANT_ID,
            station_store=_FakeStationLookup({"2009": a}),
            group_store=store,
        )

        apply_station_group(
            plan, group_store=store, clock=_clock, audit_log_store=audit
        )

        assert len(audit.entries) == 1
        entry = audit.entries[0]
        assert entry.target_type == "station_group"
        assert entry.target_id == str(plan.group_id)
        assert entry.detail["members_added"] == ["2009"]

    def test_it_never_writes_station_status(self) -> None:
        """`station_status` gates ingest as well as forecasting, and 111 stations on
        this deployment were already promoted by direct DB write. A tool that
        promoted a station to make a group valid would cost observations
        permanently — so the script must only ever READ stations.
        """
        a = StationId(uuid4())
        lookup = _FakeStationLookup({"2009": a})
        store = _FakeGroupStore()
        plan = plan_station_group(
            name="g",
            station_codes=["2009"],
            network="bafu",
            tenant_id=DEFAULT_TENANT_ID,
            station_store=lookup,
            group_store=store,
        )

        apply_station_group(plan, group_store=store, clock=_clock)

        assert lookup.status_writes == 0
