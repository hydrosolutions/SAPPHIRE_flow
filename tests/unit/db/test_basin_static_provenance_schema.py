"""Plan 120 Task 0A — structural (DB-free) schema checks.

Pure `sa.MetaData` introspection (no DB) mirroring migration 0039's intent:
`basin_static_packages` + `basin_versions` (+ its partial unique index) +
`model_artifact_basin_versions` exist with the stated PKs/FKs, `basins` gains
an additive nullable `package_id` while every pre-existing column and
`uq_basins_network_code` survive unchanged, `model_artifacts` is structurally
untouched, and the §5a table gains nullable `package_id`/`imported_at` with
its six base columns intact. Migration DATA behaviour (legacy backfill,
non-package insert, atomic-pair regression) is a real-DB concern — see
tests/integration/db/test_migration_0039_basin_static_provenance.py.

New tables are looked up via ``getattr`` on the module (not a top-level
``from ... import``) so that on pre-Task-0A code these tests fail as a real
RED assertion, not a collection-time ImportError.
"""

from __future__ import annotations

import sqlalchemy as sa

from sapphire_flow.db import metadata as db_metadata
from sapphire_flow.db.metadata import model_artifacts, recap_gateway_polygon_bindings


def _table(name: str) -> sa.Table:
    table = getattr(db_metadata, name, None)
    assert table is not None, f"expected sapphire_flow.db.metadata.{name} to exist"
    assert isinstance(table, sa.Table)
    return table


class TestBasinStaticPackagesTable:
    def test_package_id_is_text_primary_key(self) -> None:
        table = _table("basin_static_packages")
        pk_cols = list(table.primary_key.columns)
        assert len(pk_cols) == 1
        assert pk_cols[0].name == "package_id"
        assert isinstance(pk_cols[0].type, sa.Text)

    def test_has_checksums_and_network_columns(self) -> None:
        table = _table("basin_static_packages")
        assert "checksums" in table.c
        assert table.c.checksums.nullable is False
        assert "network" in table.c
        assert table.c.network.nullable is False
        assert "contract_version" in table.c


class TestBasinVersionsTable:
    def test_basin_id_fk_to_basins(self) -> None:
        table = _table("basin_versions")
        fk_targets = {fk.target_fullname for fk in table.c.basin_id.foreign_keys}
        assert fk_targets == {"basins.id"}
        assert table.c.basin_id.nullable is False

    def test_package_id_fk_is_nullable(self) -> None:
        table = _table("basin_versions")
        assert table.c.package_id.nullable is True
        fk_targets = {fk.target_fullname for fk in table.c.package_id.foreign_keys}
        assert fk_targets == {"basin_static_packages.package_id"}

    def test_natural_key_unique_basin_version(self) -> None:
        table = _table("basin_versions")
        unique_constraints = [
            c for c in table.constraints if isinstance(c, sa.UniqueConstraint)
        ]
        col_sets = [{c.name for c in uc.columns} for uc in unique_constraints]
        assert {"basin_id", "version"} in col_sets

    def test_one_current_per_basin_partial_unique_index(self) -> None:
        table = _table("basin_versions")
        indexes = {ix.name: ix for ix in table.indexes}
        assert "uq_basin_versions_one_current_per_basin" in indexes
        ix = indexes["uq_basin_versions_one_current_per_basin"]
        assert ix.unique is True
        assert {c.name for c in ix.columns} == {"basin_id"}
        where_clause = ix.dialect_options["postgresql"]["where"]
        assert where_clause is not None
        assert "superseded_at" in str(where_clause)

    def test_superseded_at_and_gateway_mapping_columns(self) -> None:
        table = _table("basin_versions")
        assert table.c.superseded_at.nullable is True
        assert "gateway_mapping" in table.c


class TestModelArtifactBasinVersionsTable:
    def test_composite_primary_key(self) -> None:
        table = _table("model_artifact_basin_versions")
        pk_names = {c.name for c in table.primary_key.columns}
        assert pk_names == {"model_artifact_id", "basin_version_id"}

    def test_fk_targets(self) -> None:
        table = _table("model_artifact_basin_versions")
        artifact_fk = {
            fk.target_fullname for fk in table.c.model_artifact_id.foreign_keys
        }
        version_fk = {
            fk.target_fullname for fk in table.c.basin_version_id.foreign_keys
        }
        assert artifact_fk == {"model_artifacts.id"}
        assert version_fk == {"basin_versions.id"}

    def test_model_artifacts_gains_no_new_column(self) -> None:
        # ck_model_artifacts_scope_xor + the pre-existing column set stay intact.
        expected = {
            "id",
            "model_id",
            "station_id",
            "group_id",
            "status",
            "artifact_path",
            "sha256_hash",
            "training_period_start",
            "training_period_end",
            "trained_at",
            "promoted_at",
            "promoted_by",
            "superseded_at",
            "created_at",
        }
        assert {c.name for c in model_artifacts.columns} == expected
        check_names = {
            c.name
            for c in model_artifacts.constraints
            if isinstance(c, sa.CheckConstraint)
        }
        assert "ck_model_artifacts_scope_xor" in check_names


class TestBasinsAdditiveColumn:
    _PRE_EXISTING_COLUMNS = {
        "id",
        "code",
        "name",
        "geometry",
        "area_km2",
        "attributes",
        "regional_basin",
        "band_geometries",
        "created_at",
        "network",
    }

    def test_package_id_is_additive_and_nullable(self) -> None:
        basins = db_metadata.basins
        assert "package_id" in basins.c, "expected basins.package_id to exist"
        assert basins.c.package_id.nullable is True
        fk_targets = {fk.target_fullname for fk in basins.c.package_id.foreign_keys}
        assert fk_targets == {"basin_static_packages.package_id"}

    def test_pre_existing_columns_unchanged(self) -> None:
        current = {c.name for c in db_metadata.basins.columns}
        assert self._PRE_EXISTING_COLUMNS.issubset(current)

    def test_uq_basins_network_code_survives(self) -> None:
        basins = db_metadata.basins
        unique_constraints = [
            c for c in basins.constraints if isinstance(c, sa.UniqueConstraint)
        ]
        names = {uc.name for uc in unique_constraints}
        assert "uq_basins_network_code" in names


class TestRecapGatewayPolygonBindingsAdditiveColumns:
    _BASE_SIX = {
        "station_id",
        "basin_id",
        "gateway_hru_name",
        "name",
        "spatial_type",
        "band_id",
    }

    def test_base_six_columns_intact(self) -> None:
        current = {c.name for c in recap_gateway_polygon_bindings.columns}
        assert self._BASE_SIX.issubset(current)

    def test_package_id_and_imported_at_are_nullable_additions(self) -> None:
        table = recap_gateway_polygon_bindings
        assert "package_id" in table.c, "expected package_id to exist"
        assert "imported_at" in table.c, "expected imported_at to exist"
        assert table.c.package_id.nullable is True
        assert table.c.imported_at.nullable is True
        fk_targets = {fk.target_fullname for fk in table.c.package_id.foreign_keys}
        assert fk_targets == {"basin_static_packages.package_id"}
