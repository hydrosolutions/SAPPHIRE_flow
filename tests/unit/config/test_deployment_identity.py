"""Plan 147 Slice E (G6): the [deployment] write-authority config block."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sapphire_flow.config.deployment_identity import (
    DeploymentIdentityConfig,
    load_deployment_identity_config,
)
from sapphire_flow.exceptions import ConfigurationError

if TYPE_CHECKING:
    from pathlib import Path


class TestDeploymentIdentityConfigValidation:
    def test_global_admin_and_writable_tenants_are_mutually_exclusive(self) -> None:
        with pytest.raises(ConfigurationError, match="mutually exclusive"):
            DeploymentIdentityConfig(
                writable_tenants=frozenset({"sapphire"}), global_admin=True
            )

    def test_neither_declared_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="writable_tenants"):
            DeploymentIdentityConfig(writable_tenants=frozenset(), global_admin=False)

    def test_writable_tenants_alone_is_valid(self) -> None:
        config = DeploymentIdentityConfig(
            writable_tenants=frozenset({"sapphire"}), global_admin=False
        )
        assert config.writable_tenants == frozenset({"sapphire"})

    def test_global_admin_alone_is_valid(self) -> None:
        config = DeploymentIdentityConfig(
            writable_tenants=frozenset(), global_admin=True
        )
        assert config.global_admin is True


class TestLoadDeploymentIdentityConfig:
    def test_reads_writable_tenants_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text('[deployment]\nwritable_tenants = ["sapphire", "dhm"]\n')
        config = load_deployment_identity_config(config_path)
        assert config.writable_tenants == frozenset({"sapphire", "dhm"})
        assert config.global_admin is False
        assert config.operator is None

    def test_reads_global_admin_and_operator(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[deployment]\nglobal_admin = true\noperator = "ops-nepal"\n'
        )
        config = load_deployment_identity_config(config_path)
        assert config.global_admin is True
        assert config.operator == "ops-nepal"

    def test_missing_section_raises_via_post_init(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text("max_retention_days = 600\n")
        with pytest.raises(ConfigurationError, match="writable_tenants"):
            load_deployment_identity_config(config_path)
