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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from sapphire_flow.config.deployment import DeploymentConfig, InputQualityConfig
from sapphire_flow.services.component_derivation import (
    _USABLE_STATUSES,
    DerivedPoint,
    derive_point,
)
from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker
from sapphire_flow.services.hindcast import _assemble_hindcast_inputs
from sapphire_flow.services.input_quality import (
    MODEL_INPUT_QC_STATUSES,
    assess_input_quality,
)
from sapphire_flow.services.operational_inputs import (
    OperationalInputMetadata,
    assemble_station_operational_inputs,
)
from sapphire_flow.services.run_station_forecast import (
    StationForecastResult,
    run_all_station_forecasts_per_track,
    run_station_forecast,
)
from sapphire_flow.services.track_assembly import (
    ReadyContext,
    assemble_assignment_inputs,
)
from sapphire_flow.types.calculated_station import ComponentWeight
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import (
    ForecastQcRuleSet,
    InputQualityFlag,
    aggregate_input_quality,
)
from sapphire_flow.types.enums import (
    InputQualityCategory,
    InputQualityLevel,
    ModelArtifactStatus,
    ModelAssignmentStatus,
    NwpCycleSource,
    ObservationQcCoverage,
    QcStatus,
    SpatialRepresentation,
    WarmUpSource,
)
from sapphire_flow.types.forcing_track import AssignmentKey, NoForcingRequired
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


@dataclass(frozen=True, kw_only=True, slots=True)
class _SeededStores:
    """One station's stores, seeded identically for BOTH routes — the two
    assemblers must be comparable, so they are never fed different data."""

    station_id: StationId
    obs_store: FakeObservationStore
    station_store: FakeStationStore
    state_store: FakeModelStateStore


def _seed_stores(
    *,
    qc_status: QcStatus,
    issue_time: UtcDatetime = _ISSUE,
    window_issue_time: UtcDatetime | None = None,
    trailing: tuple[UtcDatetime, QcStatus] | None = None,
) -> _SeededStores:
    """``window_issue_time`` anchors the observation series (it is the bucket
    boundary the rows hang off); ``trailing`` adds ONE extra row, used to put a
    reading in the freshness probe's window ``[past_targets_end, issue_time)``
    and nowhere else."""
    station_id = StationId(uuid4())
    station_store = FakeStationStore()
    station_store.store_station(make_station_config(station_id=station_id))
    obs_store = FakeObservationStore()
    obs_store.store_observations(
        _hourly_observations(
            station_id,
            qc_status=qc_status,
            issue_time=window_issue_time if window_issue_time else issue_time,
        )
    )
    if trailing is not None:
        timestamp, trailing_status = trailing
        obs_store.store_observations(
            [
                make_observation(
                    station_id=station_id,
                    parameter="discharge",
                    value=99.0,
                    timestamp=timestamp,
                    qc_status=trailing_status,
                    rng=random.Random(5),
                )
            ]
        )
    state_store = FakeModelStateStore()
    state_store.store_state(
        station_id, _MODEL_ID, ensure_utc(issue_time - _STEP), b"warm-state"
    )
    return _SeededStores(
        station_id=station_id,
        obs_store=obs_store,
        station_store=station_store,
        state_store=state_store,
    )


def _seed_artifact(
    station_id: StationId, issue_time: UtcDatetime
) -> FakeModelArtifactStore:
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
    return artifact_store


def _assignment(station_id: StationId, issue_time: UtcDatetime) -> ModelAssignment:
    return ModelAssignment(
        station_id=station_id,
        model_id=_MODEL_ID,
        time_step=_STEP,
        status=ModelAssignmentStatus.ACTIVE,
        priority=1,
        created_at=issue_time,
    )


def _config() -> DeploymentConfig:
    return DeploymentConfig(
        max_retention_days=1000,
        observation_staleness_warning_hours=6.0,
    )


def _forecast_with_observation_status(
    qc_status: QcStatus,
    *,
    issue_time: UtcDatetime = _ISSUE,
) -> StationForecastResult | None:
    """Store → assembler → runner, with every observation at ``qc_status``."""
    seeded = _seed_stores(qc_status=qc_status, issue_time=issue_time)
    station_id = seeded.station_id
    obs_store = seeded.obs_store
    station_store = seeded.station_store
    state_store = seeded.state_store

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

    return run_station_forecast(
        station_id=station_id,
        inputs=inputs,
        input_metadata=metadata,
        assignments=[_assignment(station_id, issue_time)],
        models={_MODEL_ID: model},  # type: ignore[dict-item]
        artifact_store=_seed_artifact(station_id, issue_time),
        qc_checker=ForecastOutputQualityChecker(),
        qc_rules=ForecastQcRuleSet(version="1.0", rules=()),
        qc_overrides=[],
        baselines=[],
        nwp_cycle_reference_time=_CYCLE,
        nwp_cycle_source=NwpCycleSource.PRIMARY,
        config=_config(),
        clock=_clock,
        id_gen=lambda: UUID(int=random.Random(7).getrandbits(128)),
        rng=random.Random(11),
        model_state_store=state_store,
    )


def _per_track_forecast_with_observation_status(
    qc_status: QcStatus,
    *,
    issue_time: UtcDatetime = _ISSUE,
) -> StationForecastResult | None:
    """The PER-TRACK twin of ``_forecast_with_observation_status`` — the same
    stores, the same model, the same assertions; only the assembler and the
    runner differ (``assemble_assignment_inputs`` /
    ``run_all_station_forecasts_per_track``)."""
    seeded = _seed_stores(qc_status=qc_status, issue_time=issue_time)
    station_id = seeded.station_id

    model = _ObsOnlyModel()
    ready = assemble_assignment_inputs(
        station_id=station_id,
        model_id=_MODEL_ID,
        model=model,  # type: ignore[arg-type]
        projection=NoForcingRequired(assignment=AssignmentKey((station_id, _MODEL_ID))),
        track_outcome=None,
        issue_time=issue_time,
        obs_store=seeded.obs_store,
        station_store=seeded.station_store,
        basin_store=FakeBasinStore(),
        forcing_source=FakeWeatherReanalysisSource(),
        weather_forecast_store=FakeWeatherForecastStore(),
        nwp_source=_NWP_SOURCE,
        clock=_clock,
    )
    if not isinstance(ready, ReadyContext):
        return None
    if ready.inputs.data.past_targets.height == 0:
        return None

    multi = run_all_station_forecasts_per_track(
        station_id=station_id,
        run_inputs={_MODEL_ID: ready},
        assignments=[_assignment(station_id, issue_time)],
        models={_MODEL_ID: model},  # type: ignore[dict-item]
        artifact_store=_seed_artifact(station_id, issue_time),
        qc_checker=ForecastOutputQualityChecker(),
        qc_rules=ForecastQcRuleSet(version="1.0", rules=()),
        qc_overrides=[],
        baselines=[],
        config=_config(),
        clock=_clock,
        id_gen=lambda: UUID(int=random.Random(7).getrandbits(128)),
        rng=random.Random(11),
        model_state_store=seeded.state_store,
    )
    return multi.results.get(_MODEL_ID)


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


class TestUncheckedObservationsReachTheForecastOnThePerTrackRoute:
    """The SAME deliverable as the class above, on the other production route.
    Correcting only the operational assembler would leave the per-track route
    (``run_station_forecast.py``'s ``ReadyContext`` arm) dropping the rows and
    the flag — and nothing else in the suite carries an unchecked verdict from
    ``ReadyContext`` through the runner to a forecast."""

    def test_unchecked_only_station_is_forecast_and_marked_degraded(self) -> None:
        result = _per_track_forecast_with_observation_status(QcStatus.QC_UNCHECKED)

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

    def test_checked_station_carries_no_observation_flag(self) -> None:
        """The control. ⚠️ Unlike the operational twin this cannot assert
        ``FULL``: a ``NoForcingRequired`` per-track assignment resolves to
        ``RUNOFF_ONLY``, which emits its own DEGRADED **NWP** flag on every
        forecast this route produces. Scoped to the OBSERVATION category so
        that pre-existing flag can neither hide a missing one nor fake a
        present one."""
        result = _per_track_forecast_with_observation_status(QcStatus.QC_PASSED)

        assert result is not None
        forecast = result.forecasts[0]
        assert [
            flag.category
            for flag in forecast.input_quality_flags
            if flag.category is InputQualityCategory.OBSERVATION
        ] == []
        assert {flag.category for flag in forecast.input_quality_flags} == {
            InputQualityCategory.NWP
        }


_PROBE_ISSUE = ensure_utc(datetime(2026, 1, 10, 0, 30, tzinfo=UTC))
_PROBE_NOW = ensure_utc(datetime(2026, 1, 10, 0, 45, tzinfo=UTC))
_PROBE_TRAILING = ensure_utc(datetime(2026, 1, 10, 0, 15, tzinfo=UTC))


def _probe_stores() -> _SeededStores:
    """Every MODEL-INPUT row is checked; the ONE unchecked reading sits at
    00:15, inside the probe window ``[past_targets_end, issue_time)`` and
    nowhere else — which only exists because 00:30 is not a bucket boundary."""
    return _seed_stores(
        qc_status=QcStatus.QC_PASSED,
        issue_time=_PROBE_ISSUE,
        window_issue_time=ensure_utc(datetime(2026, 1, 10, tzinfo=UTC)),
        trailing=(_PROBE_TRAILING, QcStatus.QC_UNCHECKED),
    )


class TestFreshnessProbeSeesTheRowsButDoesNotDegrade:
    """D5: the staleness probe accepts the status and "does not by itself
    degrade". Asserted on BOTH routes — the property is what makes the
    classification-before-the-probe ordering deliberate rather than accidental,
    and each assembler owns its own copy of that ordering."""

    def _assemble_operational(self) -> tuple[object, OperationalInputMetadata]:
        seeded = _probe_stores()
        assembled = assemble_station_operational_inputs(
            station_id=seeded.station_id,
            model=_ObsOnlyModel(),  # type: ignore[arg-type]
            model_id=_MODEL_ID,
            issue_time=_PROBE_ISSUE,
            cycle_time=_CYCLE,
            nwp_source=_NWP_SOURCE,
            forcing_source=FakeWeatherReanalysisSource(),
            weather_forecast_store=FakeWeatherForecastStore(),
            obs_store=seeded.obs_store,
            station_store=seeded.station_store,
            basin_store=FakeBasinStore(),
            model_state_store=seeded.state_store,
            clock=lambda: _PROBE_NOW,
            forecast_horizon_steps=3,
            time_step=_STEP,
        )
        assert assembled is not None
        return assembled

    def _assemble_per_track(self) -> ReadyContext:
        seeded = _probe_stores()
        ready = assemble_assignment_inputs(
            station_id=seeded.station_id,
            model_id=_MODEL_ID,
            model=_ObsOnlyModel(),  # type: ignore[arg-type]
            projection=NoForcingRequired(
                assignment=AssignmentKey((seeded.station_id, _MODEL_ID))
            ),
            track_outcome=None,
            issue_time=_PROBE_ISSUE,
            obs_store=seeded.obs_store,
            station_store=seeded.station_store,
            basin_store=FakeBasinStore(),
            forcing_source=FakeWeatherReanalysisSource(),
            weather_forecast_store=FakeWeatherForecastStore(),
            nwp_source=_NWP_SOURCE,
            clock=lambda: _PROBE_NOW,
        )
        assert isinstance(ready, ReadyContext)
        return ready

    def test_operational_probe_reads_the_unchecked_trailing_row(self) -> None:
        _, metadata = self._assemble_operational()

        # 00:45 − 00:15 = 0.5h. Without the probe seeing the unchecked row the
        # freshest reading would be the model-input series' last bucket at
        # 23:00, i.e. 1.75h (measured against a reverted read).
        assert metadata.observation_staleness_hours == 0.5

    def test_operational_probe_alone_leaves_the_coverage_all_checked(self) -> None:
        _, metadata = self._assemble_operational()

        assert metadata.observation_qc_coverage is ObservationQcCoverage.ALL_CHECKED

    def test_per_track_probe_reads_the_unchecked_trailing_row(self) -> None:
        ready = self._assemble_per_track()

        assert ready.observation_staleness_hours == 0.5

    def test_per_track_probe_alone_leaves_the_coverage_all_checked(self) -> None:
        ready = self._assemble_per_track()

        assert ready.observation_qc_coverage is ObservationQcCoverage.ALL_CHECKED


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


class TestACleanStationIsUnchanged:
    """The plan's "byte-identical to today". ⚠️ "FULL with no flags" is a
    WEAKER claim — it would still hold if the widened read had started
    returning different ROWS, or if the new branch had perturbed another one.
    Both are pinned here as literal values."""

    def test_a_clean_stations_rows_and_provenance_match_the_pre_316_golden(
        self,
    ) -> None:
        seeded = _seed_stores(qc_status=QcStatus.QC_PASSED)
        assembled = assemble_station_operational_inputs(
            station_id=seeded.station_id,
            model=_ObsOnlyModel(),  # type: ignore[arg-type]
            model_id=_MODEL_ID,
            issue_time=_ISSUE,
            cycle_time=_CYCLE,
            nwp_source=_NWP_SOURCE,
            forcing_source=FakeWeatherReanalysisSource(),
            weather_forecast_store=FakeWeatherForecastStore(),
            obs_store=seeded.obs_store,
            station_store=seeded.station_store,
            basin_store=FakeBasinStore(),
            model_state_store=seeded.state_store,
            clock=_clock,
            forecast_horizon_steps=3,
            time_step=_STEP,
        )
        assert assembled is not None
        inputs, metadata = assembled

        # Literal rows, not a comprehension: the claim is that the widened
        # read returns the SAME six buckets it did before, so the expectation
        # must not be derived from the same window arithmetic under test.
        assert inputs.data.past_targets.to_dicts() == [
            {
                "timestamp": ensure_utc(datetime(2026, 1, 9, 18, tzinfo=UTC)),
                "discharge": 16.0,
            },
            {
                "timestamp": ensure_utc(datetime(2026, 1, 9, 19, tzinfo=UTC)),
                "discharge": 17.0,
            },
            {
                "timestamp": ensure_utc(datetime(2026, 1, 9, 20, tzinfo=UTC)),
                "discharge": 18.0,
            },
            {
                "timestamp": ensure_utc(datetime(2026, 1, 9, 21, tzinfo=UTC)),
                "discharge": 19.0,
            },
            {
                "timestamp": ensure_utc(datetime(2026, 1, 9, 22, tzinfo=UTC)),
                "discharge": 20.0,
            },
            {
                "timestamp": ensure_utc(datetime(2026, 1, 9, 23, tzinfo=UTC)),
                "discharge": 21.0,
            },
        ]
        assert metadata == OperationalInputMetadata(
            warm_up_source=WarmUpSource.FRESH,
            warm_up_state_age_hours=1.5,
            observation_staleness_hours=1.5,
            nwp_age_hours=1.5,
            observation_qc_coverage=ObservationQcCoverage.ALL_CHECKED,
        )


# The pre-316 `(level, flags)` for a matrix of the OTHER inputs, as literals.
# `ALL_CHECKED` must reproduce each one exactly, and `CONTAINS_UNCHECKED` must
# reproduce it with the new flag PREPENDED and nothing else disturbed.
_UNCHECKED_FLAG = InputQualityFlag(
    category=InputQualityCategory.OBSERVATION,
    level=InputQualityLevel.DEGRADED,
    detail="Observations include readings no QC rule examined (qc_unchecked)",
)

_PRE_316_GOLDEN: list[tuple[str, dict[str, object], tuple[InputQualityFlag, ...]]] = [
    (
        "everything clean",
        {},
        (),
    ),
    (
        "stale observations",
        {"observation_staleness_hours": 20.0},
        (
            InputQualityFlag(
                category=InputQualityCategory.OBSERVATION,
                level=InputQualityLevel.DEGRADED,
                detail="Observations 20.0h stale (threshold: 12.0h)",
            ),
        ),
    ),
    (
        "old nwp cycle",
        {"nwp_age_hours": 10.0},
        (
            InputQualityFlag(
                category=InputQualityCategory.NWP,
                level=InputQualityLevel.PARTIAL,
                detail="NWP 10.0h stale (threshold: 9.0h)",
            ),
        ),
    ),
    (
        "cold start",
        {"warm_up_source": WarmUpSource.COLD_START, "warm_up_state_age_hours": None},
        (
            InputQualityFlag(
                category=InputQualityCategory.WARM_UP,
                level=InputQualityLevel.DEGRADED,
                detail="Cold start (no warm-up snapshot available)",
            ),
        ),
    ),
]


def _assess_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "observation_staleness_hours": 0.0,
        "warm_up_source": WarmUpSource.FRESH,
        "warm_up_state_age_hours": 1.0,
        "nwp_cycle_source": NwpCycleSource.PRIMARY,
        "nwp_age_hours": 0.0,
        "obs_partial_hours": 6.0,
        "config": InputQualityConfig(),
        "warmup_partial_hours": 24.0,
        "warmup_degraded_hours": 42.0,
    }
    return {**base, **overrides}


class TestTheNewBranchIsPurelyAdditive:
    @pytest.mark.parametrize(
        ("name", "overrides", "expected"),
        _PRE_316_GOLDEN,
        ids=[case[0] for case in _PRE_316_GOLDEN],
    )
    def test_all_checked_reproduces_the_pre_316_flags(
        self,
        name: str,
        overrides: dict[str, object],
        expected: tuple[InputQualityFlag, ...],
    ) -> None:
        level, flags = assess_input_quality(
            observation_qc_coverage=ObservationQcCoverage.ALL_CHECKED,
            **_assess_kwargs(**overrides),  # type: ignore[arg-type]
        )

        assert flags == expected
        assert level is aggregate_input_quality(list(expected))

    @pytest.mark.parametrize(
        ("name", "overrides", "expected"),
        _PRE_316_GOLDEN,
        ids=[case[0] for case in _PRE_316_GOLDEN],
    )
    def test_contains_unchecked_only_adds_its_own_flag(
        self,
        name: str,
        overrides: dict[str, object],
        expected: tuple[InputQualityFlag, ...],
    ) -> None:
        _, flags = assess_input_quality(
            observation_qc_coverage=ObservationQcCoverage.CONTAINS_UNCHECKED,
            **_assess_kwargs(**overrides),  # type: ignore[arg-type]
        )

        assert flags == (_UNCHECKED_FLAG, *expected)
