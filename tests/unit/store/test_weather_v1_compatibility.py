"""Digests measured against main d777d8b2 before adding period evidence."""

import hashlib
import json
import random
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import polars as pl

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.config.operational_weather import (
    OperationalPastWeatherRoute,
    OperationalWeatherConfig,
)
from sapphire_flow.services.forecast_evidence import (
    capture_station_evidence,
    restore_snapshot,
)
from sapphire_flow.store.operational_weather_codec import (
    completion_digest,
    make_past_snapshot,
    window_bytes,
)
from sapphire_flow.types.domain import ForecastQcRuleSet
from sapphire_flow.types.forecast_evidence import StationSourceEvidence
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.operational_weather import (
    PastWeatherRequirement,
    PastWeatherUse,
    WeatherCacheAttempt,
    WeatherCacheCompletion,
    WeatherCacheKey,
    WeatherFreshness,
    WeatherLegOutcome,
    WeatherLegState,
    WeatherUnused,
    WeatherUse,
)
from tests.fakes.operational_weather import FINGERPRINT, ISSUE, make_operational_window
from tests.unit.adapters.test_forecast_interface_adapter_predict import (
    _station_model_inputs,
)


def v1_digests() -> tuple[str, str, str, str]:
    sid = StationId(UUID(int=1))
    window = make_operational_window(sid)
    snapshot = make_past_snapshot(
        window=window, retrieved_at=ISSUE, snapshot_id=UUID(int=3), max_bytes=2**20
    )
    outcome = WeatherLegOutcome(state=WeatherLegState.SUCCESS)
    completion = WeatherCacheCompletion(
        finished_at=ISSUE,
        past_outcome=outcome,
        past_snapshot=snapshot,
        future_attempts=(),
    )
    use = WeatherUse(
        station_id=sid,
        issue_time=ISSUE,
        used_at=ISSUE,
        attempt=WeatherCacheAttempt(
            key=WeatherCacheKey(station_id=sid, binding_fingerprint=FINGERPRINT),
            generation=1,
            id=UUID(int=2),
            issue_time=ISSUE,
            started_at=ISSUE,
        ),
        refresh_finished_at=ISSUE,
        past=PastWeatherUse(
            freshness=WeatherFreshness.CURRENT,
            refresh_outcome=outcome,
            retrieved_at=ISSUE,
            requirement=PastWeatherRequirement(
                start=window.start,
                end=ISSUE,
                parameters=("precipitation", "temperature"),
            ),
            snapshot_id=snapshot.id,
            snapshot_sha256=snapshot.payload_sha256,
            snapshot_issue_time=ISSUE,
            binding_fingerprint=FINGERPRINT,
            days=window.days,
        ),
        future=WeatherUnused(),
    )
    template = _station_model_inputs()
    frame = pl.DataFrame(
        {"timestamp": [window.start], "precipitation": [8.0], "temperature": [10.0]}
    )
    inputs = replace(
        template,
        station_id=sid,
        issue_time=ISSUE,
        time_step=timedelta(days=1),
        forecast_horizon_steps=1,
        data=replace(
            template.data,
            past_targets=pl.DataFrame(),
            past_dynamic=frame,
            future_dynamic=pl.DataFrame(),
            static=None,
        ),
        source_evidence=StationSourceEvidence(weather_use=use),
    )
    evidence = capture_station_evidence(
        inputs=inputs,
        model=object(),
        model_id=ModelId("fixture"),
        artifact_bytes=b"artifact",
        prior_state=None,
        rng_state=random.Random(1).getstate(),
        config=DeploymentConfig(
            available_nwp_parameters=frozenset({"precipitation"}),
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
    assert evidence.snapshot is not None and evidence.snapshot_sha256 is not None
    source_payload = json.dumps(
        restore_snapshot(evidence.snapshot)["source_records"],
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return (
        hashlib.sha256(window_bytes(window, max_bytes=2**20)).hexdigest(),
        completion_digest(completion, max_past_bytes=2**20),
        hashlib.sha256(source_payload).hexdigest(),
        evidence.snapshot_sha256,
    )


def test_retained_v1_window_completion_and_captured_source_bytes_stay_identical() -> (
    None
):
    assert v1_digests() == (
        "157310a6a6654d3b822f7d30422101c160e91f6672d9cdcc3f2f4f6d5f8a18e6",
        "dc2fda0352c8c00a3e2b6af88adaf930779863aca402cb6b49143fc5bf0fce6d",
        "8d31bdbed445fadfe8ffd69c2d4dbc8f9c823b1800afbb63d407cb04e1e96314",
        "4d361fe00d576a8c81ad9ca1430c2d744f578590e68b90c4dabbbad3cf5cf783",
    )
