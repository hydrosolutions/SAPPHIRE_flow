"""Local decisions bound to the exact extraction content and inventory."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

from sapphire_flow.services.basin_package_loader import (
    evaluate_basin_acceptance,
    load_basin_package,
)
from sapphire_flow.types.basin_package import compute_package_fingerprint
from sapphire_flow.types.ids import StationId

from .inventory import StationInput, inventory_json

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.types.basin_package import LoadedBasinPackage


class ReviewStatus(StrEnum):
    PENDING = "pending"
    CORRECTION = "needs_correction"
    APPROVED = "approved"


class WarningAcknowledgment(StrEnum):
    NOT_ACKNOWLEDGED = "not_acknowledged"
    ACKNOWLEDGED = "acknowledged"


@dataclass(frozen=True, kw_only=True, slots=True)
class ReviewDecision:
    station_code: str
    status: ReviewStatus
    reviewer: str
    reason: str
    acknowledgment: WarningAcknowledgment
    fingerprint: str
    reviewed_at: str


@dataclass(frozen=True, kw_only=True, slots=True)
class ReviewBatch:
    package_dir: Path
    package: LoadedBasinPackage
    stations: tuple[StationInput, ...]
    fingerprint: str
    warnings: dict[str, tuple[str, ...]]
    blockers: dict[str, tuple[str, ...]]


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def open_batch(package_dir: Path, stations: tuple[StationInput, ...]) -> ReviewBatch:
    loaded = load_basin_package(package_dir)
    expected = {s.code for s in stations}
    actual = [b.station_code for b in loaded.basins]
    if set(actual) != expected or len(actual) != len(expected):
        raise ValueError(
            "extracted station set does not exactly match confirmed inventory"
        )
    # Inventory resolution only; these deterministic IDs are never stored in SAP3.
    report = evaluate_basin_acceptance(
        loaded,
        resolve_station=lambda code, network: (
            StationId(uuid5(NAMESPACE_URL, network + ":" + code))
            if code in expected
            else None
        ),
    )
    producer = {entry.station_code: entry for entry in loaded.validation_report.basins}
    warnings: dict[str, tuple[str, ...]] = {}
    blockers: dict[str, tuple[str, ...]] = {}
    structural = (
        "geometry_present",
        "geometry_valid",
        "crs_epsg_4326",
        "geometry_2d",
        "area_positive",
        "ids_unique",
        "static_row_present",
    )
    for decision in report.decisions:
        entry = producer[decision.station_code]
        warnings[decision.station_code] = tuple(
            dict.fromkeys(
                (
                    *decision.warnings,
                    *entry.warnings,
                    *(
                        ("Producer reports warning status",)
                        if entry.status == "warning"
                        else ()
                    ),
                )
            )
        )
        failed = tuple(
            f"Producer check failed: {key}"
            for key in structural
            if entry.checks[key] is not True
        )
        blockers[decision.station_code] = (
            *decision.hold_reasons,
            *entry.errors,
            *failed,
            *(("Producer reports failed status",) if entry.status == "failed" else ()),
        )
    # Hash ALL files, even when the producer declared only partial checksums.
    hashes: dict[str, str] = {}
    for path in sorted(package_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError("package must not contain symbolic links")
        if path.is_file():
            hashes[str(path.relative_to(package_dir))] = file_hash(path)
    payload = {
        "package": compute_package_fingerprint(loaded),
        "files": hashes,
        "inventory": inventory_json(stations),
    }
    fingerprint = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                allow_nan=False,
            ).encode()
        ).hexdigest()
    )
    return ReviewBatch(
        package_dir=package_dir,
        package=loaded,
        stations=stations,
        fingerprint=fingerprint,
        warnings=warnings,
        blockers=blockers,
    )


def decide(
    batch: ReviewBatch,
    *,
    codes: tuple[str, ...],
    status: ReviewStatus,
    reviewer: str,
    reason: str,
    acknowledgment: WarningAcknowledgment,
    now: str,
) -> tuple[ReviewDecision, ...]:
    if not codes or not set(codes) <= {s.code for s in batch.stations}:
        raise ValueError("select stations from the current batch")
    if not reviewer.strip():
        raise ValueError("self-declared reviewer name is required")
    if status != ReviewStatus.APPROVED and not reason.strip():
        raise ValueError("pending/correction decisions require a reason")
    if status == ReviewStatus.APPROVED:
        if any(batch.blockers[code] for code in codes):
            raise ValueError("structurally invalid or held basins cannot be approved")
        if any(batch.warnings[code] for code in codes) and acknowledgment != (
            WarningAcknowledgment.ACKNOWLEDGED
        ):
            raise ValueError("explicit warning acknowledgment is required")
    return tuple(
        ReviewDecision(
            station_code=code,
            status=status,
            reviewer=reviewer.strip(),
            reason=reason,
            acknowledgment=acknowledgment,
            fingerprint=batch.fingerprint,
            reviewed_at=now,
        )
        for code in codes
    )


def decision_json(decisions: tuple[ReviewDecision, ...]) -> list[dict[str, Any]]:
    return [asdict(d) for d in decisions]
