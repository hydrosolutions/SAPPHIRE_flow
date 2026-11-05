from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import geopandas as gpd
import pytest

from sapphire_flow.basin_operator.inventory import Coordinate, parse_inventory
from sapphire_flow.basin_operator.review import (
    ReviewStatus,
    WarningAcknowledgment,
    decide,
    open_batch,
)
from sapphire_flow.basin_operator.workspace import BasinWorkspace

NOW = "2026-11-05T12:00:00Z"


class TestReview:
    @pytest.mark.parametrize(
        "names", [["Wrong name", "Synthetic B"], ["Synthetic B", "Synthetic A"]]
    )
    def test_rejects_wrong_or_swapped_display_names(
        self,
        synthetic_package: Path,
        inventory: bytes,
        names: list[str],
    ) -> None:
        path = synthetic_package / "basins.gpkg"
        frame = gpd.read_file(path)
        frame["display_name"] = names
        path.unlink()
        frame.to_file(path, layer="polygons", driver="GPKG")
        with pytest.raises(ValueError, match="display_name.*001.0"):
            open_batch(synthetic_package, parse_inventory(inventory, ".csv"))

    def test_warning_needs_acknowledgment(
        self, synthetic_package: Path, inventory: bytes
    ) -> None:
        batch = open_batch(synthetic_package, parse_inventory(inventory, ".csv"))
        with pytest.raises(ValueError, match="acknowledgment"):
            decide(
                batch,
                codes=("002",),
                status=ReviewStatus.APPROVED,
                reviewer="Hydrologist",
                reason="",
                acknowledgment=WarningAcknowledgment.NOT_ACKNOWLEDGED,
                now=NOW,
            )
        assert (
            decide(
                batch,
                codes=("002",),
                status=ReviewStatus.APPROVED,
                reviewer="Hydrologist",
                reason="",
                acknowledgment=WarningAcknowledgment.ACKNOWLEDGED,
                now=NOW,
            )[0].status
            == "approved"
        )

    def test_producer_failed_check_blocks_approval(
        self, synthetic_package: Path, inventory: bytes
    ) -> None:
        path = synthetic_package / "validation_report.json"
        report = json.loads(path.read_text())
        report["basins"][0]["checks"]["geometry_valid"] = False
        path.write_text(json.dumps(report))
        batch = open_batch(synthetic_package, parse_inventory(inventory, ".csv"))
        with pytest.raises(ValueError, match="invalid"):
            decide(
                batch,
                codes=("001.0",),
                status=ReviewStatus.APPROVED,
                reviewer="Hydrologist",
                reason="override",
                acknowledgment=WarningAcknowledgment.ACKNOWLEDGED,
                now=NOW,
            )

    def test_unrequested_station_rejected(
        self, synthetic_package: Path, inventory: bytes
    ) -> None:
        with pytest.raises(ValueError, match="exactly match"):
            open_batch(synthetic_package, parse_inventory(inventory, ".csv")[:1])

    def test_pending_requires_reason(
        self, synthetic_package: Path, inventory: bytes
    ) -> None:
        batch = open_batch(synthetic_package, parse_inventory(inventory, ".csv"))
        with pytest.raises(ValueError, match="reason"):
            decide(
                batch,
                codes=("001.0",),
                status=ReviewStatus.PENDING,
                reviewer="Hydrologist",
                reason="",
                acknowledgment=WarningAcknowledgment.NOT_ACKNOWLEDGED,
                now=NOW,
            )


class TestBasinWorkspace:
    def test_resume_and_changed_content_reapproval(
        self, tmp_path: Path, synthetic_package: Path, inventory: bytes
    ) -> None:
        workspace = BasinWorkspace(tmp_path / "review", ())
        workspace.upload(inventory, ".csv")
        workspace.confirm()
        workspace.attach_package(synthetic_package)
        workspace.review(
            codes=("001.0",),
            status=ReviewStatus.APPROVED,
            reviewer="Hydrologist",
            reason="",
            acknowledgment=WarningAcknowledgment.NOT_ACKNOWLEDGED,
            fingerprint=workspace.batch().fingerprint,
            now=NOW,
        )
        resumed = BasinWorkspace(workspace.directory, ())
        assert resumed.decisions[0].status == "approved"
        # Undeclared producer payload also invalidates review.
        (synthetic_package / "evidence.txt").write_text("new evidence")
        with pytest.raises(ValueError, match="approvals cleared"):
            resumed.batch()
        assert resumed.decisions == ()
        with pytest.raises(ValueError, match="new package_id"):
            resumed.batch()
        restarted = BasinWorkspace(workspace.directory, ())
        with pytest.raises(ValueError, match="new package_id"):
            restarted.batch()
        with pytest.raises(ValueError, match="new package_id"):
            restarted.attach_package(synthetic_package)
        # A genuinely new package identity permits a fresh review, never old approval.
        manifest_path = synthetic_package / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["package_id"] = "synthetic-corrected"
        manifest_path.write_text(json.dumps(manifest))
        restarted.attach_package(synthetic_package)
        assert restarted.decisions == ()
        restarted.review(
            codes=("001.0",),
            status=ReviewStatus.APPROVED,
            reviewer="Hydrologist",
            reason="Fresh review",
            acknowledgment=WarningAcknowledgment.NOT_ACKNOWLEDGED,
            fingerprint=restarted.batch().fingerprint,
            now=NOW,
        )
        assert restarted.decisions[0].status == ReviewStatus.APPROVED

    def test_inventory_provenance_change_does_not_mutate_package_identity(
        self,
        tmp_path: Path,
        synthetic_package: Path,
        inventory: bytes,
    ) -> None:
        workspace = BasinWorkspace(tmp_path / "review", ())
        workspace.upload(inventory, ".csv")
        workspace.confirm()
        workspace.attach_package(synthetic_package)
        first = workspace.batch()
        workspace.state["inventory"][0]["source"] = "Clarified survey citation"
        workspace.save()
        workspace.attach_package(synthetic_package)
        current = BasinWorkspace(workspace.directory, ()).batch()
        assert current.content_fingerprint == first.content_fingerprint
        assert current.fingerprint != first.fingerprint

    def test_adjustment_preserves_original_and_requires_new_package(
        self, tmp_path: Path, synthetic_package: Path, inventory: bytes
    ) -> None:
        workspace = BasinWorkspace(tmp_path / "review", ())
        workspace.upload(inventory, ".csv")
        workspace.confirm()
        workspace.attach_package(synthetic_package)
        workspace.adjust(
            "001.0", Coordinate(latitude=28.01, longitude=85.01), "Surveyed pour point"
        )
        assert workspace.stations[0].original.latitude == 28
        assert workspace.stations[0].point.latitude == 28.01
        assert (workspace.directory / "original-upload.csv").read_bytes() == inventory
        workspace.confirm()
        with pytest.raises(ValueError, match="newly extracted"):
            workspace.attach_package(synthetic_package)
