"""Plan 155 T1/T1b — additive Caravan-attributes merge, DB-backed.

Locks the storage-layer contract the pure `services/caravan_statics.py`
tests cannot reach: `merge_namespaced_attributes` must NOT create a new
`basin_versions` row (no supersede, no `material_change`), must NOT touch a
non-namespaced (incumbent) attribute, and `import_caravan_attributes` must
join correctly on station identity end-to-end.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pandas as pd
import pytest
import sqlalchemy as sa
from shapely.geometry import MultiPolygon, Polygon

from sapphire_flow.db.metadata import basin_versions
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.caravan_import import import_caravan_attributes
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.ids import BasinId, StationId
from tests.conftest import make_station_config

_GEOM = MultiPolygon(
    [Polygon([(7.0, 46.0), (8.0, 46.0), (8.0, 47.0), (7.0, 47.0), (7.0, 46.0)])]
)
_CREATED_AT = datetime(2024, 1, 1, tzinfo=UTC)


def _make_basin(*, code: str = "2009", attributes: dict | None = None) -> Basin:
    return Basin(
        id=BasinId(uuid.uuid4()),
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

    def test_an_unmatched_code_raises_even_with_full_coverage_elsewhere(
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
            [
                {
                    "gauge_id": "caravan_camels_ch_2009",
                    "area": 250.0,
                    "slp_dg_sav": 12.5,
                },
                {"gauge_id": "caravan_camels_ch_9999", "area": 1.0},
            ],
        )

        with pytest.raises(ConfigurationError, match="exit gate failed"):
            import_caravan_attributes(
                path,
                station_store=station_store,
                basin_store=basin_store,
                required_static_names=frozenset({"area", "slope"}),
            )

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
