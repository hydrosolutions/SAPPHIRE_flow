"""Plan 072 T3 / Plan 115b4 §5D — acceptance tests for the ``reanalysis_source``
flag.

``DeploymentConfig.reanalysis_source: Literal["single", "hybrid"] = "hybrid"``.
Plan 115b4 §5D (Release A, the last step, only after §5A's parameter-drop fix
lands) flips the default from ``"single"`` to ``"hybrid"`` — the "double-dark"
regression means ``"single"`` can no longer read MeteoSwiss's per-product
source tags via a station's single ``nwp_source`` binding. ``"single"``
remains selectable (opt-out) for any station/deployment that needs it; any
other value is rejected at the Pydantic boundary.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sapphire_flow.config.deployment import DeploymentConfig

# max_retention_days must exceed forecast_hot_days (default 548) per the
# model-level retention validator.
_RETENTION = 600


class TestReanalysisSourceFlag:
    def test_default_is_hybrid(self) -> None:
        cfg = DeploymentConfig(max_retention_days=_RETENTION)

        assert cfg.reanalysis_source == "hybrid"

    def test_accepts_single_as_explicit_opt_out(self) -> None:
        cfg = DeploymentConfig(
            max_retention_days=_RETENTION, reanalysis_source="single"
        )

        assert cfg.reanalysis_source == "single"

    def test_rejects_invalid_value(self) -> None:
        with pytest.raises(ValidationError):
            DeploymentConfig(max_retention_days=_RETENTION, reanalysis_source="multi")
