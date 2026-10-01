from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness

import pytest
import sqlalchemy as sa

from tests.integration.db.test_migration_provisional_discharge import TABLES
from tests.integration.db.test_role_bootstrap import role_harness as role_harness


class TestProvisionalDischargeRoleBoundary:
    @pytest.mark.parametrize(
        "role", ["sapphire_api", "sapphire_worker", "sapphire_operator"]
    )
    def test_real_login_cannot_read_or_write_protected_data(
        self, role_harness: _RoleBootstrapHarness, role: str
    ) -> None:
        result = role_harness.run_bootstrap(
            "api-fixture", "worker-fixture", operator_password="operator-fixture"
        )
        assert result.returncode == 0, result.stderr
        password = {
            "sapphire_api": "api-fixture",
            "sapphire_worker": "worker-fixture",
            "sapphire_operator": "operator-fixture",
        }[role]
        url = role_harness.role_url(role, password)
        engine = sa.create_engine(url)
        try:
            with engine.connect() as conn:
                assert conn.scalar(sa.text("SELECT session_user")) == role
                if role != "sapphire_operator":
                    assert (
                        conn.execute(
                            sa.text(
                                "SELECT tenant_id, state "
                                "FROM provisional_discharge_permissions"
                            )
                        ).all()
                        == []
                    )
            for table in TABLES:
                assert role_harness.denied(url, f"SELECT * FROM {table}")
                assert role_harness.denied(url, f"INSERT INTO {table} DEFAULT VALUES")
                assert role_harness.denied(url, f"DELETE FROM {table}")
                assert role_harness.denied(url, f"TRUNCATE {table} CASCADE")
        finally:
            engine.dispose()

    def test_rebootstrap_revokes_stale_column_grants(self, role_harness) -> None:
        role_harness.run_bootstrap("api-fixture", "worker-fixture")
        with role_harness.owner_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "GRANT SELECT(content), INSERT(content), UPDATE(content) "
                    "ON measurement_feed_evidence TO sapphire_api"
                )
            )
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode == 0, result.stderr
        with role_harness.owner_engine.connect() as conn:
            for privilege in ("SELECT", "INSERT", "UPDATE"):
                assert not conn.scalar(
                    sa.text(
                        "SELECT has_column_privilege('sapphire_api', "
                        "'measurement_feed_evidence', 'content', :privilege)"
                    ),
                    {"privilege": privilege},
                )
