from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from sapphire_flow.basin_operator.web import create_operator_app
from sapphire_flow.basin_operator.workspace import BasinWorkspace
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
