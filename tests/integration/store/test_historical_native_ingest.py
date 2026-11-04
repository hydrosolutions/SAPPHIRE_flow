from __future__ import annotations

from dataclasses import asdict, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.config.provisional_rating import ProvisionalRatingConfig
from sapphire_flow.db.metadata import (
    native_capture_batches,
    native_measurement_captures,
    pipeline_health,
    rating_derived_discharge,
)
from sapphire_flow.flows import historical_ingest as historical
from sapphire_flow.flows import ingest_observations as flow
from sapphire_flow.store.native_capture_store import PgNativeCaptureStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.pipeline_health_store import PgPipelineHealthStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import PipelineCheckType, PipelineHealthStatus, QcStatus
from sapphire_flow.types.ids import ObservationId
from sapphire_flow.types.pipeline import PipelineHealthRecord
from sapphire_flow.types.rating_reference import canonical_content
from tests.conftest import make_observation
from tests.fakes.fake_stores import FakeClimBaselineStore
from tests.integration.cli.test_import_dhm_delivery import _NOW
from tests.integration.store.test_native_rating_capture_selection import (
    source_binding as source_binding,
)
from tests.integration.store.test_rejected_forecast_store import savepoint_factory

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.config.dhm import DhmConfig
    from sapphire_flow.config.provisional_rating import NativeRatingBinding
    from sapphire_flow.types.observation import Observation


@pytest.mark.parametrize("configured_rating", [False, True])
@pytest.mark.parametrize(
    "fetch_case",
    ["fresh", "empty", "failure", "collision", "override", "bad_threshold"],
)
def test_real_atomic_capture_and_old_only_qc_without_conversion(
    db_connection: sa.Connection,
    monkeypatch: pytest.MonkeyPatch,
    source_binding: tuple[NativeRatingBinding, DhmConfig, Observation],
    configured_rating: bool,
    fetch_case: Literal[
        "fresh", "empty", "failure", "collision", "override", "bad_threshold"
    ],
    tmp_path: Path,
) -> None:
    binding, dhm, old = source_binding
    station_store = PgStationStore(db_connection)
    station = station_store.fetch_station(binding.station_id)
    assert station is not None
    if fetch_case == "override":
        station = replace(station, water_level_datum_masl=0.0)
        dhm = replace(dhm, bindings=(replace(dhm.bindings[0], level_reference="masl"),))
        binding = replace(
            binding,
            reference=replace(
                binding.reference, level_reference="masl", curve_reference="masl"
            ),
        )
    station_store.update_station(
        replace(station, measured_parameters=frozenset({"water_level"}))
    )
    obs = PgObservationStore(db_connection)
    if fetch_case == "collision":
        from sapphire_flow.db.metadata import observations

        db_connection.execute(
            sa.update(observations)
            .where(observations.c.id == old.id)
            .values(delivery_id="official-fixture")
        )
    health = PgPipelineHealthStore(db_connection)
    health.append_health_record(
        PipelineHealthRecord(
            check_type=PipelineCheckType.OBSERVATION_QC_UNCHECKED,
            checked_at=_NOW + timedelta(days=3),
            status=PipelineHealthStatus.WARNING,
            subject="ingest_observations",
            detail={"fixture": "live latest"},
            cycle_time=None,
            created_at=_NOW,
        )
    )
    health_before = health.fetch_recent(
        PipelineCheckType.OBSERVATION_QC_UNCHECKED, limit=1
    )
    health_count = db_connection.scalar(
        sa.select(sa.func.count()).select_from(pipeline_health)
    )
    cursor = make_observation(
        station_id=station.id,
        parameter="water_level",
        value=0.2,
        timestamp=_NOW + timedelta(hours=2),
    )
    cursor = replace(cursor, id=ObservationId(uuid4()))
    obs.store_observations([cursor])
    tenant_store = PgTenantStore(db_connection)
    tenant = tenant_store.fetch_tenant(station.tenant_id)
    assert tenant is not None
    now = _NOW + timedelta(days=10)
    captures = PgNativeCaptureStore(
        savepoint_factory(db_connection), clock=lambda: now, id_gen=uuid4
    )
    stores = {
        "station_store": station_store,
        "obs_store": obs,
        "tenant_store": tenant_store,
        "baseline_store": FakeClimBaselineStore(),
        "native_capture_store": captures,
    }
    configured = ProvisionalRatingConfig(
        tenant_id=binding.tenant_id,
        station_id=binding.station_id,
        station_code=binding.station_code,
        model_ids=("fixture",),
        reference_content=canonical_content(asdict(binding.reference)),
        reconciliation_days=1,
        evidence_mode="native_capture",
    )
    monkeypatch.setattr(historical, "_bootstrap", lambda: (None, stores))
    monkeypatch.setattr(historical, "_historical_now", lambda: now)
    monkeypatch.setattr(
        historical,
        "_load_identity_config",
        lambda: DeploymentIdentityConfig(
            writable_tenants=frozenset({tenant.code}), global_admin=False
        ),
    )
    monkeypatch.setattr(
        historical,
        "_load_operational_config",
        lambda: DeploymentConfig(
            max_retention_days=3650,
            enable_observation_alerts=True,
            provisional_ratings=(configured,) if configured_rating else (),
        ),
    )
    monkeypatch.setattr(historical, "_load_dhm_config", lambda: dhm)
    monkeypatch.setattr(
        historical, "_load_rules", lambda: QcRuleSet(version="none", rules=())
    )
    monkeypatch.setattr(historical, "_fetch_window", historical._fetch_window.fn)
    monkeypatch.setattr(flow, "_run_qc_task", flow._run_qc_task.fn)
    monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
    resolved = []
    if fetch_case in ("override", "bad_threshold"):
        rules = QcRuleSet(
            version="range",
            rules=(
                QcRuleParams(
                    rule_id="range_check",
                    rule_version="dhm",
                    parameter="water_level",
                    time_step=timedelta(minutes=10),
                    thresholds={"value_min": 0.0, "value_max": 1.0},
                    network="dhm",
                ),
            ),
        )
        monkeypatch.setattr(historical, "_load_rules", lambda: rules)
        threshold_file = tmp_path / "thresholds.toml"
        specs = [
            (tenant.code, station.code),
            ("wrong-tenant", station.code),
            (tenant.code, "unrelated"),
        ]
        threshold_file.write_text(
            "\n".join(
                f'[[onboarding.station_qc_thresholds]]\ntenant_code="{owner}"\ncode="{code}"\n'
                'network="dhm"\nrule_id="range_check"\nparameter="water_level"\n'
                "time_step_seconds=600\nthresholds={value_max=0.25}\n"
                for owner, code in specs
            )
            if fetch_case == "override"
            else '[onboarding]\nstation_qc_thresholds="invalid"\n'
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(threshold_file))
        resolver = flow.resolve_configured_station_qc

        def resolve_once(*args: object, **kwargs: object):
            assert not calls
            outcome = resolver(*args, **kwargs)
            resolved.append(outcome)
            return outcome

        monkeypatch.setattr(flow, "resolve_configured_station_qc", resolve_once)
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        from tests.unit.adapters.test_dhm import response

        if fetch_case == "failure":
            return response(403)
        if fetch_case == "empty":
            return response(200, json={"results": []})
        return response(
            200,
            json={
                "results": [
                    {
                        "station": 1,
                        "waterLevel": 0.3,
                        "waterLevelOn": (_NOW + timedelta(minutes=minute)).isoformat(),
                    }
                    for minute in ((0, 10, 20) if fetch_case == "override" else (0,))
                ]
            },
        )

    monkeypatch.setattr(
        flow,
        "_dhm_http_client",
        lambda config: httpx.Client(transport=httpx.MockTransport(handle)),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("historical flow entered unrelated side effect")

    for name in (
        "_store_raw_task",
        "_derive_calculated_task",
        "_reconcile_native_ratings_task",
        "_append_fetch_health_record",
        "_append_zero_rule_health_record",
        "_append_unjudged_health_record",
        "_append_threshold_config_health_record",
    ):
        monkeypatch.setattr(flow, name, forbidden)
    if fetch_case == "bad_threshold":
        from sapphire_flow.exceptions import ConfigurationError

        with pytest.raises(
            ConfigurationError, match="^Historical capture preflight failed$"
        ):
            flow.ingest_observations_flow.fn(
                historical_tenant_code=tenant.code,
                historical_station_ids=[str(station.id)],
                historical_start=_NOW,
                historical_end=_NOW + timedelta(hours=1),
            )
        assert not calls
        assert (
            db_connection.scalar(
                sa.select(sa.func.count()).select_from(native_capture_batches)
            )
            == 0
        )
        assert (
            health.fetch_recent(PipelineCheckType.OBSERVATION_QC_UNCHECKED, limit=1)
            == health_before
        )
        return
    result = flow.ingest_observations_flow.fn(
        historical_tenant_code=tenant.code,
        historical_station_ids=[str(station.id)],
        historical_start=_NOW,
        historical_end=_NOW + timedelta(hours=1),
    )
    assert len(calls) == 1
    assert result.stations_failed == int(fetch_case in ("failure", "collision"))
    expected_written = 3 if fetch_case == "override" else int(fetch_case == "fresh")
    assert result.observations_stored == expected_written
    if fetch_case == "override":
        assert len(resolved) == 1
        assert result.qc_failed == 3
        assert result.qc_threshold_rejected == 1
        before = obs.fetch_observation_by_id(old.id)
        with db_connection.begin_nested() as comparison:
            source_rows = obs.qc_context_reader().fetch_observations(
                station.id, "water_level", _NOW, _NOW + timedelta(hours=1)
            )
            for row in source_rows:
                obs.update_qc(row.id, QcStatus.RAW, [], qc_rule_version=None)
            ordinary = flow._run_qc_task(
                obs.qc_context_reader(),
                FakeClimBaselineStore(),
                station.id,
                "water_level",
                rules,
                _NOW + timedelta(minutes=30),
                station_networks={station.id: station.network},
                datum=station.water_level_datum_masl,
                overrides=resolved[0].overrides,
            )
            assert ordinary.counts["failed"] == 3
            assert obs.fetch_observation_by_id(old.id) == before
            comparison.rollback()
    assert result.qc_unchecked == int(fetch_case == "fresh")
    assert result.rating_results == ()
    assert db_connection.scalar(
        sa.select(sa.func.count()).select_from(native_capture_batches)
    ) == int(fetch_case in ("fresh", "empty", "override"))
    assert (
        db_connection.scalar(
            sa.select(sa.func.count()).select_from(native_measurement_captures)
        )
        == expected_written
    )
    assert (
        db_connection.scalar(
            sa.select(sa.func.count()).select_from(rating_derived_discharge)
        )
        == 0
    )
    revised = obs.fetch_observation_by_id(old.id)
    untouched = obs.fetch_observation_by_id(cursor.id)
    assert revised is not None
    assert revised.value == (0.3 if fetch_case in ("fresh", "override") else old.value)
    assert revised.qc_status is (
        QcStatus.QC_UNCHECKED
        if fetch_case == "fresh"
        else QcStatus.QC_FAILED
        if fetch_case == "override"
        else old.qc_status
    )
    assert untouched == cursor
    assert (
        health.fetch_recent(PipelineCheckType.OBSERVATION_QC_UNCHECKED, limit=1)
        == health_before
    )
    assert (
        db_connection.scalar(sa.select(sa.func.count()).select_from(pipeline_health))
        == health_count
    )
