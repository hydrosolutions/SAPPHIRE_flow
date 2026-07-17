"""Plan 082 Task 1C: SAP3-compliant Gateway test GeoPackage.

Offline test — opens the fixture `.gpkg` with geopandas and asserts it
satisfies the naming/shape rules from
``docs/requirements/04-basin-static-artifact-contract.md`` §3a/§4a: lowercase
``g_<...>`` feature names with no leading digit, polygon geometry, and at
least one banded feature (``g_<...>_band_<n>``). Also proves the companion
JSON fixture (recorded HRU + per-polygon names for manual Gateway upload) is
not drifted from the actual file contents.

Not the production export pipeline (Plan 082 Task 1C scope-out) — this is a
small, hand-built fixture used to prove the compliance rules mechanically and
to drive the manual Gateway registration step documented in the runbook.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import geopandas as gpd

_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "recap"
_GPKG_PATH = _FIXTURE_DIR / "compliant_test_basins.gpkg"
_JSON_PATH = _FIXTURE_DIR / "compliant_test_basins.json"

_BAND_NAME_RE = re.compile(r"^g_.*_band_\d+$")


class TestCompliantGeoPackage:
    def test_gpkg_and_fixture_exist(self) -> None:
        assert _GPKG_PATH.is_file(), f"missing fixture: {_GPKG_PATH}"
        assert _JSON_PATH.is_file(), f"missing fixture: {_JSON_PATH}"

    def test_has_at_least_one_layer(self) -> None:
        layers = gpd.list_layers(_GPKG_PATH)
        assert len(layers) >= 1

    def test_every_feature_is_valid_polygon_geometry(self) -> None:
        gdf = gpd.read_file(_GPKG_PATH)
        assert len(gdf) >= 1
        assert (gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])).all()
        assert gdf.geometry.is_valid.all()

    def test_every_feature_name_lowercase_and_not_leading_digit(self) -> None:
        gdf = gpd.read_file(_GPKG_PATH)
        names = gdf["name"].tolist()
        assert len(names) >= 1
        for name in names:
            assert name == name.lower(), f"{name!r} is not lowercase"
            assert not name[0].isdigit(), f"{name!r} starts with a digit"

    def test_at_least_one_banded_feature(self) -> None:
        gdf = gpd.read_file(_GPKG_PATH)
        names = gdf["name"].tolist()
        assert any(_BAND_NAME_RE.match(name) for name in names)

    def test_json_fixture_names_exactly_match_layer_names(self) -> None:
        gdf = gpd.read_file(_GPKG_PATH)
        file_names = set(gdf["name"].tolist())

        fixture = json.loads(_JSON_PATH.read_text())
        fixture_names = set(fixture["names"])

        assert fixture_names == file_names
