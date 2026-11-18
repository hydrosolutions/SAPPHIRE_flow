from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.deployment
class TestDeploymentSmoke:
    def test_real_worker_api_and_cleanup(self, tmp_path: Path) -> None:
        image = os.environ.get("SAPPHIRE_SMOKE_IMAGE")
        if not image:
            pytest.skip("prepare a standard image and set SAPPHIRE_SMOKE_IMAGE")
        root = Path(__file__).resolve().parents[2]
        report = tmp_path / "smoke"
        result = subprocess.run(
            [
                sys.executable,
                str(root / "tools/deployment_smoke.py"),
                "run",
                "--image",
                image,
                "--report-dir",
                str(report),
            ],
            capture_output=True,
            text=True,
            timeout=750,
            check=False,
        )
        assert result.returncode == 0, result.stdout
        evidence = json.loads((report / "report.json").read_text())
        assert evidence["forecast_verified"] and evidence["cleanup_verified"]
        assert evidence["comparisons"] == 60
        assert evidence["host_port_verified"] is False
        assert evidence["api_probe"] == {
            "origin": "worker",
            "target": "http://api:8000",
        }
        assert evidence["observations_passed"] == 72
        assert evidence["parent_flow_run_id"] != evidence["child_flow_run_id"]
        assert not (report / "secrets").exists()
