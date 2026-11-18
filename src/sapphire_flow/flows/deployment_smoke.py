from __future__ import annotations

import json
import random

from prefect import flow
from prefect.context import FlowRunContext, get_run_context

from sapphire_flow.adapters.deployment_smoke_weather import (
    DeploymentSmokeWeatherAdapter,
)
from sapphire_flow.config.deployment import load_config
from sapphire_flow.config.forecast_qc_rules import load_forecast_qc_rules
from sapphire_flow.flows.run_forecast_cycle import (
    run_forecast_cycle_flow,
)
from sapphire_flow.services.deployment_smoke import (
    CYCLE,
    MODEL_ID,
    RESOURCE_DIR,
    RESULT,
    fixture_model,
    read_setup,
    require_smoke_environment,
    verify_resources,
)
from sapphire_flow.types.enums import ForecastDataUse


@flow(
    name="deployment-smoke",
    flow_run_name="deployment-smoke-{run_id}",
    persist_result=False,
)
def deployment_smoke_flow(run_id: str) -> dict[str, object]:
    if require_smoke_environment("worker") != run_id:
        raise ValueError("smoke worker run identity mismatch")
    setup = read_setup(run_id)
    verify_resources()
    if RESULT.exists():
        raise ValueError("smoke cycle has already produced evidence")
    state = run_forecast_cycle_flow(
        adapter=DeploymentSmokeWeatherAdapter(
            (RESOURCE_DIR / "weather.csv").read_bytes(), cycle_time=CYCLE
        ),
        models={MODEL_ID: fixture_model()},
        config=load_config(),
        qc_rules=load_forecast_qc_rules(),
        clock=lambda: CYCLE,
        rng=random.Random(540),
        cycle_time=CYCLE.isoformat(),
        data_use=ForecastDataUse.STANDARD,
        return_state=True,
    )
    result = state.result()
    if (
        result.stations_attempted,
        result.stations_succeeded,
        result.stations_failed,
        result.forecasts_stored,
    ) != (1, 1, 0, 1):
        raise ValueError("smoke cycle did not deliver exactly one forecast")
    if result.health.value != "degraded" or result.errors or result.alerts_checked:
        raise ValueError("smoke cycle health differs from the diagnostic contract")
    context = get_run_context()
    if not isinstance(context, FlowRunContext) or context.flow_run is None:
        raise ValueError("smoke requires a real Prefect flow context")
    evidence: dict[str, object] = {
        "run_id": run_id,
        "station_id": str(setup.station_id),
        "parent_flow_run_id": str(context.flow_run.id),
        "child_flow_run_id": str(state.state_details.flow_run_id),
        "health": result.health.value,
        "stations_attempted": 1,
        "stations_succeeded": 1,
        "stations_failed": 0,
        "forecasts_stored": 1,
        "cycle_duration_ms": result.duration_ms,
    }
    RESULT.write_text(json.dumps(evidence) + "\n")
    return evidence
