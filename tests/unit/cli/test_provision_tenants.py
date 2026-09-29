from __future__ import annotations

from itertools import count
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from structlog.testing import capture_logs

from sapphire_flow.cli import provision_tenants
from sapphire_flow.cli.provision_tenants import ensure_declared_tenants
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.tenant import (
    DEFAULT_TENANT_ID,
    DEFAULT_TENANT_NAME,
    DeclaredTenant,
)
from tests.fakes.fake_stores import FakeTenantStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.fixture
def keep_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() reconfigures structlog, which would replace capture_logs()."""
    monkeypatch.setattr(provision_tenants, "configure_cli_logging", lambda: None)


def _ids() -> Callable[[], UUID]:
    counter = count(1000)
    return lambda: UUID(int=next(counter))


class TestEnsureDeclaredTenants:
    def test_creates_missing_tenants_in_sorted_code_order(self) -> None:
        store = FakeTenantStore()
        declared = [
            DeclaredTenant(code="zeta", name="Zeta"),
            DeclaredTenant(code="alpha", name="Alpha"),
        ]
        result = ensure_declared_tenants(store, declared, new_id=_ids())
        assert [t.code for t in result] == ["alpha", "zeta"]
        assert store.fetch_tenant_by_code("alpha") is not None

    def test_existing_tenant_keeps_its_id(self) -> None:
        store = FakeTenantStore()
        result = ensure_declared_tenants(
            store,
            [DeclaredTenant(code="sapphire", name=DEFAULT_TENANT_NAME)],
            new_id=_ids(),
        )
        assert result[0].id == DEFAULT_TENANT_ID

    def test_name_conflict_raises_naming_code_and_both_names(self) -> None:
        store = FakeTenantStore()
        with pytest.raises(ConfigurationError, match="sapphire") as exc:
            ensure_declared_tenants(
                store,
                [DeclaredTenant(code="sapphire", name="Other")],
                new_id=_ids(),
            )
        assert "Other" in str(exc.value)
        assert DEFAULT_TENANT_NAME in str(exc.value)

    def test_injected_id_factory_supplies_the_new_tenants_id(self) -> None:
        store = FakeTenantStore()
        wanted = UUID(int=42)
        result = ensure_declared_tenants(
            store, [DeclaredTenant(code="new-t", name="New")], new_id=lambda: wanted
        )
        assert result[0].id == wanted

    def test_existing_tenant_keeps_its_created_at(self) -> None:
        store = FakeTenantStore()
        before = store.fetch_tenant_by_code("sapphire")
        assert before is not None
        result = ensure_declared_tenants(
            store,
            [DeclaredTenant(code="sapphire", name=DEFAULT_TENANT_NAME)],
            new_id=_ids(),
        )
        assert result[0].created_at == before.created_at

    def test_nothing_declared_does_nothing(self) -> None:
        store = FakeTenantStore()
        assert ensure_declared_tenants(store, [], new_id=_ids()) == []
        assert len(store.fetch_all_tenants()) == 1


class TestMain:
    def test_unset_sapphire_config_is_a_hard_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x@127.0.0.1:1/x")
        assert provision_tenants.main([]) == 1

    def test_config_without_tenants_is_a_no_op_without_touching_the_database(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = tmp_path / "c.toml"
        config.write_text("[logging]\nlevel = 'INFO'\n")
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x@127.0.0.1:1/x")
        assert provision_tenants.main([]) == 0

    def test_invalid_declaration_fails_the_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = tmp_path / "c.toml"
        config.write_text('[tenants.BAD]\nname = "x"\n')
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        assert provision_tenants.main([]) == 1

    def test_declared_count_is_logged_on_every_run(
        self, keep_capture: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = tmp_path / "c.toml"
        config.write_text("[logging]\nlevel = 'INFO'\n")
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        with capture_logs() as logs:
            assert provision_tenants.main([]) == 0
        events = [e for e in logs if e["event"] == "tenants_declared"]
        assert [e["count"] for e in events] == [0]

    def test_main_configures_cli_logging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []
        monkeypatch.setattr(
            provision_tenants, "configure_cli_logging", lambda: calls.append(1)
        )
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
        provision_tenants.main([])
        assert calls == [1]

    def test_malformed_toml_fails_the_step_with_a_logged_cause(
        self, keep_capture: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = tmp_path / "c.toml"
        config.write_text("[tenants\nname = ")
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        with capture_logs() as logs:
            assert provision_tenants.main([]) == 1
        failed = [e for e in logs if e["event"] == "tenant_provisioning_failed"]
        assert failed[0]["cause"] == "TOMLDecodeError"

    def test_unreachable_database_fails_the_step_without_a_traceback(
        self, keep_capture: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = tmp_path / "c.toml"
        config.write_text('[tenants.abc]\nname = "A"\n')
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv(
            "DATABASE_URL", "postgresql+psycopg://u:secretpw@127.0.0.1:1/x"
        )
        with capture_logs() as logs:
            assert provision_tenants.main([]) == 1
        failed = [e for e in logs if e["event"] == "tenant_provisioning_failed"]
        assert failed[0]["cause"] == "OperationalError"
        assert "secretpw" not in str(logs)

    def test_secret_in_an_invalid_declaration_never_reaches_the_logs(
        self, keep_capture: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = tmp_path / "c.toml"
        config.write_text('[tenants.abc]\nname = "A"\npassword = "secretpw"\n')
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        with capture_logs() as logs:
            assert provision_tenants.main([]) == 1
        assert "secretpw" not in str(logs)
        assert "password: extra_forbidden" in str(logs)

    def test_real_logging_pipeline_renders_the_declared_count(
        self, tmp_path: Path
    ) -> None:
        import os
        import subprocess
        import sys

        config = tmp_path / "c.toml"
        config.write_text("[logging]\nlevel = 'INFO'\n")
        env = {**os.environ, "SAPPHIRE_CONFIG": str(config)}
        env.pop("SAPPHIRE_CONFIG_OVERLAY", None)
        done = subprocess.run(
            [sys.executable, "-m", "sapphire_flow.cli.provision_tenants"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert done.returncode == 0
        assert "tenants_declared" in done.stdout + done.stderr
