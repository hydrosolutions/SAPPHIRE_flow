"""Plan 188 T1 -- the operator CLI, DB-backed acceptance tests.

Locks the CLI-level contract the pure unit tests
(`tests/unit/scripts/test_import_caravan_attributes_script.py`) cannot
reach: a real `--dry-run` structural rollback, a real manifest-station-
missing-from-parquet exit, and a real preflight failure against a live
Postgres-backed station fleet.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pytest
from shapely.geometry import MultiPolygon, Polygon

from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.ids import BasinId, StationId
from tests.conftest import make_station_config

if TYPE_CHECKING:
    import sqlalchemy as sa

_SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "import_caravan_attributes.py"
_GEOM = MultiPolygon(
    [Polygon([(7.0, 46.0), (8.0, 46.0), (8.0, 47.0), (7.0, 47.0), (7.0, 46.0)])]
)
_CREATED_AT = datetime(2024, 1, 1, tzinfo=UTC)


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location(
        "import_caravan_attributes_script_it", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["import_caravan_attributes_script_it"] = module
    spec.loader.exec_module(module)
    return module


def _write_parquet(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "data.parquet"
    pd.DataFrame(rows).to_parquet(path)
    return path


def _make_basin(*, code: str, basin_id: BasinId | None = None) -> Basin:
    return Basin(
        id=basin_id or BasinId(uuid.uuid4()),
        code=code,
        name="Test Basin",
        geometry=_GEOM,
        area_km2=123.4,
        attributes={"area": 100.0},
        band_geometries=None,
        created_at=_CREATED_AT,
        network="bafu",
    )


def _seed_station_with_basin(db_engine: sa.Engine, *, code: str) -> None:
    with db_engine.connect() as conn:
        basin = _make_basin(code=code)
        PgBasinStore(conn).store_basin(basin)
        PgStationStore(conn).store_station(
            make_station_config(
                station_id=StationId(uuid.uuid4()),
                code=code,
                network="bafu",
                basin_id=basin.id,
            )
        )
        conn.commit()


def _seed_station_without_basin(db_engine: sa.Engine, *, code: str) -> None:
    with db_engine.connect() as conn:
        PgStationStore(conn).store_station(
            make_station_config(
                station_id=StationId(uuid.uuid4()),
                code=code,
                network="bafu",
                basin_id=None,
            )
        )
        conn.commit()


class TestMissingPreconditions:
    def test_main_returns_nonzero_without_database_url(self, mod, monkeypatch) -> None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(mod, "SWISS_CARAVAN_MANIFEST_CODES", frozenset({"x"}))
        assert mod.main(["--parquet", "unused.parquet"]) == 1


class TestPinnedManifestConstant:
    """Plan 188 D1 blocker fix (review 2026-08-27): the CLI ships with the
    real, reviewed 148-station roster, not an empty placeholder that always
    fails main(). Locks that the committed literal is the T0a set, not
    merely non-empty."""

    def test_pinned_manifest_is_populated_with_exactly_148_codes(self, mod) -> None:
        assert len(mod.SWISS_CARAVAN_MANIFEST_CODES) == 148

    def test_pinned_manifest_excludes_the_dropped_gampelen_zihlbruecke_code(
        self, mod
    ) -> None:
        assert "2446" not in mod.SWISS_CARAVAN_MANIFEST_CODES

    def test_pinned_manifest_is_a_subset_of_the_onboarding_basin_ids(self, mod) -> None:
        import tomllib

        config = tomllib.loads((Path(__file__).parents[3] / "config.toml").read_text())
        onboarding_codes = frozenset(config["onboarding"]["basin_ids"])
        assert mod.SWISS_CARAVAN_MANIFEST_CODES.issubset(onboarding_codes)


class TestCleanRun:
    def test_a_clean_run_reports_every_manifest_station_matched_and_exits_0(
        self, mod, db_engine: sa.Engine, tmp_path, monkeypatch, capsys
    ) -> None:
        _seed_station_with_basin(db_engine, code="T188CLI-CLEAN")
        parquet = _write_parquet(
            tmp_path,
            [{"gauge_id": "caravan_camels_ch_T188CLI-CLEAN", "area": 250.0}],
        )
        # D1's live derivation is unit-tested in isolation
        # (test_import_caravan_attributes_script.py, TestDeriveSwissCaravan
        # Manifest); pinning it here decouples this CLI-orchestration test
        # from every OTHER D1-qualifying station any other test in this
        # shared, session-scoped `db_engine` has ever committed.
        monkeypatch.setattr(
            mod,
            "derive_swiss_caravan_manifest",
            lambda _store: frozenset({"T188CLI-CLEAN"}),
        )
        monkeypatch.setattr(
            mod, "SWISS_CARAVAN_MANIFEST_CODES", frozenset({"T188CLI-CLEAN"})
        )
        monkeypatch.setattr(
            mod, "resolve_required_static_names", lambda **_: frozenset({"area"})
        )

        result = mod.main(["--parquet", str(parquet)])

        assert result == 0
        # The name promises it REPORTS every manifest station matched — assert
        # the count actually reaches the operator's stdout, not just the exit
        # code (independent review 2026-08-27). The CLI prints counts, not
        # codes, so this pins the count line rather than the station name.
        out = capsys.readouterr().out
        assert "Matched:" in out
        assert "Missing from manifest:  0" in out
        with db_engine.connect() as check_conn:
            basin = PgStationStore(check_conn).fetch_station_by_code(
                "T188CLI-CLEAN", "bafu"
            )
            assert basin is not None and basin.basin_id is not None
            fetched = PgBasinStore(check_conn).fetch_basin(basin.basin_id)
        assert fetched is not None
        assert fetched.attributes["caravan:area"] == 250.0


class TestManifestStationMissingFromParquet:
    def test_exits_nonzero_and_names_the_missing_station(
        self, mod, db_engine: sa.Engine, tmp_path, monkeypatch, capsys
    ) -> None:
        _seed_station_with_basin(db_engine, code="T188CLI-PRESENT")
        _seed_station_without_basin(db_engine, code="T188CLI-ABSENT")
        parquet = _write_parquet(
            tmp_path,
            [{"gauge_id": "caravan_camels_ch_T188CLI-PRESENT", "area": 250.0}],
        )
        monkeypatch.setattr(
            mod,
            "derive_swiss_caravan_manifest",
            lambda _store: frozenset({"T188CLI-PRESENT", "T188CLI-ABSENT"}),
        )
        monkeypatch.setattr(
            mod,
            "SWISS_CARAVAN_MANIFEST_CODES",
            frozenset({"T188CLI-PRESENT", "T188CLI-ABSENT"}),
        )
        monkeypatch.setattr(
            mod, "resolve_required_static_names", lambda **_: frozenset({"area"})
        )

        result = mod.main(["--parquet", str(parquet)])

        assert result == 1
        # Review finding (2026-08-27): this test previously asserted only
        # `result == 1`, so a regression that dropped the diagnostic detail
        # from the CLI's error print (e.g. simplifying `except
        # ConfigurationError` to a generic message) would still pass.
        captured = capsys.readouterr()
        assert "T188CLI-ABSENT" in captured.err


class TestDryRun:
    def test_dry_run_leaves_basins_attributes_byte_identical(
        self, mod, db_engine: sa.Engine, tmp_path, monkeypatch
    ) -> None:
        _seed_station_with_basin(db_engine, code="T188CLI-DRYRUN")
        parquet = _write_parquet(
            tmp_path,
            [{"gauge_id": "caravan_camels_ch_T188CLI-DRYRUN", "area": 250.0}],
        )
        monkeypatch.setattr(
            mod,
            "derive_swiss_caravan_manifest",
            lambda _store: frozenset({"T188CLI-DRYRUN"}),
        )
        monkeypatch.setattr(
            mod, "SWISS_CARAVAN_MANIFEST_CODES", frozenset({"T188CLI-DRYRUN"})
        )
        monkeypatch.setattr(
            mod, "resolve_required_static_names", lambda **_: frozenset({"area"})
        )

        with db_engine.connect() as check_conn:
            station = PgStationStore(check_conn).fetch_station_by_code(
                "T188CLI-DRYRUN", "bafu"
            )
            assert station is not None and station.basin_id is not None
            before = PgBasinStore(check_conn).fetch_basin(station.basin_id)
        assert before is not None

        result = mod.main(["--parquet", str(parquet), "--dry-run"])

        assert result == 0
        with db_engine.connect() as check_conn:
            after = PgBasinStore(check_conn).fetch_basin(station.basin_id)
        assert after is not None
        # Read back from the DB column, not the flag -- the gate genuinely
        # ran (it would have written `caravan:area` on a real run) and the
        # transaction genuinely rolled back.
        assert after.attributes == before.attributes
        assert "caravan:area" not in after.attributes


class TestManifestMismatch:
    def test_a_one_out_one_in_swap_fails_preflight_and_prints_symmetric_difference(
        self, mod, db_engine: sa.Engine, tmp_path, monkeypatch, capsys
    ) -> None:
        _seed_station_with_basin(db_engine, code="T188CLI-SWAP-A")
        _seed_station_with_basin(db_engine, code="T188CLI-SWAP-B")
        monkeypatch.setattr(
            mod,
            "derive_swiss_caravan_manifest",
            lambda _store: frozenset({"T188CLI-SWAP-A", "T188CLI-SWAP-B"}),
        )
        # Pinned constant swaps SWAP-B for a station that does not exist --
        # same cardinality (2), differing membership.
        monkeypatch.setattr(
            mod,
            "SWISS_CARAVAN_MANIFEST_CODES",
            frozenset({"T188CLI-SWAP-A", "T188CLI-SWAP-NONEXISTENT"}),
        )
        monkeypatch.setattr(
            mod, "resolve_required_static_names", lambda **_: frozenset({"area"})
        )
        parquet = _write_parquet(tmp_path, [])

        result = mod.main(["--parquet", str(parquet)])

        assert result == 1
        # Review finding (2026-08-27): must actually contain the symmetric
        # difference, not just fail -- a regression that dropped the
        # diagnostic detail from the error print would still pass a bare
        # `result == 1` check.
        captured = capsys.readouterr()
        assert "T188CLI-SWAP-B" in captured.err  # only in derived
        assert "T188CLI-SWAP-NONEXISTENT" in captured.err  # only in pinned
        with db_engine.connect() as check_conn:
            station = PgStationStore(check_conn).fetch_station_by_code(
                "T188CLI-SWAP-B", "bafu"
            )
            assert station is not None and station.basin_id is not None
            fetched = PgBasinStore(check_conn).fetch_basin(station.basin_id)
        assert fetched is not None
        assert "caravan:area" not in fetched.attributes  # gate never ran


class TestModelAbsentPreflight:
    def test_model_absent_from_registry_fails_naming_the_model_id(
        self, mod, db_engine: sa.Engine, tmp_path, monkeypatch, capsys
    ) -> None:
        # Monkeypatched, not relying on the real environment: the standard
        # `unit`/`integration` CI jobs install `--extra aquacast`
        # (.github/workflows/ci.yml), so `cmal_pool_pt` IS genuinely
        # registered there. Force absence explicitly so the D2 preflight
        # is exercised whether or not the extra happens to be installed.
        monkeypatch.setattr(
            "sapphire_flow.services.model_registry.discover_models",
            lambda: {},
        )
        monkeypatch.setattr(mod, "SWISS_CARAVAN_MANIFEST_CODES", frozenset({"unused"}))
        parquet = _write_parquet(tmp_path, [])

        result = mod.main(["--parquet", str(parquet)])

        assert result == 1
        # The test name promises the message NAMES the model; asserting only
        # the exit code would pass against a generic failure that tells the
        # operator nothing (independent review 2026-08-27).
        assert "cmal_pool_pt" in capsys.readouterr().err
