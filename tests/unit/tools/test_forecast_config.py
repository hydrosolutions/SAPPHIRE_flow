from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "forecast_config", ROOT / "tools/forecast_config.py"
)
assert spec is not None and spec.loader is not None
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


def configuration() -> dict[str, Any]:
    baseline = {
        "services": {
            "prefect-worker": {
                "environment": {
                    "SAPPHIRE_CONFIG_OVERLAY": "/app/weather.toml,/app/model.toml",
                    "KEEP": "private-value",
                },
                "volumes": [],
            },
            "prefect-worker-ingest": {
                "environment": {"KEEP": "ingest-value"},
                "volumes": [
                    {
                        "type": "bind",
                        "source": "/host/provider.toml",
                        "target": "/app/ingest.toml",
                        "read_only": True,
                    }
                ],
            },
            "init": {"environment": {"KEEP": "init-value"}},
        }
    }
    candidate = copy.deepcopy(baseline)
    candidate["services"]["prefect-worker"]["volumes"].append(
        {
            "type": "bind",
            "source": "/host/provider.toml",
            "target": config.PROVIDER_TARGET,
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    )
    candidate["services"]["init"]["environment"][config.REGISTRATION_KEY] = (
        "/app/weather.toml,/app/model.toml," + config.PROVIDER_TARGET
    )
    return {
        "baseline": baseline,
        "candidate": candidate,
        "stage": "enabled",
        "provider_source": "/host/provider.toml",
        "ingest_provider_target": "/app/ingest.toml",
        "pool": {
            "pool_name": "default",
            "forecast_overlay_in_defaults": False,
            "forecast_overlay_in_template": False,
            "template_ambiguous": False,
        },
    }


class TestForecastConfiguration:
    def test_exact_scoped_delta_and_safe_result(self) -> None:
        data = configuration()
        original = copy.deepcopy(data)
        result = config.verify_forecast_configuration(data)
        assert result == {
            "configuration_matches": True,
            "stage": "enabled",
            "provider_target": config.PROVIDER_TARGET,
        }
        assert data == original
        assert "private-value" not in json.dumps(result)

    def test_disabled_stage_keeps_mount_without_selection(self) -> None:
        data = configuration()
        data["stage"] = "disabled"
        data["candidate"]["services"]["init"]["environment"][
            config.REGISTRATION_KEY
        ] = ""
        assert (
            config.verify_forecast_configuration(data)["configuration_matches"] is True
        )

    @pytest.mark.parametrize(
        "path,value",
        [
            (
                ("candidate", "services", "prefect-worker", "environment", "KEEP"),
                "changed",
            ),
            (
                (
                    "candidate",
                    "services",
                    "prefect-worker",
                    "environment",
                    "SAPPHIRE_CONFIG_OVERLAY",
                ),
                "/app/other.toml",
            ),
            (
                (
                    "candidate",
                    "services",
                    "init",
                    "environment",
                    config.REGISTRATION_KEY,
                ),
                "/app/provider.toml",
            ),
            (
                (
                    "candidate",
                    "services",
                    "prefect-worker-ingest",
                    "environment",
                    "KEEP",
                ),
                "changed",
            ),
            (("pool", "forecast_overlay_in_defaults"), True),
            (("pool", "template_ambiguous"), True),
            (("pool", "forecast_overlay_in_template"), "true"),
            (("pool", "pool_name"), "ingest"),
            (
                (
                    "baseline",
                    "services",
                    "prefect-worker",
                    "environment",
                    "SAPPHIRE_CONFIG_OVERLAY",
                ),
                "",
            ),
            (("provider_source",), "/different/provider.toml"),
        ],
    )
    def test_unsafe_or_unrelated_changes_refuse(
        self, path: tuple[str, ...], value: object
    ) -> None:
        data = configuration()
        parent = data
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = value
        with pytest.raises(ValueError):
            config.verify_forecast_configuration(data)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("read_only", False),
            ("source", "/other.toml"),
            ("type", "volume"),
            ("bind", {"create_host_path": True}),
        ],
    )
    def test_mount_contract_refuses(self, field: str, value: object) -> None:
        data = configuration()
        data["candidate"]["services"]["prefect-worker"]["volumes"][0][field] = value
        with pytest.raises(ValueError, match="existing read-only file bind"):
            config.verify_forecast_configuration(data)

    @pytest.mark.parametrize("raw", [None, [], {"baseline": []}])
    def test_untyped_input_refuses(self, raw: object) -> None:
        with pytest.raises(ValueError, match="string-keyed mappings"):
            config.verify_forecast_configuration(raw)

    def test_nonempty_pool_values_are_not_accepted_as_metadata(self) -> None:
        data = configuration()
        data["pool"]["env"] = {"SECRET": "private-value"}
        with pytest.raises(ValueError, match="only the approved fields") as failure:
            config.verify_forecast_configuration(data)
        assert "private-value" not in str(failure.value)


class TestComposeRendering:
    def test_real_overlay_round_trip_from_clean_project_environment(
        self, tmp_path: Path
    ) -> None:
        docker = shutil.which("docker")
        if docker is None:
            pytest.skip("Docker Compose CLI unavailable")
        provider = tmp_path / "provider.toml"
        provider.write_text("# synthetic provider fixture\n")
        baseline = configuration()["baseline"]
        for service in baseline["services"].values():
            service["image"] = "busybox:test"
        baseline["services"]["prefect-worker-ingest"]["volumes"][0]["source"] = str(
            provider
        )
        base_file = tmp_path / "compose.json"
        base_file.write_text(json.dumps(baseline))
        env_file = tmp_path / ".env"
        env_file.write_text(
            "NHS_FORECAST_JOB_OVERLAYS=/app/weather.toml,/app/model.toml,"
            + config.PROVIDER_TARGET
            + "\nNHS_PROVIDER_CONFIG_SOURCE="
            + str(provider)
            + "\n"
        )
        common = [
            docker,
            "compose",
            "--project-directory",
            str(tmp_path),
            "--env-file",
            str(env_file),
            "-f",
            str(base_file),
        ]
        clean_env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}
        # Keep CLI/plugin discovery, but no application interpolation variables.
        if "DOCKER_CONFIG" in os.environ:
            clean_env["DOCKER_CONFIG"] = os.environ["DOCKER_CONFIG"]
        before = subprocess.run(
            [*common, "config", "--format", "json"],
            capture_output=True,
            text=True,
            env=clean_env,
            check=True,
        )
        after = subprocess.run(
            [
                *common,
                "-f",
                str(ROOT / "docker-compose.nhs-forecast.yml"),
                "config",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            env=clean_env,
            check=True,
        )
        data = configuration()
        data.update(
            baseline=json.loads(before.stdout),
            candidate=json.loads(after.stdout),
            provider_source=str(provider),
        )
        assert (
            config.verify_forecast_configuration(data)["configuration_matches"] is True
        )
        env_file.write_text("")
        missing = subprocess.run(
            [
                *common,
                "-f",
                str(ROOT / "docker-compose.nhs-forecast.yml"),
                "config",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            env=clean_env,
            check=False,
        )
        assert missing.returncode != 0
        assert "approved provider file required" in missing.stderr
