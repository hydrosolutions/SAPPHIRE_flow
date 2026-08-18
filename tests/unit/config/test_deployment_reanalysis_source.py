"""Plan 072 T3 / Plan 115b4 §5D / Plan 183 T4 — acceptance tests for the
``reanalysis_source`` flag.

``DeploymentConfig.reanalysis_source: Literal["single", "hybrid", "era5_land"]
= "hybrid"``. Plan 115b4 §5D (Release A, the last step, only after §5A's
parameter-drop fix lands) flips the default from ``"single"`` to
``"hybrid"`` — the "double-dark" regression means ``"single"`` can no longer
read MeteoSwiss's per-product source tags via a station's single
``nwp_source`` binding. ``"single"`` remains selectable (opt-out) for any
station/deployment that needs it. Plan 183 T4 adds ``"era5_land"`` — the v1
(Nepal) ERA5-Land injection point, read via the same generic
``PerSourceStoreReader`` every other single-source mode uses. Any other
value is rejected at the Pydantic boundary.
"""

from __future__ import annotations

from datetime import date

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

    def test_accepts_era5_land(self) -> None:
        cfg = DeploymentConfig(
            max_retention_days=_RETENTION, reanalysis_source="era5_land"
        )

        assert cfg.reanalysis_source == "era5_land"

    def test_rejects_invalid_value(self) -> None:
        with pytest.raises(ValidationError):
            DeploymentConfig(max_retention_days=_RETENTION, reanalysis_source="multi")


class TestClimatologyWindow:
    """Owner decision 2026-08-18: T3's climatology window is the FULL
    ERA5-Land record, and a deployment whose reference statics were computed
    over a different window overrides it per organisation. Comparing a
    recomputation over one window against indices published for another is
    not a parity test — with a 5% tolerance it measures the offset between
    the two windows and calls it agreement."""

    def test_default_is_none_meaning_the_full_era5_land_record(self) -> None:
        cfg = DeploymentConfig(max_retention_days=_RETENTION)
        assert cfg.climatology_window is None

    def test_explicit_window_is_accepted(self) -> None:
        cfg = DeploymentConfig(
            max_retention_days=_RETENTION,
            climatology_window=(date(1991, 1, 1), date(2020, 12, 31)),
        )
        assert cfg.climatology_window == (date(1991, 1, 1), date(2020, 12, 31))

    def test_inverted_window_is_rejected_at_the_boundary(self) -> None:
        with pytest.raises(ValidationError, match="must precede end"):
            DeploymentConfig(
                max_retention_days=_RETENTION,
                climatology_window=(date(2020, 12, 31), date(1991, 1, 1)),
            )

    def test_equal_bounds_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must precede end"):
            DeploymentConfig(
                max_retention_days=_RETENTION,
                climatology_window=(date(2000, 1, 1), date(2000, 1, 1)),
            )
