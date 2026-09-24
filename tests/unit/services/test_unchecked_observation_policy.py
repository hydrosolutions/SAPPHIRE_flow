"""Plan 316 T2 — Plan 272 D5's consumer split, asserted end to end.

D5: an observation no QC rule examined (``QC_UNCHECKED``) still feeds
FORECASTING, and the forecast it feeds must say so (``DEGRADED`` on the
``OBSERVATION`` category). Every other consumer — alerting, skill, training,
hindcast, onboarding, calculated-station derivation, the forecast lab —
keeps excluding it.

The accepting half is driven from the STORE through the real assembler into
the real runner: a test that hand-builds the metadata would pass even if the
loader never accepted the row.
"""

from __future__ import annotations

import ast
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.services.component_derivation import (
    _USABLE_STATUSES,
    DerivedPoint,
    derive_point,
)
from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker
from sapphire_flow.services.hindcast import _assemble_hindcast_inputs
from sapphire_flow.services.input_quality import MODEL_INPUT_QC_STATUSES
from sapphire_flow.services.operational_inputs import (
    OperationalInputMetadata,
    assemble_station_operational_inputs,
)
from sapphire_flow.services.run_station_forecast import (
    StationForecastResult,
    run_station_forecast,
)
from sapphire_flow.types.calculated_station import ComponentWeight
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import ForecastQcRuleSet
from sapphire_flow.types.enums import (
    InputQualityCategory,
    InputQualityLevel,
    ModelArtifactStatus,
    ModelAssignmentStatus,
    NwpCycleSource,
    ObservationQcCoverage,
    QcStatus,
    SpatialRepresentation,
)
from sapphire_flow.types.ids import FormulaId, ModelId, StationId
from sapphire_flow.types.model import ModelDataRequirements
from sapphire_flow.types.station import ModelAssignment
from tests.conftest import make_observation, make_station_config
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeModelArtifactStore,
    FakeModelStateStore,
    FakeObservationStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)

if TYPE_CHECKING:
    from sapphire_flow.types.observation import Observation

_STEP = timedelta(hours=1)
_ISSUE = ensure_utc(datetime(2026, 1, 10, tzinfo=UTC))
_CYCLE = ensure_utc(datetime(2026, 1, 9, 23, tzinfo=UTC))
_NOW = ensure_utc(datetime(2026, 1, 10, 0, 30, tzinfo=UTC))
_MODEL_ID = ModelId("unchecked-policy-model")
_NWP_SOURCE = "icon_ch2_eps"
_LOOKBACK = 6


class _ObsOnlyModel(FakeStationForecastModel):
    """Targets only — no forcing, no statics, so the assembler's verdict turns
    on the observation read alone."""

    data_requirements: ModelDataRequirements = ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset(),
        static_features=frozenset(),
        supported_time_steps=frozenset({_STEP}),
        lookback_steps=_LOOKBACK,
        forecast_horizon_steps=3,
        spatial_input_type=SpatialRepresentation.POINT,
    )


def _clock() -> UtcDatetime:
    return _NOW


def _hourly_observations(
    station_id: StationId,
    *,
    qc_status: QcStatus,
    n: int = 12,
    issue_time: UtcDatetime = _ISSUE,
) -> list[Observation]:
    rng = random.Random(3)
    return [
        make_observation(
            station_id=station_id,
            parameter="discharge",
            value=10.0 + step,
            timestamp=ensure_utc(issue_time - (n - step) * _STEP),
            qc_status=qc_status,
            rng=rng,
        )
        for step in range(n)
    ]


def _forecast_with_observation_status(
    qc_status: QcStatus,
    *,
    issue_time: UtcDatetime = _ISSUE,
) -> StationForecastResult | None:
    """Store → assembler → runner, with every observation at ``qc_status``."""
    station_id = StationId(uuid4())
    station_store = FakeStationStore()
    station_store.store_station(make_station_config(station_id=station_id))
    obs_store = FakeObservationStore()
    obs_store.store_observations(
        _hourly_observations(station_id, qc_status=qc_status, issue_time=issue_time)
    )

    state_store = FakeModelStateStore()
    state_store.store_state(
        station_id, _MODEL_ID, ensure_utc(issue_time - _STEP), b"warm-state"
    )

    model = _ObsOnlyModel()
    assembled = assemble_station_operational_inputs(
        station_id=station_id,
        model=model,  # type: ignore[arg-type]
        model_id=_MODEL_ID,
        issue_time=issue_time,
        cycle_time=_CYCLE,
        nwp_source=_NWP_SOURCE,
        forcing_source=FakeWeatherReanalysisSource(),
        weather_forecast_store=FakeWeatherForecastStore(),
        obs_store=obs_store,
        station_store=station_store,
        basin_store=FakeBasinStore(),
        model_state_store=state_store,
        clock=_clock,
        forecast_horizon_steps=3,
        time_step=_STEP,
    )
    if assembled is None:
        return None
    inputs, metadata = assembled
    if inputs.data.past_targets.height == 0:
        return None

    artifact_store = FakeModelArtifactStore()
    artifact_store.store_artifact(
        model_id=_MODEL_ID,
        artifact_bytes=b"artifact",
        training_period_start=issue_time - timedelta(days=30),
        training_period_end=issue_time - timedelta(days=1),
        trained_at=issue_time - timedelta(days=1),
        station_id=station_id,
        status=ModelArtifactStatus.ACTIVE,
    )
    return run_station_forecast(
        station_id=station_id,
        inputs=inputs,
        input_metadata=metadata,
        assignments=[
            ModelAssignment(
                station_id=station_id,
                model_id=_MODEL_ID,
                time_step=_STEP,
                status=ModelAssignmentStatus.ACTIVE,
                priority=1,
                created_at=issue_time,
            )
        ],
        models={_MODEL_ID: model},  # type: ignore[dict-item]
        artifact_store=artifact_store,
        qc_checker=ForecastOutputQualityChecker(),
        qc_rules=ForecastQcRuleSet(version="1.0", rules=()),
        qc_overrides=[],
        baselines=[],
        nwp_cycle_reference_time=_CYCLE,
        nwp_cycle_source=NwpCycleSource.PRIMARY,
        config=DeploymentConfig(
            max_retention_days=1000,
            observation_staleness_warning_hours=6.0,
        ),
        clock=_clock,
        id_gen=lambda: UUID(int=random.Random(7).getrandbits(128)),
        rng=random.Random(11),
        model_state_store=state_store,
    )


class TestUncheckedObservationsReachForecastingAsDegraded:
    def test_unchecked_only_station_is_forecast_and_marked_degraded(self) -> None:
        result = _forecast_with_observation_status(QcStatus.QC_UNCHECKED)

        assert result is not None, "an unchecked-only station must still be forecast"
        forecast = result.forecasts[0]
        assert forecast.input_quality is InputQualityLevel.DEGRADED
        observation_flags = [
            flag
            for flag in forecast.input_quality_flags
            if flag.category is InputQualityCategory.OBSERVATION
        ]
        assert [flag.level for flag in observation_flags] == [
            InputQualityLevel.DEGRADED
        ]
        assert "unchecked" in observation_flags[0].detail.lower()

    def test_checked_station_stays_full_and_unflagged(self) -> None:
        """The control: the same station, the same window, rules having run.

        Without it a spurious staleness flag would make the test above pass
        for the wrong reason.
        """
        result = _forecast_with_observation_status(QcStatus.QC_PASSED)

        assert result is not None
        forecast = result.forecasts[0]
        assert forecast.input_quality is InputQualityLevel.FULL
        assert forecast.input_quality_flags == ()


class TestFreshnessProbeSeesTheRowsButDoesNotDegrade:
    """D5: the staleness probe accepts the status and "does not by itself
    degrade". The probe reads the trailing gap ``[past_targets_end,
    issue_time)``, which only exists when the issue time is not a bucket
    boundary."""

    _ISSUE_OFF_BOUNDARY = ensure_utc(datetime(2026, 1, 10, 0, 30, tzinfo=UTC))
    _NOW_OFF_BOUNDARY = ensure_utc(datetime(2026, 1, 10, 0, 45, tzinfo=UTC))

    def _assemble(self) -> tuple[object, OperationalInputMetadata]:
        station_id = StationId(uuid4())
        station_store = FakeStationStore()
        station_store.store_station(make_station_config(station_id=station_id))
        obs_store = FakeObservationStore()
        # Every MODEL-INPUT row is checked; only the trailing probe window
        # holds an unchecked one.
        obs_store.store_observations(
            _hourly_observations(
                station_id,
                qc_status=QcStatus.QC_PASSED,
                issue_time=ensure_utc(datetime(2026, 1, 10, tzinfo=UTC)),
            )
        )
        rng = random.Random(5)
        obs_store.store_observations(
            [
                make_observation(
                    station_id=station_id,
                    parameter="discharge",
                    value=99.0,
                    timestamp=ensure_utc(datetime(2026, 1, 10, 0, 15, tzinfo=UTC)),
                    qc_status=QcStatus.QC_UNCHECKED,
                    rng=rng,
                )
            ]
        )
        state_store = FakeModelStateStore()
        state_store.store_state(
            station_id,
            _MODEL_ID,
            ensure_utc(self._ISSUE_OFF_BOUNDARY - _STEP),
            b"warm-state",
        )
        assembled = assemble_station_operational_inputs(
            station_id=station_id,
            model=_ObsOnlyModel(),  # type: ignore[arg-type]
            model_id=_MODEL_ID,
            issue_time=self._ISSUE_OFF_BOUNDARY,
            cycle_time=_CYCLE,
            nwp_source=_NWP_SOURCE,
            forcing_source=FakeWeatherReanalysisSource(),
            weather_forecast_store=FakeWeatherForecastStore(),
            obs_store=obs_store,
            station_store=station_store,
            basin_store=FakeBasinStore(),
            model_state_store=state_store,
            clock=lambda: self._NOW_OFF_BOUNDARY,
            forecast_horizon_steps=3,
            time_step=_STEP,
        )
        assert assembled is not None
        return assembled

    def test_probe_reads_the_unchecked_trailing_row(self) -> None:
        _, metadata = self._assemble()

        # 00:45 − 00:15 = 0.5h. Without the probe seeing the unchecked row the
        # freshest reading would be 00:00, i.e. 0.75h.
        assert metadata.observation_staleness_hours == 0.5

    def test_probe_alone_leaves_the_coverage_all_checked(self) -> None:
        _, metadata = self._assemble()

        assert metadata.observation_qc_coverage is ObservationQcCoverage.ALL_CHECKED


class TestExcludingConsumersStayExcluding:
    """Plan 272 D5 and its three-further-readers table. Assert; do not edit."""

    def test_calculated_station_derivation_skips_an_unchecked_component(self) -> None:
        """272's table: **"No — with a consequence"**. A calculated station whose
        component is unchecked goes DARK, which 272 records as an accepted
        exception, not an oversight — inherited here knowingly."""
        component = StationId(uuid4())
        weight = ComponentWeight(
            id=FormulaId(uuid4()),
            calculated_station_id=StationId(uuid4()),
            component_station_id=component,
            parameter="discharge",
            weight=1.0,
            effective_from=_ISSUE,
            effective_to=None,
            created_at=_ISSUE,
        )
        unchecked = make_observation(
            station_id=component,
            parameter="discharge",
            value=42.0,
            timestamp=_ISSUE,
            qc_status=QcStatus.QC_UNCHECKED,
        )

        assert QcStatus.QC_UNCHECKED not in _USABLE_STATUSES
        assert derive_point([(weight, unchecked)]) == DerivedPoint(
            value=None, qc_status=QcStatus.MISSING, qc_flags=[]
        )

    def test_hindcast_in_memory_comparison_excludes_unchecked(self) -> None:
        """``services/hindcast.py`` re-filters an already-fetched list in
        memory, so it is not one of the store-filter sites."""
        station_id = StationId(uuid4())

        inputs = _assemble_hindcast_inputs(
            station_id=station_id,
            issue_time=_ISSUE,
            lookback_steps=_LOOKBACK,
            time_step=_STEP,
            forecast_horizon_steps=3,
            required_features=[],
            all_forcing=[],
            all_observations=_hourly_observations(
                station_id, qc_status=QcStatus.QC_UNCHECKED
            ),
            weather_sources=[],
            static_attributes=None,
        )

        assert inputs is None


def _observation_read_inventory() -> dict[str, tuple[str, ...]]:
    """Every ``ObservationStore.fetch_observations`` call in ``src/``, grouped by
    module, with the source text of each call's ``qc_status`` argument
    (``"<unfiltered>"`` when it passes none).

    An inventory rather than a grep: a NEW consumer that forgets D5's split
    shows up as an unexpected entry instead of passing silently. The
    ``>= 3 arguments`` guard drops the unrelated *adapter* method of the same
    name (``fetch_observations(station_configs, since)``).
    """
    src_root = Path(__file__).resolve().parents[3] / "src" / "sapphire_flow"
    found: dict[str, list[str]] = {}
    for path in sorted(src_root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "fetch_observations":
                continue
            if len(node.args) + len(node.keywords) < 3:
                continue
            qc_arg = next(
                (kw.value for kw in node.keywords if kw.arg == "qc_status"), None
            )
            key = str(path.relative_to(src_root.parent.parent))
            found.setdefault(key, []).append(
                "<unfiltered>" if qc_arg is None else ast.unparse(qc_arg)
            )
    return {key: tuple(sorted(value)) for key, value in found.items()}


# Plan 272 D5's split, module by module. ACCEPTING is exactly the two
# assemblers (a model-input read and a freshness probe each). Everything else
# either names a single excluding status, or passes none and filters in
# memory: `ingest_observations`/`calculated_station_onboarding` run the
# calculated-station derivation (`_USABLE_STATUSES`, which excludes
# `QC_UNCHECKED` — 272's accepted "No — with a consequence") plus the QC
# task's own RAW sweep, and `api_stations` is the caller-parameterised raw
# endpoint, which returns each row's own `qc_status` alongside it.
_EXPECTED_READ_INVENTORY: dict[str, tuple[str, ...]] = {
    "src/sapphire_flow/api/routes/api_stations.py": ("qc",),
    "src/sapphire_flow/flows/compute_skills.py": ("QcStatus.QC_PASSED",),
    "src/sapphire_flow/flows/ingest_observations.py": ("<unfiltered>", "<unfiltered>"),
    "src/sapphire_flow/services/calculated_station_onboarding.py": ("<unfiltered>",),
    "src/sapphire_flow/services/forecast_lab/db_sources.py": ("QcStatus.QC_PASSED",),
    "src/sapphire_flow/services/hindcast.py": (
        "QcStatus.QC_PASSED",
        "QcStatus.QC_PASSED",
    ),
    "src/sapphire_flow/services/observation_alert_checker.py": ("QcStatus.QC_PASSED",),
    "src/sapphire_flow/services/onboarding.py": (
        "QcStatus.QC_PASSED",
        "QcStatus.QC_PASSED",
        "QcStatus.QC_PASSED",
        "QcStatus.RAW",
    ),
    "src/sapphire_flow/services/operational_inputs.py": (
        "MODEL_INPUT_QC_STATUSES",
        "MODEL_INPUT_QC_STATUSES",
    ),
    "src/sapphire_flow/services/track_assembly.py": (
        "MODEL_INPUT_QC_STATUSES",
        "MODEL_INPUT_QC_STATUSES",
    ),
    "src/sapphire_flow/services/training_data.py": ("QcStatus.QC_PASSED",),
}


class TestTheReadInventoryMatchesD5:
    def test_the_whole_inventory_is_exactly_d5s_split(self) -> None:
        assert _observation_read_inventory() == _EXPECTED_READ_INVENTORY

    def test_the_accepting_constant_is_exactly_passed_plus_unchecked(self) -> None:
        assert (
            frozenset({QcStatus.QC_PASSED, QcStatus.QC_UNCHECKED})
            == MODEL_INPUT_QC_STATUSES
        )
