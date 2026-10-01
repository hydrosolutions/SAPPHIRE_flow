from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa

from sapphire_flow.api.routes import tables
from tests.integration.api.test_dashboard_forecasts import _client, app_overrides_clear
from tests.integration.db.test_migration_provisional_discharge import TABLES
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.integration.store.test_provisional_discharge_store import seed

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness


@pytest.fixture
def api_connection(role_harness: _RoleBootstrapHarness) -> Iterator[sa.Connection]:
    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    # The canary is committed by the disposable owner, not by the API connection.
    with role_harness.owner_engine.begin() as conn:
        seed(conn)
    engine = sa.create_engine(role_harness.role_url("sapphire_api", "api-fixture"))
    try:
        with engine.connect() as conn:
            assert conn.scalar(sa.text("SELECT session_user")) == "sapphire_api"
            yield conn
    finally:
        engine.dispose()


class TestProtectedTableEligibility:
    def test_ordinary_inventory_remains_available(
        self,
        api_connection: sa.Connection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(tables, "_reflected", None)
        client = _client(api_connection)
        try:
            response = client.get("/tables/")
            assert response.status_code == 200
            assert 'href="/tables/stations/"' in response.text
            assert all(name not in response.text for name in TABLES)
            ordinary = client.get("/tables/stations/")
            assert ordinary.status_code == 200
        finally:
            client.close()
            app_overrides_clear()

    @pytest.mark.parametrize("table_name", TABLES)
    @pytest.mark.parametrize("suffix", ["/", "/rows"])
    def test_protected_by_name_is_not_found(
        self,
        api_connection: sa.Connection,
        monkeypatch: pytest.MonkeyPatch,
        table_name: str,
        suffix: str,
    ) -> None:
        monkeypatch.setattr(tables, "_reflected", None)
        client = _client(api_connection)
        try:
            response = client.get(f"/tables/{table_name}{suffix}")
            assert response.status_code == 404
            assert "fixture-reference" not in response.text
            assert "permission denied" not in response.text
        finally:
            client.close()
            app_overrides_clear()
