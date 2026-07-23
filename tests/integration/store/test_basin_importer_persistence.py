"""Plan 120 Phase 2 (Task 2A + the Task 2B package-driven §5a population).

Red-first acceptance tests locked from ``docs/plans/120-basin-static-
importer.md`` Task 2A/2B, exercised against the real, contract-compliant
fixture at ``tests/fixtures/basin_static/nepal-dhm-basins/`` (loaded via the
Phase-1 loader — ``services/basin_package_loader.py`` — then persisted via
``store/basin_importer.py``).
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
from sqlalchemy.exc import IntegrityError

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
from sapphire_flow.store.model_artifact_lineage import record_artifact_basin_lineage
from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ids import BasinId, ModelId, PackageId, StationId
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

_CLOCK_VALUE = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


def _clock() -> UtcDatetime:
    return _CLOCK_VALUE


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
) -> tuple[LoadedBasinPackage, BasinPackageAcceptanceReport]:
    loaded = load_basin_package(FIXTURE_DIR)
    report = evaluate_basin_acceptance(
        loaded,
        resolve_station=lambda code, network: (
            station_id if (code, network) == ("123", "dhm") else None
        ),
    )
    return loaded, report


class TestDissolveIntoBasins:
    """Task 2A — a NEW ``(network, basin_code)`` dissolves into `basins` +
    a `version=1` snapshot + `basin_static_packages` provenance."""

    def test_accepted_package_writes_basin_and_version_rows(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded, report = _load_and_accept(station_id)
        assert len(report.accepted) == 1

        result = import_basin_package(db_connection, loaded, report, clock=_clock)

        assert result.already_imported is False
        assert len(result.imported_basins) == 1
        imported = result.imported_basins[0]
        assert imported.outcome == "inserted"
        assert imported.material_change is False

        basin_row = (
            db_connection.execute(
                sa.select(basins).where(
                    sa.and_(basins.c.code == "123", basins.c.network == "dhm")
                )
            )
            .mappings()
            .one()
        )
        assert basin_row["package_id"] == "nepal-dhm-basins"
        assert basin_row["geometry"] is not None
        assert basin_row["attributes"]
        assert set(basin_row["attributes"]) == set(
            loaded.static_attributes["nepal_123"]
        )

        version_rows = (
            db_connection.execute(
                sa.select(basin_versions).where(
                    basin_versions.c.basin_id == basin_row["id"]
                )
            )
            .mappings()
            .all()
        )
        assert len(version_rows) == 1
        assert version_rows[0]["version"] == 1
        assert version_rows[0]["superseded_at"] is None
        assert version_rows[0]["package_id"] == "nepal-dhm-basins"
        assert version_rows[0]["attributes"] == basin_row["attributes"]

        package_row = (
            db_connection.execute(
                sa.select(basin_static_packages).where(
                    basin_static_packages.c.package_id == "nepal-dhm-basins"
                )
            )
            .mappings()
            .one()
        )
        assert package_row["checksums"] == loaded.computed_checksums
        assert package_row["network"] == "dhm"

    def test_null_attribute_round_trips_as_json_null(
        self, db_connection: sa.Connection
    ) -> None:
        """`{"foo": null}` — a present key with a JSON null value — never
        `attributes IS NULL` and never a `0` sentinel (04:352-354/04:422)."""
        station_id = _seed_station(db_connection)
        loaded = load_basin_package(FIXTURE_DIR)
        attr_name = next(iter(loaded.static_attributes["nepal_123"]))
        nulled = dict(loaded.static_attributes)
        nulled["nepal_123"] = {**nulled["nepal_123"], attr_name: None}
        loaded = dataclasses.replace(loaded, static_attributes=nulled)
        report = evaluate_basin_acceptance(
            loaded,
            resolve_station=lambda code, network: (
                station_id if (code, network) == ("123", "dhm") else None
            ),
        )

        import_basin_package(db_connection, loaded, report, clock=_clock)

        attributes = db_connection.execute(
            sa.select(basins.c.attributes).where(basins.c.code == "123")
        ).scalar_one()
        assert attr_name in attributes
        assert attributes[attr_name] is None


class TestFKOrderNegative:
    """`basins`/`basin_versions`/the §5a table all carry an IMMEDIATE FK to
    `basin_static_packages` — inserting either before the package row
    raises a live `ForeignKeyViolation`. `import_basin_package` writes the
    package row FIRST (canonical step 2), so the same call completes
    without it (proven by `TestDissolveIntoBasins` above)."""

    def test_basin_before_package_row_raises_fk_violation(
        self, db_connection: sa.Connection
    ) -> None:
        geometry = MultiPolygon(
            [Polygon([(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)])]
        )
        basin = Basin(
            id=BasinId(uuid.uuid4()),
            code="FK-ORDER-01",
            name="FK order test",
            geometry=geometry,
            area_km2=1.0,
            attributes=None,
            regional_basin=None,
            band_geometries=None,
            created_at=_CLOCK_VALUE,
            network="dhm",
            package_id=PackageId("does-not-exist-yet"),
        )
        store = PgBasinStore(db_connection)

        with pytest.raises(IntegrityError):
            store.store_basin(basin)


class TestFiveAMappingPopulation:
    """Task 2B — the package-driven population of the §5a mapping table."""

    def test_basin_average_row_written_with_provenance(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded, report = _load_and_accept(station_id)

        import_basin_package(db_connection, loaded, report, clock=_clock)

        rows = (
            db_connection.execute(
                sa.select(recap_gateway_polygon_bindings).where(
                    recap_gateway_polygon_bindings.c.station_id == station_id
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["spatial_type"] == "basin_average"
        assert row["band_id"] is None
        assert row["gateway_hru_name"] == "nepal_dhm_v1"
        assert row["name"] == "g_123"
        # Genuine Task 2B red-first case (unlike the band_geometries round
        # trip, already green as of Task 0A): provenance columns written.
        assert row["package_id"] == "nepal-dhm-basins"
        assert row["imported_at"] is not None

    def test_package_without_bands_gpkg_has_null_band_geometries(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded, report = _load_and_accept(station_id)
        assert loaded.bands is None  # the fixture ships no bands.gpkg

        import_basin_package(db_connection, loaded, report, clock=_clock)

        band_geometries = db_connection.execute(
            sa.select(basins.c.band_geometries).where(basins.c.code == "123")
        ).scalar_one()
        assert not band_geometries

    def test_correction_with_hru_rename_leaves_exactly_one_row(
        self, db_connection: sa.Connection
    ) -> None:
        """A correction (new package_id) that renames `gateway_hru_name`
        leaves EXACTLY ONE `basin_average` row for the station — not two,
        and not an `IntegrityError` against
        `uq_recap_gateway_polygon_bindings_one_basin_average_per_station`."""
        station_id = _seed_station(db_connection)
        loaded, report = _load_and_accept(station_id)
        import_basin_package(db_connection, loaded, report, clock=_clock)

        renamed_basins = tuple(
            dataclasses.replace(b, gateway_hru_name="nepal_dhm_v2")
            if b.basin_code == "123"
            else b
            for b in loaded.basins
        )
        corrected_manifest = dataclasses.replace(
            loaded.manifest,
            package_id="nepal-dhm-basins-v2",
            gateway_hru_names=frozenset({"nepal_dhm_v2"}),
        )
        corrected = dataclasses.replace(
            loaded,
            manifest=corrected_manifest,
            basins=renamed_basins,
            computed_checksums={"basins.gpkg": "sha256:" + "b" * 64},
        )
        corrected_report = evaluate_basin_acceptance(
            corrected,
            resolve_station=lambda code, network: (
                station_id if (code, network) == ("123", "dhm") else None
            ),
        )

        result = import_basin_package(
            db_connection, corrected, corrected_report, clock=_clock
        )

        assert result.imported_basins[0].outcome == "corrected"
        rows = (
            db_connection.execute(
                sa.select(recap_gateway_polygon_bindings).where(
                    sa.and_(
                        recap_gateway_polygon_bindings.c.station_id == station_id,
                        recap_gateway_polygon_bindings.c.spatial_type
                        == "basin_average",
                    )
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 1
        assert rows[0]["gateway_hru_name"] == "nepal_dhm_v2"


def _resolver(station_id: StationId):  # noqa: ANN202 - test helper
    return lambda code, network: (
        station_id if (code, network) == ("123", "dhm") else None
    )


class TestReportPackageBinding:
    """Finding 1(a): the acceptance report must be BOUND to the exact loaded
    package via the canonical fingerprint. A report whose fingerprint does not
    equal the loaded package's fingerprint is rejected BEFORE any idempotency
    check or write — a report can never be silently applied to a different
    package than the one it was evaluated on."""

    def test_report_fingerprint_mismatch_rejected_before_any_write(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded, report = _load_and_accept(station_id)
        tampered = dataclasses.replace(report, fingerprint="sha256:" + "0" * 64)

        with pytest.raises(BasinPackageRejectedError, match="fingerprint"):
            import_basin_package(db_connection, loaded, tampered, clock=_clock)

        package_count = db_connection.execute(
            sa.select(sa.func.count()).select_from(basin_static_packages)
        ).scalar_one()
        assert package_count == 0


class TestDecisionCoverage:
    """Finding 1(b): the decision set must be a 1:1 cover of the package's
    basins. A report that omits (or double-decides, or over-decides) a package
    basin is rejected before any write."""

    def test_report_missing_a_package_basin_rejected(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded, report = _load_and_accept(station_id)
        # Drop the only decision — the report no longer covers the package.
        empty_report = dataclasses.replace(report, decisions=())

        with pytest.raises(BasinPackageRejectedError, match="cover"):
            import_basin_package(db_connection, loaded, empty_report, clock=_clock)

        package_count = db_connection.execute(
            sa.select(sa.func.count()).select_from(basin_static_packages)
        ).scalar_one()
        assert package_count == 0


class TestWriteInvariantReenforcement:
    """Finding 1(c): the write boundary INDEPENDENTLY re-enforces the
    persistence-critical invariants for every accepted basin — it never trusts
    the acceptance label. For EACH invariant, a genuinely held basin is flipped
    hold->accepted and the importer MUST still reject BEFORE the
    `basin_static_packages` provenance row is written."""

    def _held_report_flipped_to_accepted(
        self,
        loaded: LoadedBasinPackage,
        station_id: StationId,
        *,
        keep_hold_reasons: bool,
        assigned_model_features=None,  # noqa: ANN001 - test seam
    ) -> BasinPackageAcceptanceReport:
        report = evaluate_basin_acceptance(
            loaded,
            resolve_station=_resolver(station_id),
            assigned_model_features=assigned_model_features,
        )
        assert report.decisions[0].outcome == "onboarding_hold"
        flipped = tuple(
            dataclasses.replace(
                d,
                outcome="accepted",
                hold_reasons=d.hold_reasons if keep_hold_reasons else (),
            )
            for d in report.decisions
        )
        return dataclasses.replace(report, decisions=flipped)

    def _assert_no_provenance(self, conn: sa.Connection) -> None:
        count = conn.execute(
            sa.select(sa.func.count()).select_from(basin_static_packages)
        ).scalar_one()
        assert count == 0

    def test_invalid_geometry_flipped_to_accepted_rejected(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded = load_basin_package(FIXTURE_DIR)
        # A self-intersecting (bow-tie) polygon — a valid Polygon TYPE but
        # topologically invalid; PostGIS would store it happily without the
        # write-boundary re-derivation, so this proves fail-before cleanly.
        bowtie = Polygon([(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0), (0.0, 0.0)])
        assert not bowtie.is_valid
        bad_basins = tuple(
            dataclasses.replace(b, geometry=bowtie) if b.basin_code == "123" else b
            for b in loaded.basins
        )
        bad_loaded = dataclasses.replace(loaded, basins=bad_basins)
        report = self._held_report_flipped_to_accepted(
            bad_loaded, station_id, keep_hold_reasons=False
        )

        with pytest.raises(BasinPackageRejectedError, match="geometry"):
            import_basin_package(db_connection, bad_loaded, report, clock=_clock)
        self._assert_no_provenance(db_connection)

    def test_non_positive_area_flipped_to_accepted_rejected(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded = load_basin_package(FIXTURE_DIR)
        bad_basins = tuple(
            dataclasses.replace(b, area_km2=-5.0) if b.basin_code == "123" else b
            for b in loaded.basins
        )
        bad_loaded = dataclasses.replace(loaded, basins=bad_basins)
        report = self._held_report_flipped_to_accepted(
            bad_loaded, station_id, keep_hold_reasons=False
        )

        with pytest.raises(BasinPackageRejectedError, match="area"):
            import_basin_package(db_connection, bad_loaded, report, clock=_clock)
        self._assert_no_provenance(db_connection)

    def test_outside_coverage_flipped_to_accepted_rejected(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded = load_basin_package(FIXTURE_DIR)
        entry = loaded.validation_report.basins[0]
        outside_entry = dataclasses.replace(
            entry, checks={**entry.checks, "coverage_status": "outside"}
        )
        bad_report_source = dataclasses.replace(
            loaded,
            validation_report=dataclasses.replace(
                loaded.validation_report, basins=(outside_entry,)
            ),
        )
        report = self._held_report_flipped_to_accepted(
            bad_report_source, station_id, keep_hold_reasons=False
        )

        with pytest.raises(BasinPackageRejectedError, match="coverage"):
            import_basin_package(db_connection, bad_report_source, report, clock=_clock)
        self._assert_no_provenance(db_connection)

    def test_required_static_feature_hold_flipped_to_accepted_rejected(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded = load_basin_package(FIXTURE_DIR)
        attr = loaded.feature_catalog[0].name
        # Make `attr` catalog-required AND null it for the basin, then declare
        # it required by the basin's assigned model — a genuine §9 hold.
        catalog = tuple(
            dataclasses.replace(e, required_by_models=("dummy_model",))
            if e.name == attr
            else e
            for e in loaded.feature_catalog
        )
        nulled = dict(loaded.static_attributes)
        nulled["nepal_123"] = {**nulled["nepal_123"], attr: None}
        bad_loaded = dataclasses.replace(
            loaded, feature_catalog=catalog, static_attributes=nulled
        )
        report = self._held_report_flipped_to_accepted(
            bad_loaded,
            station_id,
            keep_hold_reasons=True,
            assigned_model_features=lambda basin: frozenset({attr}),
        )

        with pytest.raises(BasinPackageRejectedError, match="hold reason"):
            import_basin_package(db_connection, bad_loaded, report, clock=_clock)
        self._assert_no_provenance(db_connection)


class TestStationIdentityValidation:
    """Fixer round (major finding, 2026-07-23): ``_basin_for_decision`` only
    verifies the ``(network, basin_code)`` KEY exists in the loaded package
    — it does not verify the decision's station identity still matches that
    key. A stale/mismatched acceptance report paired with a package
    containing the same basin key but a changed station identity must
    reject the whole package rather than silently write the §5a row and
    ``stations.basin_id`` against the wrong station."""

    def test_stale_decision_station_code_mismatch_rejected_no_writes(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection, code="123", network="dhm")
        loaded, report = _load_and_accept(station_id)

        # Same (network, basin_code) KEY ("dhm", "123") but the basin's own
        # station_code has changed underneath the stale `report` — exactly
        # the divergence this guard exists to catch.
        mutated_basins = tuple(
            dataclasses.replace(b, station_code="999") if b.basin_code == "123" else b
            for b in loaded.basins
        )
        mutated = dataclasses.replace(loaded, basins=mutated_basins)

        with pytest.raises(BasinPackageRejectedError, match="station_code"):
            import_basin_package(db_connection, mutated, report, clock=_clock)

        package_count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_static_packages)
            .where(basin_static_packages.c.package_id == "nepal-dhm-basins")
        ).scalar_one()
        assert package_count == 0
        binding_count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(recap_gateway_polygon_bindings)
            .where(recap_gateway_polygon_bindings.c.station_id == station_id)
        ).scalar_one()
        assert binding_count == 0
        unchanged = PgStationStore(db_connection).fetch_station(station_id)
        assert unchanged is not None
        assert unchanged.basin_id is None

    def test_decision_station_id_identity_mismatch_rejected_no_writes(
        self, db_connection: sa.Connection
    ) -> None:
        correct_station_id = _seed_station(db_connection, code="123", network="dhm")
        wrong_station_id = _seed_station(
            db_connection, code="999", network="other-network"
        )
        loaded, report = _load_and_accept(correct_station_id)

        # decision.station_code still says "123" (matches the basin) but the
        # resolved station_id now points to an entirely different station
        # whose own code/network have diverged from the decision.
        mutated_decisions = tuple(
            dataclasses.replace(d, station_id=wrong_station_id)
            for d in report.decisions
        )
        mutated_report = dataclasses.replace(report, decisions=mutated_decisions)

        with pytest.raises(BasinPackageRejectedError, match="identity"):
            import_basin_package(db_connection, loaded, mutated_report, clock=_clock)

        package_count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_static_packages)
            .where(basin_static_packages.c.package_id == "nepal-dhm-basins")
        ).scalar_one()
        assert package_count == 0
        unchanged = PgStationStore(db_connection).fetch_station(wrong_station_id)
        assert unchanged is not None
        assert unchanged.basin_id is None


class TestCorrectionRefreshesBasinName:
    """Fixer round (major finding, 2026-07-23): a corrected package's
    ``display_name`` must refresh ``basins.name`` — the operational
    projection — not just the correction path's other columns."""

    def test_correction_with_new_display_name_updates_basin_name(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        loaded, report = _load_and_accept(station_id)
        import_basin_package(db_connection, loaded, report, clock=_clock)

        renamed_basins = tuple(
            dataclasses.replace(b, display_name="Renamed Basin Display Name")
            if b.basin_code == "123"
            else b
            for b in loaded.basins
        )
        corrected_manifest = dataclasses.replace(
            loaded.manifest, package_id="nepal-dhm-basins-v2"
        )
        corrected = dataclasses.replace(
            loaded,
            manifest=corrected_manifest,
            basins=renamed_basins,
            computed_checksums={"basins.gpkg": "sha256:" + "c" * 64},
        )
        corrected_report = evaluate_basin_acceptance(
            corrected,
            resolve_station=lambda code, network: (
                station_id if (code, network) == ("123", "dhm") else None
            ),
        )

        result = import_basin_package(
            db_connection, corrected, corrected_report, clock=_clock
        )

        assert result.imported_basins[0].outcome == "corrected"
        basin = PgBasinStore(db_connection).fetch_basin(
            result.imported_basins[0].basin_id
        )
        assert basin is not None
        assert basin.name == "Renamed Basin Display Name"


def _seed_model(conn: sa.Connection) -> ModelId:
    mid = ModelId(f"basin_binding_test_model_{uuid.uuid4().hex[:8]}")
    conn.execute(
        sa.insert(models).values(
            id=mid,
            display_name="Basin Binding Test Model",
            artifact_scope="station",
            description="Fixer-round station-basin-binding end-to-end test",
        )
    )
    return mid


class TestStationBasinBinding:
    """Fixer round (major finding): a newly imported basin must be assigned
    to the matched station's `stations.basin_id` — without this,
    `assemble_station_training_data`/`record_artifact_basin_lineage` (which
    both follow `stations.basin_id`, never the package) can never reach the
    static attributes/basin version this package just wrote."""

    def test_new_basin_binds_the_matched_station(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        assert PgStationStore(db_connection).fetch_station(station_id).basin_id is None
        loaded, report = _load_and_accept(station_id)

        result = import_basin_package(db_connection, loaded, report, clock=_clock)

        station = PgStationStore(db_connection).fetch_station(station_id)
        assert station is not None
        assert station.basin_id == result.imported_basins[0].basin_id

    def test_conflicting_basin_binding_rejected_not_silently_remapped(
        self, db_connection: sa.Connection
    ) -> None:
        other_basin = Basin(
            id=BasinId(uuid.uuid4()),
            code="OTHER-BASIN",
            name="Some other basin",
            geometry=MultiPolygon(
                [Polygon([(2.0, 2.0), (2.0, 3.0), (3.0, 3.0), (3.0, 2.0)])]
            ),
            area_km2=3.0,
            attributes=None,
            regional_basin=None,
            band_geometries=None,
            created_at=_CLOCK_VALUE,
            network="dhm",
            package_id=None,
        )
        PgBasinStore(db_connection).store_basin(other_basin)
        station = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="123",
            network="dhm",
            basin_id=other_basin.id,
        )
        PgStationStore(db_connection).store_station(station)
        loaded, report = _load_and_accept(station.id)

        with pytest.raises(BasinPackageRejectedError, match="already bound"):
            import_basin_package(db_connection, loaded, report, clock=_clock)

        # No half-applied provenance row either — the conflict must be
        # detected inside the same package transaction the caller controls.
        unchanged = PgStationStore(db_connection).fetch_station(station.id)
        assert unchanged is not None
        assert unchanged.basin_id == other_basin.id

    def test_static_training_data_and_lineage_reachable_after_import(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """End-to-end proof (no manual join-table inserts): a station that
        starts with `basin_id=None`, once imported, has its basin reachable
        via `PgBasinStore.fetch_basin(station.basin_id)` — exactly the path
        `assemble_station_training_data` uses (`services/training_data.py`)
        — and `record_artifact_basin_lineage` (the REAL helper, not a manual
        `model_artifact_basin_versions` insert) writes the lineage row."""
        station_id = _seed_station(db_connection)
        loaded, report = _load_and_accept(station_id)
        result = import_basin_package(db_connection, loaded, report, clock=_clock)
        imported_basin_id = result.imported_basins[0].basin_id

        station = PgStationStore(db_connection).fetch_station(station_id)
        assert station is not None
        assert station.basin_id == imported_basin_id

        basin = PgBasinStore(db_connection).fetch_basin(station.basin_id)
        assert basin is not None
        assert basin.attributes
        assert set(basin.attributes) == set(loaded.static_attributes["nepal_123"])

        model_id = _seed_model(db_connection)
        artifact_id, _ = PgModelArtifactStore(db_connection, tmp_path).store_artifact(
            model_id,
            b"payload",
            _CLOCK_VALUE,
            _CLOCK_VALUE,
            _CLOCK_VALUE,
            station_id=station_id,
        )

        record_artifact_basin_lineage(db_connection, artifact_id, {station_id})

        lineage_rows = (
            db_connection.execute(
                sa.select(model_artifact_basin_versions).where(
                    model_artifact_basin_versions.c.model_artifact_id == artifact_id
                )
            )
            .mappings()
            .all()
        )
        assert len(lineage_rows) == 1
        current_version_id = db_connection.execute(
            sa.select(basin_versions.c.id).where(
                sa.and_(
                    basin_versions.c.basin_id == imported_basin_id,
                    basin_versions.c.superseded_at.is_(None),
                )
            )
        ).scalar_one()
        assert lineage_rows[0]["basin_version_id"] == current_version_id


class TestMissingStaticAttributes:
    """Fixer round (major finding): a missing `static_attributes` row for an
    accepted basin must never silently become `{}` — reject loudly instead."""

    def test_accepted_basin_missing_from_static_attributes_rejects(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        # Build a valid, ACCEPTED report against the real, complete
        # package first (so Task 1B's own gauge_id-join validation, which
        # `import_basin_package` does NOT re-run, is satisfied) — then
        # simulate a stale/mismatched `loaded` (attributes row gone) being
        # replayed against that same report, exactly the divergence
        # scenario this guard exists to catch.
        loaded, report = _load_and_accept(station_id)
        assert len(report.accepted) == 1  # per-basin acceptance is unaffected
        diverged_loaded = dataclasses.replace(loaded, static_attributes={})

        savepoint = db_connection.begin_nested()
        with pytest.raises(BasinPackageRejectedError, match="static_attributes"):
            import_basin_package(db_connection, diverged_loaded, report, clock=_clock)
        savepoint.rollback()

        # A caller that wraps the call (as the transaction guard requires)
        # and rolls back on error sees NOTHING committed — not even the
        # provenance row `import_basin_package` writes before reaching the
        # per-basin attribute check.
        count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_static_packages)
            .where(basin_static_packages.c.package_id == "nepal-dhm-basins")
        ).scalar_one()
        assert count == 0


class TestTransactionGuard:
    """Blocker fixer round: `import_basin_package` must refuse to run at all
    — before writing anything — unless `conn` is genuinely inside a
    non-AUTOCOMMIT transaction. Production connections
    (`flows/_db.py::setup_production_stores`) run under
    `isolation_level="AUTOCOMMIT"`, where individual statements commit
    independently even after an explicit `conn.begin()` (verified against a
    live Postgres): this guard is what makes the package-level "one
    transaction" contract enforceable rather than just documented."""

    def test_autocommit_connection_refused_before_any_write(
        self, db_engine: sa.Engine
    ) -> None:
        station_id = StationId(uuid.uuid4())
        loaded, report = _load_and_accept(station_id)
        conn = db_engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            with pytest.raises(RuntimeError, match="AUTOCOMMIT"):
                import_basin_package(conn, loaded, report, clock=_clock)
            count = conn.execute(
                sa.select(sa.func.count()).select_from(basin_static_packages)
            ).scalar_one()
            assert count == 0
        finally:
            conn.close()

    def test_connection_with_no_open_transaction_refused(
        self, db_engine: sa.Engine
    ) -> None:
        station_id = StationId(uuid.uuid4())
        loaded, report = _load_and_accept(station_id)
        conn = db_engine.connect()
        try:
            assert not conn.in_transaction()
            with pytest.raises(RuntimeError, match="transaction"):
                import_basin_package(conn, loaded, report, clock=_clock)
        finally:
            conn.rollback()
            conn.close()


class TestPackageAtomicity:
    """Blocker fixer round: once a caller satisfies the transaction guard
    (a real, open, non-AUTOCOMMIT transaction), a mid-pipeline failure that
    happens AFTER `store_basin`/`store_binding` already executed their own
    (individually atomic) statements must still roll back completely when
    the caller rolls back — proving the package-level "one transaction, all-
    or-nothing" contract actually holds end to end, not just per statement."""

    def test_mid_pipeline_failure_after_basin_and_binding_writes_rolls_back_all(
        self, db_connection: sa.Connection
    ) -> None:
        # A pre-existing basin the station is ALREADY (wrongly, for this
        # package) bound to — forces `_assign_station_basin` to raise AFTER
        # `store_basin` and `store_binding` have both already executed their
        # own writes for the new "123" basin this package would create.
        conflicting_basin = Basin(
            id=BasinId(uuid.uuid4()),
            code="PRE-EXISTING",
            name="Pre-existing conflicting basin",
            geometry=MultiPolygon(
                [Polygon([(4.0, 4.0), (4.0, 5.0), (5.0, 5.0), (5.0, 4.0)])]
            ),
            area_km2=9.0,
            attributes=None,
            regional_basin=None,
            band_geometries=None,
            created_at=_CLOCK_VALUE,
            network="dhm",
            package_id=None,
        )
        PgBasinStore(db_connection).store_basin(conflicting_basin)
        station = make_station_config(
            station_id=StationId(uuid.uuid4()),
            code="123",
            network="dhm",
            basin_id=conflicting_basin.id,
        )
        PgStationStore(db_connection).store_station(station)
        loaded, report = _load_and_accept(station.id)

        savepoint = db_connection.begin_nested()
        with pytest.raises(BasinPackageRejectedError, match="already bound"):
            import_basin_package(db_connection, loaded, report, clock=_clock)
        savepoint.rollback()

        # Prove atomicity: the basin/version/package/§5a rows that
        # `_insert_new_basin` already wrote before the conflict was detected
        # must NOT have survived the caller's rollback.
        basin_count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basins)
            .where(sa.and_(basins.c.code == "123", basins.c.network == "dhm"))
        ).scalar_one()
        assert basin_count == 0
        package_count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_static_packages)
            .where(basin_static_packages.c.package_id == "nepal-dhm-basins")
        ).scalar_one()
        assert package_count == 0
        binding_count = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(recap_gateway_polygon_bindings)
            .where(recap_gateway_polygon_bindings.c.station_id == station.id)
        ).scalar_one()
        assert binding_count == 0
        # And the station's ORIGINAL binding is exactly as it was.
        unchanged = PgStationStore(db_connection).fetch_station(station.id)
        assert unchanged is not None
        assert unchanged.basin_id == conflicting_basin.id
