"""Plan 072 T3 — LOCKED acceptance tests for the ``reanalysis_source`` flag.

``DeploymentConfig.reanalysis_source: Literal["single", "hybrid"] = "single"``.
Default preserves v0a single-source behaviour; ``"hybrid"`` opts in; any other
value is rejected at the Pydantic boundary.

REDs on the current tree:
- ``test_default_is_single`` raises ``AttributeError`` (field absent).
- ``test_rejects_invalid_value`` does NOT raise (extra key ignored today).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sapphire_flow.config.deployment import DeploymentConfig

# max_retention_days must exceed forecast_hot_days (default 548) per the
# model-level retention validator.
_RETENTION = 600


class TestReanalysisSourceFlag:
    def test_default_is_single(self) -> None:
        cfg = DeploymentConfig(max_retention_days=_RETENTION)

        assert cfg.reanalysis_source == "single"

    def test_accepts_hybrid(self) -> None:
        cfg = DeploymentConfig(
            max_retention_days=_RETENTION, reanalysis_source="hybrid"
        )

        assert cfg.reanalysis_source == "hybrid"

    def test_rejects_invalid_value(self) -> None:
        with pytest.raises(ValidationError):
            DeploymentConfig(max_retention_days=_RETENTION, reanalysis_source="multi")
