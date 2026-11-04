from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.encoders import jsonable_encoder
from prefect import flow
from prefect.context import FlowRunContext, get_run_context
from prefect.exceptions import PrefectHTTPStatusError
from prefect.settings import get_current_settings

from tests.conftest import make_raw_historical_forcing
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_stores import (
    FakeHistoricalForcingStore,
    FakeObservationStore,
    FakeWeatherForecastStore,
)


class TestPrefectFakeParameterMetadata:
    @pytest.mark.parametrize(
        "dependency",
        [
            FakeObservationStore(),
            FakeHistoricalForcingStore(),
            FakeWeatherForecastStore(),
            FakeWeatherReanalysisSource(),
        ],
    )
    def test_only_typed_fakes_receive_markers(self, dependency: object) -> None:
        @flow
        def example() -> None:
            pass

        ordinary = {"obs_store": {"_observations": [1, 2]}, "cycle_time": "2026-01-01"}
        assert example.serialize_parameters(
            {"arbitrary_name": dependency, **ordinary}
        ) == {"arbitrary_name": f"<{type(dependency).__name__}>", **ordinary}

    def test_real_flow_keeps_all_fake_data_and_persists_small_metadata(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        records = [
            make_raw_historical_forcing(
                valid_time=start + timedelta(minutes=i), value=float(i)
            )
            for i in range(3000)
        ]
        original = FakeWeatherReanalysisSource(records)
        assert len(json.dumps(jsonable_encoder(original)).encode()) > 524_288

        @flow
        def inspect_data(dependency: object, obs_store: str) -> dict[str, object]:
            assert dependency is original
            assert isinstance(dependency, FakeWeatherReanalysisSource)
            assert dependency.records() == records
            assert obs_store == "ordinary-control"
            context = get_run_context()
            assert isinstance(context, FlowRunContext)
            return context.flow_run.parameters

        metadata = inspect_data(original, "ordinary-control")
        assert metadata == {
            "dependency": "<FakeWeatherReanalysisSource>",
            "obs_store": "ordinary-control",
        }
        assert len(json.dumps(metadata).encode()) < 524_288

    def test_real_server_rejects_oversized_nonfake_before_flow_body(self) -> None:
        assert get_current_settings().server.api.max_parameter_size == 524_288
        called: list[str] = []

        @flow
        def ordinary(obs_store: str) -> None:
            called.append(obs_store)

        with pytest.raises(
            PrefectHTTPStatusError, match="Flow run parameters must be less"
        ) as error:
            ordinary("x" * 524_289)

        assert error.value.response.status_code == 422
        assert called == []
