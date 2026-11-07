from __future__ import annotations

import random
from dataclasses import replace
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.config.operational_weather import (
    OperationalPastWeatherRoute,
    OperationalWeatherConfig,
)
from sapphire_flow.db.metadata import forecasts
from sapphire_flow.exceptions import (
    ForecastRetryConflictError,
    WeatherEvidenceCaptureError,
)
from sapphire_flow.services.forecast_evidence import capture_station_evidence
from sapphire_flow.services.selected_weather_evidence import restore_selected_weather
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.types.domain import ForecastQcRuleSet
from sapphire_flow.types.forecast_evidence import StationSourceEvidence
from sapphire_flow.types.ids import ForecastId
from sapphire_flow.types.operational_weather import WeatherUnused, WeatherUse
from tests.integration.store.test_forecast_store import (
    _make_forecast,
    _seed_artifact,
    _seed_model,
    _seed_station,
    savepoint_factory,
)
from tests.unit.adapters.test_forecast_interface_adapter_predict import (
    _station_model_inputs,
)

if TYPE_CHECKING:
    from sapphire_flow.types.forecast import OperationalForecast


def selected(forecast: OperationalForecast, *, seed: int = 1) -> OperationalForecast:
    use = WeatherUse(
        station_id=forecast.station_id,
        issue_time=forecast.issued_at,
        used_at=forecast.issued_at,
        attempt=None,
        refresh_finished_at=None,
        past=WeatherUnused(),
        future=WeatherUnused(),
    )
    inputs = replace(
        _station_model_inputs(),
        station_id=forecast.station_id,
        issue_time=forecast.issued_at,
        source_evidence=StationSourceEvidence(weather_use=use),
    )
    evidence = capture_station_evidence(
        inputs=inputs,
        model=object(),
        model_id=forecast.model_id,
        artifact_bytes=b"artifact",
        prior_state=None,
        rng_state=random.Random(seed).getstate(),
        config=DeploymentConfig(
            max_retention_days=1000,
            operational_weather=OperationalWeatherConfig(
                route=OperationalPastWeatherRoute.RECAP_COMBINED
            ),
        ),
        qc_rules=ForecastQcRuleSet(version="test", rules=()),
        qc_overrides=[],
        baselines=[],
        water_level_datum_masl=None,
    )
    return replace(forecast, weather_evidence_version=1, evidence=evidence)


@pytest.fixture
def stored_case(
    db_connection: sa.Connection,
) -> tuple[PgForecastStore, OperationalForecast]:
    sid = _seed_station(db_connection)
    mid = _seed_model(db_connection)
    aid = _seed_artifact(db_connection, sid, mid)
    return PgForecastStore(
        db_connection, transaction_factory=savepoint_factory(db_connection)
    ), _make_forecast(sid, mid, aid)


class TestSelectedWeatherPersistence:
    def test_typed_round_trip(
        self, stored_case: tuple[PgForecastStore, OperationalForecast]
    ) -> None:
        store, base = stored_case
        forecast = selected(base)
        store.store_forecast(forecast)
        restored = store.fetch_forecast(forecast.id)
        assert restored is not None and restored.weather_evidence_version == 1
        evidence = store.fetch_evidence(forecast.id)
        assert evidence is not None
        uses = restore_selected_weather(
            evidence, model_id=forecast.model_id, issue_time=forecast.issued_at
        )
        assert uses[forecast.station_id].past == WeatherUnused()

    @pytest.mark.parametrize("was_selected", [False, True])
    def test_marker_only_retry_refused(
        self,
        stored_case: tuple[PgForecastStore, OperationalForecast],
        was_selected: bool,
    ) -> None:
        store, base = stored_case
        stored = selected(base) if was_selected else base
        store.store_forecast(stored)
        retry = base if was_selected else selected(base)
        with pytest.raises(
            ForecastRetryConflictError, match="weather evidence version differs"
        ):
            store.store_forecast(replace(retry, id=ForecastId(uuid4())))
        retained = store.fetch_forecast(stored.id)
        assert (
            retained is not None
            and retained.weather_evidence_version == stored.weather_evidence_version
        )

    def test_identical_uses_original_evidence(
        self, stored_case: tuple[PgForecastStore, OperationalForecast]
    ) -> None:
        store, base = stored_case
        stored = selected(base)
        store.store_forecast(stored)
        original = store.fetch_evidence(stored.id)
        retry = selected(replace(base, id=ForecastId(uuid4())), seed=2)
        assert stored.evidence is not None and retry.evidence is not None
        assert stored.evidence.snapshot_sha256 != retry.evidence.snapshot_sha256
        assert store.store_forecast(retry) == stored.id
        assert store.fetch_evidence(stored.id) == original

    def test_invalid_selected_retry_cannot_use_identical_shortcut(
        self, stored_case: tuple[PgForecastStore, OperationalForecast]
    ) -> None:
        store, base = stored_case
        stored = selected(base)
        store.store_forecast(stored)
        with pytest.raises(WeatherEvidenceCaptureError, match="requires matching"):
            store.store_forecast(replace(stored, evidence=None))

    @pytest.mark.parametrize("was_selected", [False, True])
    def test_database_marker_is_immutable(
        self,
        db_connection: sa.Connection,
        stored_case: tuple[PgForecastStore, OperationalForecast],
        was_selected: bool,
    ) -> None:
        store, base = stored_case
        stored = selected(base) if was_selected else base
        store.store_forecast(stored)
        with (
            pytest.raises(sa.exc.DBAPIError, match="is immutable"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(
                sa.update(forecasts)
                .where(forecasts.c.id == stored.id)
                .values(weather_evidence_version=None if was_selected else 1)
            )


class TestCombinedWeatherPersistence:
    @pytest.mark.parametrize("session_timezone", ["UTC", "Asia/Kathmandu"])
    def test_resumed_contributor_uses_retained_weather_evidence(
        self,
        db_connection: sa.Connection,
        stored_case: tuple[PgForecastStore, OperationalForecast],
        session_timezone: Literal["UTC", "Asia/Kathmandu"],
    ) -> None:
        from sapphire_flow.flows.run_forecast_cycle import _persist_forecast
        from sapphire_flow.services.forecast_evidence import capture_combined_evidence
        from sapphire_flow.types.operational_weather import DerivedWeatherUse

        original_zone = db_connection.execute(sa.text("SHOW TIME ZONE")).scalar_one()
        try:
            db_connection.execute(
                sa.text("SELECT set_config('TimeZone', :zone, true)"),
                {"zone": session_timezone},
            )
            store, base = stored_case
            original = selected(base)
            store.store_forecast(original)
            resumed = _persist_forecast(
                store, selected(replace(base, id=ForecastId(uuid4())), seed=2)
            )
            assert original.evidence is not None and resumed.evidence is not None
            assert resumed.evidence.snapshot_sha256 == original.evidence.snapshot_sha256
            combined_id = _seed_model(db_connection, "weather_combined")
            evidence = capture_combined_evidence(
                model_id=combined_id,
                strategy="pool",
                contributors=(resumed,),
                weights=None,
                qc_rules=ForecastQcRuleSet(version="test", rules=()),
                qc_overrides=[],
                baselines=[],
                water_level_datum_masl=None,
            )
            combined = replace(
                base,
                id=ForecastId(uuid4()),
                model_id=combined_id,
                model_artifact_id=None,
                combination_strategy="pool",
                source_model_ids=[base.model_id],
                weather_evidence_version=1,
                evidence=evidence,
            )
            store.store_forecast(combined)
            persisted = store.fetch_evidence(combined.id)
            assert persisted is not None
            restored = restore_selected_weather(
                persisted, model_id=combined_id, issue_time=base.issued_at
            )[base.station_id]
            assert isinstance(restored, DerivedWeatherUse)
            assert (
                restored.contributors[0].evidence_sha256
                == original.evidence.snapshot_sha256
            )
        finally:
            db_connection.execute(
                sa.text("SELECT set_config('TimeZone', :zone, true)"),
                {"zone": original_zone},
            )

    def test_unpersisted_recapture_cannot_replace_contributor_facts(
        self,
        db_connection: sa.Connection,
        stored_case: tuple[PgForecastStore, OperationalForecast],
    ) -> None:
        from sapphire_flow.services.forecast_evidence import capture_combined_evidence

        store, base = stored_case
        store.store_forecast(selected(base))
        recaptured = selected(base, seed=2)
        combined_id = _seed_model(db_connection, "weather_combined")
        evidence = capture_combined_evidence(
            model_id=combined_id,
            strategy="pool",
            contributors=(recaptured,),
            weights=None,
            qc_rules=ForecastQcRuleSet(version="test", rules=()),
            qc_overrides=[],
            baselines=[],
            water_level_datum_masl=None,
        )
        combined = replace(
            base,
            id=ForecastId(uuid4()),
            model_id=combined_id,
            model_artifact_id=None,
            combination_strategy="pool",
            source_model_ids=[base.model_id],
            weather_evidence_version=1,
            evidence=evidence,
        )
        with pytest.raises(
            WeatherEvidenceCaptureError, match="contributor evidence mismatch"
        ):
            store.store_forecast(combined)
        assert store.fetch_forecast(combined.id) is None
