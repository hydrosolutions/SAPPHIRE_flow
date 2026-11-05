from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import geopandas as gpd
import pytest

from sapphire_flow.basin_operator.handover import export_handover
from sapphire_flow.basin_operator.inventory import parse_inventory
from sapphire_flow.basin_operator.presentation import map_data, render_page
from sapphire_flow.basin_operator.review import (
    ReviewStatus,
    WarningAcknowledgment,
    decide,
    file_hash,
    open_batch,
)
from sapphire_flow.services.basin_package_loader import load_basin_package

NOW = "2026-11-05T12:00:00Z"


class TestExportHandover:
    def test_approved_only_valid_immutable_subset(
        self, tmp_path: Path, synthetic_package: Path, inventory: bytes
    ) -> None:
        originals = {p.name: file_hash(p) for p in synthetic_package.iterdir()}
        batch = open_batch(synthetic_package, parse_inventory(inventory, ".csv"))
        decisions = decide(
            batch,
            codes=("001.0",),
            status=ReviewStatus.APPROVED,
            reviewer="Hydrologist",
            reason="",
            acknowledgment=WarningAcknowledgment.NOT_ACKNOWLEDGED,
            now=NOW,
        )
        html = render_page(data=map_data(batch, decisions), inventory=[])
        target = tmp_path / "handover"
        export_handover(
            batch,
            decisions,
            target,
            package_id="synthetic-approved",
            now=NOW,
            map_html=html,
        )
        derived = load_basin_package(target / "package")
        assert [b.station_code for b in derived.basins] == ["001.0"]
        assert set(derived.static_attributes) == {"nepal_001.0"}
        assert derived.manifest.package_id == "synthetic-approved"
        assert derived.basins[0].geometry.equals_exact(
            batch.package.basins[0].geometry, 0
        )
        assert derived.validation_report.passed == 1
        assert (
            "002,pending,Awaiting review"
            in (target / "excluded-stations.csv").read_text()
        )
        assert {p.name: file_hash(p) for p in synthetic_package.iterdir()} == originals
        for line in (target / "checksums.sha256").read_text().splitlines():
            checksum, filename = line.split("  ", 1)
            assert file_hash(target / filename) == "sha256:" + checksum
        with pytest.raises(ValueError, match="immutable"):
            export_handover(
                batch, decisions, target, package_id="other", now=NOW, map_html=html
            )

    def test_changed_geometry_invalidates_approval(
        self, tmp_path: Path, synthetic_package: Path, inventory: bytes
    ) -> None:
        batch = open_batch(synthetic_package, parse_inventory(inventory, ".csv"))
        decisions = decide(
            batch,
            codes=("001.0",),
            status=ReviewStatus.APPROVED,
            reviewer="Hydrologist",
            reason="",
            acknowledgment=WarningAcknowledgment.NOT_ACKNOWLEDGED,
            now=NOW,
        )
        frame = gpd.read_file(synthetic_package / "basins.gpkg")
        frame.geometry = frame.geometry.translate(xoff=0.001)
        (synthetic_package / "basins.gpkg").unlink()
        frame.to_file(
            synthetic_package / "basins.gpkg", layer="polygons", driver="GPKG"
        )
        with pytest.raises(ValueError, match="changed"):
            export_handover(
                batch,
                decisions,
                tmp_path / "out",
                package_id="other",
                now=NOW,
                map_html="",
            )


class TestRenderPage:
    def test_feedback_is_read_only_and_escapes_user_data(
        self, synthetic_package: Path, inventory: bytes
    ) -> None:
        path = synthetic_package / "basins.gpkg"
        frame = gpd.read_file(path)
        frame.loc[0, "display_name"] = "</script><script>alert(1)</script>"
        path.unlink()
        frame.to_file(path, layer="polygons", driver="GPKG")
        batch = open_batch(
            synthetic_package,
            parse_inventory(
                inventory.replace(
                    b"Synthetic A", b"</script><script>alert(1)</script>"
                ),
                ".csv",
            ),
        )
        html = render_page(data=map_data(batch, ()), inventory=[])
        assert "Read-only feedback copy" in html
        assert "X-Basin-CSRF" not in html
        assert "data-decision" not in html
        assert "</script><script>alert(1)</script>" not in html
        assert "True-color satellite" in html and "Terrain" in html
        assert "tileerror" in html
        assert "alternative" not in html.lower()
