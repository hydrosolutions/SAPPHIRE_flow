"""Plan 511 T2: the merged configuration of the Nepal cloud host.

Loads the base config plus ``nepal-cloud.toml`` the way the services do and
asserts the effective values — an empty or missing overlay must fail these,
because it would inherit the Swiss ``writable_tenants``, tenant, timezone,
basin list and NWP adapter."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from sapphire_flow.cli.import_dhm_delivery import DELIVERY_TENANT_NAME
from sapphire_flow.config._overlay import load_merged_toml
from sapphire_flow.config.deployment_identity import load_deployment_identity_config

if TYPE_CHECKING:
    import pytest


def _root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "config.toml").is_file():
            return parent
    raise FileNotFoundError("config.toml not found above test file")


def _merged(*overlays: str) -> dict[str, Any]:
    root = _root()
    return cast(
        "dict[str, Any]",
        load_merged_toml(root / "config.toml", [root / o for o in overlays]),
    )


NEPAL = "config/overlays/nepal-cloud.toml"


class TestNepalCloudMergedConfig:
    def test_only_the_nepal_tenant_is_writable(self) -> None:
        assert _merged(NEPAL)["deployment"]["writable_tenants"] == ["chwrr"]

    def test_the_only_declared_tenant_is_chwrr_with_the_delivery_name(self) -> None:
        assert _merged(NEPAL)["tenants"] == {"chwrr": {"name": DELIVERY_TENANT_NAME}}

    def test_swiss_adapters_that_the_overlay_controls_are_inactive(self) -> None:
        adapters = _merged(NEPAL)["adapters"]
        assert adapters["weather_forecast"]["enabled"] is False
        assert "bafu_forecast" not in adapters
        assert "bafu_observation" not in adapters

    def test_onboarding_is_nepal_shaped_not_the_swiss_basin_list(self) -> None:
        onboarding = _merged(NEPAL)["onboarding"]
        assert onboarding["basin_ids"] == []
        assert onboarding["tenant"] == "chwrr"
        assert onboarding["data_source"] != "camels-ch"

    def test_display_timezone_is_nepal(self) -> None:
        assert _merged(NEPAL)["default_display_timezone"] == "Asia/Kathmandu"

    def test_deployment_identity_loader_sees_only_chwrr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _root()
        monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", str(root / NEPAL))
        identity = load_deployment_identity_config(root / "config.toml")
        assert identity.writable_tenants == frozenset({"chwrr"})
        assert identity.global_admin is False


class TestTheChecksDiscriminate:
    """Without the Nepal overlay the same keys hold the Swiss values, so the
    tests above cannot pass on a base-only (or empty-overlay) configuration."""

    def test_base_config_is_swiss(self) -> None:
        base = _merged()
        assert base["deployment"]["writable_tenants"] == ["sapphire"]
        assert "tenants" not in base
        assert base["adapters"]["weather_forecast"]["enabled"] is True
        assert base["default_display_timezone"] == "Europe/Zurich"
        assert base["onboarding"]["basin_ids"]

    def test_the_macmini_overlay_declares_the_same_tenant_the_same_way(self) -> None:
        assert (
            _merged("config/overlays/mac-mini.toml")["tenants"]
            == _merged(NEPAL)["tenants"]
        )
