"""Synthetic external manifests for isolated compatibility tests."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def incomplete_profile(directory: Path) -> Path:
    profile = directory / "profile.toml"
    profile.write_text((ROOT / "deploy/examples/profile.example.toml").read_text())
    (directory / "application.example.toml").write_text(
        (ROOT / "deploy/examples/application.example.toml").read_text()
    )
    return profile


def recipe_profile(directory: Path, recipe: str = "macmini") -> Path:
    overlay = "mac-mini" if recipe == "macmini" else "nepal-cloud"
    profile = directory / f"{recipe}.toml"
    template = (ROOT / "deploy/examples/profile.example.toml").read_text()
    credentials = template[template.index("[secret_requirements.db_password]") :]
    profile.write_text(
        'schema_version = 1\nname = "private test label"\nstate = "configured"\n'
        f'base_config = "{ROOT}/config.toml"\n'
        f'overlays = ["{ROOT}/config/overlays/{overlay}.toml"]\n'
        f'compose_files = ["{ROOT}/docker-compose.yml", '
        f'"{ROOT}/docker-compose.{recipe}.yml"]\n'
        f'runbook = "{ROOT}/docs/deployment-quickstart.md"\n'
        '[[scopes]]\nkind = "chwrr_import"\n'
        f'when_compose = "{ROOT}/docker-compose.operator.yml"\n'
        f'base_config = "{ROOT}/config.toml"\n'
        f'overlays = ["{ROOT}/config/overlays/chwrr-import.toml"]\n'
        '[[scopes]]\nkind = "forecast_only"\n'
        f'when_compose = "{ROOT}/docker-compose.nhs-forecast.yml"\n'
        'provider_input = "NHS_PROVIDER_CONFIG_SOURCE"\n'
        + credentials
        + '[secret_requirements.sapphire_operator_db_password]\nuse = "runtime"\n'
        'consumers = ["operator", "init"]\n'
        f'when_compose = "{ROOT}/docker-compose.operator.yml"\n'
        '[secret_requirements.sapphire_dg_api_key]\nuse = "runtime"\n'
        'consumers = ["prefect-worker", "prefect-worker-ingest"]\n'
        f'when_compose = "{ROOT}/docker-compose.recap.yml"\n'
    )
    return profile
