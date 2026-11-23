from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest

from sapphire_flow.cli.fine_tune import submit_fine_tuning
from sapphire_flow.config.fine_tuning import parse_fine_tuning
from sapphire_flow.exceptions import ConfigurationError
from tests.unit.config.test_fine_tuning import request_document

if TYPE_CHECKING:
    from prefect.client.orchestration import PrefectClient


class FakeClient:
    def __init__(self) -> None:
        self.flow_name = "train-models"
        self.schema = {"properties": {"fine_tuning": {}}}
        self.runs: dict[str, Any] = {}

    async def read_deployment_by_name(self, name: str) -> Any:
        return SimpleNamespace(
            id=uuid4(), flow_id=uuid4(), parameter_openapi_schema=self.schema
        )

    async def read_flow(self, flow_id: object) -> Any:
        return SimpleNamespace(name=self.flow_name)

    async def create_flow_run_from_deployment(
        self, deployment_id: object, *, parameters: dict, idempotency_key: str
    ) -> Any:
        if idempotency_key not in self.runs:
            self.runs[idempotency_key] = SimpleNamespace(
                id=uuid4(), parameters=parameters
            )
        return self.runs[idempotency_key]


class TestFineTuningSubmission:
    @pytest.mark.asyncio
    async def test_repeat_submission_returns_same_run(self) -> None:
        client = FakeClient()
        request = parse_fine_tuning(request_document())
        first = await submit_fine_tuning(request, cast("PrefectClient", client))
        second = await submit_fine_tuning(request, cast("PrefectClient", client))
        assert first == second
        assert len(client.runs) == 1

    @pytest.mark.asyncio
    async def test_rejects_reused_id_with_changed_configuration(self) -> None:
        client = FakeClient()
        document = request_document()
        await submit_fine_tuning(
            parse_fine_tuning(document), cast("PrefectClient", client)
        )
        document["training"]["seed"] = 43
        with pytest.raises(ConfigurationError, match="different configuration"):
            await submit_fine_tuning(
                parse_fine_tuning(document), cast("PrefectClient", client)
            )
        assert len(client.runs) == 1

    @pytest.mark.asyncio
    async def test_refuses_old_deployment_before_submission(self) -> None:
        client = FakeClient()
        client.schema = {}
        with pytest.raises(ConfigurationError, match="does not support"):
            await submit_fine_tuning(
                parse_fine_tuning(request_document()), cast("PrefectClient", client)
            )
        assert not client.runs

    @pytest.mark.asyncio
    async def test_refuses_unrelated_flow(self) -> None:
        client = FakeClient()
        client.flow_name = "forecast-cycle"
        with pytest.raises(ConfigurationError, match="train-models deployment"):
            await submit_fine_tuning(
                parse_fine_tuning(request_document()), cast("PrefectClient", client)
            )
        assert not client.runs
