from __future__ import annotations

from pathlib import Path

import pytest

from sapphire_flow.cli import onboard_nepal
from sapphire_flow.config.deployment_identity import load_deployment_identity_config
from sapphire_flow.config.recap_gateway import load_recap_gateway_config


class TestOnboardNepal:
    def test_operator_overlays_scope_identity_and_configure_gateway(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_RECAP_BASE_URL", "https://gateway.example.org")
        monkeypatch.setenv(
            "SAPPHIRE_CONFIG_OVERLAY",
            "config/overlays/nepal-history.toml,config/overlays/chwrr-import.toml",
        )
        identity = load_deployment_identity_config(Path("config.toml"))
        assert identity.writable_tenants == frozenset({"chwrr"})
        assert not identity.global_admin
        assert (
            load_recap_gateway_config(Path("config.toml")).base_url
            == "https://gateway.example.org"
        )

    def test_wrong_identity_fails_before_database_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        def fail_if_connected() -> None:
            pytest.fail("wrong tenant must be rejected before database access")

        monkeypatch.setattr(onboard_nepal, "create_engine_from_env", fail_if_connected)
        assert (
            onboard_nepal.main(
                [
                    "history",
                    "--config",
                    "config.toml",
                    "--tenant",
                    "chwrr",
                    "--confirm-t8",
                    "--start",
                    "2020-01-01T00:00:00Z",
                    "--end",
                    "2021-01-01T00:00:00Z",
                ]
            )
            == 1
        )

    def test_basin_import_requires_snow_and_join_acknowledgments(self) -> None:
        with pytest.raises(SystemExit, match="2"):
            onboard_nepal.parser().parse_args(
                [
                    "basins",
                    "--config",
                    "config.toml",
                    "--tenant",
                    "chwrr",
                    "--confirm-t8",
                    "--package-dir",
                    "accepted-package",
                ]
            )
