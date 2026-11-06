from __future__ import annotations

import json
import random
from dataclasses import asdict, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from prefect.testing.utilities import prefect_test_harness

from sapphire_flow.config.deployment import load_config
from sapphire_flow.config.dhm import DhmConfig
from sapphire_flow.config.provisional_rating import ProvisionalRatingConfig
from sapphire_flow.config.river_stations import load_river_station_config
from sapphire_flow.db.metadata import models as model_table
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows import _db
from sapphire_flow.flows import run_forecast_cycle as cycle
from sapphire_flow.services.rating_inputs import NativeRatingInputs
from sapphire_flow.services.run_group_forecast import GroupInputAssembly
from sapphire_flow.store.rating_derived_store import PgRatingDerivedStore
from sapphire_flow.store.rating_reference_store import parse_reference_evidence
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.enums import (
    ForecastDataUse,
    ModelAssignmentStatus,
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import (
    ModelId,
    RatingReferenceProofId,
    StationGroupId,
    StationId,
)
from sapphire_flow.types.rating_reference import CurveSnapshot, canonical_content
from sapphire_flow.types.station import (
    GroupModelAssignment,
    StationGroup,
    StationWeatherSource,
)
from tests.conftest import DEFAULT_TENANT_ID, make_station_config
from tests.unit.flows.test_run_forecast_cycle import (
    _empty_qc_rules,
    _SmallFakeGroupModel,
)
from tests.unit.services.test_rating_inputs import NOW, samples

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(scope="module")
def local_flow_server() -> Any:
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("PREFECT_SERVER_ANALYTICS_ENABLED", "false")
        environment.delenv("PREFECT_API_KEY", raising=False)
        with prefect_test_harness():
            yield


def provider_files(
    tmp_path: Path, model_id: ModelId
) -> tuple[Path, Path, list[ProvisionalRatingConfig]]:
    original = parse_reference_evidence(
        canonical_content(json.loads(samples()[0].content)["reference"])
    )
    opt_ins = []
    provider = [
        "[adapters.river_stations]",
        'type = "dhm"',
        f"endpoint = {json.dumps(original.endpoint)}",
        "max_requests_per_batch = 1",
        "max_bytes_per_batch = 1000",
        "max_batch_seconds = 30.0",
    ]
    for index in range(17):
        station_id = StationId(uuid4())
        curve_data = json.loads(original.curve.content)
        curve_data["station_id"] = str(station_id)
        reference = replace(
            original,
            id=RatingReferenceProofId(uuid4()),
            tenant_id=DEFAULT_TENANT_ID,
            station_id=station_id,
            curve=CurveSnapshot(content=canonical_content(curve_data)),
            api_station_id=index + 1,
        )
        opt_ins.append(
            ProvisionalRatingConfig(
                tenant_id=reference.tenant_id,
                station_id=station_id,
                station_code=f"synthetic-{station_id}",
                model_ids=(str(model_id),),
                reference_content=canonical_content(asdict(reference)),
                reconciliation_days=2,
                custody_start=NOW - timedelta(days=2),
                custody_end=NOW + timedelta(days=2),
                custody_reference="synthetic",
            )
        )
        provider += [
            "[[adapters.river_stations.bindings]]",
            'network = "dhm"',
            f'station_code = "synthetic-{station_id}"',
            f"api_station_id = {index + 1}",
            'level_reference = "gauge_zero"',
        ]
    base = tmp_path / "base.toml"
    lines = [
        "max_retention_days = 600",
        "[adapters.river_stations]",
        'type = "hydro_scraper"',
        'endpoint = "https://example.invalid/base"',
        "max_cache_age_hours = 24",
        "[adapters.river_stations.monitoring]",
        "expected_interval_hours = 0.17",
        "[adapters.weather_forecast]",
        "enabled = false",
    ]
    for opt in opt_ins:
        lines += ["[[provisional_ratings]]"]
        lines += [
            f"{key} = {json.dumps(value)}"
            for key, value in opt.model_dump(mode="json", exclude_none=True).items()
        ]
    base.write_text("\n".join(lines) + "\n")
    overlay = tmp_path / "provider.toml"
    overlay.write_text("\n".join(provider) + "\n")
    return base, overlay, opt_ins


class TestProductionProviderComposition:
    @pytest.mark.parametrize(
        "mode",
        [
            "configured",
            "missing",
            "duplicate",
            "missing_reader",
            "injected_config",
            "standard",
        ],
    )
    def test_real_loader_and_store_factory(
        self,
        isolated_committed_db_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        mode: str,
        local_flow_server: None,
    ) -> None:
        model_id = ModelId(f"provider-composition-{uuid4()}")
        base, overlay, opt_ins = provider_files(tmp_path, model_id)
        first = opt_ins[0]
        monkeypatch.setenv(
            "DATABASE_URL",
            isolated_committed_db_engine.url.render_as_string(hide_password=False),
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(base))
        monkeypatch.setenv("SAPPHIRE_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        baseline_config = load_config(base)
        from sapphire_flow.config import river_stations as provider_config

        worker_overlay = tmp_path / "worker.toml"
        worker_overlay.write_text("")
        monkeypatch.setattr(provider_config, "FORECAST_PROVIDER_PATH", overlay)
        monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", str(worker_overlay))
        monkeypatch.delenv("SAPPHIRE_FORECAST_CONFIG_OVERLAY", raising=False)
        if mode != "missing":
            monkeypatch.setenv(
                "SAPPHIRE_FORECAST_CONFIG_OVERLAY", f"{worker_overlay},{overlay}"
            )
        if mode == "standard":
            monkeypatch.setenv(
                "SAPPHIRE_FORECAST_CONFIG_OVERLAY", "invalid-ignored-selection"
            )
        if mode == "duplicate":
            binding_text = overlay.read_text().split(
                "[[adapters.river_stations.bindings]]"
            )[1]
            overlay.write_text(
                overlay.read_text()
                + "[[adapters.river_stations.bindings]]"
                + binding_text
            )
        assert load_config(base) == baseline_config
        group_id = StationGroupId(uuid4())
        with isolated_committed_db_engine.begin() as conn:
            conn.execute(
                sa.insert(model_table).values(
                    id=str(model_id),
                    display_name="Synthetic group",
                    artifact_scope="group",
                    description="Provider composition fixture",
                )
            )
            PgStationStore(conn).store_station(
                make_station_config(
                    station_id=StationId(first.station_id),
                    tenant_id=first.to_domain().tenant_id,
                    code=first.station_code,
                    network="dhm",
                    water_level_unit="m",
                )
            )
            PgStationStore(conn).store_weather_source(
                StationWeatherSource(
                    station_id=StationId(first.station_id),
                    nwp_source="icon_ch1_eps",
                    extraction_type=SpatialRepresentation.POINT,
                    status=WeatherSourceStatus.ACTIVE,
                    role=WeatherSourceRole.FORECAST,
                )
            )
        # Group writes use their own transaction factory and require committed parents.
        with isolated_committed_db_engine.begin() as conn:
            groups = PgStationGroupStore(conn)
            groups.store_group(
                StationGroup(
                    id=group_id,
                    name=f"provider-{group_id}",
                    station_ids=frozenset({StationId(first.station_id)}),
                    description=None,
                    created_at=NOW,
                )
            )
            groups.store_group_model_assignment(
                GroupModelAssignment(
                    group_id=group_id,
                    model_id=model_id,
                    time_step=timedelta(days=1),
                    status=ModelAssignmentStatus.ACTIVE,
                    priority=1,
                    created_at=NOW,
                )
            )
        composed: list[dict[str, Any]] = []
        actual_setup = _db.setup_production_stores

        def setup(*args: Any, **kwargs: Any) -> Any:
            connection, stores = actual_setup(*args, **kwargs)
            if mode == "missing_reader":
                stores = {**stores, "rating_derived_store": None}
            composed.append(stores)
            return connection, stores

        observed: list[NativeRatingInputs | None] = []

        def assembly(**kwargs: Any) -> GroupInputAssembly:
            observed.append(kwargs["rating_inputs"])
            return GroupInputAssembly(
                expected_station_ids=tuple(kwargs["group"].station_ids),
                inputs=None,
                metadata_by_station={},
                unavailable_members=(),
            )

        actual_send = httpx.Client.send

        def local_transport(
            client: httpx.Client, request: httpx.Request, **kwargs: Any
        ) -> httpx.Response:
            if request.url.host not in ("localhost", "127.0.0.1", "::1"):
                raise AssertionError("provider/weather transport must not run")
            return actual_send(client, request, **kwargs)

        monkeypatch.setattr(_db, "setup_production_stores", setup)
        monkeypatch.setattr(
            "sapphire_flow.services.run_group_forecast.assemble_group_operational_inputs_outcome",
            assembly,
        )
        monkeypatch.setattr(httpx.Client, "send", local_transport)
        data_use = (
            ForecastDataUse.STANDARD
            if mode == "standard"
            else ForecastDataUse.PROVISIONAL_RATING
        )
        model = _SmallFakeGroupModel()
        model.data_requirements = replace(
            model.data_requirements,
            supported_time_steps=frozenset({timedelta(days=1)}),
            past_dynamic_features=frozenset(),
            future_dynamic_features=frozenset(),
        )
        kwargs = dict(
            models={model_id: model},
            qc_rules=_empty_qc_rules(),
            clock=lambda: NOW,
            rng=random.Random(42),
            data_use=data_use,
        )
        if mode == "injected_config":
            kwargs["config"] = baseline_config
        refusal_messages = {
            "missing": "requires native DHM reader",
            "missing_reader": "requires native DHM reader",
            "duplicate": "invalid forecast provider configuration",
            "injected_config": "cannot accompany injected config",
        }
        if mode in refusal_messages:
            with pytest.raises(ConfigurationError, match=refusal_messages[mode]):
                cycle.run_forecast_cycle_flow(**kwargs)
            assert observed == []
            return
        result = cycle.run_forecast_cycle_flow(**kwargs)
        assert result.forecasts_stored == 0
        assert len(composed) == 1
        assert composed[0]["forecast_store"].data_use is data_use
        assert composed[0]["rejected_forecast_store"].data_use is data_use
        assert observed, result.errors
        if mode == "standard":
            assert all(item is None for item in observed)
        else:
            native = observed[0]
            assert isinstance(native, NativeRatingInputs)
            assert isinstance(native.reader, PgRatingDerivedStore)
            assert native.reader is composed[0]["rating_derived_store"]
            assert isinstance(native.dhm, DhmConfig)
            assert native.dhm == load_river_station_config(
                base, [worker_overlay, overlay]
            )
            assert len(native.bindings) == len(native.dhm.bindings) == 17
            for opt, provider in zip(native.bindings, native.dhm.bindings, strict=True):
                assert opt.reference.endpoint == native.dhm.endpoint
                assert opt.reference.api_station_id == provider.api_station_id
                assert opt.station_code == provider.station_code


class TestProviderConfigurationProcess:
    def test_job_environment_reaches_real_configuration_boundary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os
        import subprocess
        import sys

        from sapphire_flow.cli.register_deployments import (
            ForecastConfigOverlay,
            forecast_job_variables,
        )
        from sapphire_flow.config import river_stations as provider_config

        base, provider, _ = provider_files(tmp_path, ModelId("synthetic-process"))
        worker = tmp_path / "worker.toml"
        worker.write_text("")
        monkeypatch.setattr(provider_config, "FORECAST_PROVIDER_PATH", provider)
        selection = ForecastConfigOverlay(paths=(worker, provider))
        variables = forecast_job_variables({"env": {"KEEP": "unchanged"}}, selection)
        environment = {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "PREFECT_HOME": str(tmp_path / "prefect"),
            "SAPPHIRE_CONFIG": str(base),
            "SAPPHIRE_CONFIG_OVERLAY": str(worker),
            **cast("dict[str, str]", variables["env"]),
        }
        # Only the filesystem mount target is mapped to a synthetic temporary file.
        # The real parser, selection boundary and process environment are unchanged.
        code = """
import json, sys
from pathlib import Path
from sapphire_flow.config import river_stations
river_stations.FORECAST_PROVIDER_PATH = Path(sys.argv[1])
from sapphire_flow.flows.run_forecast_cycle import _load_selected_provider_config
from sapphire_flow.types.enums import ForecastDataUse
config, provider = _load_selected_provider_config(
    None, ForecastDataUse.PROVISIONAL_RATING
)
assert config is not None and provider is not None
print(json.dumps({
    "kind": type(provider).__name__, "bindings": len(provider.bindings),
    "opt_ins": len(config.provisional_ratings),
}))
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-c", code, str(provider)],
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        assert json.loads(completed.stdout) == {
            "kind": "DhmConfig",
            "bindings": 17,
            "opt_ins": 17,
        }
