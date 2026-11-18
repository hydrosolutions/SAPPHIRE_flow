from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves annotations at runtime

from pydantic import BaseModel, ConfigDict
from sqlalchemy.engine import make_url

from sapphire_flow.adapters.forecast_interface import ForecastInterfaceAdapter
from sapphire_flow.models.deployment_smoke import DeploymentSmokeModel
from sapphire_flow.services.model_registry import carry_model_classification
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import ModelId

CYCLE = ensure_utc(datetime(2025, 1, 3, tzinfo=UTC))
MODEL_ID = ModelId("deployment_smoke")
POOL = "deployment-smoke"
RESOURCE_DIR = Path(str(files("sapphire_flow").joinpath("data/deployment_smoke")))
IMAGE_CONFIG_DIR = Path("/app/src/sapphire_flow/data/deployment_smoke")
MARKER = Path("/data/artifacts/smoke-setup.json")
RESULT = Path("/data/artifacts/smoke-result.json")


class SmokeSetup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    station_id: UUID
    artifact_id: UUID
    observations_passed: Literal[72]


def require_smoke_environment(role: Literal["owner", "api", "worker"]) -> str:
    run_id = os.environ.get("SAPPHIRE_SMOKE_RUN_ID", "")
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("smoke run identity is required")
    expected = {
        "SAPPHIRE_CONFIG": str(IMAGE_CONFIG_DIR / "qc-rules.toml"),
        "SAPPHIRE_CONFIG_OVERLAY": str(IMAGE_CONFIG_DIR / "runtime.toml"),
        "PREFECT_API_URL": "http://prefect-server:4200/api",
        "SAPPHIRE_DATA_DIR": "/data",
    }
    if any(os.environ.get(key) != value for key, value in expected.items()):
        raise ValueError("smoke configuration does not match packaged scenario")
    for name in ("qc-rules.toml", "runtime.toml"):
        if (IMAGE_CONFIG_DIR / name).read_bytes() != (RESOURCE_DIR / name).read_bytes():
            raise ValueError("smoke image configuration differs from package resources")
    url = make_url(os.environ.get("DATABASE_URL", ""))
    username = {
        "owner": "sapphire",
        "api": "sapphire_api",
        "worker": "sapphire_worker",
    }[role]
    if (url.host, url.port, url.database, url.username) != (
        "postgres",
        5432,
        "sapphire",
        username,
    ):
        raise ValueError("smoke database identity does not match isolated service")
    return run_id


def read_setup(run_id: str) -> SmokeSetup:
    setup = SmokeSetup.model_validate_json(MARKER.read_bytes())
    if setup.run_id != run_id:
        raise ValueError("smoke setup belongs to another run")
    return setup


def fixture_model() -> ForecastInterfaceAdapter:
    model = DeploymentSmokeModel((RESOURCE_DIR / "model-config.json").read_bytes())
    adapter = ForecastInterfaceAdapter(model)
    carry_model_classification(model, adapter)
    return adapter


def verify_resources() -> dict[str, str]:
    manifest = json.loads((RESOURCE_DIR / "manifest.json").read_text())
    hashes: dict[str, str] = manifest["sha256"]
    for name, digest in hashes.items():
        if (
            Path(name).name != name
            or hashlib.sha256((RESOURCE_DIR / name).read_bytes()).hexdigest() != digest
        ):
            raise ValueError("packaged smoke resource mismatch")
    return hashes
