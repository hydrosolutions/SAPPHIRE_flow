from __future__ import annotations

from pathlib import Path

from sapphire_flow.api.routes.tables import SAPPHIRE_TABLES

PROTECTED_NEPAL21_TABLES = {
    "restricted_delivery_releases",
    "restricted_delivery_release_witnesses",
    "restricted_delivery_generations",
    "dhm_delivery_origins",
    "dhm_origin_sets",
    "dhm_origin_set_members",
    "dhm_rating_declarations",
    "dhm_delivery_observation_attributions",
    "expired_rating_test_permissions",
}


def test_nepal21_protected_tables_are_hidden_from_browser() -> None:
    assert PROTECTED_NEPAL21_TABLES.isdisjoint(SAPPHIRE_TABLES)


def test_bootstrap_excludes_nepal21_tables_from_runtime_select_grant() -> None:
    sql = Path("docker/bootstrap-roles.sql").read_text()
    for table in PROTECTED_NEPAL21_TABLES:
        assert table in sql
    assert "sapphire_dhm21_delivery_writer" in sql
    assert "public.sf_lock_dhm21_delivery_write(uuid,uuid)" in sql
