from __future__ import annotations

import random
from datetime import UTC, datetime
from uuid import UUID

import pytest

from sapphire_flow.services.flow_regime import compute_flow_regime
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.ids import StationId
from tests.conftest import make_observation

_FIXED_TIME = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_STATION_ID = StationId(UUID(int=1))
_OTHER_STATION_ID = StationId(UUID(int=2))

clock = lambda: _FIXED_TIME  # noqa: E731
uuid_factory = lambda: UUID(int=42)  # noqa: E731


def _make_obs_batch(
    values: list[float],
    station_id: StationId = _STATION_ID,
    parameter: str = "discharge",
) -> list:
    rng = random.Random(0)
    return [
        make_observation(station_id=station_id, parameter=parameter, value=v, rng=rng)
        for v in values
    ]


class TestComputeFlowRegime:
    def test_basic_computation(self) -> None:
        rng = random.Random(99)
        values = [rng.uniform(0, 100) for _ in range(1000)]
        obs = _make_obs_batch(values)

        result = compute_flow_regime(
            obs, _STATION_ID, "discharge", clock, uuid_factory, min_observations=365
        )

        assert result is not None
        assert abs(result.p50 - 50.0) < 5.0
        assert abs(result.p90 - 90.0) < 5.0

    def test_known_distribution(self) -> None:
        values = list(range(1, 101))
        obs = _make_obs_batch([float(v) for v in values])

        result = compute_flow_regime(
            obs, _STATION_ID, "discharge", clock, uuid_factory, min_observations=100
        )

        assert result is not None
        assert result.p50 == pytest.approx(50.5)
        assert result.p90 == pytest.approx(90.1)

    def test_insufficient_data_returns_none(self) -> None:
        obs = _make_obs_batch([float(v) for v in range(1, 101)])

        result = compute_flow_regime(
            obs, _STATION_ID, "discharge", clock, uuid_factory, min_observations=365
        )

        assert result is None

    def test_filters_by_station_and_parameter(self) -> None:
        target = _make_obs_batch(
            [float(v) for v in range(1, 401)],
            station_id=_STATION_ID,
            parameter="discharge",
        )
        other_station = _make_obs_batch(
            [999.0] * 400, station_id=_OTHER_STATION_ID, parameter="discharge"
        )
        other_param = _make_obs_batch(
            [999.0] * 400, station_id=_STATION_ID, parameter="temperature"
        )
        obs = target + other_station + other_param

        result = compute_flow_regime(
            obs, _STATION_ID, "discharge", clock, uuid_factory, min_observations=365
        )

        assert result is not None
        assert result.observation_count == 400
        assert result.p50 != pytest.approx(999.0)

    def test_none_values_excluded(self) -> None:
        rng = random.Random(0)
        valid = [
            make_observation(
                station_id=_STATION_ID, parameter="discharge", value=float(v), rng=rng
            )
            for v in range(1, 401)
        ]
        missing = [
            make_observation(
                station_id=_STATION_ID,
                parameter="discharge",
                qc_status=QcStatus.MISSING,
                rng=rng,
            )
            for _ in range(50)
        ]
        obs = valid + missing

        result = compute_flow_regime(
            obs, _STATION_ID, "discharge", clock, uuid_factory, min_observations=365
        )

        assert result is not None
        assert result.observation_count == 400

    def test_version_and_metadata(self) -> None:
        obs = _make_obs_batch([float(v) for v in range(1, 401)])

        result = compute_flow_regime(
            obs,
            _STATION_ID,
            "discharge",
            clock,
            uuid_factory,
            version=3,
            min_observations=365,
        )

        assert result is not None
        assert result.version == 3
        assert result.computed_at == _FIXED_TIME
        assert result.created_at == _FIXED_TIME
        assert result.id == UUID(int=42)
        assert result.station_id == _STATION_ID
        assert result.observation_count == 400
