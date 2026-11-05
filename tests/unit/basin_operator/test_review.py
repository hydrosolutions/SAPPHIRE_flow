from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

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
