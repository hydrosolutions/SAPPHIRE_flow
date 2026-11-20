from pathlib import Path
from types import SimpleNamespace

import pytest

from sapphire_flow.adapters.recap_gateway import (
    RecapGatewayForecastAdapter,
    RecapGatewayReanalysisAdapter,
)
from sapphire_flow.cli.onboard_nepal import build_history_adapter
from sapphire_flow.flows.ingest_recap_reanalysis import _build_production_snow_adapter
from sapphire_flow.flows.run_forecast_cycle import _build_recap_forecast_adapter
from sapphire_flow.types.recap_intervals import GatewayIntervalContract


def test_all_production_roots_preserve_normalization_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import recap_client

    from sapphire_flow.config import recap_gateway

    path = tmp_path / "config.toml"
    path.write_text(
        '[adapters.recap_gateway]\nbase_url="https://recap.example.org"\n'
        "timeout_s=120\nverify_tls=true\nstaleness_threshold_hours=12\n"
        'hru_metadata_source="manual_gpkg_upload"\nmax_retries=5\n'
        'normalized_interval_contract="normalized_start_v1"\nnormalization_revision="test-normalized-v1"\n'
    )
    monkeypatch.setenv("SAPPHIRE_CONFIG", str(path))
    monkeypatch.setattr(recap_gateway, "load_recap_api_key", lambda: "test-fixture")
    monkeypatch.setattr(recap_client, "RecapClient", lambda config: SimpleNamespace())
    injected, _ = _build_recap_forecast_adapter(
        config_path=str(path),
        gateway_polygon_store=object(),
        recap_client=SimpleNamespace(),
    )
    production, _ = _build_recap_forecast_adapter(
        config_path=str(path), gateway_polygon_store=object(), recap_client=None
    )
    history = build_history_adapter(None, path)
    snow = _build_production_snow_adapter(object())
    assert isinstance(injected, RecapGatewayForecastAdapter)
    assert isinstance(production, RecapGatewayForecastAdapter)
    assert isinstance(snow, RecapGatewayReanalysisAdapter)
    for adapter in (injected, production, history, snow):
        assert (
            adapter.interval_qualification.contract
            is GatewayIntervalContract.NORMALIZED_START_V1
        )
        assert adapter.interval_qualification.revision == "test-normalized-v1"
    assert (
        injected.with_resolver(SimpleNamespace()).interval_qualification
        == injected.interval_qualification
    )
