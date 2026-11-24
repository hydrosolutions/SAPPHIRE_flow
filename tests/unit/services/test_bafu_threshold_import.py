from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.bafu_threshold_import import (
    import_one,
    make_onboarding_threshold_importer,
)
from sapphire_flow.services.threshold_import import ThresholdImportMode
from sapphire_flow.types.datetime import ensure_utc
from tests.conftest import make_deployment_config, make_station_config

NOW = ensure_utc(datetime(2026, 11, 24, tzinfo=UTC))


class TestImportOne:
    def test_enabled_forecast_alerts_hold_apply(self, tmp_path: Path) -> None:
        station = make_station_config(code="2150", network="bafu")
        with (
            httpx.Client() as client,
            pytest.raises(ConfigurationError, match="forecast alerts"),
        ):
            import_one(
                station=station,
                tenant_id=station.tenant_id,
                config=make_deployment_config(enable_forecast_alerts=True),
                engine=sa.create_engine("sqlite://"),
                client=client,
                mode=ThresholdImportMode.APPLY,
                report_dir=tmp_path,
                clock=lambda: NOW,
            )


class TestOnboardingThresholdImporter:
    @pytest.mark.parametrize(
        "error",
        [
            ValueError("parse"),
            httpx.ConnectError("source"),
            SQLAlchemyError("db"),
            OSError("disk"),
        ],
    )
    def test_failure_does_not_stop_onboarding(
        self, tmp_path: Path, error: Exception
    ) -> None:
        station = make_station_config(code="2150", network="bafu")
        callback = make_onboarding_threshold_importer(
            engine=sa.create_engine("sqlite://"),
            config=make_deployment_config(),
            tenant_id=station.tenant_id,
            report_dir=tmp_path,
            clock=lambda: NOW,
            operator="test",
        )
        with patch(
            "sapphire_flow.services.bafu_threshold_import.import_one", side_effect=error
        ) as importer:
            callback(station)
        importer.assert_called_once()

    def test_other_network_does_not_fetch(self, tmp_path: Path) -> None:
        station = make_station_config(code="2150", network="dhm")
        callback = make_onboarding_threshold_importer(
            engine=sa.create_engine("sqlite://"),
            config=make_deployment_config(),
            tenant_id=station.tenant_id,
            report_dir=tmp_path,
            clock=lambda: NOW,
            operator="test",
        )
        with patch(
            "sapphire_flow.services.bafu_threshold_import.import_one"
        ) as importer:
            callback(station)
        importer.assert_not_called()
