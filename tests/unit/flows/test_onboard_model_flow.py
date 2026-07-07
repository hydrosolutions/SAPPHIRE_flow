from __future__ import annotations

import random
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
import structlog.testing

from sapphire_flow.exceptions import ModelSmokeTestError
from sapphire_flow.flows.onboard_model import onboard_model_flow
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import OnboardingOutcome
from sapphire_flow.types.ids import (
    CLIMATOLOGY_FALLBACK_MODEL_ID,
    ArtifactId,
    ModelId,
    StationId,
)
from tests.conftest import make_deployment_config, make_station_config
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeModelArtifactStore,
    FakeModelStore,
    FakeParameterStore,
    FakeSkillStore,
    FakeStationGroupStore,
    FakeStationStore,
)

_EPOCH = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_RNG = random.Random(42)
_MODEL_ID = "fake_station_model"


def _uuid() -> UUID:
    return UUID(int=_RNG.getrandbits(128), version=4)


def _fixed_clock() -> object:
    return _EPOCH


@contextmanager
def _noop_concurrency():
    yield


def _make_stores(station_id: StationId | None = None) -> dict:
    station_store = FakeStationStore()
    if station_id is not None:
        station = make_station_config(station_id=station_id)
        station_store.store_station(station)
    # Non-None placeholders satisfy the defensive None-check block added in
    # Plan 059; downstream tasks are patched so the stores are never called.
    return {
        "model_store": FakeModelStore(),
        "station_store": station_store,
        "group_store": FakeStationGroupStore(),
        "obs_store": MagicMock(),
        "basin_store": MagicMock(),
        "artifact_store": FakeModelArtifactStore(),
        "hindcast_store": MagicMock(),
        "skill_store": FakeSkillStore(),
        "flow_regime_store": MagicMock(),
        "parameter_store": FakeParameterStore(),
        "forcing_source": None,
        "deployment_config": make_deployment_config(max_retention_days=3650),
    }


def _passing_skill_gate(artifact_id: ArtifactId) -> MagicMock:
    gate = MagicMock()
    gate.passed = True
    gate.failing_metrics = frozenset()
    gate.metric_scores = (("nse", 0.85),)
    return gate


def _failing_skill_gate(artifact_id: ArtifactId) -> MagicMock:
    gate = MagicMock()
    gate.passed = False
    gate.failing_metrics = frozenset({"nse"})
    gate.metric_scores = (("nse", 0.2),)
    return gate


def _compat_ok() -> MagicMock:
    report = MagicMock()
    report.is_compatible = True
    return report


def _compat_fail() -> MagicMock:
    report = MagicMock()
    report.is_compatible = False
    return report


def _run_flow(
    sid: StationId,
    artifact_id: ArtifactId,
    stores: dict,
    *,
    model_id: str = _MODEL_ID,
    compat: MagicMock | None = None,
    skill_gate: MagicMock | None = None,
    smoke_side_effect: Exception | None = None,
) -> tuple[object, MagicMock, MagicMock]:
    fake_model = FakeStationForecastModel()
    fake_discovered = {ModelId(model_id): fake_model}
    fake_flow_run = MagicMock()
    fake_flow_run.id = uuid4()

    compat_report = compat if compat is not None else _compat_ok()
    gate = skill_gate if skill_gate is not None else _passing_skill_gate(artifact_id)

    with (
        patch(
            "sapphire_flow.flows.onboard_model.concurrency",
            side_effect=lambda *a, **kw: _noop_concurrency(),
        ),
        patch(
            "sapphire_flow.services.model_registry.discover_models",
            return_value=fake_discovered,
        ),
        patch("prefect.runtime.flow_run", fake_flow_run),
        patch(
            "sapphire_flow.flows.onboard_model._validate_compatibility_task",
            return_value=compat_report,
        ),
        patch(
            "sapphire_flow.flows.onboard_model._smoke_test_model_task",
            side_effect=smoke_side_effect,
        ),
        patch(
            "sapphire_flow.flows.onboard_model._assemble_onboarding_data_task",
            return_value=MagicMock(),
        ),
        patch(
            "sapphire_flow.flows.onboard_model._train_and_store_artifact_task",
            return_value=(artifact_id, b"fake_artifact"),
        ),
        patch(
            "sapphire_flow.flows.onboard_model.run_hindcast_flow",
            return_value=[],
        ),
        patch(
            "sapphire_flow.flows.onboard_model.compute_skills_task",
        ) as mock_skills,
        patch(
            "sapphire_flow.flows.onboard_model._evaluate_skill_gate_task",
            return_value=gate,
        ),
        patch(
            "sapphire_flow.flows.onboard_model._promote_artifact_task"
        ) as mock_promote,
        patch(
            "sapphire_flow.flows.onboard_model._create_assignment_task"
        ) as mock_assign,
    ):
        mock_skills.map.return_value = []

        result = onboard_model_flow.fn(
            model_id=model_id,
            station_ids=[str(sid)],
            period_start="2023-01-01T00:00:00+00:00",
            period_end="2025-01-01T00:00:00+00:00",
            clock=_fixed_clock,
            rng=random.Random(0),
            **stores,
        )

    return result, mock_promote, mock_assign


class TestHappyPath:
    def test_model_promoted_and_assignment_created(self) -> None:
        sid = StationId(_uuid())
        artifact_id = ArtifactId(_uuid())
        stores = _make_stores(station_id=sid)

        result, mock_promote, mock_assign = _run_flow(sid, artifact_id, stores)

        assert result.promoted_count() == 1
        assert result.failed_count() == 0
        assert result.skipped_count() == 0
        assert len(result.units) == 1
        assert result.units[0].outcome == OnboardingOutcome.PROMOTED
        mock_promote.assert_called_once()
        mock_assign.assert_called_once()

    def test_omitted_assignment_priority_uses_canonical_fallback_priority(self) -> None:
        sid = StationId(_uuid())
        artifact_id = ArtifactId(_uuid())
        stores = _make_stores(station_id=sid)
        stores["deployment_config"] = make_deployment_config(model_priorities={})

        result, _, mock_assign = _run_flow(
            sid,
            artifact_id,
            stores,
            model_id=str(CLIMATOLOGY_FALLBACK_MODEL_ID),
        )

        assert result.promoted_count() == 1
        assert mock_assign.call_args.kwargs["assignment_priority"] == 100

    def test_model_not_found_raises_value_error(self) -> None:
        stores = _make_stores()

        with (
            patch(
                "sapphire_flow.flows.onboard_model.concurrency",
                side_effect=lambda *a, **kw: _noop_concurrency(),
            ),
            patch(
                "sapphire_flow.services.model_registry.discover_models",
                return_value={},
            ),
            patch("prefect.runtime.flow_run", MagicMock(id=uuid4())),
            pytest.raises(ValueError, match="not found in discovered models"),
        ):
            onboard_model_flow.fn(
                model_id="nonexistent_model",
                period_start="2023-01-01T00:00:00+00:00",
                period_end="2025-01-01T00:00:00+00:00",
                clock=_fixed_clock,
                **stores,
            )


class TestCompatibilityCheck:
    def test_incompatible_model_skipped_for_station(self) -> None:
        sid = StationId(_uuid())
        artifact_id = ArtifactId(_uuid())
        stores = _make_stores(station_id=sid)

        result, mock_promote, mock_assign = _run_flow(
            sid, artifact_id, stores, compat=_compat_fail()
        )

        assert result.promoted_count() == 0
        assert result.skipped_count() == 1
        assert result.units[0].outcome == OnboardingOutcome.SKIPPED_COMPAT
        assert result.units[0].artifact_id is None
        mock_promote.assert_not_called()
        mock_assign.assert_not_called()


class TestSmokeTest:
    def test_failed_smoke_test_returns_failed_outcome(self) -> None:
        sid = StationId(_uuid())
        artifact_id = ArtifactId(_uuid())
        stores = _make_stores(station_id=sid)

        result, mock_promote, mock_assign = _run_flow(
            sid,
            artifact_id,
            stores,
            smoke_side_effect=ModelSmokeTestError("under operational floor"),
        )

        assert result.failed_count() == 1
        assert result.units[0].outcome == OnboardingOutcome.FAILED_SMOKE_TEST
        assert result.units[0].artifact_id is None
        assert result.units[0].error == "under operational floor"
        mock_promote.assert_not_called()
        mock_assign.assert_not_called()


class TestSkillGate:
    def test_skill_gate_rejection_leaves_artifact_unpromoted(self) -> None:
        sid = StationId(_uuid())
        artifact_id = ArtifactId(_uuid())
        stores = _make_stores(station_id=sid)

        result, mock_promote, mock_assign = _run_flow(
            sid,
            artifact_id,
            stores,
            skill_gate=_failing_skill_gate(artifact_id),
        )

        assert result.gate_rejected_count() == 1
        assert result.promoted_count() == 0
        assert result.units[0].outcome == OnboardingOutcome.GATE_REJECTED
        mock_promote.assert_not_called()
        mock_assign.assert_not_called()


class TestIdempotency:
    def test_already_assigned_model_does_not_duplicate_assignment(self) -> None:
        sid = StationId(_uuid())
        artifact_id = ArtifactId(_uuid())
        stores = _make_stores(station_id=sid)

        result1, _, mock_assign1 = _run_flow(sid, artifact_id, stores)
        result2, _, mock_assign2 = _run_flow(sid, artifact_id, stores)

        assert result1.promoted_count() == 1
        assert result2.promoted_count() == 1
        mock_assign1.assert_called_once()
        mock_assign2.assert_called_once()


class TestStructuredLogging:
    def test_skill_gate_completed_warning_emitted_when_failed(self) -> None:
        sid = StationId(_uuid())
        artifact_id = ArtifactId(_uuid())
        stores = _make_stores(station_id=sid)
        fake_model = FakeStationForecastModel()
        fake_discovered = {ModelId(_MODEL_ID): fake_model}
        fake_flow_run = MagicMock()
        fake_flow_run.id = uuid4()
        gate = _failing_skill_gate(artifact_id)

        with (
            structlog.testing.capture_logs() as cap_logs,
            patch(
                "sapphire_flow.flows.onboard_model.concurrency",
                side_effect=lambda *a, **kw: _noop_concurrency(),
            ),
            patch(
                "sapphire_flow.services.model_registry.discover_models",
                return_value=fake_discovered,
            ),
            patch("prefect.runtime.flow_run", fake_flow_run),
            patch(
                "sapphire_flow.flows.onboard_model._validate_compatibility_task",
                return_value=_compat_ok(),
            ),
            patch("sapphire_flow.flows.onboard_model._smoke_test_model_task"),
            patch(
                "sapphire_flow.flows.onboard_model._assemble_onboarding_data_task",
                return_value=MagicMock(),
            ),
            patch(
                "sapphire_flow.flows.onboard_model._train_and_store_artifact_task",
                return_value=(artifact_id, b"fake_artifact"),
            ),
            patch(
                "sapphire_flow.flows.onboard_model.run_hindcast_flow",
                return_value=[],
            ),
            patch(
                "sapphire_flow.flows.onboard_model.compute_skills_task",
            ) as mock_skills,
            patch(
                "sapphire_flow.flows.onboard_model._evaluate_skill_gate_task",
                return_value=gate,
            ),
            patch("sapphire_flow.flows.onboard_model._promote_artifact_task"),
            patch("sapphire_flow.flows.onboard_model._create_assignment_task"),
        ):
            mock_skills.map.return_value = []

            onboard_model_flow.fn(
                model_id=_MODEL_ID,
                station_ids=[str(sid)],
                period_start="2023-01-01T00:00:00+00:00",
                period_end="2025-01-01T00:00:00+00:00",
                clock=_fixed_clock,
                rng=random.Random(0),
                **stores,
            )

        gate_logs = [
            e for e in cap_logs if e.get("event") == "model.skill_gate_completed"
        ]
        assert gate_logs, "Expected model.skill_gate_completed log entry"
        warning_logs = [e for e in gate_logs if e.get("log_level") == "warning"]
        assert warning_logs, "Expected model.skill_gate_completed at WARNING level"
        assert warning_logs[0].get("passed") is False


class TestBootstrapPath:
    def test_bootstrap_resolves_stores_when_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stores_dict = {
            "model_store": MagicMock(),
            "station_store": MagicMock(),
            "group_store": MagicMock(),
            "obs_store": MagicMock(),
            "basin_store": MagicMock(),
            "artifact_store": MagicMock(),
            "hindcast_store": MagicMock(),
            "skill_store": MagicMock(),
            "flow_regime_store": MagicMock(),
            "parameter_store": MagicMock(),
        }
        captured: dict[str, object] = {}

        def fake_setup(url: str) -> tuple[object, dict]:
            captured["url"] = url
            return (MagicMock(), stores_dict)

        monkeypatch.setenv("DATABASE_URL", "sqlite://")
        monkeypatch.setattr(
            "sapphire_flow.flows._db.setup_production_stores", fake_setup
        )
        monkeypatch.setattr(
            "sapphire_flow.services.model_registry.discover_models",
            lambda: {ModelId(_MODEL_ID): FakeStationForecastModel()},
        )

        with (
            patch(
                "sapphire_flow.flows.onboard_model.concurrency",
                side_effect=lambda *a, **kw: _noop_concurrency(),
            ),
            patch("prefect.runtime.flow_run", MagicMock(id=uuid4())),
            patch(
                "sapphire_flow.flows.onboard_model._determine_onboarding_scope_task",
                return_value=[],
            ),
            patch("sapphire_flow.flows.onboard_model.register_models") as mock_register,
        ):
            onboard_model_flow.fn(
                model_id=_MODEL_ID,
                period_start="2023-01-01T00:00:00+00:00",
                period_end="2025-01-01T00:00:00+00:00",
                clock=_fixed_clock,
                rng=random.Random(0),
            )

        assert captured["url"] == "sqlite://"
        # register_models was called with the bootstrapped model_store
        assert mock_register.called
        args, _ = mock_register.call_args
        assert args[1] is stores_dict["model_store"]
