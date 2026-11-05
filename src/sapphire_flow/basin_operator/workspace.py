"""One facilitator's resumable batch and explicit external extraction process."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from sapphire_flow.exceptions import BasinPackageRejectedError

from .archive import unpack_package
from .handover import write_json
from .inventory import (
    Coordinate,
    StationInput,
    inventory_json,
    parse_inventory,
    restore_inventory,
)
from .review import (
    ReviewBatch,
    ReviewDecision,
    ReviewStatus,
    WarningAcknowledgment,
    decide,
    decision_json,
    file_hash,
    open_batch,
)


def extraction_argv(
    prefix: tuple[str, ...],
    stations: tuple[StationInput, ...],
    *,
    package_id: str,
    output: Path,
) -> list[str]:
    if not prefix or any(not arg or "\x00" in arg for arg in prefix):
        raise ValueError("configure a supported extractor argv prefix")
    if any(arg in ("--station", "--output", "--package-id") for arg in prefix):
        raise ValueError("station/output/package-id arguments belong to the operator")
    for index, argument in enumerate(prefix):
        if argument == "--snap-strategy" and (
            index + 1 >= len(prefix) or prefix[index + 1] != "distance-first"
        ):
            raise ValueError(
                "only the accepted default distance-first snap is supported"
            )
        if argument.startswith("--snap-strategy=") and argument != (
            "--snap-strategy=distance-first"
        ):
            raise ValueError(
                "only the accepted default distance-first snap is supported"
            )
    args = list(prefix)
    if not any(
        arg == "--snap-strategy" or arg.startswith("--snap-strategy=") for arg in prefix
    ):
        args.extend(["--snap-strategy", "distance-first"])
    for station in stations:
        args.extend(
            [
                "--station",
                station.code,
                station.display_name,
                str(station.point.latitude),
                str(station.point.longitude),
            ]
        )
    return [*args, "--package-id", package_id, "--output", str(output)]


class BasinWorkspace:
    def __init__(self, directory: Path, command: tuple[str, ...]) -> None:
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.command = command
        self.process: subprocess.Popen[bytes] | None = None
        self.state: dict[str, Any] = {
            "inventory": [],
            "confirmed": False,
            "decisions": [],
            "package_dir": None,
            "fingerprint": None,
            "extraction": "not_started",
        }
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
            if self.state["extraction"] == "running":
                self.state["extraction"] = (
                    "interrupted; inspect extraction log/output before retry"
                )
                self.save()

    @property
    def state_path(self) -> Path:
        return self.directory / "review-state.json"

    @property
    def stations(self) -> tuple[StationInput, ...]:
        return restore_inventory(self.state["inventory"])

    @property
    def decisions(self) -> tuple[ReviewDecision, ...]:
        return tuple(
            ReviewDecision(
                station_code=row["station_code"],
                status=ReviewStatus(row["status"]),
                acknowledgment=WarningAcknowledgment(row["acknowledgment"]),
                reviewer=row["reviewer"],
                reason=row["reason"],
                fingerprint=row["fingerprint"],
                reviewed_at=row["reviewed_at"],
            )
            for row in self.state["decisions"]
        )

    def save(self) -> None:
        temporary = self.directory / ".review-state.tmp"
        write_json(temporary, self.state)
        temporary.replace(self.state_path)

    def upload(self, content: bytes, suffix: str) -> None:
        if self.state["inventory"]:
            raise ValueError(
                "this workspace already has an inventory; "
                "start a new workspace for a new batch"
            )
        stations = parse_inventory(content, suffix)
        (self.directory / ("original-upload" + suffix)).write_bytes(content)
        self.state["inventory"] = inventory_json(stations)
        self.save()

    def confirm(self) -> None:
        _ = self.stations  # validates restored inventory too
        self.state["confirmed"] = True
        self.save()

    def adjust(self, code: str, point: Coordinate, reason: str) -> None:
        if self.process is not None and self.process.poll() is None:
            raise ValueError("wait for extraction to finish before adjusting inputs")
        if code not in {s.code for s in self.stations}:
            raise ValueError("unknown station")
        if self.state["package_dir"]:
            previous_package = self.batch().package.manifest.package_id
            self.state.setdefault("superseded_packages", []).append(previous_package)
        previous = self.state["inventory"]
        stations = tuple(
            replace(s, extraction_point=point, adjustment_reason=reason)
            if s.code == code
            else s
            for s in self.stations
        )
        self.state.setdefault("input_history", []).append(previous)
        self.state["inventory"] = inventory_json(stations)
        self.state["confirmed"] = False
        self.state["decisions"] = []
        self.state["package_dir"] = None
        self.state["fingerprint"] = None
        self.save()

    def start_extraction(self) -> None:
        if not self.state["confirmed"]:
            raise ValueError("confirm interpreted inventory before extraction")
        if self.process is not None and self.process.poll() is None:
            raise ValueError("extraction is already running")
        attempt = self.directory / ("extraction-" + uuid4().hex)
        attempt.mkdir()
        output = attempt / "package.zip"
        package_id = "basins-" + uuid4().hex
        argv = extraction_argv(
            self.command,
            self.stations,
            package_id=package_id,
            output=output,
        )
        write_json(
            attempt / "invocation.json",
            {"argv": argv, "inventory": inventory_json(self.stations)},
        )
        with (attempt / "extractor.log").open("wb") as log:
            self.process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                shell=False,
                cwd=attempt,
            )
        self.state.update(
            extraction="running",
            output=str(output),
            expected_package_id=package_id,
            decisions=[],
            package_dir=None,
            fingerprint=None,
        )
        self.save()

    def refresh(self) -> None:
        if self.process is None:
            return
        code = self.process.poll()
        if code is None:
            return
        self.process = None
        self.state["extraction"] = (
            "completed" if code == 0 else f"failed (exit {code}); inspect extractor.log"
        )
        self.save()
        if code == 0:
            try:
                archive = Path(self.state["output"])
                directory = archive.parent / "package"
                unpack_package(archive, directory)
                batch = open_batch(directory, self.stations)
                if (
                    batch.package.manifest.package_id
                    != self.state["expected_package_id"]
                ):
                    raise ValueError(
                        "extractor output package_id differs from requested ID"
                    )
                self.attach_package(directory)
                self.state["source_archive_sha256"] = file_hash(archive)
                self.save()
            except (ValueError, OSError, BasinPackageRejectedError) as exc:
                self.state["extraction"] = f"invalid output: {exc}"
                self.save()

    def attach_package(self, directory: Path) -> None:
        if not self.state["confirmed"]:
            raise ValueError(
                "confirm interpreted inventory before attaching extraction"
            )
        batch = open_batch(directory.resolve(), self.stations)
        if batch.package.manifest.package_id in self.state.get(
            "superseded_packages", []
        ):
            raise ValueError(
                "corrected inputs require a newly extracted package identity"
            )
        self._observe_package_identity(batch)
        if batch.fingerprint != self.state["fingerprint"]:
            self.state["decisions"] = []
        self.state.update(
            package_dir=str(directory.resolve()),
            fingerprint=batch.fingerprint,
            source_archive_sha256=None,
        )
        self.save()

    def batch(self) -> ReviewBatch:
        if not self.state["package_dir"]:
            raise ValueError("no valid extraction package attached")
        batch = open_batch(Path(self.state["package_dir"]), self.stations)
        self._observe_package_identity(batch)
        if batch.fingerprint != self.state["fingerprint"]:
            self.state["decisions"] = []
            self.state["fingerprint"] = batch.fingerprint
            self.save()
            raise ValueError(
                "package changed; previous approvals cleared; reload and review again"
            )
        return batch

    def _observe_package_identity(self, batch: ReviewBatch) -> None:
        identities = self.state.setdefault("package_identities", {})
        package_id = batch.package.manifest.package_id
        observed = identities.get(package_id)
        if observed is not None and observed != batch.content_fingerprint:
            self.state["decisions"] = []
            self.save()
            raise ValueError(
                "package identity reused for changed content; approvals cleared; "
                "a new package_id is required"
            )
        if observed is None:
            identities[package_id] = batch.content_fingerprint
            self.save()

    def review(
        self,
        *,
        codes: tuple[str, ...],
        status: ReviewStatus,
        reviewer: str,
        reason: str,
        acknowledgment: WarningAcknowledgment,
        fingerprint: str,
        now: str,
    ) -> None:
        batch = self.batch()
        if fingerprint != batch.fingerprint:
            raise ValueError("stale review page; reload the current package")
        updated = decide(
            batch,
            codes=codes,
            status=status,
            reviewer=reviewer,
            reason=reason,
            acknowledgment=acknowledgment,
            now=now,
        )
        decisions = {d.station_code: d for d in self.decisions}
        decisions.update({d.station_code: d for d in updated})
        self.state["decisions"] = decision_json(tuple(decisions.values()))
        self.save()
