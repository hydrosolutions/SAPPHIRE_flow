from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sapphire_flow.config.deployment import load_config
from sapphire_flow.config.forecast_qc_rules import load_forecast_qc_rules
from sapphire_flow.services import deployment_smoke as smoke
from sapphire_flow.types.enums import AlertEligibility, ModelTier

if TYPE_CHECKING:
    from pathlib import Path


class TestDeploymentSmoke:
    def test_image_config_and_package_resources_must_match(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("qc-rules.toml", "runtime.toml"):
            (tmp_path / name).write_bytes((smoke.RESOURCE_DIR / name).read_bytes())
        monkeypatch.setattr(smoke, "IMAGE_CONFIG_DIR", tmp_path)
        for key, value in {
            "SAPPHIRE_SMOKE_RUN_ID": "a" * 32,
            "SAPPHIRE_CONFIG": str(tmp_path / "qc-rules.toml"),
            "SAPPHIRE_CONFIG_OVERLAY": str(tmp_path / "runtime.toml"),
            "PREFECT_API_URL": "http://prefect-server:4200/api",
            "SAPPHIRE_DATA_DIR": "/data",
            "DATABASE_URL": "postgresql+psycopg://sapphire_api:disposable@postgres:5432/sapphire",
        }.items():
            monkeypatch.setenv(key, value)
        assert smoke.require_smoke_environment("api") == "a" * 32
        with pytest.raises(ValueError, match="database identity"):
            smoke.require_smoke_environment("owner")
        (tmp_path / "runtime.toml").write_text("max_retention_days=700")
        with pytest.raises(ValueError, match="differs from package"):
            smoke.require_smoke_environment("api")

    def test_runtime_config_uses_explicit_fixture_choices_and_model_classification(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(smoke.RESOURCE_DIR / "qc-rules.toml"))
        monkeypatch.setenv(
            "SAPPHIRE_CONFIG_OVERLAY", str(smoke.RESOURCE_DIR / "runtime.toml")
        )
        config = load_config()
        assert config.available_nwp_parameters == frozenset({"precipitation"})
        assert not config.enable_forecast_alerts
        assert not config.enable_observation_alerts
        assert not config.enable_pipeline_alerts
        assert config.operational_weather.route.value == "stored_reanalysis"
        assert not config.publication_active_tenant_ids
        assert len(load_forecast_qc_rules().rules) == 7
        model = smoke.fixture_model()
        assert getattr(model, "model_tier", None) == ModelTier.SKILL
        assert (
            getattr(model, "alert_eligibility", None)
            == AlertEligibility.NO_EVENT_INFORMATION
        )
        assert "runtime.toml" in smoke.verify_resources()
