from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from sapphire_flow.exceptions import ConfigurationError, ModelSmokeTestError
from sapphire_flow.services.model_onboarding import (
    create_group_assignment,
    create_station_assignment,
    evaluate_skill_gate,
    smoke_test_model,
    validate_compatibility_for_unit,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    ModelAssignmentStatus,
    SkillFreshness,
    SkillSource,
    SpatialRepresentation,
)
from sapphire_flow.types.ids import (
    CLIMATOLOGY_FALLBACK_MODEL_ID,
    ArtifactId,
    ModelId,
    StationGroupId,
    StationId,
)
from sapphire_flow.types.model import ModelDataRequirements
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.conftest import (
    make_deployment_config,
    make_station_config,
    make_training_unit,
)
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeSkillStore,
    FakeStationGroupStore,
    FakeStationStore,
)

_NOW = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_RNG = random.Random(42)
_CLOCK = lambda: _NOW  # noqa: E731


def _uuid() -> UUID:
    return UUID(int=random.Random(99).getrandbits(128), version=4)


def _make_skill_score(
    *,
    model_id: ModelId,
    artifact_id: ArtifactId,
    metric: str,
    score: float,
    sample_size: int = 200,
) -> object:
    from sapphire_flow.types.skill import SkillScore

    return SkillScore(
        id=uuid4(),
        station_id=StationId(uuid4()),
        model_id=model_id,
        parameter="discharge",
        model_artifact_id=artifact_id,
        skill_source=SkillSource.HINDCAST_REANALYSIS,
        forcing_type=None,
        computation_version=1,
        computed_at=_NOW,
        lead_time_hours=24,
        season=None,
        flow_regime=None,
        flow_regime_config_id=None,
        metric=metric,
        score=score,
        sample_size=sample_size,
        freshness=SkillFreshness.CURRENT,
        eval_period_start=_NOW,
        eval_period_end=_NOW,
        created_at=_NOW,
    )


def _make_model_with_reqs(
    *,
    target_parameters: frozenset[str] = frozenset({"discharge"}),
    past_dynamic: frozenset[str] = frozenset({"precipitation", "temperature"}),
    future_dynamic: frozenset[str] = frozenset(),
    static_features: frozenset[str] = frozenset(),
    supported_time_steps: frozenset[timedelta] | None = None,
) -> FakeStationForecastModel:
    model = FakeStationForecastModel()
    # FakeStationForecastModel is not a frozen dataclass — patch data_requirements
    model.__class__ = type(
        "PatchedModel",
        (FakeStationForecastModel,),
        {
            "data_requirements": ModelDataRequirements(
                target_parameters=target_parameters,
                past_dynamic_features=past_dynamic,
                future_dynamic_features=future_dynamic,
                static_features=static_features,
                supported_time_steps=supported_time_steps
                or frozenset({timedelta(days=1)}),
                lookback_steps=7,
                forecast_horizon_steps=5,
                spatial_input_type=SpatialRepresentation.POINT,
            )
        },
    )
    return model


class TestValidateCompatibility:
    def _call(
        self,
        model: FakeStationForecastModel,
        station_forecast_targets: frozenset[str] | None,
        available_features: frozenset[str],
        available_static: frozenset[str],
        time_step: timedelta,
        available_past_features: frozenset[str] | None = None,
        available_future_features: frozenset[str] | None = None,
    ) -> object:
        # ``available_features`` seeds BOTH the past- and future-dynamic sides
        # (Plan 115b1 §1E) unless a caller passes an explicit override for one.
        station = make_station_config(forecast_targets=station_forecast_targets)
        station_store = FakeStationStore()
        station_store.store_station(station)
        unit = make_training_unit(model_id=ModelId("test_model"), station_id=station.id)
        return validate_compatibility_for_unit(
            model_id=ModelId("test_model"),
            model=model,
            unit=unit,
            station_store=station_store,
            group_store=None,  # type: ignore[arg-type]
            available_past_features=available_past_features
            if available_past_features is not None
            else available_features,
            available_future_features=available_future_features
            if available_future_features is not None
            else available_features,
            available_static_by_station={station.id: available_static},
            requested_time_step=time_step,
        )

    def test_compatible_station(self) -> None:
        model = _make_model_with_reqs()
        report = self._call(
            model,
            station_forecast_targets=frozenset({"discharge"}),
            available_features=frozenset({"precipitation", "temperature"}),
            available_static=frozenset(),
            time_step=timedelta(days=1),
        )
        assert report.is_compatible  # type: ignore[union-attr]

    def test_missing_target_parameters(self) -> None:
        model = _make_model_with_reqs()
        report = self._call(
            model,
            station_forecast_targets=None,
            available_features=frozenset({"precipitation", "temperature"}),
            available_static=frozenset(),
            time_step=timedelta(days=1),
        )
        assert not report.is_compatible  # type: ignore[union-attr]
        assert "discharge" in report.missing_target_parameters  # type: ignore[union-attr]

    def test_missing_past_dynamic_features(self) -> None:
        model = _make_model_with_reqs(
            past_dynamic=frozenset({"precipitation", "temperature", "wind_speed"})
        )
        report = self._call(
            model,
            station_forecast_targets=frozenset({"discharge"}),
            available_features=frozenset({"precipitation", "temperature"}),
            available_static=frozenset(),
            time_step=timedelta(days=1),
        )
        assert not report.is_compatible  # type: ignore[union-attr]
        assert "wind_speed" in report.missing_past_dynamic  # type: ignore[union-attr]

    def test_time_step_incompatible(self) -> None:
        model = _make_model_with_reqs(
            supported_time_steps=frozenset({timedelta(hours=1)})
        )
        report = self._call(
            model,
            station_forecast_targets=frozenset({"discharge"}),
            available_features=frozenset({"precipitation", "temperature"}),
            available_static=frozenset(),
            time_step=timedelta(days=1),
        )
        assert not report.is_compatible  # type: ignore[union-attr]
        assert not report.time_step_compatible  # type: ignore[union-attr]

    def test_compatible_with_static_features(self) -> None:
        model = _make_model_with_reqs(static_features=frozenset({"elevation", "area"}))
        report = self._call(
            model,
            station_forecast_targets=frozenset({"discharge"}),
            available_features=frozenset({"precipitation", "temperature"}),
            available_static=frozenset({"elevation", "area"}),
            time_step=timedelta(days=1),
        )
        assert report.is_compatible  # type: ignore[union-attr]

    def test_future_dynamic_compatible_when_nwp_available(self) -> None:
        """M2 (Fix #2 gate): an NWP model declares precip/temp as future forcing
        with discharge as target history (NOT past forcing). When precip/temp are
        in available_nwp_parameters, the model is compatible — discharge does not
        appear in missing_past, and there is no missing_future.
        """
        model = _make_model_with_reqs(
            past_dynamic=frozenset(),
            future_dynamic=frozenset({"precipitation", "temperature"}),
        )
        report = self._call(
            model,
            station_forecast_targets=frozenset({"discharge"}),
            available_features=frozenset({"precipitation", "temperature"}),
            available_static=frozenset(),
            time_step=timedelta(days=1),
        )
        assert report.is_compatible  # type: ignore[union-attr]
        assert "discharge" not in report.missing_past_dynamic  # type: ignore[union-attr]
        assert not report.missing_past_dynamic  # type: ignore[union-attr]
        assert not report.missing_future_dynamic  # type: ignore[union-attr]

    def test_missing_future_dynamic_when_nwp_absent(self) -> None:
        """The config gate still works: precip/temp absent from
        available_nwp_parameters → reported as missing_future_dynamic.
        """
        model = _make_model_with_reqs(
            past_dynamic=frozenset(),
            future_dynamic=frozenset({"precipitation", "temperature"}),
        )
        report = self._call(
            model,
            station_forecast_targets=frozenset({"discharge"}),
            available_features=frozenset(),
            available_static=frozenset(),
            time_step=timedelta(days=1),
        )
        assert not report.is_compatible  # type: ignore[union-attr]
        assert "precipitation" in report.missing_future_dynamic  # type: ignore[union-attr]
        assert "temperature" in report.missing_future_dynamic  # type: ignore[union-attr]


class TestPastVsFutureAvailabilitySplit:
    """Plan 115b1 §1E (round-4 blocker): a reanalysis-only parameter (SrelD /
    relative_sunshine_duration) has no forecast counterpart. It must be
    accepted when declared past_dynamic and REJECTED when declared
    future_dynamic — proving the past/future availability sets are genuinely
    distinct, not a single conflated set.

    Soundness: fails against a single ``available_features`` set shared by
    both past and future checks (today's ``available_nwp_parameters``), which
    would incorrectly accept the future_dynamic declaration too.
    """

    def _call(
        self,
        model: FakeStationForecastModel,
        *,
        available_past_features: frozenset[str],
        available_future_features: frozenset[str],
    ) -> object:
        station = make_station_config(forecast_targets=frozenset({"discharge"}))
        station_store = FakeStationStore()
        station_store.store_station(station)
        unit = make_training_unit(model_id=ModelId("test_model"), station_id=station.id)
        return validate_compatibility_for_unit(
            model_id=ModelId("test_model"),
            model=model,
            unit=unit,
            station_store=station_store,
            group_store=None,  # type: ignore[arg-type]
            available_past_features=available_past_features,
            available_future_features=available_future_features,
            available_static_by_station={station.id: frozenset()},
            requested_time_step=timedelta(days=1),
        )

    def test_sreld_accepted_as_past_dynamic(self) -> None:
        model = _make_model_with_reqs(
            past_dynamic=frozenset({"relative_sunshine_duration"}),
            future_dynamic=frozenset(),
        )
        report = self._call(
            model,
            available_past_features=frozenset({"relative_sunshine_duration"}),
            available_future_features=frozenset(),
        )
        assert report.is_compatible  # type: ignore[union-attr]
        assert not report.missing_past_dynamic  # type: ignore[union-attr]

    def test_sreld_rejected_as_future_dynamic(self) -> None:
        model = _make_model_with_reqs(
            past_dynamic=frozenset(),
            future_dynamic=frozenset({"relative_sunshine_duration"}),
        )
        report = self._call(
            model,
            available_past_features=frozenset({"relative_sunshine_duration"}),
            available_future_features=frozenset(),  # NOT future-available
        )
        assert not report.is_compatible  # type: ignore[union-attr]
        assert (
            "relative_sunshine_duration" in report.missing_future_dynamic  # type: ignore[union-attr]
        )


class TestSmokeTestModel:
    def test_passes_for_valid_model(self) -> None:
        model = FakeStationForecastModel()
        rng = random.Random(7)
        smoke_test_model(model=model, rng=rng)

    def test_fails_for_broken_model(self) -> None:
        class BrokenModel(FakeStationForecastModel):
            def train(self, data, params, rng):  # type: ignore[override]
                raise RuntimeError("training exploded")

        model = BrokenModel()
        rng = random.Random(7)
        with pytest.raises(ModelSmokeTestError, match="training exploded"):
            smoke_test_model(model=model, rng=rng)


class TestAssertModelConformsFutureForcing:
    """Regression (epic-088 M2): the conformance suite's synthetic training data
    must deliver future-known forcing TIMESTAMP-ALIGNED with past_targets. A
    future-forcing model fits target[t] ~ forcing[t] and looks up forcing at every
    target timestamp; disjoint synthetic timestamps raised KeyError → FAILED_SMOKE_TEST.
    """

    @pytest.mark.parametrize("model_cls", ["NwpRegression", "NwpRainfallRunoff"])
    def test_adapter_wrapped_future_forcing_model_conforms(
        self, model_cls: str
    ) -> None:
        from sapphire_flow.adapters.forecast_interface import adapt_if_fi
        from sapphire_flow.models import nwp_regression
        from sapphire_flow.services.model_onboarding import assert_model_conforms

        model = getattr(nwp_regression, model_cls)()
        adapter = adapt_if_fi(model)
        # Sanity: it really is a future-forcing model (precip/temp future-known).
        assert adapter.data_requirements.future_dynamic_features == frozenset(  # type: ignore[union-attr]
            {"precipitation", "temperature"}
        )
        # Trains + serializes + predicts through SAP3's protocol surface without
        # KeyError; determinism check runs the synthetic pipeline twice.
        assert_model_conforms(adapter, random.Random(1))  # type: ignore[arg-type]


class TestEvaluateSkillGate:
    def test_empty_thresholds_passes(self) -> None:
        skill_store = FakeSkillStore()
        model_id = ModelId("m1")
        artifact_id = ArtifactId(uuid4())
        config = make_deployment_config(skill_gate_thresholds={})

        result = evaluate_skill_gate(
            model_id=model_id,
            model_artifact_id=artifact_id,
            skill_store=skill_store,
            config=config,
        )

        assert result.passed
        assert result.failing_metrics == frozenset()

    def test_passes_with_sufficient_scores(self) -> None:
        from sapphire_flow.types.model_onboarding import SkillGateMetric

        skill_store = FakeSkillStore()
        model_id = ModelId("m2")
        artifact_id = ArtifactId(uuid4())
        score = _make_skill_score(
            model_id=model_id, artifact_id=artifact_id, metric="nse", score=0.8
        )
        skill_store._scores.append(score)  # type: ignore[attr-defined]

        config = make_deployment_config(
            skill_gate_thresholds={
                "nse": SkillGateMetric(threshold=0.5, higher_is_better=True)
            },
            min_skill_samples=100,
        )

        result = evaluate_skill_gate(
            model_id=model_id,
            model_artifact_id=artifact_id,
            skill_store=skill_store,
            config=config,
        )

        assert result.passed
        assert result.failing_metrics == frozenset()

    def test_fails_with_insufficient_scores(self) -> None:
        from sapphire_flow.types.model_onboarding import SkillGateMetric

        skill_store = FakeSkillStore()
        model_id = ModelId("m3")
        artifact_id = ArtifactId(uuid4())
        score = _make_skill_score(
            model_id=model_id, artifact_id=artifact_id, metric="nse", score=0.2
        )
        skill_store._scores.append(score)  # type: ignore[attr-defined]

        config = make_deployment_config(
            skill_gate_thresholds={
                "nse": SkillGateMetric(threshold=0.5, higher_is_better=True)
            },
            min_skill_samples=100,
        )

        result = evaluate_skill_gate(
            model_id=model_id,
            model_artifact_id=artifact_id,
            skill_store=skill_store,
            config=config,
        )

        assert not result.passed
        assert "nse" in result.failing_metrics

    def test_skipped_insufficient_eval(self) -> None:
        from sapphire_flow.types.model_onboarding import SkillGateMetric

        skill_store = FakeSkillStore()
        model_id = ModelId("m4")
        artifact_id = ArtifactId(uuid4())
        # Score exists but sample_size < min_skill_samples — filtered out
        score = _make_skill_score(
            model_id=model_id,
            artifact_id=artifact_id,
            metric="nse",
            score=0.8,
            sample_size=5,
        )
        skill_store._scores.append(score)  # type: ignore[attr-defined]

        config = make_deployment_config(
            skill_gate_thresholds={
                "nse": SkillGateMetric(threshold=0.5, higher_is_better=True)
            },
            min_skill_samples=100,
        )

        result = evaluate_skill_gate(
            model_id=model_id,
            model_artifact_id=artifact_id,
            skill_store=skill_store,
            config=config,
        )

        # No valid strata → metric_scores is empty, gate fails (no data for nse)
        assert len(result.metric_scores) == 0
        assert not result.passed


class TestCreateAssignment:
    def test_creates_station_assignment(self) -> None:
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)
        model_id = ModelId("my_model")

        assignment = create_station_assignment(
            station_id=station.id,
            model_id=model_id,
            time_step=timedelta(days=1),
            priority=0,
            station_store=station_store,
            clock=_CLOCK,
        )

        assert assignment.station_id == station.id
        assert assignment.model_id == model_id
        assert assignment.status == ModelAssignmentStatus.ACTIVE

    def test_skips_inactive_assignment(self) -> None:
        from sapphire_flow.types.station import ModelAssignment

        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)
        model_id = ModelId("my_model")

        # Pre-seed an INACTIVE assignment
        inactive = ModelAssignment(
            station_id=station.id,
            model_id=model_id,
            time_step=timedelta(days=1),
            status=ModelAssignmentStatus.INACTIVE,
            priority=0,
            created_at=_NOW,
        )
        station_store._assignments.append(inactive)  # type: ignore[attr-defined]

        returned = create_station_assignment(
            station_id=station.id,
            model_id=model_id,
            time_step=timedelta(days=1),
            priority=0,
            station_store=station_store,
            clock=_CLOCK,
        )

        # Returns the existing inactive assignment, does not overwrite
        assert returned.status == ModelAssignmentStatus.INACTIVE
        assignments = station_store.fetch_model_assignments(station.id)
        assert len(assignments) == 1
        assert assignments[0].status == ModelAssignmentStatus.INACTIVE

    def test_reonboard_updates_priority_idempotently(self) -> None:
        """Plan 089: re-onboarding with a changed config priority upserts the
        existing active assignment (single row, new priority)."""
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)
        model_id = ModelId("nwp_rainfall_runoff")

        create_station_assignment(
            station_id=station.id,
            model_id=model_id,
            time_step=timedelta(days=1),
            priority=100,
            station_store=station_store,
            clock=_CLOCK,
        )
        # Re-onboard with a new (lower) priority — e.g. config retuned.
        create_station_assignment(
            station_id=station.id,
            model_id=model_id,
            time_step=timedelta(days=1),
            priority=20,
            station_store=station_store,
            clock=_CLOCK,
        )

        assignments = station_store.fetch_model_assignments(station.id)
        assert len(assignments) == 1
        assert assignments[0].priority == 20

    def test_below_tier_fallback_station_assignment_raises(self) -> None:
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        with pytest.raises(ConfigurationError, match="climatology_fallback"):
            create_station_assignment(
                station_id=station.id,
                model_id=CLIMATOLOGY_FALLBACK_MODEL_ID,
                time_step=timedelta(days=1),
                priority=0,
                station_store=station_store,
                clock=_CLOCK,
            )

    def test_below_tier_fallback_group_assignment_raises(self) -> None:
        group_store = FakeStationGroupStore()

        with pytest.raises(ConfigurationError, match="climatology_fallback"):
            create_group_assignment(
                group_id=StationGroupId(uuid4()),
                model_id=CLIMATOLOGY_FALLBACK_MODEL_ID,
                time_step=timedelta(days=1),
                priority=0,
                group_store=group_store,
                clock=_CLOCK,
            )


class TestCreateAssignmentTenantIsolation:
    """Plan 147 Slice E (R5/G6): a cross-tenant assignment write is rejected
    + audited, never persisted; a same-tenant write proceeds normally."""

    def test_cross_tenant_station_assignment_rejected(self) -> None:
        from sapphire_flow.exceptions import TenantIsolationError
        from sapphire_flow.types.ids import TenantId
        from sapphire_flow.types.write_principal import WritePrincipal
        from tests.fakes.fake_stores import FakeAuditLogStore

        tenant_b = TenantId(uuid4())
        station_store = FakeStationStore()
        station = make_station_config(tenant_id=tenant_b)
        station_store.store_station(station)
        audit = FakeAuditLogStore()
        principal = WritePrincipal(id=None, tenant_id=DEFAULT_TENANT_ID)

        with pytest.raises(TenantIsolationError):
            create_station_assignment(
                station_id=station.id,
                model_id=ModelId("my_model"),
                time_step=timedelta(days=1),
                priority=0,
                station_store=station_store,
                clock=_CLOCK,
                principal=principal,
                audit_log_store=audit,
            )

        assert station_store.fetch_model_assignments(station.id) == []
        assert len(audit._entries) == 1  # type: ignore[attr-defined]
        assert audit._entries[0].event_type.value == "model_assigned"  # type: ignore[attr-defined]
        assert audit._entries[0].detail["outcome"] == "rejected_tenant_mismatch"  # type: ignore[attr-defined]

    def test_same_tenant_station_assignment_succeeds(self) -> None:
        from sapphire_flow.types.write_principal import WritePrincipal
        from tests.fakes.fake_stores import FakeAuditLogStore

        station_store = FakeStationStore()
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        station_store.store_station(station)
        audit = FakeAuditLogStore()
        principal = WritePrincipal(id=None, tenant_id=DEFAULT_TENANT_ID)

        assignment = create_station_assignment(
            station_id=station.id,
            model_id=ModelId("my_model"),
            time_step=timedelta(days=1),
            priority=0,
            station_store=station_store,
            clock=_CLOCK,
            principal=principal,
            audit_log_store=audit,
        )

        assert assignment.status == ModelAssignmentStatus.ACTIVE
        assert any(
            e.detail.get("outcome") == "assigned"  # type: ignore[attr-defined]
            for e in audit._entries  # type: ignore[attr-defined]
        )

    def test_global_admin_crosses_tenants(self) -> None:
        from sapphire_flow.types.ids import TenantId
        from sapphire_flow.types.write_principal import WritePrincipal

        tenant_b = TenantId(uuid4())
        station_store = FakeStationStore()
        station = make_station_config(tenant_id=tenant_b)
        station_store.store_station(station)
        principal = WritePrincipal(id=None, tenant_id=None)

        assignment = create_station_assignment(
            station_id=station.id,
            model_id=ModelId("my_model"),
            time_step=timedelta(days=1),
            priority=0,
            station_store=station_store,
            clock=_CLOCK,
            principal=principal,
        )

        assert assignment.status == ModelAssignmentStatus.ACTIVE

    def test_cross_tenant_group_assignment_rejected(self) -> None:
        from sapphire_flow.exceptions import TenantIsolationError
        from sapphire_flow.types.ids import TenantId
        from sapphire_flow.types.station import StationGroup
        from sapphire_flow.types.write_principal import WritePrincipal

        tenant_b = TenantId(uuid4())
        group_store = FakeStationGroupStore()
        group = StationGroup(
            id=StationGroupId(uuid4()),
            name="group-b",
            station_ids=frozenset(),
            created_at=_NOW,
            tenant_id=tenant_b,
        )
        group_store.store_group(group)
        principal = WritePrincipal(id=None, tenant_id=DEFAULT_TENANT_ID)

        with pytest.raises(TenantIsolationError):
            create_group_assignment(
                group_id=group.id,
                model_id=ModelId("my_group_model"),
                time_step=timedelta(days=1),
                priority=0,
                group_store=group_store,
                clock=_CLOCK,
                principal=principal,
            )

        assert group_store.fetch_group_model_assignments(group.id) == ()


class TestHindcastDays:
    """Verify the hindcast_days narrowing logic in _run_onboarding()."""

    def test_hindcast_days_rejects_zero(self) -> None:
        from unittest.mock import patch

        from sapphire_flow.services.onboarding import _run_onboarding
        from sapphire_flow.types.basin import Basin
        from sapphire_flow.types.datetime import ensure_utc
        from sapphire_flow.types.domain import QcRuleSet
        from sapphire_flow.types.ids import BasinId, ModelId
        from tests.conftest import make_deployment_config
        from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
        from tests.fakes.fake_stores import (
            FakeBasinStore,
            FakeClimBaselineStore,
            FakeFlowRegimeConfigStore,
            FakeHindcastStore,
            FakeHistoricalForcingStore,
            FakeModelArtifactStore,
            FakeModelStore,
            FakeObservationStore,
            FakeSkillStore,
            FakeStationGroupStore,
        )

        start = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
        end = ensure_utc(datetime(2022, 1, 1, tzinfo=UTC))

        sid = StationId(uuid4())
        station = make_station_config(station_id=sid, code="HD001")
        basin = Basin(
            id=BasinId(uuid4()),
            code="HD001",
            name="HD001",
            geometry=None,
            area_km2=100.0,
            attributes=None,
            band_geometries=None,
            created_at=start,
            network="bafu",
        )

        group_store = FakeStationGroupStore()
        s_station = FakeStationStore()
        s_station.store_station(station)

        fake_model_id = ModelId("fake_model")
        fake_model = FakeStationForecastModel()

        with patch(
            "sapphire_flow.services.model_registry.discover_models",
            return_value={fake_model_id: fake_model},
        ):
            result = _run_onboarding(
                stations=[station],
                basins=[basin],
                obs_by_station={sid: []},
                forcing_by_station={sid: []},
                basin_store=FakeBasinStore(),
                station_store=s_station,
                obs_store=FakeObservationStore(),
                forcing_store=FakeHistoricalForcingStore(),
                baseline_store=FakeClimBaselineStore(),
                flow_regime_store=FakeFlowRegimeConfigStore(),
                qc_rules=QcRuleSet(version="test", rules=()),
                clock=lambda: start,
                start_utc=start,
                end_utc=end,
                model_store=FakeModelStore(),
                artifact_store=FakeModelArtifactStore(group_store=group_store),
                group_store=group_store,
                hindcast_store=FakeHindcastStore(),
                skill_store=FakeSkillStore(),
                forcing_source=FakeWeatherReanalysisSource(),
                deployment_config=make_deployment_config(),
                hindcast_days=0,
            )
        assert any("hindcast_days must be >= 1" in e for e in result.errors)

    def test_hindcast_days_rejects_negative(self) -> None:
        from unittest.mock import patch

        from sapphire_flow.services.onboarding import _run_onboarding
        from sapphire_flow.types.basin import Basin
        from sapphire_flow.types.datetime import ensure_utc
        from sapphire_flow.types.domain import QcRuleSet
        from sapphire_flow.types.ids import BasinId, ModelId
        from tests.conftest import make_deployment_config
        from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
        from tests.fakes.fake_stores import (
            FakeBasinStore,
            FakeClimBaselineStore,
            FakeFlowRegimeConfigStore,
            FakeHindcastStore,
            FakeHistoricalForcingStore,
            FakeModelArtifactStore,
            FakeModelStore,
            FakeObservationStore,
            FakeSkillStore,
            FakeStationGroupStore,
        )

        start = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
        end = ensure_utc(datetime(2022, 1, 1, tzinfo=UTC))

        sid = StationId(uuid4())
        station = make_station_config(station_id=sid, code="HD002")
        basin = Basin(
            id=BasinId(uuid4()),
            code="HD002",
            name="HD002",
            geometry=None,
            area_km2=100.0,
            attributes=None,
            band_geometries=None,
            created_at=start,
            network="bafu",
        )

        group_store = FakeStationGroupStore()
        s_station = FakeStationStore()
        s_station.store_station(station)

        fake_model_id = ModelId("fake_model")
        fake_model = FakeStationForecastModel()

        with patch(
            "sapphire_flow.services.model_registry.discover_models",
            return_value={fake_model_id: fake_model},
        ):
            result = _run_onboarding(
                stations=[station],
                basins=[basin],
                obs_by_station={sid: []},
                forcing_by_station={sid: []},
                basin_store=FakeBasinStore(),
                station_store=s_station,
                obs_store=FakeObservationStore(),
                forcing_store=FakeHistoricalForcingStore(),
                baseline_store=FakeClimBaselineStore(),
                flow_regime_store=FakeFlowRegimeConfigStore(),
                qc_rules=QcRuleSet(version="test", rules=()),
                clock=lambda: start,
                start_utc=start,
                end_utc=end,
                model_store=FakeModelStore(),
                artifact_store=FakeModelArtifactStore(group_store=group_store),
                group_store=group_store,
                hindcast_store=FakeHindcastStore(),
                skill_store=FakeSkillStore(),
                forcing_source=FakeWeatherReanalysisSource(),
                deployment_config=make_deployment_config(),
                hindcast_days=-5,
            )
        assert any("hindcast_days must be >= 1" in e for e in result.errors)
