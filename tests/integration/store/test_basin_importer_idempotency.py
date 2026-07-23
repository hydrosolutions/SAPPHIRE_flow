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
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from shapely.geometry import MultiPolygon, Polygon

from sapphire_flow.db.metadata import (
    basin_static_packages,
    basin_versions,
    basins,
    model_artifact_basin_versions,
    models,
    recap_gateway_polygon_bindings,
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
from sapphire_flow.types.basin_package import ClimatologyWindow, SourceDataset
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ids import ArtifactId, BasinId, ModelId, StationId
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from collections.abc import Callable

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


def _replace_manifest(
    loaded: LoadedBasinPackage, **changes: object
) -> LoadedBasinPackage:
    return dataclasses.replace(
        loaded, manifest=dataclasses.replace(loaded.manifest, **changes)
    )


# Finding 1: the canonical fingerprint must cover EVERY validated manifest field
# (except `package_id`, which cannot change while staying the SAME package_id —
# a different package_id is a new package, not an immutability violation). Each
# mutation is additive/non-conflicting so the basin stays ACCEPTED — the reject
# is unambiguously the fingerprint immutability check, not a load/acceptance
# side effect. Neuter any field's inclusion in `compute_package_fingerprint` and
# ONLY that field's case flips to a real AssertionError (fingerprint unchanged).
_MANIFEST_FIELD_MUTATIONS: list[
    tuple[str, Callable[[LoadedBasinPackage], LoadedBasinPackage]]
] = [
    (
        "contract_version",
        lambda pkg: _replace_manifest(
            pkg, contract_version=pkg.manifest.contract_version + "-mutated"
        ),
    ),
    (
        "created_at",
        lambda pkg: _replace_manifest(pkg, created_at="2099-01-01T00:00:00+00:00"),
    ),
    (
        "network",
        lambda pkg: _replace_manifest(pkg, network=pkg.manifest.network + "-mutated"),
    ),
    ("crs", lambda pkg: _replace_manifest(pkg, crs="EPSG:3857")),
    (
        "extractor_name",
        lambda pkg: _replace_manifest(
            pkg, extractor_name=pkg.manifest.extractor_name + "-mutated"
        ),
    ),
    (
        "extractor_version",
        lambda pkg: _replace_manifest(
            pkg, extractor_version=pkg.manifest.extractor_version + "-mutated"
        ),
    ),
    (
        "source_datasets",
        lambda pkg: _replace_manifest(
            pkg,
            source_datasets=(
                *pkg.manifest.source_datasets,
                SourceDataset(name="extra_ds", version="1", purpose="misc"),
            ),
        ),
    ),
    (
        "gateway_hru_names",
        lambda pkg: _replace_manifest(
            pkg, gateway_hru_names=pkg.manifest.gateway_hru_names | {"g_extra_hru"}
        ),
    ),
    (
        "climatology_window",
        lambda pkg: _replace_manifest(
            pkg,
            climatology_window=ClimatologyWindow(
                start=date(1988, 1, 1), end=date(2001, 12, 31)
            ),
        ),
    ),
    (
        "files",
        lambda pkg: _replace_manifest(
            pkg, files={**pkg.manifest.files, "extra.txt": "extra.txt"}
        ),
    ),
    (
        "checksums",
        lambda pkg: _replace_manifest(
            pkg,
            checksums={**pkg.manifest.checksums, "extra.txt": "sha256:" + "0" * 64},
        ),
    ),
]


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

    def test_same_package_id_manifest_only_mutation_rejects(
        self, db_connection: sa.Connection
    ) -> None:
        """Finding 3: idempotency compares the CANONICAL FINGERPRINT, not the
        payload checksums alone. A re-import under the SAME `package_id` with
        IDENTICAL payload checksums but a changed manifest metadata field
        (here: `climatology_window`) is an immutability violation, not a silent
        `already_imported` no-op (contract §11, 04:676)."""
        station_id = _seed_station(db_connection)
        loaded, report = _load_and_accept(station_id)
        import_basin_package(db_connection, loaded, report, clock=_clock)

        # SAME package_id, SAME computed_checksums — only the manifest's
        # climatology_window changes.
        mutated = dataclasses.replace(
            loaded,
            manifest=dataclasses.replace(
                loaded.manifest,
                climatology_window=ClimatologyWindow(
                    start=date(1990, 1, 1), end=date(2019, 12, 31)
                ),
            ),
        )
        mutated_report = evaluate_basin_acceptance(
            mutated,
            resolve_station=lambda code, network: (
                station_id if (code, network) == ("123", "dhm") else None
            ),
        )
        # The fingerprint must actually cover the mutated manifest field —
        # else this test could not distinguish it from an identical re-import.
        assert mutated_report.fingerprint != report.fingerprint
        assert mutated.computed_checksums == loaded.computed_checksums

        with pytest.raises(BasinPackageRejectedError, match="fingerprint"):
            import_basin_package(db_connection, mutated, mutated_report, clock=_clock)

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


class TestCorrectionStationIdentityGuard:
    """Finding 2 (major): a correction (new `package_id` over an existing
    `(network, basin_code)`) whose resolved station differs from the basin's
    EXISTING station binding must be REJECTED — the station association is part
    of stable basin identity. A silent migration would leave BOTH stations
    bound (two §5a rows)."""

    def test_correction_naming_different_station_rejected_leaves_all_unchanged(
        self, db_connection: sa.Connection
    ) -> None:
        station_a = _seed_station(db_connection, code="123", network="dhm")
        loaded, report = _load_and_accept(station_a)
        import_basin_package(db_connection, loaded, report, clock=_clock)
        basin_id = db_connection.execute(
            sa.select(basins.c.id).where(basins.c.code == "123")
        ).scalar_one()

        # A DIFFERENT station (same network, code "999") the correction wants
        # to migrate the SAME basin (basin_code 123) onto.
        station_b = _seed_station(db_connection, code="999", network="dhm")
        corrected_basins = tuple(
            dataclasses.replace(b, station_code="999", name="g_999")
            if b.basin_code == "123"
            else b
            for b in loaded.basins
        )
        corrected = dataclasses.replace(
            loaded,
            manifest=dataclasses.replace(
                loaded.manifest, package_id="nepal-dhm-basins-v2"
            ),
            basins=corrected_basins,
            computed_checksums={"basins.gpkg": "sha256:" + "a" * 64},
        )
        corrected_report = evaluate_basin_acceptance(
            corrected,
            resolve_station=lambda code, network: (
                station_b if (code, network) == ("999", "dhm") else None
            ),
        )
        assert corrected_report.decisions[0].outcome == "accepted"

        with pytest.raises(BasinPackageRejectedError, match="station"):
            import_basin_package(
                db_connection, corrected, corrected_report, clock=_clock
            )

        # Everything is UNCHANGED: station A still bound, B still unbound,
        # a single version, station A's the only §5a binding, and no v2
        # provenance row.
        stations_store = PgStationStore(db_connection)
        assert stations_store.fetch_station(station_a).basin_id == basin_id
        assert stations_store.fetch_station(station_b).basin_id is None
        version_count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_versions)
            .where(basin_versions.c.basin_id == basin_id)
        ).scalar_one()
        assert version_count == 1
        binding_stations = (
            db_connection.execute(
                sa.select(recap_gateway_polygon_bindings.c.station_id).where(
                    recap_gateway_polygon_bindings.c.basin_id == basin_id
                )
            )
            .scalars()
            .all()
        )
        assert set(binding_stations) == {station_a}
        pkg_count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_static_packages)
            .where(basin_static_packages.c.package_id == "nepal-dhm-basins-v2")
        ).scalar_one()
        assert pkg_count == 0


class TestManifestFieldFingerprintCoverage:
    """Finding 1: the canonical fingerprint must cover EVERY validated manifest
    field, so a re-import under the SAME `package_id` with IDENTICAL payload
    checksums but a changed manifest-metadata field is an immutability violation
    (contract §11, 04:676), never a silent `already_imported` no-op."""

    @pytest.mark.parametrize(
        ("field_name", "mutate"),
        _MANIFEST_FIELD_MUTATIONS,
        ids=[name for name, _ in _MANIFEST_FIELD_MUTATIONS],
    )
    def test_manifest_field_mutation_under_same_package_id_rejects(
        self,
        db_connection: sa.Connection,
        field_name: str,
        mutate: Callable[[LoadedBasinPackage], LoadedBasinPackage],
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded, report = _load_and_accept(station_id)
        import_basin_package(db_connection, loaded, report, clock=_clock)

        mutated = mutate(loaded)
        # SAME package_id and SAME computed payload checksums — the ONLY change
        # is this one manifest-metadata field.
        assert mutated.manifest.package_id == loaded.manifest.package_id
        assert mutated.computed_checksums == loaded.computed_checksums

        mutated_report = evaluate_basin_acceptance(
            mutated,
            resolve_station=lambda code, network: (
                station_id if (code, network) == ("123", "dhm") else None
            ),
        )
        # The basin must stay accepted (the mutation is additive/non-conflicting),
        # so the reject is unambiguously the fingerprint immutability check.
        assert mutated_report.decisions[0].outcome == "accepted"
        # The fingerprint MUST cover this field — else this case cannot be
        # distinguished from an identical re-import (red-before proof: neutering
        # this field's inclusion flips THIS assertion to a real failure).
        assert mutated_report.fingerprint != report.fingerprint, (
            f"fingerprint does not cover manifest field {field_name!r}"
        )

        with pytest.raises(BasinPackageRejectedError, match="fingerprint"):
            import_basin_package(db_connection, mutated, mutated_report, clock=_clock)


class TestLegacyNullFingerprintRow:
    """Finding 2: a pre-0040 `basin_static_packages` row carries a NULL
    `fingerprint`. Row-absent and row-present-with-NULL-fingerprint must NOT be
    conflated — a re-import over a legacy row must REJECT explicitly (immutability
    cannot be verified) instead of falling through to a provenance PRIMARY-KEY
    IntegrityError."""

    def test_reimport_over_legacy_null_fingerprint_row_rejects(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded, report = _load_and_accept(station_id)

        # Seed a legacy/pre-0040 provenance row for this package_id: all the
        # 0039 NOT-NULL columns present, but a NULL fingerprint (added by 0040).
        db_connection.execute(
            sa.insert(basin_static_packages).values(
                package_id=loaded.manifest.package_id,
                network=loaded.manifest.network,
                contract_version=loaded.manifest.contract_version,
                checksums=loaded.computed_checksums,
                fingerprint=None,
            )
        )

        # Without the fix, the NULL fingerprint reads as "package not found",
        # the importer proceeds to INSERT a duplicate provenance row, and the
        # call dies with an IntegrityError (NOT a domain reject) — so this
        # `pytest.raises(BasinPackageRejectedError)` is the red-before proof.
        with pytest.raises(BasinPackageRejectedError, match="legacy"):
            import_basin_package(db_connection, loaded, report, clock=_clock)


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
