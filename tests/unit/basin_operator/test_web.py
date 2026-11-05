from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sapphire_flow.basin_operator.inventory import Coordinate
from sapphire_flow.basin_operator.web import create_operator_app
from sapphire_flow.basin_operator.workspace import BasinWorkspace, ExtractorInterface
from sapphire_flow.services.basin_package_loader import load_basin_package


def csrf_header(client: TestClient) -> dict[str, str]:
    page = client.get("/")
    match = re.search(r"const csrf=(.*?);", page.text)
    assert match is not None
    return {
        "X-Basin-CSRF": json.loads(match.group(1)),
        "Origin": "http://127.0.0.1:8765",
    }


class TestOperatorApp:
    def test_local_host_and_csrf_guard(self, tmp_path: Path) -> None:
        workspace = BasinWorkspace(tmp_path, ())
        app = create_operator_app(workspace, port=8765)
        client = TestClient(app, base_url="http://127.0.0.1:8765")
        assert client.get("/", headers={"Host": "evil.test:8765"}).status_code == 403
        assert client.post("/confirm").status_code == 403
        headers = csrf_header(client)
        headers["Origin"] = "https://evil.test"
        assert client.post("/confirm", headers=headers).status_code == 403

    def test_synthetic_upload_extract_review_export_rehearsal(
        self, tmp_path: Path, synthetic_package: Path, inventory: bytes
    ) -> None:
        # External-process fake copies synthetic contract files. No science.
        script = """
import json, pathlib, sys, zipfile
output = sys.argv[sys.argv.index('--output') + 1]
assert output.endswith('.zip')
package_id = sys.argv[sys.argv.index('--package-id') + 1]
with zipfile.ZipFile(output, 'w') as archive:
    for path in pathlib.Path(sys.argv[1]).iterdir():
        content = path.read_bytes()
        if path.name == 'manifest.json':
            manifest = json.loads(content)
            manifest['package_id'] = package_id
            content = json.dumps(manifest).encode()
        archive.writestr(path.name, content)
"""
        workspace = BasinWorkspace(
            tmp_path / "review", (sys.executable, "-c", script, str(synthetic_package))
        )
        client = TestClient(
            create_operator_app(workspace, port=8765), base_url="http://127.0.0.1:8765"
        )
        headers = csrf_header(client)
        assert (
            client.post(
                "/upload",
                headers=headers,
                json={
                    "suffix": ".csv",
                    "content": base64.b64encode(inventory).decode(),
                },
            ).status_code
            == 200
        )
        assert client.post("/extract", headers=headers).status_code == 400
        assert client.post("/confirm", headers=headers).status_code == 200
        assert client.post("/extract", headers=headers).status_code == 200
        assert workspace.process is not None
        workspace.process.wait(timeout=10)
        page = client.get("/")
        assert page.status_code == 200, page.text
        assert page.text.count('class="selection"') == 2
        assert page.text.count("checked>") == 2
        request = {
            "codes": ["001.0"],
            "status": "approved",
            "reviewer": "Synthetic reviewer",
            "reason": "",
            "acknowledgment": "not_acknowledged",
            "fingerprint": workspace.batch().fingerprint,
        }
        response = client.post("/decision", headers=headers, json=request)
        assert response.status_code == 200, response.text
        request.update(codes=["002"])
        assert (
            client.post("/decision", headers=headers, json=request).status_code == 400
        )
        request.update(status="needs_correction", reason="Check source coordinates")
        assert (
            client.post("/decision", headers=headers, json=request).status_code == 200
        )
        response = client.post("/export", headers=headers)
        assert response.status_code == 200, response.text
        result = Path(response.json()["handover"])
        approval = json.loads((result / "approval.json").read_text())
        assert approval["source_archive_sha256"].startswith("sha256:")
        assert Path(workspace.state["output"]).is_file()
        assert [
            b.station_code for b in load_basin_package(result / "package").basins
        ] == ["001.0"]
        assert "needs_correction" in (result / "excluded-stations.csv").read_text()
        assert "X-Basin-CSRF" not in client.get("/feedback").text
        assert "sapphire_flow.app" not in sys.modules

    def test_successful_process_with_invalid_output_is_visible_failure(
        self,
        tmp_path: Path,
        inventory: bytes,
    ) -> None:
        workspace = BasinWorkspace(
            tmp_path / "invalid-output", (sys.executable, "-c", "pass")
        )
        workspace.upload(inventory, ".csv")
        workspace.confirm()
        workspace.start_extraction()
        assert workspace.process is not None
        workspace.process.wait(timeout=10)
        workspace.refresh()
        assert workspace.state["extraction"].startswith("invalid output:")
        assert workspace.state["package_dir"] is None

    def test_attached_package_disables_direct_extraction(
        self, tmp_path: Path, synthetic_package: Path, inventory: bytes
    ) -> None:
        workspace = BasinWorkspace(tmp_path / "attached", ())
        workspace.upload(inventory, ".csv")
        workspace.confirm()
        workspace.attach_package(synthetic_package)
        client = TestClient(
            create_operator_app(workspace, port=8765, package_dir=synthetic_package),
            base_url="http://127.0.0.1:8765",
        )
        headers = csrf_header(client)
        assert (
            client.post(
                "/decision",
                headers=headers,
                json={
                    "codes": ["001.0"],
                    "status": "approved",
                    "reviewer": "Hydrologist",
                    "reason": "",
                    "acknowledgment": "acknowledged",
                    "fingerprint": workspace.batch().fingerprint,
                },
            ).status_code
            == 200
        )
        before = json.loads(workspace.state_path.read_text())
        response = client.post("/extract", headers=headers)
        assert response.status_code == 400
        assert "precomputed" in response.json()["detail"]
        assert workspace.state == before
        assert "Existing extraction loaded" in client.get("/").text

    def test_release_shape_accepts_selected_interface(
        self, tmp_path: Path, inventory: bytes
    ) -> None:
        script = (
            "import argparse; p=argparse.ArgumentParser(); "
            "p.add_argument('--station', nargs=4, action='append'); "
            "p.add_argument('--package-id'); p.add_argument('--output'); p.parse_args()"
        )
        workspace = BasinWorkspace(
            tmp_path / "release-shape",
            (sys.executable, "-c", script),
            interface=ExtractorInterface.RELEASE_V020,
        )
        workspace.upload(inventory, ".csv")
        workspace.confirm()
        workspace.start_extraction()
        assert workspace.process is not None
        assert workspace.process.wait(timeout=10) == 0

    @pytest.mark.parametrize("reviewer", ["", "   "])
    def test_server_rejects_blank_reviewer_without_state_change(
        self, tmp_path: Path, synthetic_package: Path, inventory: bytes, reviewer: str
    ) -> None:
        workspace = BasinWorkspace(tmp_path / "blank", ())
        workspace.upload(inventory, ".csv")
        workspace.confirm()
        workspace.attach_package(synthetic_package)
        client = TestClient(
            create_operator_app(workspace, port=8765), base_url="http://127.0.0.1:8765"
        )
        before = workspace.state_path.read_bytes()
        response = client.post(
            "/decision",
            headers=csrf_header(client),
            json={
                "codes": ["001.0"],
                "status": "approved",
                "reviewer": reviewer,
                "reason": "",
                "acknowledgment": "acknowledged",
                "fingerprint": workspace.batch().fingerprint,
            },
        )
        assert response.status_code == 400
        assert workspace.state_path.read_bytes() == before

    def test_empty_unconfigured_and_corrected_attachment_states(
        self, tmp_path: Path, synthetic_package: Path, inventory: bytes
    ) -> None:
        workspace = BasinWorkspace(tmp_path / "states", ())
        client = TestClient(
            create_operator_app(workspace, port=8765), base_url="http://127.0.0.1:8765"
        )
        assert client.get("/").status_code == 200
        workspace.upload(inventory, ".csv")
        workspace.confirm()
        page = client.get("/").text
        assert "Configure --extractor-command" in page
        assert 'id="extract" disabled' in page
        before = workspace.state_path.read_bytes()
        response = client.post("/extract", headers=csrf_header(client))
        assert response.status_code == 400
        assert "configure --extractor-command" in response.json()["detail"]
        assert workspace.state_path.read_bytes() == before
        workspace.attach_package(synthetic_package)
        workspace.adjust(
            "001.0", Coordinate(latitude=28.01, longitude=85.01), "Survey correction"
        )
        client = TestClient(
            create_operator_app(workspace, port=8765, package_dir=synthetic_package),
            base_url="http://127.0.0.1:8765",
        )
        page = client.get("/").text
        assert "Existing extraction loaded" not in page
        assert "Attach a newly extracted package" in page
        assert client.post("/confirm", headers=csrf_header(client)).status_code == 400
        assert workspace.state["package_dir"] is None

    def test_ambiguous_launch_is_rejected(
        self, tmp_path: Path, synthetic_package: Path
    ) -> None:
        workspace = BasinWorkspace(tmp_path / "ambiguous", (sys.executable,))
        with pytest.raises(ValueError, match="not both"):
            create_operator_app(workspace, port=8765, package_dir=synthetic_package)

    def test_old_attached_ledger_is_unchanged_on_resume(
        self, tmp_path: Path, synthetic_package: Path, inventory: bytes
    ) -> None:
        workspace = BasinWorkspace(tmp_path / "old", ())
        workspace.upload(inventory, ".csv")
        workspace.confirm()
        workspace.attach_package(synthetic_package)
        workspace.state.pop("source_archive_sha256")
        workspace.save()
        before = workspace.state_path.read_bytes()
        resumed = BasinWorkspace(workspace.directory, ())
        client = TestClient(
            create_operator_app(resumed, port=8765, package_dir=synthetic_package),
            base_url="http://127.0.0.1:8765",
        )
        assert "Existing extraction loaded" in client.get("/").text
        assert resumed.state_path.read_bytes() == before
        assert resumed.state["extraction"] == "not_started"

    @pytest.mark.parametrize(
        "status, disabled",
        [
            ("not_started", False),
            ("running", True),
            ("completed", False),
            ("failed (exit 7)", False),
        ],
    )
    def test_automatic_state_display(
        self, tmp_path: Path, inventory: bytes, status: str, disabled: bool
    ) -> None:
        workspace = BasinWorkspace(tmp_path / "auto-state", (sys.executable,))
        workspace.upload(inventory, ".csv")
        workspace.confirm()
        workspace.state["extraction"] = status
        client = TestClient(
            create_operator_app(workspace, port=8765), base_url="http://127.0.0.1:8765"
        )
        page = client.get("/").text
        assert f"Extraction: {status}" in page
        assert ('id="extract" disabled' in page) == disabled
        workspace.state["confirmed"] = False
        assert 'id="extract" disabled' in client.get("/").text
