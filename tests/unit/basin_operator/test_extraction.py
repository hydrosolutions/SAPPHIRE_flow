from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Literal

import geopandas as gpd
import polars as pl
import pytest
from fastapi.testclient import TestClient

from sapphire_flow.basin_operator.inventory import Coordinate
from sapphire_flow.basin_operator.review import ReviewStatus, WarningAcknowledgment
from sapphire_flow.basin_operator.web import create_operator_app
from sapphire_flow.basin_operator.workspace import (
    PINNED_SOURCE,
    BasinWorkspace,
    ExtractorInterface,
)

# Real external process; strict argparse reproduces the release CLI boundary.
EXTRACTOR = """
import argparse, json, pathlib, zipfile
p = argparse.ArgumentParser(allow_abbrev=False)
p.add_argument('source')
p.add_argument('--station', nargs=4, action='append', required=True)
p.add_argument('--package-id', required=True)
p.add_argument('--output', required=True)
{legacy}
a = p.parse_args()
assert a.station[0] == ['00001', 'Synthetic A', '28.01', '85.01']
assert a.output.endswith('.zip')
with zipfile.ZipFile(a.output, 'w') as archive:
    for path in pathlib.Path(a.source).iterdir():
        content = path.read_bytes()
        if path.name == 'manifest.json':
            manifest = json.loads(content)
            manifest['package_id'] = a.package_id
            content = json.dumps(manifest).encode()
        archive.writestr(path.name, content)
"""


@pytest.fixture
def leading_zero_package(synthetic_package: Path) -> Path:
    path = synthetic_package / "basins.gpkg"
    frame = gpd.read_file(path)
    for column in ("station_code", "basin_code"):
        frame.loc[0, column] = "00001"
    frame.loc[0, "gauge_id"] = "nepal_00001"
    frame.loc[0, "name"] = "g_00001"
    path.unlink()
    frame.to_file(path, layer="polygons", driver="GPKG")
    path = synthetic_package / "static_attributes.parquet"
    pl.read_parquet(path).with_columns(
        pl.col("gauge_id").replace({"nepal_001.0": "nepal_00001"})
    ).write_parquet(path)
    path = synthetic_package / "validation_report.json"
    report = json.loads(path.read_text())
    report["basins"][0].update(station_code="00001", basin_code="00001", name="g_00001")
    path.write_text(json.dumps(report))
    return synthetic_package


@pytest.mark.parametrize("interface", list(ExtractorInterface))
@pytest.mark.parametrize("replacement_path", ["direct", "page"])
def test_strict_subprocess_resume_and_export(
    tmp_path: Path,
    leading_zero_package: Path,
    inventory: bytes,
    interface: ExtractorInterface,
    replacement_path: Literal["direct", "page"],
) -> None:
    legacy = (
        "p.add_argument('--snap-strategy', choices=['distance-first'], required=True)"
        if interface is ExtractorInterface.HISTORICAL_V2
        else ""
    )
    script = EXTRACTOR.replace("{legacy}", legacy)
    workspace = BasinWorkspace(
        tmp_path / "automatic",
        (sys.executable, "-c", script, str(leading_zero_package)),
        interface=interface,
    )
    workspace.upload(inventory.replace(b"001.0", b"00001"), ".csv")
    workspace.adjust(
        "00001", Coordinate(latitude=28.01, longitude=85.01), "Surveyed point"
    )
    workspace.confirm()
    workspace.start_extraction()
    assert workspace.state["extraction"] == "running"
    assert workspace.process is not None
    assert workspace.process.wait(timeout=10) == 0
    workspace.refresh()
    assert workspace.state["extraction"] == "completed"
    batch = workspace.batch()
    assert batch.package.manifest.package_id == workspace.state["expected_package_id"]
    assert [s.code for s in batch.stations] == ["00001", "002"]
    archive_hash = workspace.state["source_archive_sha256"]
    assert archive_hash.startswith("sha256:")
    invocation_path = Path(workspace.state["output"]).parent / "invocation.json"
    invocation_bytes = invocation_path.read_bytes()
    invocation = json.loads(invocation_bytes)
    assert invocation["interface"] == interface.value
    assert invocation["inventory"][0]["extraction_point"] == {
        "latitude": 28.01,
        "longitude": 85.01,
    }
    if interface is ExtractorInterface.RELEASE_V020:
        assert invocation["expected_source_configuration"]["commit"] == PINNED_SOURCE
        assert "--snap-strategy" not in invocation["argv"]
    workspace.review(
        codes=("00001",),
        status=ReviewStatus.APPROVED,
        reviewer="Synthetic reviewer",
        reason="",
        acknowledgment=WarningAcknowledgment.ACKNOWLEDGED,
        fingerprint=batch.fingerprint,
        now="2026-11-05T12:00:00Z",
    )
    decisions = workspace.decisions
    resumed = BasinWorkspace(workspace.directory, ())
    client = TestClient(
        create_operator_app(
            resumed, port=8765, package_dir=Path(workspace.state["package_dir"])
        ),
        base_url="http://127.0.0.1:8765",
    )
    import re

    match = re.search(r"const csrf=(.*?);", client.get("/").text)
    assert match is not None
    headers = {"X-Basin-CSRF": json.loads(match.group(1))}
    assert client.post("/confirm", headers=headers).status_code == 200
    assert resumed.decisions == decisions
    assert resumed.batch().fingerprint == batch.fingerprint
    assert resumed.state["source_archive_sha256"] == archive_hash
    response = client.post("/export", headers=headers)
    assert response.status_code == 200
    approval = json.loads(
        (Path(response.json()["handover"]) / "approval.json").read_text()
    )
    assert approval["source_archive_sha256"] == archive_hash
    assert invocation_path.read_bytes() == invocation_bytes
    if replacement_path == "page":
        # Replacing the attached directory first goes through page invalidation.
        manifest_path = Path(resumed.state["package_dir"]) / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["package_id"] = "replacement-producer-package"
        manifest_path.write_text(json.dumps(manifest))
        changed = client.get("/")
        assert changed.status_code == 400
        assert "package changed" in changed.json()["detail"]
        assert resumed.decisions == ()
        assert client.post("/confirm", headers=headers).status_code == 200
        replacement = resumed.batch()
        assert replacement.package.manifest.package_id == "replacement-producer-package"
        assert replacement.fingerprint != batch.fingerprint
        response = client.post(
            "/decision",
            headers=headers,
            json={
                "codes": ["00001"],
                "status": "approved",
                "reviewer": "Fresh reviewer",
                "reason": "Replacement reviewed",
                "acknowledgment": "acknowledged",
                "fingerprint": replacement.fingerprint,
            },
        )
        assert response.status_code == 200
        response = client.post("/export", headers=headers)
        assert response.status_code == 200
        approval = json.loads(
            (Path(response.json()["handover"]) / "approval.json").read_text()
        )
        assert approval["source_archive_sha256"] is None
        assert resumed.state["source_archive_sha256"] is None
    # A direct different-package attachment must not borrow a ZIP hash either.
    resumed.attach_package(leading_zero_package)
    assert resumed.state["source_archive_sha256"] is None
    assert resumed.decisions == ()


@pytest.mark.parametrize(
    "script, expected",
    [
        ("raise SystemExit(7)", "failed (exit 7)"),
        (
            "import pathlib,sys; "
            "pathlib.Path(sys.argv[sys.argv.index('--output')+1])"
            ".write_bytes(b'not a ZIP')",
            "invalid output:",
        ),
    ],
)
def test_external_failure_states(
    tmp_path: Path, inventory: bytes, script: str, expected: str
) -> None:
    workspace = BasinWorkspace(
        tmp_path / "failure",
        (sys.executable, "-c", script),
        interface=ExtractorInterface.RELEASE_V020,
    )
    workspace.upload(inventory, ".csv")
    workspace.confirm()
    workspace.start_extraction()
    assert workspace.process is not None
    workspace.process.wait(timeout=10)
    workspace.refresh()
    assert workspace.state["extraction"].startswith(expected)
    assert workspace.state["package_dir"] is None
    client = TestClient(
        create_operator_app(workspace, port=8765), base_url="http://127.0.0.1:8765"
    )
    assert expected in client.get("/").text
