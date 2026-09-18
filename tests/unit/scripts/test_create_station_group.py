"""Plan 262 T3a: the operator route for group CREATION — the capability that had
no non-test caller.

These exercise the pure planning/apply functions against fakes. The script's own
`main` is thin wiring over them; what needs locking is the behaviour: dry run
writes nothing, re-runs are no-ops, unknown codes never reach a write, and
station status is never touched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from sapphire_flow.exceptions import TenantIsolationError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import PrincipalId, StationGroupId, StationId, TenantId
from sapphire_flow.types.station import StationGroup
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID, Tenant
from sapphire_flow.types.write_principal import WritePrincipal
from scripts.create_station_group import (
    GroupPlan,
    apply_station_group,
    plan_station_group,
)

if TYPE_CHECKING:
    from pathlib import Path

_NOW = ensure_utc(datetime(2026, 9, 11, 12, tzinfo=UTC))
_PRINCIPAL = WritePrincipal(id=None, tenant_id=DEFAULT_TENANT_ID)


@pytest.fixture(autouse=True)
def deployment_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.toml"
    path.write_text('[deployment]\nglobal_admin = true\noperator = "configured"\n')
    monkeypatch.setenv("SAPPHIRE_CONFIG", str(path))
    monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
    return path


def _clock():  # noqa: ANN202
    return _NOW


class _FakeStation:
    def __init__(self, station_id: StationId, tenant_id=DEFAULT_TENANT_ID) -> None:  # noqa: ANN001
        self.id = station_id
        self.tenant_id = tenant_id


class _FakeStationLookup:
    """Resolves only the codes it was given, so an unknown code is a real miss."""

    def __init__(self, by_code: dict[str, StationId], *, tenants=None) -> None:  # noqa: ANN001
        self._by_code = by_code
        self._tenants = tenants or {}
        self.status_writes = 0

    def fetch_station_by_code(self, code: str, network: str) -> object | None:
        del network
        station_id = self._by_code.get(code)
        if station_id is None:
            return None
        return _FakeStation(station_id, self._tenants.get(code, DEFAULT_TENANT_ID))

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


class _FakeConn:
    """`PgStationGroupStore.__init__` reads `conn.engine` for its default
    transaction factory, so a bare object is not enough even when the stores never
    execute anything.
    """

    @property
    def engine(self) -> _FakeEngine:
        return _FakeEngine()

    def execution_options(self, **kwargs: object) -> _FakeConn:
        del kwargs
        return self

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeEngine:
    """Enough engine to let `main` run its dry-run branch: a connect() context and a
    begin() context. Neither executes anything — the stores are monkeypatched out.
    """

    def connect(self) -> _FakeConn:
        return _FakeConn()

    def begin(self) -> _FakeConn:
        return _FakeConn()


_PLAN = GroupPlan(
    name="g",
    tenant_id=DEFAULT_TENANT_ID,
    group_id=StationGroupId(uuid4()),
    group_exists=False,
    members_to_add=(("2009", StationId(uuid4())),),
    already_members=(),
    unresolved_codes=(),
    wrong_tenant_codes=(),
)


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

        apply_station_group(
            plan,
            group_store=store,
            clock=_clock,
            principal=_PRINCIPAL,
            audit_log_store=_FakeAuditLog(),
        )

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

        apply_station_group(
            plan,
            group_store=store,
            clock=_clock,
            principal=_PRINCIPAL,
            audit_log_store=_FakeAuditLog(),
        )

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
            apply_station_group(
                plan,
                group_store=store,
                clock=_clock,
                principal=_PRINCIPAL,
                audit_log_store=_FakeAuditLog(),
            )

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
            plan,
            group_store=store,
            clock=_clock,
            principal=_PRINCIPAL,
            audit_log_store=audit,
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

        apply_station_group(
            plan,
            group_store=store,
            clock=_clock,
            principal=_PRINCIPAL,
            audit_log_store=_FakeAuditLog(),
        )

        assert lookup.status_writes == 0


class TestTenantSafety:
    def test_a_station_from_another_tenant_is_refused_before_any_write(self) -> None:
        """It resolves by code, so only a tenant check catches it. Left through, the
        dry run reports success and the apply fails on the membership's composite
        tenant FK — after the group row already exists.
        """
        from sapphire_flow.types.ids import TenantId

        a, other = StationId(uuid4()), StationId(uuid4())
        store = _FakeGroupStore()
        plan = plan_station_group(
            name="g",
            station_codes=["2009", "8888"],
            network="bafu",
            tenant_id=DEFAULT_TENANT_ID,
            station_store=_FakeStationLookup(
                {"2009": a, "8888": other},
                tenants={"8888": TenantId(uuid4())},
            ),
            group_store=store,
        )

        assert plan.wrong_tenant_codes == ("8888",)
        assert [code for code, _ in plan.members_to_add] == ["2009"]
        assert "8888" in plan.blocking_codes

        with pytest.raises(ValueError, match="8888"):
            apply_station_group(
                plan,
                group_store=store,
                clock=_clock,
                principal=_PRINCIPAL,
                audit_log_store=_FakeAuditLog(),
            )

        assert store.stored == []
        assert store.added == []


class TestTheDryRunGuard:
    """Independent review 2026-09-11 (minor): every other test calls the planning and
    apply functions DIRECTLY, so moving the write above the `--apply` check would
    leave them all green while the default invocation started writing. These call
    `main()` and are the only thing that can catch that.

    🪤 They monkeypatch `sqlalchemy.create_engine` rather than a seam in this module:
    a confirming review pointed out that an injectable `_engine_for` wrapper was
    indirection existing only for tests, and erased the engine's type to `object`.
    Patch the library boundary; leave production code alone.
    """

    def test_the_default_invocation_writes_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.create_station_group as mod

        applied: list[object] = []
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u@h/db")
        monkeypatch.setattr(mod, "plan_station_group", lambda **_: _PLAN)
        monkeypatch.setattr(
            mod, "apply_station_group", lambda *a, **k: applied.append(a)
        )
        monkeypatch.setattr("sqlalchemy.create_engine", lambda *a, **k: _FakeEngine())

        exit_code = mod.main(["--name", "g", "--station-code", "2009"])

        assert exit_code == 0
        assert applied == [], "a dry run must not reach the write path"

    def test_apply_reaches_the_write_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The discriminating other half — without it, a main() that never writes at
        all would pass the test above.
        """
        import scripts.create_station_group as mod

        applied: list[object] = []
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u@h/db")
        monkeypatch.setattr(mod, "plan_station_group", lambda **_: _PLAN)
        monkeypatch.setattr(
            mod, "apply_station_group", lambda *a, **k: applied.append(a)
        )
        monkeypatch.setattr("sqlalchemy.create_engine", lambda *a, **k: _FakeEngine())

        exit_code = mod.main(["--name", "g", "--station-code", "2009", "--apply"])

        assert exit_code == 0
        assert len(applied) == 1


_FOREIGN_TENANT_ID = TenantId(uuid4())


class _FakeTenantStore:
    def __init__(self, conn: object) -> None:
        del conn

    def fetch_tenant_by_code(self, code: str) -> Tenant | None:
        tenant_id = {"sapphire": DEFAULT_TENANT_ID, "foreign": _FOREIGN_TENANT_ID}.get(
            code
        )
        if tenant_id is None:
            return None
        return Tenant(id=tenant_id, code=code, name=code, created_at=_NOW)


@pytest.fixture
def cli_stores(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_FakeGroupStore, _FakeAuditLog, _FakeStationLookup]:
    groups, audit = _FakeGroupStore(), _FakeAuditLog()
    stations = _FakeStationLookup({"2009": StationId(uuid4())})
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u@h/db")
    monkeypatch.setattr("sqlalchemy.create_engine", lambda *a, **k: _FakeEngine())
    monkeypatch.setattr(
        "sapphire_flow.store.tenant_store.PgTenantStore", _FakeTenantStore
    )
    monkeypatch.setattr(
        "sapphire_flow.store.station_store.PgStationStore", lambda conn: stations
    )
    monkeypatch.setattr(
        "sapphire_flow.store.station_group_store.PgStationGroupStore",
        lambda *a, **k: groups,
    )
    monkeypatch.setattr(
        "sapphire_flow.store.audit_log_store.PgAuditLogStore", lambda conn: audit
    )
    return groups, audit, stations


class TestCliWriteAuthorization:
    @pytest.mark.parametrize(
        "identity", ['writable_tenants = ["sapphire"]', "global_admin = true"]
    )
    @pytest.mark.parametrize("apply", [False, True])
    def test_configured_authority_controls_real_apply(
        self,
        identity: str,
        apply: bool,
        deployment_config: Path,
        cli_stores: tuple[_FakeGroupStore, _FakeAuditLog, _FakeStationLookup],
    ) -> None:
        from scripts.create_station_group import main

        deployment_config.write_text(
            f'[deployment]\n{identity}\noperator = "configured"\n'
        )
        groups, audit, stations = cli_stores
        args = ["--name", "g", "--station-code", "2009"]
        assert main(args + (["--apply"] if apply else [])) == 0
        assert len(groups.stored) == int(apply)
        assert len(groups.added) == int(apply)
        assert len(audit.entries) == int(apply)
        assert stations.status_writes == 0
        if apply:
            assert audit.entries[0].detail["operator"] == "configured"
            assert audit.entries[0].detail["tenant_id"] == str(DEFAULT_TENANT_ID)

    @pytest.mark.parametrize("apply", [False, True])
    def test_foreign_principal_cannot_write_default_tenant(
        self,
        apply: bool,
        deployment_config: Path,
        cli_stores: tuple[_FakeGroupStore, _FakeAuditLog, _FakeStationLookup],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from scripts.create_station_group import main

        deployment_config.write_text('[deployment]\nwritable_tenants = ["foreign"]\n')
        groups, audit, stations = cli_stores
        args = ["--name", "g", "--station-code", "2009", "--operator", "override"]
        assert main(args + (["--apply"] if apply else [])) == 1
        assert "not authorized" in capsys.readouterr().err
        assert groups.stored == groups.added == []
        assert stations.status_writes == 0
        assert len(audit.entries) == int(apply)
        if apply:
            assert audit.entries[0].detail["outcome"] == "rejected_tenant_mismatch"
            assert audit.entries[0].detail["operator"] == "override"
            assert audit.entries[0].detail["principal_tenant_id"] == str(
                _FOREIGN_TENANT_ID
            )

    @pytest.mark.parametrize(
        ("identity", "message"),
        [
            ("", "must declare either"),
            ('writable_tenants = ["sapphire", "foreign"]', "explicit tenant code"),
            ('writable_tenants = ["unknown"]', "unknown"),
            (
                'writable_tenants = ["sapphire"]\nglobal_admin = true',
                "mutually exclusive",
            ),
        ],
    )
    def test_invalid_config_fails_without_any_write(
        self,
        identity: str,
        message: str,
        deployment_config: Path,
        cli_stores: tuple[_FakeGroupStore, _FakeAuditLog, _FakeStationLookup],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from scripts.create_station_group import main

        deployment_config.write_text(f"[deployment]\n{identity}\n")
        groups, audit, stations = cli_stores
        assert main(["--name", "g", "--station-code", "2009", "--apply"]) == 1
        assert message in capsys.readouterr().err
        assert groups.stored == groups.added == audit.entries == []
        assert stations.status_writes == 0

    def test_missing_config_does_not_connect(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from scripts.create_station_group import main

        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u@h/db")
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)

        def unexpected_connection(*args: object, **kwargs: object) -> None:
            pytest.fail("missing authority must fail before connecting")

        monkeypatch.setattr("sqlalchemy.create_engine", unexpected_connection)
        assert main(["--name", "g", "--station-code", "2009", "--apply"]) == 1
        assert "SAPPHIRE_CONFIG" in capsys.readouterr().err

    def test_apply_defensively_rejects_a_foreign_principal(self) -> None:
        groups, audit = _FakeGroupStore(), _FakeAuditLog()
        with pytest.raises(TenantIsolationError, match="not authorized"):
            apply_station_group(
                _PLAN,
                group_store=groups,
                clock=_clock,
                principal=WritePrincipal(
                    id=PrincipalId("foreign"), tenant_id=_FOREIGN_TENANT_ID
                ),
                audit_log_store=audit,
            )
        assert groups.stored == groups.added == audit.entries == []
