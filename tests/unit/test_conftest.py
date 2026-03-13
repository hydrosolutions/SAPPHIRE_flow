from __future__ import annotations

from sapphire_flow.types.alert import Alert
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import EnsembleRepresentation
from sapphire_flow.types.model import ModelArtifactRecord
from sapphire_flow.types.observation import Observation
from sapphire_flow.types.station import StationConfig
from tests.conftest import (
    make_alert,
    make_deployment_config,
    make_forecast_ensemble,
    make_model_artifact_record,
    make_nwp_forecast,
    make_observation,
    make_observations,
    make_station_config,
)


class TestFactories:
    def test_make_station_config(self) -> None:
        sc = make_station_config()
        assert isinstance(sc, StationConfig)
        assert sc.code == "TEST-001"

    def test_make_observation(self) -> None:
        obs = make_observation()
        assert isinstance(obs, Observation)

    def test_make_observations(self) -> None:
        obs_list = make_observations(5)
        assert len(obs_list) == 5
        assert all(isinstance(o, Observation) for o in obs_list)

    def test_make_nwp_forecast(self) -> None:
        result = make_nwp_forecast()
        assert len(result) == 1

    def test_make_forecast_ensemble_members(self) -> None:
        ens = make_forecast_ensemble(n_members=5, n_steps=10)
        assert isinstance(ens, ForecastEnsemble)
        assert ens.representation == EnsembleRepresentation.MEMBERS

    def test_make_forecast_ensemble_quantiles(self) -> None:
        ens = make_forecast_ensemble(
            representation=EnsembleRepresentation.QUANTILES, n_steps=10
        )
        assert isinstance(ens, ForecastEnsemble)
        assert ens.representation == EnsembleRepresentation.QUANTILES

    def test_make_deployment_config(self) -> None:
        cfg = make_deployment_config()
        assert cfg.max_retention_days == 3650

    def test_make_alert(self) -> None:
        a = make_alert()
        assert isinstance(a, Alert)

    def test_make_model_artifact_record(self) -> None:
        r = make_model_artifact_record()
        assert isinstance(r, ModelArtifactRecord)
