"""Plan 510 T4 — the guard migration (0067) stays tied to the import's identity
and to the role bootstrap, and its downgrade revokes before it drops."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from sapphire_flow.cli.import_dhm_delivery import DELIVERY_TENANT_CODE
from sapphire_flow.types.dhm_delivery import DELIVERY_ID

if TYPE_CHECKING:
    from types import ModuleType

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION = _ROOT / "alembic/versions/0067_operator_delivery_guard.py"
_BOOTSTRAP_SQL = _ROOT / "docker/bootstrap-roles.sql"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("guard_0067", _MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordingOp:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


@pytest.fixture
def migration(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, _RecordingOp]:
    module = _load()
    recorder = _RecordingOp()
    monkeypatch.setattr(module, "op", recorder)
    return module, recorder


class TestLiteralsAreTiedToTheImport:
    def test_the_delivery_id_literal_is_the_import_constant(self) -> None:
        assert _load().GUARDED_DELIVERY_ID == DELIVERY_ID

    def test_the_tenant_code_literal_is_the_import_constant(self) -> None:
        assert _load().GUARDED_TENANT_CODE == DELIVERY_TENANT_CODE

    def test_the_function_sql_carries_both_literals(self) -> None:
        module: Any = _load()
        assert f"'{DELIVERY_ID}'" in module._ROW_FUNCTION
        assert f"'{DELIVERY_TENANT_CODE}'" in module._ROW_FUNCTION
        assert f"'{DELIVERY_TENANT_CODE}'" in module._STATION_FUNCTION

    def test_the_head_revision_chain(self) -> None:
        module = _load()
        assert (module.revision, module.down_revision) == ("0067", "0066")


class TestBootstrapExpectsExactlyTheMigrationsTriggers:
    def test_every_trigger_the_migration_creates_is_named_in_the_bootstrap(
        self,
    ) -> None:
        module: Any = _load()
        created = {name for _, name, _ in module.ROW_TRIGGERS}
        created.add(module.STATION_TRIGGER[1])
        created |= {name for _, name in module.TRUNCATE_TRIGGERS}
        sql = _BOOTSTRAP_SQL.read_text()
        named = set(re.findall(r"'(trg_[a-z_]+_operator_guard_[a-z]+)'", sql))
        assert named == created
        assert len(created) == 10

    def test_the_bootstrap_names_the_role_the_guard_watches(self) -> None:
        module = _load()
        assert f"session_user = '{module.OPERATOR_ROLE}'" in module._WHEN
        assert f"ALTER ROLE {module.OPERATOR_ROLE}" in _BOOTSTRAP_SQL.read_text()


class TestDowngradeOrder:
    def test_the_operators_dml_is_revoked_before_any_guard_object_is_dropped(
        self, migration: tuple[ModuleType, _RecordingOp]
    ) -> None:
        module, recorder = migration
        module.downgrade()
        first, *rest = recorder.statements
        assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE" in first
        assert "observations, rating_curves, stations" in first
        assert all(s.startswith("DROP ") for s in rest)

    def test_the_revoke_is_skipped_when_the_role_does_not_exist(
        self, migration: tuple[ModuleType, _RecordingOp]
    ) -> None:
        module, recorder = migration
        module.downgrade()
        assert (
            "FROM pg_catalog.pg_roles WHERE rolname = 'sapphire_operator'"
            in (recorder.statements[0])
        )

    def test_triggers_go_before_the_functions_they_call(
        self, migration: tuple[ModuleType, _RecordingOp]
    ) -> None:
        module, recorder = migration
        module.downgrade()
        kinds = [s.split()[1] for s in recorder.statements[1:]]
        assert kinds == ["TRIGGER"] * 10 + ["FUNCTION"] * 3


class TestUpgradeBuildsTheHardenedFunctions:
    def test_every_function_is_invoker_rights_with_a_pinned_search_path(
        self, migration: tuple[ModuleType, _RecordingOp]
    ) -> None:
        module, recorder = migration
        module.upgrade()
        functions = [s for s in recorder.statements if "CREATE FUNCTION" in s]
        assert len(functions) == 3
        for function in functions:
            assert "SECURITY INVOKER" in function
            assert "SECURITY DEFINER" not in function
            assert "SET search_path = pg_catalog, public, pg_temp" in function

    def test_every_trigger_is_limited_to_the_operator_login(
        self, migration: tuple[ModuleType, _RecordingOp]
    ) -> None:
        module, recorder = migration
        module.upgrade()
        triggers = [s for s in recorder.statements if s.startswith("CREATE TRIGGER")]
        assert len(triggers) == 10
        assert all("WHEN (session_user = 'sapphire_operator')" in t for t in triggers)

    def test_lookups_are_schema_qualified(
        self, migration: tuple[ModuleType, _RecordingOp]
    ) -> None:
        module, _ = migration
        for sql in (module._ROW_FUNCTION, module._STATION_FUNCTION):
            assert not re.search(r"(FROM|JOIN)\s+(stations|tenants)\b", sql)
