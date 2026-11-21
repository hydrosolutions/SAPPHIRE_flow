import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from sapphire_flow.cli import operator_daily_retry as cli
from sapphire_flow.config.daily_evaluation import (
    DailyEvaluationConfig,
    DailyOutputContractConfig,
)
from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.daily_forecast_claim import DailyClaimKey
from sapphire_flow.types.daily_operator_retry import (
    OperatorDailyForecast,
    OperatorRetryRequest,
)
from sapphire_flow.types.enums import EnsembleRepresentation
from sapphire_flow.types.ids import (
    ArtifactId,
    ModelId,
    PrincipalId,
    StationGroupId,
    TenantId,
)


@pytest.fixture
def request_config() -> tuple[OperatorRetryRequest, DeploymentConfig]:
    tenant, group, deployment = uuid4(), uuid4(), uuid4()
    config = DeploymentConfig(
        max_retention_days=600,
        daily_evaluation=DailyEvaluationConfig(
            tenant_id=tenant,
            deployment="configured-deployment",
            prefect_deployment_id=deployment,
            group_ids=(group,),
            output_contracts=(
                DailyOutputContractConfig(
                    parameter="discharge",
                    units="m³/s",
                    representation=EnsembleRepresentation.MEMBERS,
                    first_lead_hours=24,
                    horizon_steps=2,
                    series_keys=(0.0,),
                ),
            ),
        ),
    )
    request = OperatorRetryRequest(
        request_id=uuid4(),
        parent_id=uuid4(),
        key=DailyClaimKey(
            tenant_id=TenantId(tenant),
            deployment="configured-deployment",
            group_id=StationGroupId(group),
            model_id=ModelId("cmal_small"),
            context=OperatorDailyForecast(target_date=date(2026, 11, 20)),
        ),
        artifact_id=ArtifactId(uuid4()),
        artifact_sha256="a" * 64,
        configuration_sha256="b" * 64,
        roster_sha256="c" * 64,
        actor=PrincipalId("operator"),
        reason="repair deployed",
    )
    return request, config


@pytest.mark.parametrize(
    "case", ["normal", "conflicting_parameters", "duplicate_payload"]
)
def test_submission_preserves_parameters_and_binds_request(
    monkeypatch: pytest.MonkeyPatch,
    request_config: tuple[OperatorRetryRequest, DeploymentConfig],
    case: str,
) -> None:
    request, config = request_config
    calls: list[dict[str, Any]] = []
    run_id = uuid4()

    class Client:
        async def read_deployment(self, deployment_id: object) -> SimpleNamespace:
            return SimpleNamespace(
                parameters={
                    "custom_setting": "kept",
                    "cycle_time": "2026-11-20"
                    if case == "conflicting_parameters"
                    else None,
                }
            )

        async def create_flow_run_from_deployment(
            self, deployment_id: object, **kwargs: object
        ) -> SimpleNamespace:
            calls.append(kwargs)
            parameters = kwargs["parameters"]
            return SimpleNamespace(
                id=run_id, parameters=parameters if case != "duplicate_payload" else {}
            )

    @asynccontextmanager
    async def client() -> AsyncIterator[Client]:
        yield Client()

    monkeypatch.setattr(cli, "get_client", client)
    if case == "conflicting_parameters":
        with pytest.raises(ConfigurationError, match="parameters conflict"):
            asyncio.run(cli.submit_retry(request, config))
        assert calls == []
    elif case == "duplicate_payload":
        with pytest.raises(ConfigurationError, match="different payload"):
            asyncio.run(cli.submit_retry(request, config))
    else:
        assert asyncio.run(cli.submit_retry(request, config)) == run_id
        assert calls[0]["parameters"]["custom_setting"] == "kept"
        assert calls[0]["parameters"]["operator_daily_retry"] == request.document()
        assert (
            calls[0]["idempotency_key"] == f"operator-daily-retry:{request.request_id}"
        )
