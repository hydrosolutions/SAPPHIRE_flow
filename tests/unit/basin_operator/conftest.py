from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import Polygon

from sapphire_flow.basin_operator.handover import write_json

INVENTORY = (
    b"station_code,display_name,latitude,longitude,published_area_km2,source\n"
    b"001.0,Synthetic A,28,85,10,Synthetic survey\n"
    b"002,Synthetic B,28.2,85.2,,Synthetic survey\n"
)


@pytest.fixture
def inventory() -> bytes:
    return INVENTORY


@pytest.fixture
def synthetic_package(tmp_path: Path) -> Path:
    package = tmp_path / "source-package"
    package.mkdir()
    (package / "README.md").write_text(
        "Synthetic package for integration tests only.\n"
    )
    rows = []
    for code, name, x, y in (
        ("001.0", "Synthetic A", 85.0, 28.0),
        ("002", "Synthetic B", 85.2, 28.2),
    ):
        rows.append(
            {
                "network": "dhm",
                "station_code": code,
                "basin_code": code,
                "gauge_id": "nepal_" + code,
                "gateway_hru_name": "synthetic_basins",
                "name": "g_" + code.replace(".", "_"),
                "display_name": name,
                "area_km2": 12.0,
                "outlet_lon": x,
                "outlet_lat": y,
                "latitude": y,
                "longitude": x,
                "delineation_method": "synthetic",
                "outlet_snap_distance_m": 20.0,
                "geometry": Polygon(
                    [(x, y), (x + 0.01, y), (x + 0.01, y + 0.01), (x, y + 0.01)]
                ),
            }
        )
    gpd.GeoDataFrame(rows, crs="EPSG:4326").to_file(
        package / "basins.gpkg", layer="polygons", driver="GPKG"
    )
    pl.DataFrame(
        {"gauge_id": [row["gauge_id"] for row in rows], "mean_elev_m": [1000.0, 1100.0]}
    ).write_parquet(package / "static_attributes.parquet")
    write_json(
        package / "feature_catalog.json",
        {
            "features": [
                {
                    "name": "mean_elev_m",
                    "type": "float",
                    "unit": "m",
                    "source_dataset": "synthetic",
                    "aggregation": "mean",
                    "description": "Synthetic elevation",
                    "climatology_window": None,
                    "required_by_models": [],
                }
            ]
        },
    )
    write_json(
        package / "validation_report.json",
        {
            "summary": {"passed": 1, "failed": 0, "warnings": 1},
            "basins": [
                {
                    **{
                        key: row[key]
                        for key in (
                            "network",
                            "basin_code",
                            "station_code",
                            "gateway_hru_name",
                            "name",
                        )
                    },
                    "status": "warning" if row["station_code"] == "002" else "passed",
                    "warnings": ["Synthetic snap warning"]
                    if row["station_code"] == "002"
                    else [],
                    "errors": [],
                    "checks": {
                        **{
                            key: True
                            for key in (
                                "geometry_present",
                                "geometry_valid",
                                "crs_epsg_4326",
                                "geometry_2d",
                                "area_positive",
                                "ids_unique",
                                "static_row_present",
                                "required_static_features_present",
                            )
                        },
                        "outlet_snap_distance_m": 20.0,
                        "coverage_status": "inside",
                    },
                }
                for row in rows
            ],
        },
    )
    write_json(
        package / "manifest.json",
        {
            "contract_version": "basin-static-artifact/v1",
            "package_id": "synthetic-source",
            "created_at": "2026-11-05T12:00:00Z",
            "network": "dhm",
            "crs": "EPSG:4326",
            "extractor": {"name": "synthetic-test", "version": "1"},
            "source_datasets": [
                {"name": "synthetic", "version": "1", "purpose": "geometry"}
            ],
            "gateway_hru_names": ["synthetic_basins"],
            "files": {
                "basins": "basins.gpkg",
                "static_attributes": "static_attributes.parquet",
                "feature_catalog": "feature_catalog.json",
                "validation_report": "validation_report.json",
            },
        },
    )
    return package
