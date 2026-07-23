"""Plan 120 Phase 2 (Task 2C) — incremental upsert + versioned corrections +
idempotency + the correction→affected-artifact set.

Red-first acceptance tests locked from ``docs/plans/120-basin-static-
importer.md`` Task 2C. The re-run and correction cases MUST FAIL against the
insert-only ``PgBasinStore.store_basin`` path alone (`basin_store.py:43`) —
they exercise ``store/basin_importer.py``'s idempotency/correction branch
(`_package_import_decision` / `PgBasinStore.update_basin_from_package`).
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from shapely.geometry import MultiPolygon, Polygon

from sapphire_flow.db.metadata import (
    basin_versions,
    basins,
    model_artifact_basin_versions,
    models,
)
from sapphire_flow.exceptions import BasinPackageRejectedError
from sapphire_flow.services.basin_package_loader import (
    evaluate_basin_acceptance,
    load_basin_package,
)
from sapphire_flow.store.basin_importer import import_basin_package
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ids import ArtifactId, BasinId, ModelId, StationId
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from sapphire_flow.types.basin_package import (
        BasinPackageAcceptanceReport,
        LoadedBasinPackage,
    )

FIXTURE_DIR = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "basin_static"
    / "nepal-dhm-basins"
)

_T0 = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_T1 = ensure_utc(datetime(2025, 6, 1, tzinfo=UTC))
_T2 = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


def _clock() -> UtcDatetime:
    return _T2


def _seed_station(
    conn: sa.Connection, *, code: str = "123", network: str = "dhm"
) -> StationId:
    station = make_station_config(
        station_id=StationId(uuid.uuid4()), code=code, network=network, basin_id=None
    )
    PgStationStore(conn).store_station(station)
    return station.id


def _load_and_accept(
    station_id: StationId,
    *,
    package_id: str | None = None,
    area_km2: float | None = None,
    checksums: dict[str, str] | None = None,
) -> tuple[LoadedBasinPackage, BasinPackageAcceptanceReport]:
    """Load the real fixture and optionally mutate it into a "new package"
    variant (different `package_id`/checksums/area_km2) to simulate a
    re-import or a correction WITHOUT touching any file on disk — Task
    2C/2A operate purely on the Phase-1 domain objects, never re-reading
    files."""
    loaded = load_basin_package(FIXTURE_DIR)
    if package_id is not None:
        loaded = dataclasses.replace(
            loaded, manifest=dataclasses.replace(loaded.manifest, package_id=package_id)
        )
    if area_km2 is not None:
        new_basins = tuple(
            dataclasses.replace(b, area_km2=area_km2) if b.basin_code == "123" else b
            for b in loaded.basins
        )
        loaded = dataclasses.replace(loaded, basins=new_basins)
    if checksums is not None:
        loaded = dataclasses.replace(loaded, computed_checksums=checksums)
    report = evaluate_basin_acceptance(
        loaded,
        resolve_station=lambda code, network: (
            station_id if (code, network) == ("123", "dhm") else None
        ),
    )
    return loaded, report


def _current_version_id(conn: sa.Connection, basin_id: BasinId) -> uuid.UUID:
    return conn.execute(
        sa.select(basin_versions.c.id).where(
            sa.and_(
                basin_versions.c.basin_id == basin_id,
                basin_versions.c.superseded_at.is_(None),
            )
        )
    ).scalar_one()


class TestReimportAndCorrections:
    def test_reimport_identical_package_is_noop(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded, report = _load_and_accept(station_id)
        first = import_basin_package(db_connection, loaded, report, clock=_clock)
        assert first.already_imported is False

        loaded_again, report_again = _load_and_accept(station_id)
        second = import_basin_package(
            db_connection, loaded_again, report_again, clock=_clock
        )

        assert second.already_imported is True
        assert second.imported_basins == ()
        rows = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basins)
            .where(sa.and_(basins.c.code == "123", basins.c.network == "dhm"))
        ).scalar_one()
        assert rows == 1

    def test_reimport_same_package_id_different_checksum_rejects(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded, report = _load_and_accept(station_id)
        import_basin_package(db_connection, loaded, report, clock=_clock)

        mutated_checksums = dict(loaded.computed_checksums)
        mutated_checksums["basins.gpkg"] = "sha256:" + "c" * 64
        loaded_mutated, report_mutated = _load_and_accept(
            station_id, checksums=mutated_checksums
        )

        with pytest.raises(BasinPackageRejectedError, match="different"):
            import_basin_package(
                db_connection, loaded_mutated, report_mutated, clock=_clock
            )

    def test_correction_updates_projection_and_preserves_prior_snapshot(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded, report = _load_and_accept(station_id)
        import_basin_package(db_connection, loaded, report, clock=_clock)
        basin_id = db_connection.execute(
            sa.select(basins.c.id).where(basins.c.code == "123")
        ).scalar_one()
        original_area = db_connection.execute(
            sa.select(basins.c.area_km2).where(basins.c.id == basin_id)
        ).scalar_one()

        corrected_loaded, corrected_report = _load_and_accept(
            station_id,
            package_id="nepal-dhm-basins-v2",
            area_km2=original_area + 500.0,
            checksums={"basins.gpkg": "sha256:" + "d" * 64},
        )
        result = import_basin_package(
            db_connection, corrected_loaded, corrected_report, clock=_clock
        )

        assert len(result.imported_basins) == 1
        corrected = result.imported_basins[0]
        assert corrected.outcome == "corrected"
        assert corrected.material_change is True

        new_basin_row = (
            db_connection.execute(sa.select(basins).where(basins.c.id == basin_id))
            .mappings()
            .one()
        )
        assert new_basin_row["area_km2"] == pytest.approx(original_area + 500.0)
        assert new_basin_row["package_id"] == "nepal-dhm-basins-v2"

        version_rows = (
            db_connection.execute(
                sa.select(basin_versions)
                .where(basin_versions.c.basin_id == basin_id)
                .order_by(basin_versions.c.version)
            )
            .mappings()
            .all()
        )
        assert len(version_rows) == 2
        v1, v2 = version_rows
        assert v1["version"] == 1
        assert v1["superseded_at"] is not None
        assert v1["area_km2"] == pytest.approx(original_area)
        assert v1["package_id"] == "nepal-dhm-basins"
        assert v2["version"] == 2
        assert v2["superseded_at"] is None
        assert v2["area_km2"] == pytest.approx(original_area + 500.0)
        assert v2["package_id"] == "nepal-dhm-basins-v2"

    def test_basin_absent_from_package_left_untouched(
        self, db_connection: sa.Connection
    ) -> None:
        """Decision A: a basin already in `basins` but NOT present in the
        incoming package is left COMPLETELY untouched — no delete, no
        version bump, no flag."""
        unrelated_id = BasinId(uuid.uuid4())
        unrelated = Basin(
            id=unrelated_id,
            code="999",
            name="Unrelated basin",
            geometry=MultiPolygon(
                [Polygon([(2.0, 2.0), (2.0, 3.0), (3.0, 3.0), (3.0, 2.0)])]
            ),
            area_km2=7.0,
            attributes={"pre_existing": 1.0},
            regional_basin=None,
            band_geometries=None,
            created_at=_T0,
            network="dhm",
            package_id=None,
        )
        PgBasinStore(db_connection).store_basin(unrelated)
        unrelated_version_id_before = _current_version_id(db_connection, unrelated_id)

        station_id = _seed_station(db_connection)
        loaded, report = _load_and_accept(station_id)
        import_basin_package(db_connection, loaded, report, clock=_clock)

        unrelated_after = (
            db_connection.execute(sa.select(basins).where(basins.c.id == unrelated_id))
            .mappings()
            .one()
        )
        assert unrelated_after["area_km2"] == pytest.approx(7.0)
        assert unrelated_after["attributes"] == {"pre_existing": 1.0}
        assert unrelated_after["package_id"] is None
        assert (
            _current_version_id(db_connection, unrelated_id)
            == unrelated_version_id_before
        )
        version_count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_versions)
            .where(basin_versions.c.basin_id == unrelated_id)
        ).scalar_one()
        assert version_count == 1


def _seed_model(conn: sa.Connection) -> ModelId:
    mid = ModelId(f"basin_import_test_model_{uuid.uuid4().hex[:8]}")
    conn.execute(
        sa.insert(models).values(
            id=mid,
            display_name="Basin Import Test Model",
            artifact_scope="station",
            description="Plan 120 Task 2C affected-artifact-set test",
        )
    )
    return mid


def _seed_artifact(
    conn: sa.Connection, tmp_path: Path, model_id: ModelId, station_id: StationId
) -> ArtifactId:
    store = PgModelArtifactStore(conn, tmp_path)
    artifact_id, _ = store.store_artifact(
        model_id, b"payload", _T0, _T1, _T2, station_id=station_id
    )
    return artifact_id


def _link_lineage(
    conn: sa.Connection, artifact_id: ArtifactId, basin_version_id: uuid.UUID
) -> None:
    conn.execute(
        sa.insert(model_artifact_basin_versions).values(
            model_artifact_id=artifact_id, basin_version_id=basin_version_id
        )
    )


class TestCorrectionAffectedArtifacts:
    def test_correction_emits_exactly_the_artifacts_trained_on_the_superseded_version(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        station_id = _seed_station(db_connection)
        model_id = _seed_model(db_connection)

        # v1: two artifacts trained on it.
        loaded_v1, report_v1 = _load_and_accept(station_id)
        import_basin_package(db_connection, loaded_v1, report_v1, clock=_clock)
        basin_id = db_connection.execute(
            sa.select(basins.c.id).where(basins.c.code == "123")
        ).scalar_one()
        v1_id = _current_version_id(db_connection, basin_id)
        artifact_1 = _seed_artifact(db_connection, tmp_path, model_id, station_id)
        artifact_2 = _seed_artifact(db_connection, tmp_path, model_id, station_id)
        _link_lineage(db_connection, artifact_1, v1_id)
        _link_lineage(db_connection, artifact_2, v1_id)

        # v2 (supersedes v1): one artifact trained on it.
        loaded_v2, report_v2 = _load_and_accept(
            station_id,
            package_id="nepal-dhm-basins-v2",
            area_km2=50.0,
            checksums={"basins.gpkg": "sha256:" + "e" * 64},
        )
        import_basin_package(db_connection, loaded_v2, report_v2, clock=_clock)
        v2_id = _current_version_id(db_connection, basin_id)
        artifact_3 = _seed_artifact(db_connection, tmp_path, model_id, station_id)
        _link_lineage(db_connection, artifact_3, v2_id)

        # v3 (supersedes v2) — the correction under test.
        loaded_v3, report_v3 = _load_and_accept(
            station_id,
            package_id="nepal-dhm-basins-v3",
            area_km2=75.0,
            checksums={"basins.gpkg": "sha256:" + "f" * 64},
        )
        result = import_basin_package(db_connection, loaded_v3, report_v3, clock=_clock)

        assert len(result.imported_basins) == 1
        affected = set(result.imported_basins[0].affected_artifact_ids)
        # MUST be exactly {artifact_3} — not all three (that would mean the
        # query ignores which version each artifact trained on), and not
        # none (that would mean the query never fires).
        assert affected == {artifact_3}
        assert artifact_1 not in affected
        assert artifact_2 not in affected
