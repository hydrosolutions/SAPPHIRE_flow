"""Plan 312 — the operational forecast cycle must supply the GROUP
``station_code_resolver`` a ForecastInterface adapter needs.

``discover_models()`` wraps FI models into a ``ForecastInterfaceAdapter`` with
NO resolver (``services/model_registry.py``), and the cycle never attached one,
so a GROUP-scoped FI model could be onboarded but not served: ``predict_batch``
raised ``ConfigurationError("station_code_resolver required ...")``,
``run_group_forecast`` swallowed it and returned ``{}``, and the cycle completed
normally with nothing persisted. "The cycle passed" therefore proves nothing —
every test here asserts the ``predict_batch_failed`` log AND the persisted rows.

Deliberately NOT gated on the ``aquacast`` extra (CI installs it conditionally):
the GROUP FI model below is synthetic, built on ``forecast_interface`` types
only, the way ``tests/unit/models/test_aquacast_shim_translation.py`` is.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import polars as pl
import pytest
from structlog.testing import capture_logs

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.flows.run_forecast_cycle import run_forecast_cycle_flow
from sapphire_flow.types.enums import AlertEligibility, ModelTier, StationStatus
from sapphire_flow.types.ids import ModelId, StationId
from tests.conftest import make_station_config
from tests.fakes.fake_adapters import FakeWeatherForecastSource
from tests.fakes.fake_stores import (
    FakeAlertStore,
    FakeBasinStore,
    FakeClimBaselineStore,
    FakeForecastStore,
    FakeHistoricalForcingStore,
    FakeModelArtifactStore,
    FakeModelStateStore,
    FakeObservationStore,
    FakeStationGroupStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)
from tests.unit.flows.test_run_forecast_cycle import (
    _build_station_and_stores,
    _clock,
    _empty_qc_rules,
    _make_alerting_config,
    _store_group_run,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.types.forecast import OperationalForecast

_STEP = timedelta(hours=1)
_HORIZON = 3
_LOOKBACK = 3
_GROUP_MODEL_ID = ModelId("synthetic_group_fi_model")
_CODE_A = "gauge-a"
_CODE_B = "gauge-b"
# Distinguishable per station: station value = base + step index.
_BASE_BY_CODE = {_CODE_A: 100.0, _CODE_B: 200.0}


def _group_fi_requirement() -> fi_boundary.InputRequirement:
    return fi_boundary.InputRequirement(
        targets={
            "discharge": fi_boundary.TargetSpec(
                unit=fi_boundary.Unit.M3_PER_S,
                representations=frozenset(
                    {fi_boundary.OutputRepresentation.DETERMINISTIC}
                ),
            )
        },
        dynamic={
            _STEP: fi_boundary.SpatialInputSpec(
                data={
                    fi_boundary.FISpatialRepresentation.POINT: (
                        fi_boundary.DynamicInputSpec(
                            past_known={
                                "obs": {
                                    "discharge": fi_boundary.PastKnownVariable(
                                        lookback=_LOOKBACK,
                                        max_nan=0,
                                        unit=fi_boundary.Unit.M3_PER_S,
                                    )
                                }
                            },
                            future_known={
                                "nwp": {
                                    "precipitation": (
                                        fi_boundary.FutureKnownVariable(
                                            future_steps=_HORIZON,
                                            max_nan=0,
                                            unit=fi_boundary.Unit.MM,
                                        )
                                    ),
                                    "temperature": fi_boundary.FutureKnownVariable(
                                        future_steps=_HORIZON,
                                        max_nan=0,
                                        unit=fi_boundary.Unit.DEG_C,
                                    ),
                                }
                            },
                        )
                    )
                }
            )
        },
    )


class SyntheticGroupFIModel:
    """A GROUP-scoped FI model whose output is an exact function of the station
    code, so the persisted rows prove the code -> station-id mapping ran."""

    model_tier = ModelTier.SKILL
    alert_eligibility = AlertEligibility.SKILL_FORECAST

    def __init__(self) -> None:
        self.artifact_scope = fi_boundary.FIArtifactScope.GROUP
        self._input_requirement = _group_fi_requirement()

    @property
    def input_requirement(self) -> fi_boundary.InputRequirement:
        return self._input_requirement

    def train(
        self,
        inputs: fi_boundary.ModelInputs,
        *,
        config: object,
        rng: random.Random,
    ) -> bytes:
        return b"synthetic_group_artifact"

    def predict(
        self,
        artifact: bytes,
        *,
        inputs: fi_boundary.ModelInputs,
        issue_datetime: datetime,
        rng: random.Random,
    ) -> fi_boundary.ModelResult:
        return fi_boundary.ModelSuccess(
            output=fi_boundary.ModelOutput(
                model_name="synthetic-group-fi",
                issue_datetime=issue_datetime,
                variables={
                    station_key: {
                        "discharge": _variable_output(station_key, issue_datetime)
                    }
                    for station_key in inputs.stations
                },
            )
        )

    def serialize_artifact(self, artifact: bytes) -> bytes:
        return artifact

    def deserialize_artifact(self, raw: bytes) -> bytes:
        return raw


def _variable_output(
    station_key: str, issue_datetime: datetime
) -> fi_boundary.VariableOutput:
    base = _BASE_BY_CODE[station_key]
    frame = pl.DataFrame(
        {
            "issue_datetime": [issue_datetime] * _HORIZON,
            "datetime": [
                issue_datetime + (step + 1) * _STEP for step in range(_HORIZON)
            ],
            "value": [base + float(step) for step in range(_HORIZON)],
        }
    ).with_columns(
        pl.col("issue_datetime").cast(pl.Datetime("us", "UTC")),
        pl.col("datetime").cast(pl.Datetime("us", "UTC")),
    )
    return fi_boundary.VariableOutput(
        metadata=fi_boundary.VariableMetadata(
            unit=fi_boundary.Unit.M3_PER_S,
            timedelta=_STEP,
            forecast_horizon=_HORIZON,
            offset=0,
        ),
        deterministic=fi_boundary.DeterministicData(data=frame),
        flags=frozenset(),
        status=fi_boundary.VariableStatus.SUCCESS,
    )


def _wrapped_group_adapter(
    station_code_resolver: Callable[[StationId], str] | None = None,
) -> fi_boundary.ForecastInterfaceAdapter:
    """What ``discover_models()`` hands back: an ALREADY-WRAPPED adapter,
    carrying the discovery-copied classification attributes and (unless a
    resolver is passed) NO station-code resolver."""
    raw = SyntheticGroupFIModel()
    adapter = fi_boundary.adapt_if_fi(raw, station_code_resolver=station_code_resolver)
    assert isinstance(adapter, fi_boundary.ForecastInterfaceAdapter)
    adapter.model_tier = raw.model_tier  # type: ignore[attr-defined]
    adapter.alert_eligibility = raw.alert_eligibility  # type: ignore[attr-defined]
    return adapter


class _Stores:
    def __init__(self) -> None:
        self.station_store = FakeStationStore()
        self.obs_store = FakeObservationStore()
        self.nwp_store = FakeWeatherForecastStore()
        self.artifact_store = FakeModelArtifactStore()
        self.forecast_store = FakeForecastStore()
        self.state_store = FakeModelStateStore()
        self.alert_store = FakeAlertStore()
        self.baseline_store = FakeClimBaselineStore()
        self.basin_store = FakeBasinStore()
        self.group_store = FakeStationGroupStore()
        self.forcing_store = FakeHistoricalForcingStore()


def _seed_group_of_two() -> tuple[_Stores, StationId, StationId]:
    stores = _Stores()
    sid_a = StationId(uuid4())
    sid_b = StationId(uuid4())
    for sid, code in ((sid_a, _CODE_A), (sid_b, _CODE_B)):
        _build_station_and_stores(
            sid,
            _GROUP_MODEL_ID,
            stores.station_store,
            stores.obs_store,
            stores.nwp_store,
            stores.artifact_store,
            stores.forcing_store,
            seed_model_assignment=False,
            seed_artifact=False,
        )
        # Distinct station codes: the mapping the resolver exists for. The
        # shared builder gives every station the same default code.
        stores.station_store.store_station(
            make_station_config(
                station_id=sid,
                code=code,
                station_status=StationStatus.OPERATIONAL,
                measured_parameters=frozenset({"discharge"}),
                forecast_targets=frozenset({"discharge"}),
            )
        )
    _store_group_run(
        stores.group_store,
        stores.artifact_store,
        _GROUP_MODEL_ID,
        frozenset({sid_a, sid_b}),
    )
    return stores, sid_a, sid_b


def _run_cycle(
    stores: _Stores,
    *,
    models: dict[ModelId, object] | None,
    monkeypatch: pytest.MonkeyPatch | None = None,
    discovered: dict[ModelId, object] | None = None,
) -> list[dict[str, Any]]:
    if discovered is not None:
        assert monkeypatch is not None
        monkeypatch.setattr(
            "sapphire_flow.services.model_registry.discover_models",
            lambda: discovered,
        )
    with capture_logs() as events:
        run_forecast_cycle_flow(
            station_store=stores.station_store,
            obs_store=stores.obs_store,
            weather_forecast_store=stores.nwp_store,
            forecast_store=stores.forecast_store,
            model_state_store=stores.state_store,
            artifact_store=stores.artifact_store,
            alert_store=stores.alert_store,
            baseline_store=stores.baseline_store,
            basin_store=stores.basin_store,
            group_store=stores.group_store,
            forcing_store=stores.forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models=models,  # type: ignore[arg-type]
            config=_make_alerting_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )
    return events


def _group_rows(stores: _Stores) -> list[OperationalForecast]:
    return [
        forecast
        for forecast in stores.forecast_store._forecasts.values()  # noqa: SLF001
        if forecast.model_id == _GROUP_MODEL_ID
    ]


def _predict_batch_failures(events: list[dict[str, Any]]) -> list[str]:
    return [
        str(event.get("error"))
        for event in events
        if event.get("event") == "run_group_forecast.predict_batch_failed"
    ]


def _assert_two_stations_forecast(
    stores: _Stores, sid_a: StationId, sid_b: StationId, events: list[dict[str, Any]]
) -> None:
    """The fault is SILENT — `run_group_forecast` catches the resolver error and
    returns {}, so the cycle completes either way. Both halves are asserted: the
    `predict_batch_failed` log must be empty AND the rows must be there."""
    failures = _predict_batch_failures(events)
    rows = _group_rows(stores)
    assert failures == [], (
        f"run_group_forecast.predict_batch_failed: {failures}; "
        f"group forecast rows persisted: {len(rows)}"
    )
    assert {row.station_id for row in rows} == {sid_a, sid_b}

    values_by_station = {
        row.station_id: sorted(
            row.ensemble.values.sort("valid_time")["value"].to_list()
        )
        for row in rows
    }
    expected = [_BASE_BY_CODE[_CODE_A] + float(s) for s in range(_HORIZON)]
    assert values_by_station[sid_a] == expected
    assert values_by_station[sid_b] == [
        _BASE_BY_CODE[_CODE_B] + float(s) for s in range(_HORIZON)
    ]


class TestGroupFiResolverInTheForecastCycle:
    def test_discovered_wrapped_group_adapter_without_resolver_is_served(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Acceptance case (a): the DISCOVERED route. discover_models() already
        wraps FI models, with no resolver — a guard that skipped already-wrapped
        adapters would skip exactly this case."""
        stores, sid_a, sid_b = _seed_group_of_two()
        adapter = _wrapped_group_adapter()

        events = _run_cycle(
            stores,
            models=None,
            monkeypatch=monkeypatch,
            discovered={_GROUP_MODEL_ID: adapter},
        )

        _assert_two_stations_forecast(stores, sid_a, sid_b, events)

    def test_caller_supplied_wrapped_group_adapter_without_resolver_is_served(
        self,
    ) -> None:
        """Acceptance case (b): the caller-supplied route — injected models skip
        discovery entirely and reach the same GROUP dispatch."""
        stores, sid_a, sid_b = _seed_group_of_two()
        adapter = _wrapped_group_adapter()

        events = _run_cycle(stores, models={_GROUP_MODEL_ID: adapter})

        _assert_two_stations_forecast(stores, sid_a, sid_b, events)

    def test_resolver_rejects_unknown_station_and_empty_code(self) -> None:
        """The relocated builder's own failure modes still raise clearly."""
        from sapphire_flow.exceptions import ConfigurationError
        from sapphire_flow.services.model_registry import build_station_code_resolver

        station_store = FakeStationStore()
        known = StationId(uuid4())
        blank = StationId(uuid4())
        station_store.store_station(make_station_config(station_id=known, code="abc"))
        station_store.store_station(make_station_config(station_id=blank, code="  "))
        resolve = build_station_code_resolver(station_store)

        assert resolve(known) == "abc"
        with pytest.raises(ConfigurationError, match="could not resolve station_id"):
            resolve(StationId(uuid4()))
        with pytest.raises(ConfigurationError, match="without a code"):
            resolve(blank)
