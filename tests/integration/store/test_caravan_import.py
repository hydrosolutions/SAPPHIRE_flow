"""Plan 155 T1/T1b — additive Caravan-attributes merge, DB-backed.

Locks the storage-layer contract the pure `services/caravan_statics.py`
tests cannot reach: `merge_namespaced_attributes` must NOT create a new
`basin_versions` row (no supersede, no `material_change`), must NOT touch a
non-namespaced (incumbent) attribute, and `import_caravan_attributes` must
join correctly on station identity end-to-end.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime

import pandas as pd
import pytest
import sqlalchemy as sa
from shapely.geometry import MultiPolygon, Polygon

from sapphire_flow.db.metadata import basin_versions
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.caravan_import import (
    import_caravan_attributes,
    run_operational_caravan_import,
)
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.ids import BasinId, StationId
from tests.conftest import make_station_config

_GEOM = MultiPolygon(
    [Polygon([(7.0, 46.0), (8.0, 46.0), (8.0, 47.0), (7.0, 47.0), (7.0, 46.0)])]
)
_CREATED_AT = datetime(2024, 1, 1, tzinfo=UTC)


def _make_basin(
    *,
    code: str = "2009",
    basin_id: BasinId | None = None,
    attributes: dict | None = None,
) -> Basin:
    return Basin(
        id=basin_id or BasinId(uuid.uuid4()),
        code=code,
        name="Test Basin",
        geometry=_GEOM,
        area_km2=123.4,
        attributes=attributes or {"area": 100.0},  # CAMELS-CH's own bare "area"
        band_geometries=None,
        created_at=_CREATED_AT,
        network="bafu",
    )


class TestMergeNamespacedAttributes:
    def test_additive_merge_leaves_incumbent_attributes_untouched(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgBasinStore(db_connection)
        basin = _make_basin(attributes={"area": 100.0, "elevation": 500.0})
        store.store_basin(basin)

        store.merge_namespaced_attributes(
            basin.id, attributes={"caravan:area": 250.0, "caravan:slp_dg_sav": 12.5}
        )

        fetched = store.fetch_basin(basin.id)
        assert fetched is not None
        assert fetched.attributes["area"] == 100.0  # untouched
        assert fetched.attributes["elevation"] == 500.0  # untouched
        assert fetched.attributes["caravan:area"] == 250.0
        assert fetched.attributes["caravan:slp_dg_sav"] == 12.5

    def test_does_not_create_a_new_basin_version(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgBasinStore(db_connection)
        basin = _make_basin()
        store.store_basin(basin)

        version_count_before = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_versions)
            .where(basin_versions.c.basin_id == basin.id)
        ).scalar_one()

        store.merge_namespaced_attributes(basin.id, attributes={"caravan:area": 250.0})

        version_count_after = db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_versions)
            .where(basin_versions.c.basin_id == basin.id)
        ).scalar_one()

        assert version_count_after == version_count_before

    def test_rejects_a_key_without_the_prefix(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgBasinStore(db_connection)
        basin = _make_basin()
        store.store_basin(basin)

        with pytest.raises(ValueError, match="caravan:"):
            store.merge_namespaced_attributes(basin.id, attributes={"area": 999.0})

        # Structurally guarded (Plan 155 T1b): the rejected call must not
        # have touched the incumbent attribute either.
        fetched = store.fetch_basin(basin.id)
        assert fetched is not None
        assert fetched.attributes["area"] == 100.0

    def test_raises_for_a_basin_that_does_not_exist(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgBasinStore(db_connection)
        with pytest.raises(ValueError, match="not found"):
            store.merge_namespaced_attributes(
                BasinId(uuid.uuid4()), attributes={"caravan:area": 1.0}
            )

    def test_second_merge_is_idempotent(self, db_connection: sa.Connection) -> None:
        store = PgBasinStore(db_connection)
        basin = _make_basin()
        store.store_basin(basin)

        store.merge_namespaced_attributes(basin.id, attributes={"caravan:area": 250.0})
        store.merge_namespaced_attributes(basin.id, attributes={"caravan:area": 250.0})

        fetched = store.fetch_basin(basin.id)
        assert fetched is not None
        assert fetched.attributes["caravan:area"] == 250.0

    def test_a_changed_value_under_an_already_merged_key_raises(
        self, db_connection: sa.Connection
    ) -> None:
        """Plan 155 fixer round (major finding): "merge_namespaced_attributes
        uses JSONB || which silently overwrites existing caravan:* values
        on a changed re-import". A re-import carrying a DIFFERENT value for
        an already-merged key must raise, not silently overwrite."""
        store = PgBasinStore(db_connection)
        basin = _make_basin()
        store.store_basin(basin)

        store.merge_namespaced_attributes(basin.id, attributes={"caravan:area": 250.0})

        with pytest.raises(ConfigurationError, match="caravan:area"):
            store.merge_namespaced_attributes(
                basin.id, attributes={"caravan:area": 999.0}
            )

        # The stale write attempt must not have gone through.
        fetched = store.fetch_basin(basin.id)
        assert fetched is not None
        assert fetched.attributes["caravan:area"] == 250.0

    def test_a_changed_value_alongside_an_unrelated_new_key_still_raises(
        self, db_connection: sa.Connection
    ) -> None:
        """The guard must catch a conflict even when it rides along with
        otherwise-legitimate new keys in the same merge call -- and must
        not partially apply the new keys before raising."""
        store = PgBasinStore(db_connection)
        basin = _make_basin()
        store.store_basin(basin)

        store.merge_namespaced_attributes(basin.id, attributes={"caravan:area": 250.0})

        with pytest.raises(ConfigurationError):
            store.merge_namespaced_attributes(
                basin.id,
                attributes={"caravan:area": 999.0, "caravan:new_key": 1.0},
            )

        fetched = store.fetch_basin(basin.id)
        assert fetched is not None
        assert "caravan:new_key" not in fetched.attributes


def _write_parquet(tmp_path, rows: list[dict]):
    path = tmp_path / "data.parquet"
    pd.DataFrame(rows).to_parquet(path)
    return path


class TestImportCaravanAttributes:
    def test_joins_on_station_identity_and_merges_additively(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)

        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station = make_station_config(code="2009", network="bafu", basin_id=basin.id)
        station_store.store_station(station)

        path = _write_parquet(
            tmp_path,
            [
                {
                    "gauge_id": "caravan_camels_ch_2009",
                    "area": 250.0,
                    "slp_dg_sav": 12.5,
                },
                # No matching station for this one -- an unmatched code.
                {
                    "gauge_id": "caravan_camels_ch_9999",
                    "area": 1.0,
                    "slp_dg_sav": 2.0,
                },
            ],
        )

        result = import_caravan_attributes(
            path, station_store=station_store, basin_store=basin_store
        )

        assert result.matched_codes == frozenset({"2009"})
        assert result.unmatched_codes == frozenset({"9999"})
        assert result.stations_without_basin == frozenset()

        fetched = basin_store.fetch_basin(basin.id)
        assert fetched is not None
        assert fetched.attributes["area"] == 100.0  # incumbent untouched
        assert fetched.attributes["caravan:area"] == 250.0
        assert fetched.attributes["caravan:slp_dg_sav"] == 12.5

    def test_station_without_basin_is_reported_not_silently_dropped(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)

        station = make_station_config(code="2011", network="bafu", basin_id=None)
        station_store.store_station(station)

        path = _write_parquet(
            tmp_path, [{"gauge_id": "caravan_camels_ch_2011", "area": 1.0}]
        )

        result = import_caravan_attributes(
            path, station_store=station_store, basin_store=basin_store
        )

        assert result.stations_without_basin == frozenset({"2011"})
        assert result.matched_codes == frozenset()

    def test_a_manifest_station_absent_from_the_parquet_is_reported(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        """Plan 155 post-implementation-review MAJOR: `matched_codes`/
        `unmatched_codes` alone only ever see codes the parquet actually
        contains -- a T0a-manifest station that never shows up as a row AT
        ALL is silently invisible to both. `expected_codes` closes that
        gap."""
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)

        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin.id)
        )

        path = _write_parquet(
            tmp_path, [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0}]
        )

        result = import_caravan_attributes(
            path,
            station_store=station_store,
            basin_store=basin_store,
            expected_codes=frozenset({"2009", "2446"}),
        )

        assert result.matched_codes == frozenset({"2009"})
        assert result.missing_from_manifest == frozenset({"2446"})

    def test_no_expected_codes_reports_no_manifest_gap(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)

        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin.id)
        )

        path = _write_parquet(
            tmp_path, [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0}]
        )

        result = import_caravan_attributes(
            path, station_store=station_store, basin_store=basin_store
        )

        assert result.missing_from_manifest == frozenset()

    def test_source_dataset_version_threads_into_the_returned_provenance(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        """Plan 155 post-implementation-review MAJOR: "the import API
        accepts only extractor_version ... there is no parameter to pass
        [the confirmed release] through" -- `source_dataset_version` closes
        that gap."""
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)

        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin.id)
        )

        path = _write_parquet(
            tmp_path, [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0}]
        )

        result = import_caravan_attributes(
            path,
            station_store=station_store,
            basin_store=basin_store,
            source_dataset_version="camels-ch-v1.1",
        )

        assert result.provenance.source_dataset_version == "camels-ch-v1.1"

    def test_omitted_source_dataset_version_keeps_the_honest_placeholder(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)

        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin.id)
        )

        path = _write_parquet(
            tmp_path, [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0}]
        )

        result = import_caravan_attributes(
            path, station_store=station_store, basin_store=basin_store
        )

        assert result.provenance.source_dataset_version == (
            "unconfirmed@delivered-2026-08-13"
        )

    def test_content_fingerprint_is_stable_across_identical_imports(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        """Plan 155 fixer round (major finding: "no fingerprint and no
        immutability guard") -- the SAME parsed content must yield the
        SAME fingerprint across two independent imports."""
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)
        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin.id)
        )
        rows = [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0}]
        path = _write_parquet(tmp_path, rows)

        first = import_caravan_attributes(
            path, station_store=station_store, basin_store=basin_store
        )
        second = import_caravan_attributes(
            path, station_store=station_store, basin_store=basin_store
        )

        assert first.provenance.content_fingerprint is not None
        assert (
            first.provenance.content_fingerprint
            == second.provenance.content_fingerprint
        )

    def test_content_fingerprint_changes_with_the_data(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        # Two independent stations (not a re-import of the same station)
        # so this test exercises fingerprint content-sensitivity without
        # also tripping the (separately-tested) changed-value guard.
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)
        basin_a = _make_basin(code="2009")
        basin_store.store_basin(basin_a)
        station_store.store_station(
            make_station_config(
                station_id=StationId(uuid.uuid4()),
                code="2009",
                network="bafu",
                basin_id=basin_a.id,
            )
        )
        basin_b = _make_basin(code="2010")
        basin_store.store_basin(basin_b)
        station_store.store_station(
            make_station_config(
                station_id=StationId(uuid.uuid4()),
                code="2010",
                network="bafu",
                basin_id=basin_b.id,
            )
        )

        first = import_caravan_attributes(
            _write_parquet(
                tmp_path, [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0}]
            ),
            station_store=station_store,
            basin_store=basin_store,
        )
        second = import_caravan_attributes(
            _write_parquet(
                tmp_path, [{"gauge_id": "caravan_camels_ch_2010", "area": 999.0}]
            ),
            station_store=station_store,
            basin_store=basin_store,
        )

        assert (
            first.provenance.content_fingerprint
            != second.provenance.content_fingerprint
        )


class TestImportCaravanAttributesRequiredStaticNames:
    """Plan 155 fixer round (major finding): "T1's exit gate is neither
    enforced nor genuinely tested -- no production caller invokes it".
    `required_static_names` wires `verify_static_coverage` into the
    OPERATIONAL import itself."""

    def test_full_coverage_returns_normally(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)
        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin.id)
        )
        path = _write_parquet(
            tmp_path,
            [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0, "slp_dg_sav": 12.5}],
        )

        result = import_caravan_attributes(
            path,
            station_store=station_store,
            basin_store=basin_store,
            expected_codes=frozenset({"2009"}),
            required_static_names=frozenset({"area", "slope"}),
        )

        assert result.matched_codes == frozenset({"2009"})

    def test_a_missing_static_raises_before_returning(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)
        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin.id)
        )
        # No "slp_dg_sav" column -- "slope" cannot resolve.
        path = _write_parquet(
            tmp_path, [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0}]
        )

        with pytest.raises(ConfigurationError, match="exit gate failed"):
            import_caravan_attributes(
                path,
                station_store=station_store,
                basin_store=basin_store,
                expected_codes=frozenset({"2009"}),
                required_static_names=frozenset({"area", "slope"}),
            )

    def test_a_manifest_station_missing_entirely_raises(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)
        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin.id)
        )
        path = _write_parquet(
            tmp_path,
            [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0, "slp_dg_sav": 12.5}],
        )

        with pytest.raises(ConfigurationError, match="exit gate failed"):
            import_caravan_attributes(
                path,
                station_store=station_store,
                basin_store=basin_store,
                expected_codes=frozenset({"2009", "2446"}),
                required_static_names=frozenset({"area", "slope"}),
            )

    def test_an_out_of_manifest_unmatched_code_does_not_raise(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        """Plan 155 round-2 review BLOCKER, locked directly: "the T1 exit
        gate is unusable in both directions -- unmatched collects every
        parquet row with no configured station", so a real-shaped delivery
        (out-of-scope Swiss codes the parquet legitimately carries beyond
        our own configured stations) always raised, even on a flawless
        manifest import. Code "9999" is unmatched (no configured station)
        and NOT in `expected_codes` -- it must be reported, never fatal."""
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)
        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin.id)
        )
        path = _write_parquet(
            tmp_path,
            [
                {
                    "gauge_id": "caravan_camels_ch_2009",
                    "area": 250.0,
                    "slp_dg_sav": 12.5,
                },
                # Out-of-scope: not a configured station, not in the manifest.
                {"gauge_id": "caravan_camels_ch_9999", "area": 1.0},
            ],
        )

        result = import_caravan_attributes(
            path,
            station_store=station_store,
            basin_store=basin_store,
            expected_codes=frozenset({"2009"}),
            required_static_names=frozenset({"area", "slope"}),
        )

        assert result.matched_codes == frozenset({"2009"})
        assert result.unmatched_codes == frozenset({"9999"})

    def test_a_manifest_unmatched_code_raises(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        """The converse of the above: an unmatched code that IS in the
        manifest (a T0a station with no configured station-store row at
        all) must still gate the exit -- manifest-scoping narrows what is
        fatal, it does not disable the gate for the manifest itself."""
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)
        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin.id)
        )
        path = _write_parquet(
            tmp_path,
            [
                {
                    "gauge_id": "caravan_camels_ch_2009",
                    "area": 250.0,
                    "slp_dg_sav": 12.5,
                },
                # "9999" is in the manifest but has no configured station.
                {"gauge_id": "caravan_camels_ch_9999", "area": 1.0},
            ],
        )

        with pytest.raises(ConfigurationError, match="exit gate failed"):
            import_caravan_attributes(
                path,
                station_store=station_store,
                basin_store=basin_store,
                expected_codes=frozenset({"2009", "9999"}),
                required_static_names=frozenset({"area", "slope"}),
            )

    def test_required_static_names_without_expected_codes_raises_immediately(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        """Plan 155 round-2 review fix (2): "make the operational entrypoint
        require expected_codes + required_static_names, so the always-skip
        path cannot be taken by accident" -- supplying one without the
        other is a caller error, not a silently-disabled gate."""
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)
        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin.id)
        )
        path = _write_parquet(
            tmp_path,
            [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0, "slp_dg_sav": 12.5}],
        )

        with pytest.raises(ConfigurationError, match="expected_codes"):
            import_caravan_attributes(
                path,
                station_store=station_store,
                basin_store=basin_store,
                required_static_names=frozenset({"area", "slope"}),
            )

        # Guard fires before any write -- the incumbent basin is untouched.
        fetched = basin_store.fetch_basin(basin.id)
        assert fetched is not None
        assert "caravan:area" not in fetched.attributes

    def test_no_required_static_names_never_raises_on_a_gap(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        """Backward compatibility: omitting `required_static_names` keeps
        the exit gate OFF (the default, non-enforcing behaviour every
        existing caller relies on)."""
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)
        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin.id)
        )
        path = _write_parquet(
            tmp_path, [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0}]
        )

        result = import_caravan_attributes(
            path, station_store=station_store, basin_store=basin_store
        )

        assert result.matched_codes == frozenset({"2009"})

    def test_exit_gate_failure_after_a_successful_merge_rolls_back_on_caller_rollback(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        """Plan 155 round-2 review MAJOR (atomicity), mirroring
        `test_basin_importer_persistence.py::TestPackageAtomicity`: a
        mid-loop write (station "2009", full coverage) happens BEFORE the
        exit-gate check for station "2010" (missing "slope") raises. Once
        the caller rolls back, on a real transaction, NEITHER station's
        `caravan:` keys may survive -- proving the "one import, all-or-
        nothing" contract end to end, not just that the exception fired."""
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)
        basin_2009 = _make_basin(code="2009")
        basin_2010 = _make_basin(code="2010")
        basin_store.store_basin(basin_2009)
        basin_store.store_basin(basin_2010)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin_2009.id)
        )
        station_store.store_station(
            make_station_config(
                station_id=StationId(uuid.uuid4()),
                code="2010",
                network="bafu",
                basin_id=basin_2010.id,
            )
        )
        path = _write_parquet(
            tmp_path,
            [
                {
                    "gauge_id": "caravan_camels_ch_2009",
                    "area": 250.0,
                    "slp_dg_sav": 12.5,
                },
                # "2010" is missing "slp_dg_sav" -- fails the exit gate.
                {"gauge_id": "caravan_camels_ch_2010", "area": 1.0},
            ],
        )

        savepoint = db_connection.begin_nested()
        with pytest.raises(ConfigurationError, match="exit gate failed"):
            import_caravan_attributes(
                path,
                station_store=station_store,
                basin_store=basin_store,
                expected_codes=frozenset({"2009", "2010"}),
                required_static_names=frozenset({"area", "slope"}),
            )
        savepoint.rollback()

        fetched_2009 = basin_store.fetch_basin(basin_2009.id)
        fetched_2010 = basin_store.fetch_basin(basin_2010.id)
        assert fetched_2009 is not None
        assert fetched_2010 is not None
        assert "caravan:area" not in fetched_2009.attributes
        assert "caravan:area" not in fetched_2010.attributes


class TestRunOperationalCaravanImport:
    """Plan 155 fixer round (BLOCKER finding): "the operational exit gate
    remains optional and trivially bypassable" -- `import_caravan_attributes`
    defaults both `expected_codes` and `required_static_names` to `None`
    and only rejects the one-supplied-without-the-other case, so calling it
    with BOTH omitted writes data and returns success with no validation
    at all; an explicit empty set validates nothing either.
    `run_operational_caravan_import` is the fix: both are mandatory
    (no-default keyword-only parameters -- omitting either is a `TypeError`
    at the call site, not a runtime bypass) and additionally checked
    non-empty."""

    def test_omitting_expected_codes_is_a_typeerror_not_a_silent_success(
        self,
    ) -> None:
        """Structural proof the "both omitted" bypass the review found is
        gone: Python itself refuses the call before any of this module's
        own code runs."""
        with pytest.raises(TypeError):
            run_operational_caravan_import(  # type: ignore[call-arg]
                "unused.parquet",
                station_store=None,  # type: ignore[arg-type]
                basin_store=None,  # type: ignore[arg-type]
                required_static_names=frozenset({"area"}),
            )

    def test_omitting_required_static_names_is_a_typeerror_not_a_silent_success(
        self,
    ) -> None:
        with pytest.raises(TypeError):
            run_operational_caravan_import(  # type: ignore[call-arg]
                "unused.parquet",
                station_store=None,  # type: ignore[arg-type]
                basin_store=None,  # type: ignore[arg-type]
                expected_codes=frozenset({"2009"}),
            )

    def test_an_empty_expected_codes_raises_before_any_read_or_write(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)
        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin.id)
        )
        path = _write_parquet(
            tmp_path, [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0}]
        )

        with pytest.raises(ConfigurationError, match="expected_codes"):
            run_operational_caravan_import(
                path,
                station_store=station_store,
                basin_store=basin_store,
                expected_codes=frozenset(),
                required_static_names=frozenset({"area"}),
            )

        fetched = basin_store.fetch_basin(basin.id)
        assert fetched is not None
        assert "caravan:area" not in fetched.attributes

    def test_an_empty_required_static_names_raises_before_any_read_or_write(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)
        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin.id)
        )
        path = _write_parquet(
            tmp_path, [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0}]
        )

        with pytest.raises(ConfigurationError, match="required_static_names"):
            run_operational_caravan_import(
                path,
                station_store=station_store,
                basin_store=basin_store,
                expected_codes=frozenset({"2009"}),
                required_static_names=frozenset(),
            )

        fetched = basin_store.fetch_basin(basin.id)
        assert fetched is not None
        assert "caravan:area" not in fetched.attributes

    def test_a_genuinely_gated_import_still_succeeds_on_full_coverage(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)
        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin.id)
        )
        path = _write_parquet(
            tmp_path,
            [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0, "slp_dg_sav": 12.5}],
        )

        result = run_operational_caravan_import(
            path,
            station_store=station_store,
            basin_store=basin_store,
            expected_codes=frozenset({"2009"}),
            required_static_names=frozenset({"area", "slope"}),
        )

        assert result.matched_codes == frozenset({"2009"})

    def test_a_genuinely_gated_import_still_raises_on_a_real_gap(
        self, db_connection: sa.Connection, tmp_path
    ) -> None:
        station_store = PgStationStore(db_connection)
        basin_store = PgBasinStore(db_connection)
        basin = _make_basin(code="2009")
        basin_store.store_basin(basin)
        station_store.store_station(
            make_station_config(code="2009", network="bafu", basin_id=basin.id)
        )
        # No "slp_dg_sav" column -- "slope" cannot resolve.
        path = _write_parquet(
            tmp_path, [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0}]
        )

        with pytest.raises(ConfigurationError, match="exit gate failed"):
            run_operational_caravan_import(
                path,
                station_store=station_store,
                basin_store=basin_store,
                expected_codes=frozenset({"2009"}),
                required_static_names=frozenset({"area", "slope"}),
            )


class TestMergeNamespacedAttributesConcurrency:
    """Plan 155 fixer round (major finding): "the changed-value replay
    guard has a check/update race" -- `merge_namespaced_attributes` read
    the basin's current attributes without locking, so two concurrent
    imports could both observe no existing key and the second silently
    overwrote the first's committed value. Real two-connection
    concurrency test, mirroring
    `test_station_group_store.py::TestStoreGroupConcurrentTenantRace`:
    two threads, each on its OWN connection/transaction, attempt to merge
    DIFFERING values for the SAME new key at (as close to) the same
    instant, synchronized via a barrier. `SELECT ... FOR UPDATE` (the fix)
    serializes them: whichever commits first wins; the second blocks until
    the first commits, re-reads the NOW-current (committed) attributes,
    and raises on the conflict this method promises to catch -- rather
    than both silently winning/losing with no trace.
    """

    def test_concurrent_differing_merges_exactly_one_wins_the_other_raises(
        self, db_engine: sa.Engine
    ) -> None:
        basin_id = BasinId(uuid.uuid4())
        with db_engine.connect() as seed_conn:
            PgBasinStore(seed_conn).store_basin(
                _make_basin(code="2009", basin_id=basin_id)
            )
            seed_conn.commit()

        barrier = threading.Barrier(2)
        outcomes: dict[str, object] = {}

        def _attempt(value: float, key: str) -> None:
            with db_engine.connect() as conn:
                conn.begin()
                # Widen the read-then-write window deterministically: a
                # local Postgres round trip is sub-millisecond, so relying
                # on the barrier alone to interleave the two threads'
                # SELECT-then-UPDATE sequences is timing-luck, not a
                # reliable proof (verified: without this delay, GIL
                # scheduling happened to fully serialize each thread's
                # read+write before the other's read ran, on every
                # attempt). A short sleep injected right after the FIRST
                # `execute()` call (the attribute SELECT) returns forces a
                # genuine interleaving: with the fix (`SELECT ... FOR
                # UPDATE`), the SECOND thread's own first `execute()`
                # instead BLOCKS inside Postgres until the first thread
                # commits, so its delay only fires after it already sees
                # the post-commit row -- no deadlock either way.
                orig_execute = conn.execute
                state = {"first_call_done": False}

                def _delayed_execute(*args: object, **kwargs: object) -> object:
                    result = orig_execute(*args, **kwargs)  # type: ignore[arg-type]
                    if not state["first_call_done"]:
                        state["first_call_done"] = True
                        time.sleep(0.05)
                    return result

                conn.execute = _delayed_execute  # type: ignore[method-assign]
                store = PgBasinStore(conn)
                barrier.wait(timeout=10)
                try:
                    store.merge_namespaced_attributes(
                        basin_id, attributes={"caravan:area": value}
                    )
                    conn.commit()
                    outcomes[key] = "ok"
                except ConfigurationError as exc:
                    conn.rollback()
                    outcomes[key] = exc

        t_a = threading.Thread(target=_attempt, args=(100.0, "a"))
        t_b = threading.Thread(target=_attempt, args=(200.0, "b"))
        t_a.start()
        t_b.start()
        t_a.join(timeout=15)
        t_b.join(timeout=15)

        assert not t_a.is_alive() and not t_b.is_alive(), "race threads hung"
        assert set(outcomes) == {"a", "b"}

        oks = [k for k, v in outcomes.items() if v == "ok"]
        errs = [
            (k, v) for k, v in outcomes.items() if isinstance(v, ConfigurationError)
        ]
        assert len(oks) == 1, f"expected exactly one winner, got {outcomes}"
        assert len(errs) == 1, f"expected exactly one conflict, got {outcomes}"
        assert "caravan:area" in str(errs[0][1])

        winner_value = 100.0 if oks[0] == "a" else 200.0
        with db_engine.connect() as check_conn:
            fetched = PgBasinStore(check_conn).fetch_basin(basin_id)
        assert fetched is not None
        # The loser's write never landed -- persisted value matches the winner only.
        assert fetched.attributes["caravan:area"] == winner_value


class TestTransactionGuard:
    """Plan 155 round-2 review MAJOR: "the repo's production connection is
    AUTOCOMMIT, so a gate failure can leave a partially applied import ...
    the canonical basin importer explicitly refuses this transaction
    shape; this one does not." Mirrors
    `test_basin_importer_persistence.py::TestTransactionGuard` via the
    shared `store/_helpers.py::require_real_transaction`."""

    def test_autocommit_connection_refused_before_any_write(
        self, db_engine: sa.Engine, tmp_path
    ) -> None:
        conn = db_engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            basin_store = PgBasinStore(conn)
            station_store = PgStationStore(conn)
            # No basin/station seeded at all: the guard must fire before
            # the parquet is even read or any store is queried, so nothing
            # needs cleanup afterwards on this shared, real-commit engine.
            path = _write_parquet(
                tmp_path, [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0}]
            )

            with pytest.raises(RuntimeError, match="AUTOCOMMIT"):
                import_caravan_attributes(
                    path, station_store=station_store, basin_store=basin_store
                )
        finally:
            conn.close()

    def test_connection_with_no_open_transaction_refused(
        self, db_engine: sa.Engine, tmp_path
    ) -> None:
        conn = db_engine.connect()
        try:
            assert not conn.in_transaction()
            basin_store = PgBasinStore(conn)
            station_store = PgStationStore(conn)
            path = _write_parquet(
                tmp_path, [{"gauge_id": "caravan_camels_ch_2009", "area": 250.0}]
            )

            with pytest.raises(RuntimeError, match="transaction"):
                import_caravan_attributes(
                    path, station_store=station_store, basin_store=basin_store
                )
        finally:
            conn.rollback()
            conn.close()
