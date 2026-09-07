from __future__ import annotations

import random
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import polars as pl
import pytest

from sapphire_flow.config.forecast_qc_rules import (
    _default_swiss_forecast_qc_rules,  # pyright: ignore[reportPrivateUsage]
)
from sapphire_flow.services.forecast_combination import (
    _BMA_TARGET_MEMBERS,
    build_combined_forecasts,
    combine_ensembles_bma,
    combine_ensembles_pooled,
)
from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker
from sapphire_flow.services.run_station_forecast import (
    MultiModelForecastResult,
    StationForecastResult,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import (
    ForecastQcRuleParams,
    ForecastQcRuleSet,
    InputQualityFlag,
)
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    ForecastStatus,
    InputQualityCategory,
    InputQualityLevel,
    ModelCombinationStrategy,
    NwpCycleSource,
    QcStatus,
)
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.ids import (
    BMA_MODEL_ID,
    POOLED_MODEL_ID,
    ArtifactId,
    ForecastId,
    ModelId,
    StationId,
)
from tests.conftest import make_forecast_ensemble

_STATION = StationId(uuid4())
_MODEL_A = ModelId("model-a")
_MODEL_B = ModelId("model-b")
_MODEL_C = ModelId("model-c")
_NOW = ensure_utc(datetime(2025, 6, 1, 6, 0, tzinfo=UTC))


def _clock() -> object:
    return _NOW


def _qc_checker() -> ForecastOutputQualityChecker:
    return ForecastOutputQualityChecker()


def _empty_qc_rules() -> ForecastQcRuleSet:
    return ForecastQcRuleSet(version="1.0", rules=())


def _discharge_range_qc_rules(
    *,
    value_min: float = 0.0,
    value_max: float = 1000.0,
    time_step: timedelta = timedelta(hours=1),
) -> ForecastQcRuleSet:
    """A REAL forecast QC rule (`range_check`), the same rule
    `config/forecast_qc_rules.py` declares in production — not a mocked
    checker verdict (Plan 246 exit gate 4).

    Every test whose combination is EXPECTED to persist must pass a rule
    set that covers the step that combination lands on: since the Plan 246
    review fix, `build_combined_forecasts` refuses to persist a combination
    whose `(parameter, time_step)` selects no rules at all, because
    `worst_qc_status([])` would otherwise report an unchecked combination
    as QC_PASSED. `_empty_qc_rules()` therefore now means "nothing is
    publishable", and only tests that return before QC still use it.
    """
    return ForecastQcRuleSet(
        version="1.0",
        rules=(
            ForecastQcRuleParams(
                rule_id="range_check",
                rule_version="1.0",
                parameter="discharge",
                time_step=time_step,
                thresholds={"value_min": value_min, "value_max": value_max},
            ),
        ),
    )


def _water_level_range_qc_rules(
    *, value_min: float = -2.0, value_max: float = 20.0
) -> ForecastQcRuleSet:
    return ForecastQcRuleSet(
        version="1.0",
        rules=(
            ForecastQcRuleParams(
                rule_id="range_check",
                rule_version="1.0",
                parameter="water_level",
                time_step=timedelta(hours=1),
                thresholds={"value_min": value_min, "value_max": value_max},
            ),
        ),
    )


def _uuid_seq() -> object:
    ids = [uuid4() for _ in range(20)]
    idx = [0]

    def gen() -> UUID:
        val = ids[idx[0]]
        idx[0] += 1
        return val

    return gen


def _make_result(
    model_id: ModelId,
    params: list[str] | None = None,
    representation: EnsembleRepresentation = EnsembleRepresentation.MEMBERS,
    n_members: int = 5,
) -> StationForecastResult:
    params = params or ["discharge"]
    ensembles = {
        p: make_forecast_ensemble(
            station_id=_STATION,
            representation=representation,
            n_members=n_members,
            n_steps=10,
            parameter=p,
            model_id=model_id,
        )
        for p in params
    }
    return StationForecastResult(
        station_id=_STATION,
        model_id=model_id,
        artifact_id=ArtifactId(uuid4()),
        forecasts=[],
        new_state=None,
        ensembles=ensembles,
    )


def _make_multi(
    results: dict[ModelId, StationForecastResult],
    priorities: dict[ModelId, int] | None = None,
) -> MultiModelForecastResult:
    priorities = priorities or {mid: 1 for mid in results}
    return MultiModelForecastResult(
        station_id=_STATION,
        results=results,
        priorities=priorities,
        primary_model_id=next(iter(results), None),
        failed_models={},
    )


def _members_ensemble_at(
    *,
    model_id: ModelId,
    valid_times: list[UtcDatetime],
    n_members: int,
    parameter: str = "discharge",
) -> ForecastEnsemble:
    """Plan 222 T3 — unlike `tests.conftest.make_forecast_ensemble` (always
    hourly steps from a shared epoch, so every fixture sits on the same
    grid), this places an ensemble at an EXPLICIT `valid_times` set so a
    test can put two models on disjoint or partially-overlapping grids —
    the exact shape `combine_ensembles_pooled` never saw before Plan 222."""
    rows = [
        {"valid_time": vt, "member_id": m, "value": float(m) + 1.0}
        for vt in valid_times
        for m in range(n_members)
    ]
    df = pl.DataFrame(rows).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
        pl.col("member_id").cast(pl.Int32),
    )
    return ForecastEnsemble.from_members(
        station_id=_STATION,
        issued_at=_NOW,
        parameter=parameter,
        units="m3/s",
        time_step=timedelta(hours=1),
        values=df,
        model_id=model_id,
    )


def _members_ensemble_ragged(
    *,
    model_id: ModelId,
    member_counts: dict[UtcDatetime, int],
    parameter: str = "discharge",
) -> ForecastEnsemble:
    """Plan 222 fixer round — unlike `_members_ensemble_at` (every
    `valid_time` carries the FULL `n_members` set), this drops the
    highest-numbered members at specific timestamps per `member_counts`, so
    a single contributor can present a full member set at some timestamps
    and a short one at others — the ragged-member shape the T3 fix's
    presence-only intersection let through."""
    rows = [
        {"valid_time": vt, "member_id": m, "value": float(m) + 1.0}
        for vt, count in member_counts.items()
        for m in range(count)
    ]
    df = pl.DataFrame(rows).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
        pl.col("member_id").cast(pl.Int32),
    )
    return ForecastEnsemble.from_members(
        station_id=_STATION,
        issued_at=_NOW,
        parameter=parameter,
        units="m3/s",
        time_step=timedelta(hours=1),
        values=df,
        model_id=model_id,
    )


def _member_forecast(
    model_id: ModelId,
    ensemble: ForecastEnsemble,
    *,
    input_quality: InputQualityLevel | None,
    input_quality_flags: tuple[InputQualityFlag, ...] = (),
) -> OperationalForecast:
    return OperationalForecast(
        id=ForecastId(uuid4()),
        station_id=_STATION,
        model_id=model_id,
        model_artifact_id=ArtifactId(uuid4()),
        issued_at=_NOW,
        nwp_cycle_reference_time=_NOW,
        nwp_cycle_source=NwpCycleSource.PRIMARY,
        representation=ensemble.representation,
        status=ForecastStatus.RAW,
        version=1,
        warm_up_source=None,
        warm_up_state_age_hours=None,
        observation_staleness_hours=None,
        ensemble=ensemble,
        created_at=_NOW,
        updated_at=_NOW,
        input_quality=input_quality,
        input_quality_flags=input_quality_flags,
    )


def _result_with_ensemble(
    model_id: ModelId,
    ensemble: ForecastEnsemble,
    parameter: str = "discharge",
    *,
    input_quality: InputQualityLevel | None = None,
    input_quality_flags: tuple[InputQualityFlag, ...] = (),
) -> StationForecastResult:
    forecasts = (
        [
            _member_forecast(
                model_id,
                ensemble,
                input_quality=input_quality,
                input_quality_flags=input_quality_flags,
            )
        ]
        if input_quality is not None
        else []
    )
    return StationForecastResult(
        station_id=_STATION,
        model_id=model_id,
        artifact_id=ArtifactId(uuid4()),
        forecasts=forecasts,
        new_state=None,
        ensembles={parameter: ensemble},
    )


class TestCombineEnsemblesPooled:
    def test_two_members_ensembles_merged(self) -> None:
        rng_a = random.Random(1)
        rng_b = random.Random(2)
        ens_a = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=5,
            n_steps=10,
            model_id=_MODEL_A,
            rng=rng_a,
        )
        ens_b = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=3,
            n_steps=10,
            model_id=_MODEL_B,
            rng=rng_b,
        )
        result = combine_ensembles_pooled(
            {_MODEL_A: {"discharge": ens_a}, _MODEL_B: {"discharge": ens_b}}
        )

        assert "discharge" in result
        combined = result["discharge"]

        # Member count == sum of inputs
        assert combined.member_count == 8  # 5 + 3
        assert combined.model_id == POOLED_MODEL_ID

        # Combined mean ≈ weighted average of input means (weighted by member count)
        mean_a = ens_a.values["value"].mean()
        mean_b = ens_b.values["value"].mean()
        expected_mean = (5 * mean_a + 3 * mean_b) / 8
        actual_mean = combined.values["value"].mean()
        assert actual_mean == pytest.approx(expected_mean, rel=1e-6)

        # Combined range spans both input ranges
        min_a = ens_a.values["value"].min()
        max_a = ens_a.values["value"].max()
        min_b = ens_b.values["value"].min()
        max_b = ens_b.values["value"].max()
        combined_min = combined.values["value"].min()
        combined_max = combined.values["value"].max()
        assert combined_min <= min(min_a, min_b) + 1e-9
        assert combined_max >= max(max_a, max_b) - 1e-9

    def test_quantiles_model_skipped(self) -> None:
        """A quantiles-representation contributor is excluded from the
        pool. A third MEMBERS model (`_MODEL_C`) keeps the surviving
        contributor count at the D2 floor of two, on the same n_steps=10
        grid as `_MODEL_A` — otherwise this would collapse into
        `test_pooled_drops_parameter_below_two_contributors` below."""
        ens_members_a = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=5,
            n_steps=10,
            model_id=_MODEL_A,
        )
        ens_members_c = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=4,
            n_steps=10,
            model_id=_MODEL_C,
        )
        ens_quantiles = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.QUANTILES,
            n_members=9,
            n_steps=10,
            model_id=_MODEL_B,
        )
        result = combine_ensembles_pooled(
            {
                _MODEL_A: {"discharge": ens_members_a},
                _MODEL_B: {"discharge": ens_quantiles},
                _MODEL_C: {"discharge": ens_members_c},
            }
        )

        assert "discharge" in result
        combined = result["discharge"]
        # 5 + 4 members from models A and C (quantiles model skipped)
        assert combined.member_count == 9

    def test_pooled_drops_parameter_below_two_contributors(self) -> None:
        """D2 (Plan 222 T3) — a parameter with only one MEMBERS contributor
        is not published at all, not pooled on its own. Pre-Plan-222 this
        published a single-model 'pool'."""
        ens_members = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=5,
            n_steps=10,
            model_id=_MODEL_A,
        )
        ens_quantiles = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.QUANTILES,
            n_members=9,
            n_steps=10,
            model_id=_MODEL_B,
        )
        result = combine_ensembles_pooled(
            {
                _MODEL_A: {"discharge": ens_members},
                _MODEL_B: {"discharge": ens_quantiles},
            }
        )

        assert result == {}

    def test_two_parameters_both_in_result(self) -> None:
        ens_a_q = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=4,
            n_steps=10,
            parameter="discharge",
            model_id=_MODEL_A,
        )
        ens_a_wl = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=4,
            n_steps=10,
            parameter="water_level",
            model_id=_MODEL_A,
        )
        ens_b_q = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=4,
            n_steps=10,
            parameter="discharge",
            model_id=_MODEL_B,
        )
        ens_b_wl = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=4,
            n_steps=10,
            parameter="water_level",
            model_id=_MODEL_B,
        )
        result = combine_ensembles_pooled(
            {
                _MODEL_A: {"discharge": ens_a_q, "water_level": ens_a_wl},
                _MODEL_B: {"discharge": ens_b_q, "water_level": ens_b_wl},
            }
        )

        assert set(result.keys()) == {"discharge", "water_level"}
        assert result["discharge"].member_count == 8
        assert result["water_level"].member_count == 8

    def test_empty_input_returns_empty(self) -> None:
        result = combine_ensembles_pooled({})
        assert result == {}


class TestCombineEnsemblesBma:
    def test_two_models_weighted(self) -> None:
        rng_a = random.Random(10)
        rng_b = random.Random(20)
        ens_a = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=50,
            n_steps=10,
            model_id=_MODEL_A,
            rng=rng_a,
        )
        ens_b = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=50,
            n_steps=10,
            model_id=_MODEL_B,
            rng=rng_b,
        )
        weights = {_MODEL_A: 0.7, _MODEL_B: 0.3}
        result = combine_ensembles_bma(
            {_MODEL_A: {"discharge": ens_a}, _MODEL_B: {"discharge": ens_b}},
            weights=weights,
        )

        assert "discharge" in result
        combined = result["discharge"]
        assert combined.model_id == BMA_MODEL_ID

        # BMA member count == 100 (the _BMA_TARGET_MEMBERS constant)
        assert combined.member_count == _BMA_TARGET_MEMBERS

        # BMA mean ≈ weighted sum of model means (within 10% relative tolerance)
        mean_a = ens_a.values["value"].mean()
        mean_b = ens_b.values["value"].mean()
        expected_mean = 0.7 * mean_a + 0.3 * mean_b
        actual_mean = combined.values["value"].mean()
        assert actual_mean == pytest.approx(expected_mean, rel=0.1)

    def test_member_count_equals_target(self) -> None:
        ens_a = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=50,
            n_steps=5,
            model_id=_MODEL_A,
        )
        ens_b = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=50,
            n_steps=5,
            model_id=_MODEL_B,
        )
        result = combine_ensembles_bma(
            {_MODEL_A: {"discharge": ens_a}, _MODEL_B: {"discharge": ens_b}},
            weights={_MODEL_A: 0.6, _MODEL_B: 0.4},
        )

        assert result["discharge"].member_count == _BMA_TARGET_MEMBERS

    def test_zero_weight_model_excluded(self) -> None:
        ens_a = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=50,
            n_steps=10,
            model_id=_MODEL_A,
        )
        ens_b = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=50,
            n_steps=10,
            model_id=_MODEL_B,
        )
        ens_c = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=50,
            n_steps=10,
            model_id=_MODEL_C,
        )
        result = combine_ensembles_bma(
            {
                _MODEL_A: {"discharge": ens_a},
                _MODEL_B: {"discharge": ens_b},
                _MODEL_C: {"discharge": ens_c},
            },
            weights={_MODEL_A: 0.6, _MODEL_B: 0.4, _MODEL_C: 0.0},
        )

        assert "discharge" in result
        # Total still equals target, model C excluded
        assert result["discharge"].member_count == _BMA_TARGET_MEMBERS

    def test_all_zero_weight_returns_empty(self) -> None:
        ens_a = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=5,
            n_steps=5,
            model_id=_MODEL_A,
        )
        result = combine_ensembles_bma(
            {_MODEL_A: {"discharge": ens_a}},
            weights={_MODEL_A: 0.0},
        )
        assert result == {}

    def test_quantiles_model_skipped(self) -> None:
        ens_members = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.MEMBERS,
            n_members=50,
            n_steps=10,
            model_id=_MODEL_A,
        )
        ens_quantiles = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.QUANTILES,
            n_members=9,
            n_steps=10,
            model_id=_MODEL_B,
        )
        # model_b has non-zero weight but only quantiles — should be skipped
        result = combine_ensembles_bma(
            {
                _MODEL_A: {"discharge": ens_members},
                _MODEL_B: {"discharge": ens_quantiles},
            },
            weights={_MODEL_A: 0.7, _MODEL_B: 0.3},
        )

        assert "discharge" in result
        assert result["discharge"].model_id == BMA_MODEL_ID


class TestCombineEnsemblesPooledGridAlignment:
    """Plan 222 T3 (D2) — pooling on the `valid_time` INTERSECTION, not the
    union. Reproduces the reported `_pooled` sawtooth: two models on
    disjoint or partially-overlapping grids used to concatenate into a
    union ensemble whose member count (and therefore the median)
    alternated timestamp to timestamp
    (`docs/plans/222-pooled-grid-alignment.md`)."""

    def test_disjoint_grids_drop_the_parameter(self) -> None:
        """Today: a union ensemble with an alternating member count — the
        reported defect. After the fix: the parameter is absent entirely,
        since the intersection is empty."""
        vts_a = [ensure_utc(_NOW + timedelta(hours=h)) for h in (1, 2, 3)]
        vts_b = [ensure_utc(_NOW + timedelta(hours=h, minutes=1)) for h in (1, 2, 3)]
        ens_a = _members_ensemble_at(model_id=_MODEL_A, valid_times=vts_a, n_members=5)
        ens_b = _members_ensemble_at(model_id=_MODEL_B, valid_times=vts_b, n_members=3)

        result = combine_ensembles_pooled(
            {_MODEL_A: {"discharge": ens_a}, _MODEL_B: {"discharge": ens_b}}
        )

        assert "discharge" not in result

    def test_partially_overlapping_grids_pool_only_the_intersection(self) -> None:
        """Proves intersection rather than all-or-nothing suppression: the
        3 shared timestamps are pooled, each with the FULL 8-member
        contributor count — never an alternating one."""
        vts_a = [ensure_utc(_NOW + timedelta(hours=h)) for h in (1, 2, 3, 4)]
        vts_b = [ensure_utc(_NOW + timedelta(hours=h)) for h in (2, 3, 4, 5)]
        ens_a = _members_ensemble_at(model_id=_MODEL_A, valid_times=vts_a, n_members=5)
        ens_b = _members_ensemble_at(model_id=_MODEL_B, valid_times=vts_b, n_members=3)

        result = combine_ensembles_pooled(
            {_MODEL_A: {"discharge": ens_a}, _MODEL_B: {"discharge": ens_b}}
        )

        assert "discharge" in result
        combined = result["discharge"]
        overlap = {ensure_utc(_NOW + timedelta(hours=h)) for h in (2, 3, 4)}
        assert set(combined.values["valid_time"].to_list()) == overlap
        for vt in overlap:
            n_rows = combined.values.filter(pl.col("valid_time") == vt).height
            assert n_rows == 8

    def test_three_contributors_intersect_all_three_not_just_the_first_two(
        self,
    ) -> None:
        """The reported incident involved THREE ensembles, not two — an
        implementation that only intersects its first two contributors
        (e.g. accumulating pairwise but stopping early, or ignoring a
        third model entirely) would still pass every two-model test above.
        Model C narrows the A/B intersection {t2,t3,t4} down to {t3,t4}."""
        vts_a = [ensure_utc(_NOW + timedelta(hours=h)) for h in (1, 2, 3, 4)]
        vts_b = [ensure_utc(_NOW + timedelta(hours=h)) for h in (2, 3, 4, 5)]
        vts_c = [ensure_utc(_NOW + timedelta(hours=h)) for h in (3, 4, 5, 6)]
        ens_a = _members_ensemble_at(model_id=_MODEL_A, valid_times=vts_a, n_members=5)
        ens_b = _members_ensemble_at(model_id=_MODEL_B, valid_times=vts_b, n_members=3)
        ens_c = _members_ensemble_at(model_id=_MODEL_C, valid_times=vts_c, n_members=4)

        result = combine_ensembles_pooled(
            {
                _MODEL_A: {"discharge": ens_a},
                _MODEL_B: {"discharge": ens_b},
                _MODEL_C: {"discharge": ens_c},
            }
        )

        assert "discharge" in result
        combined = result["discharge"]
        expected = {ensure_utc(_NOW + timedelta(hours=h)) for h in (3, 4)}
        assert set(combined.values["valid_time"].to_list()) == expected
        for vt in expected:
            n_rows = combined.values.filter(pl.col("valid_time") == vt).height
            assert n_rows == 12
        assert combined.values.height == 24

    def test_ragged_contributor_is_dropped_not_pooled_incomplete(self) -> None:
        """`_MODEL_A` presents its FULL 5-member set at t1 and t2, but only
        4 members at t3 (one member's row is simply missing — the ragged
        shape a presence-only `valid_time` intersection let through:
        `_MODEL_A` still 'has' t3, just incompletely). Pooling on t3 would
        reproduce the sawtooth (9 rows there vs. 10 at t1/t2); the
        timestamp must be dropped from the intersection instead."""
        t1, t2, t3 = (ensure_utc(_NOW + timedelta(hours=h)) for h in (1, 2, 3))
        ens_a = _members_ensemble_ragged(
            model_id=_MODEL_A,
            member_counts={t1: 5, t2: 5, t3: 4},
        )
        ens_b = _members_ensemble_at(
            model_id=_MODEL_B, valid_times=[t1, t2, t3], n_members=5
        )

        result = combine_ensembles_pooled(
            {_MODEL_A: {"discharge": ens_a}, _MODEL_B: {"discharge": ens_b}}
        )

        assert "discharge" in result
        combined = result["discharge"]
        assert set(combined.values["valid_time"].to_list()) == {t1, t2}
        for vt in (t1, t2):
            n_rows = combined.values.filter(pl.col("valid_time") == vt).height
            assert n_rows == 10

    def test_duplicate_member_row_drops_that_timestamp_not_pooled_overweighted(
        self,
    ) -> None:
        """D2 — completeness is counted BOTH ways (row count AND
        `n_unique(member_id)`), each independently able to catch a defect
        the other misses. At t1, member 1's row is dropped and member 0's
        row is duplicated in its place: still 5 rows total (row count
        alone says "complete"), but only 4 unique member ids — `{0, 2, 3,
        4}` — with member 1 silently missing (`n_unique` alone says
        "incomplete", but only by comparing against the wrong signal if a
        row-count check is skipped). A check that only counts rows would
        wrongly accept t1 as complete and let `_ensemble_points()`
        overweight member 0's duplicated value while silently dropping
        member 1 — neither `ForecastEnsemble.from_members` nor the
        database enforces uniqueness of `(valid_time, member_id)`. t2
        carries the model's full, unique `{0..4}` set (establishing the
        contributor's true 5-member total) and has no duplicate, so it
        must stay; t1 must be dropped from the intersection."""
        t1, t2 = (ensure_utc(_NOW + timedelta(hours=h)) for h in (1, 2))
        ens_a = _members_ensemble_at(
            model_id=_MODEL_A, valid_times=[t1, t2], n_members=5
        )
        without_member_1_at_t1 = ens_a.values.filter(
            ~((pl.col("valid_time") == t1) & (pl.col("member_id") == 1))
        )
        duplicate_member_0_at_t1 = ens_a.values.filter(
            (pl.col("valid_time") == t1) & (pl.col("member_id") == 0)
        )
        ens_a = replace(
            ens_a,
            values=pl.concat([without_member_1_at_t1, duplicate_member_0_at_t1]),
        )
        # t1 now has 5 rows (row count matches the full 5-member total) but
        # only 4 unique member ids — the shape a row-count-only check would
        # wrongly accept.
        at_t1 = ens_a.values.filter(pl.col("valid_time") == t1)
        assert at_t1.height == 5
        assert at_t1["member_id"].n_unique() == 4
        ens_b = _members_ensemble_at(
            model_id=_MODEL_B, valid_times=[t1, t2], n_members=5
        )

        result = combine_ensembles_pooled(
            {_MODEL_A: {"discharge": ens_a}, _MODEL_B: {"discharge": ens_b}}
        )

        assert "discharge" in result
        combined = result["discharge"]
        assert set(combined.values["valid_time"].to_list()) == {t2}
        assert combined.values.filter(pl.col("valid_time") == t2).height == 10

    def test_extra_duplicate_row_drops_that_timestamp_even_with_all_members_unique(
        self,
    ) -> None:
        """D2's other half — the previous test (5 rows / 4 unique member
        ids) locks the `n_unique(member_id)` check, but an implementation
        that checks ONLY `n_unique` (never comparing row COUNT against the
        contributor's member total) would still pass it, since `n_unique`
        alone already flags that shape as incomplete. This test locks the
        row-count check independently: at t1, member 0's row is
        duplicated IN ADDITION to its own row (not in its place) — so all
        5 members `{0..4}` are present and unique, but there are 6 rows.
        `n_unique(member_id) == 5` says "complete"; only the row-count
        comparison (`n_rows == 5`) catches the extra row and drops t1.
        t2 carries the model's full, unique, non-duplicated `{0..4}` set
        and must stay in the intersection."""
        t1, t2 = (ensure_utc(_NOW + timedelta(hours=h)) for h in (1, 2))
        ens_a = _members_ensemble_at(
            model_id=_MODEL_A, valid_times=[t1, t2], n_members=5
        )
        duplicate_member_0_at_t1 = ens_a.values.filter(
            (pl.col("valid_time") == t1) & (pl.col("member_id") == 0)
        )
        ens_a = replace(
            ens_a,
            values=pl.concat([ens_a.values, duplicate_member_0_at_t1]),
        )
        # t1 now has 6 rows but still all 5 unique member ids — the shape
        # an n_unique-only check would wrongly accept.
        at_t1 = ens_a.values.filter(pl.col("valid_time") == t1)
        assert at_t1.height == 6
        assert at_t1["member_id"].n_unique() == 5
        ens_b = _members_ensemble_at(
            model_id=_MODEL_B, valid_times=[t1, t2], n_members=5
        )

        result = combine_ensembles_pooled(
            {_MODEL_A: {"discharge": ens_a}, _MODEL_B: {"discharge": ens_b}}
        )

        assert "discharge" in result
        combined = result["discharge"]
        assert set(combined.values["valid_time"].to_list()) == {t2}
        assert combined.values.filter(pl.col("valid_time") == t2).height == 10

    def test_single_shared_timestamp_pools_directly_but_is_not_persisted(
        self,
    ) -> None:
        """D6 — a single shared timestamp is a legal `combine_ensembles_pooled`
        result (the skill combiner calls the same function per hindcast
        step, where one timestamp is normal) but must never reach
        `build_combined_forecasts()`'s output: the store would fabricate a
        one-hour `time_step` for it — a fresh instance of the sawtooth
        defect."""
        vts = [ensure_utc(_NOW + timedelta(hours=1))]
        ens_a = _members_ensemble_at(model_id=_MODEL_A, valid_times=vts, n_members=5)
        ens_b = _members_ensemble_at(model_id=_MODEL_B, valid_times=vts, n_members=3)

        direct = combine_ensembles_pooled(
            {_MODEL_A: {"discharge": ens_a}, _MODEL_B: {"discharge": ens_b}}
        )
        assert "discharge" in direct
        assert direct["discharge"].member_count == 8

        multi = _make_multi(
            {
                _MODEL_A: _result_with_ensemble(_MODEL_A, ens_a),
                _MODEL_B: _result_with_ensemble(_MODEL_B, ens_b),
            }
        )

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_discharge_range_qc_rules(),
            qc_overrides=[],
            baselines=[],
        )

        assert forecasts == []


class TestBuildCombinedForecasts:
    def test_pooled_two_models_returns_forecasts(self) -> None:
        result_a = _make_result(_MODEL_A, n_members=5)
        result_b = _make_result(_MODEL_B, n_members=3)
        multi = _make_multi({_MODEL_A: result_a, _MODEL_B: result_b})

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_discharge_range_qc_rules(),
            qc_overrides=[],
            baselines=[],
        )

        assert len(forecasts) == 1
        fc = forecasts[0]
        assert fc.combination_strategy == "pooled"
        assert set(fc.source_model_ids) == {_MODEL_A, _MODEL_B}  # type: ignore[arg-type]
        assert fc.model_id == POOLED_MODEL_ID
        assert fc.model_artifact_id is None
        assert fc.ensemble.member_count == 8

    def test_pooled_one_combinable_model_returns_empty(self) -> None:
        result_a = _make_result(_MODEL_A, n_members=5)
        # Only one model — not enough to combine
        multi = _make_multi({_MODEL_A: result_a})

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_empty_qc_rules(),
            qc_overrides=[],
            baselines=[],
        )

        assert forecasts == []

    def test_primary_strategy_returns_empty(self) -> None:
        result_a = _make_result(_MODEL_A)
        result_b = _make_result(_MODEL_B)
        multi = _make_multi({_MODEL_A: result_a, _MODEL_B: result_b})

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.PRIMARY,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_empty_qc_rules(),
            qc_overrides=[],
            baselines=[],
        )

        assert forecasts == []

    def test_bma_strategy_returns_forecasts(self) -> None:
        result_a = _make_result(_MODEL_A, n_members=50)
        result_b = _make_result(_MODEL_B, n_members=50)
        multi = _make_multi({_MODEL_A: result_a, _MODEL_B: result_b})

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.BMA,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_discharge_range_qc_rules(),
            qc_overrides=[],
            baselines=[],
            weights={_MODEL_A: 0.7, _MODEL_B: 0.3},
        )

        assert len(forecasts) == 1
        fc = forecasts[0]
        assert fc.combination_strategy == "bma"
        assert fc.model_id == BMA_MODEL_ID
        assert fc.ensemble.member_count == _BMA_TARGET_MEMBERS

    def test_bma_without_weights_raises(self) -> None:
        result_a = _make_result(_MODEL_A)
        result_b = _make_result(_MODEL_B)
        multi = _make_multi({_MODEL_A: result_a, _MODEL_B: result_b})

        with pytest.raises(ValueError, match="BMA strategy requires weights"):
            build_combined_forecasts(
                station_id=_STATION,
                multi_result=multi,
                strategy=ModelCombinationStrategy.BMA,
                nwp_cycle_reference_time=_NOW,
                nwp_cycle_source=NwpCycleSource.PRIMARY,
                clock=_clock,  # type: ignore[arg-type]
                uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
                qc_checker=_qc_checker(),
                qc_rules=_empty_qc_rules(),
                qc_overrides=[],
                baselines=[],
            )


class TestBuildCombinedForecastsUniformSpacing:
    """D6 fixer round — counting retained timestamps is not enough: an
    INTERIOR drop (as opposed to a trailing one) leaves a non-uniform grid
    that a count-only floor still persists. `store/forecast_store.py`
    derives `native_step_seconds` from the first two rows on readback, so
    an irregular grid publishes one misleading step — the original defect
    in a new costume (`docs/plans/222-pooled-grid-alignment.md` D6)."""

    def test_interior_incomplete_timestamp_yields_ragged_grid_not_persisted(
        self,
    ) -> None:
        """4 candidate timestamps t1..t4, hourly. `_MODEL_B` is short one
        member only at the INTERIOR t2 (not the final timestamp — the case
        the pre-existing ragged test never covers). The intersection is
        {t1, t3, t4}: deltas of 2h then 1h, not uniform. Direct pooling
        still legally returns it (D6 lives only at the persistence
        boundary); `build_combined_forecasts()` must not persist it."""
        t1, t2, t3, t4 = (ensure_utc(_NOW + timedelta(hours=h)) for h in (1, 2, 3, 4))
        ens_a = _members_ensemble_at(
            model_id=_MODEL_A, valid_times=[t1, t2, t3, t4], n_members=5
        )
        ens_b = _members_ensemble_ragged(
            model_id=_MODEL_B,
            member_counts={t1: 5, t2: 4, t3: 5, t4: 5},
        )

        direct = combine_ensembles_pooled(
            {_MODEL_A: {"discharge": ens_a}, _MODEL_B: {"discharge": ens_b}}
        )
        assert set(direct["discharge"].values["valid_time"].to_list()) == {
            t1,
            t3,
            t4,
        }

        multi = _make_multi(
            {
                _MODEL_A: _result_with_ensemble(_MODEL_A, ens_a),
                _MODEL_B: _result_with_ensemble(_MODEL_B, ens_b),
            }
        )

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_discharge_range_qc_rules(),
            qc_overrides=[],
            baselines=[],
        )

        assert forecasts == []

    def test_uniformly_coarsened_intersection_derives_time_step_from_grid(
        self,
    ) -> None:
        """D6 fixer round (review) — both contributors are declared hourly
        (`_members_ensemble_at` hardcodes `time_step=timedelta(hours=1)`)
        but are only actually present at every OTHER hour: t1, t3, t5. The
        intersection {t1, t3, t5} is uniform (`_derive_uniform_time_step`
        passes it — a single 2h delta), so the count-and-uniformity floor
        alone would happily persist it with the stale 1h label carried
        over from `ref_ensemble.time_step`. The persisted forecast's
        `ensemble.time_step` must reflect the grid that actually survived
        (2h), not the declared-but-no-longer-true step, or an in-memory
        read and a post-persistence reload (`store/forecast_store.py`
        derives `native_step_seconds` from the first two rows) disagree
        about the same forecast."""
        t1, t3, t5 = (ensure_utc(_NOW + timedelta(hours=h)) for h in (1, 3, 5))
        ens_a = _members_ensemble_at(
            model_id=_MODEL_A, valid_times=[t1, t3, t5], n_members=5
        )
        ens_b = _members_ensemble_at(
            model_id=_MODEL_B, valid_times=[t1, t3, t5], n_members=5
        )
        assert ens_a.time_step == timedelta(hours=1)

        multi = _make_multi(
            {
                _MODEL_A: _result_with_ensemble(_MODEL_A, ens_a),
                _MODEL_B: _result_with_ensemble(_MODEL_B, ens_b),
            }
        )

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_discharge_range_qc_rules(time_step=timedelta(hours=2)),
            qc_overrides=[],
            baselines=[],
        )

        assert len(forecasts) == 1
        assert forecasts[0].ensemble.time_step == timedelta(hours=2)


class TestBuildCombinedForecastsProductionQcRuleCoverage:
    """Plan 246 review (fail closed) — with the rule set the deployment
    actually ships, not an injected one.

    `ForecastQcRuleSet.rules_for` matches `time_step` by EQUALITY and
    production declares forecast QC rules at 3600 s and 86400 s only
    (`config/forecast_qc_rules.py`), so a combination rebuilt on a derived
    step outside that pair selects zero rules, produces zero flags, and
    `worst_qc_status([])` returns QC_PASSED. Injecting a rule at the
    derived step — what the other classes here do, legitimately, to reach
    the QC wiring — is exactly what conceals that."""

    def test_production_has_no_rule_at_a_coarsened_two_hour_step(self) -> None:
        production = _default_swiss_forecast_qc_rules()

        assert production.rules_for("discharge", timedelta(hours=2)) == ()
        assert production.rules_for("discharge", timedelta(hours=1)) != ()

    def test_coarsened_step_combination_is_not_returned_for_storage(self) -> None:
        """Both contributors are declared hourly but present only at t1,
        t3, t5, so the combination is rebuilt on a 2-hour step. Production
        cannot check it, so it must not be published as checked."""
        vts = [ensure_utc(_NOW + timedelta(hours=h)) for h in (1, 3, 5)]
        multi = _make_multi(
            {
                _MODEL_A: _result_with_ensemble(
                    _MODEL_A,
                    _members_ensemble_at(
                        model_id=_MODEL_A, valid_times=vts, n_members=5
                    ),
                ),
                _MODEL_B: _result_with_ensemble(
                    _MODEL_B,
                    _members_ensemble_at(
                        model_id=_MODEL_B, valid_times=vts, n_members=5
                    ),
                ),
            }
        )

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_default_swiss_forecast_qc_rules(),
            qc_overrides=[],
            baselines=[],
        )

        assert forecasts == [], (
            "an unchecked 2-hour combination was returned with qc_status="
            f"{[fc.qc_status for fc in forecasts]}"
        )

    def test_hourly_step_combination_is_checked_and_returned(self) -> None:
        """The guard is not a blanket refusal: the same contributors on a
        grid whose derived step production DOES cover are checked on the
        real production rules and returned for storage."""
        vts = [ensure_utc(_NOW + timedelta(hours=h)) for h in (1, 2, 3)]
        multi = _make_multi(
            {
                _MODEL_A: _result_with_ensemble(
                    _MODEL_A,
                    _members_ensemble_at(
                        model_id=_MODEL_A, valid_times=vts, n_members=5
                    ),
                ),
                _MODEL_B: _result_with_ensemble(
                    _MODEL_B,
                    _members_ensemble_at(
                        model_id=_MODEL_B, valid_times=vts, n_members=5
                    ),
                ),
            }
        )

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_default_swiss_forecast_qc_rules(),
            qc_overrides=[],
            baselines=[],
        )

        assert len(forecasts) == 1
        assert forecasts[0].ensemble.time_step == timedelta(hours=1)
        assert forecasts[0].qc_status == QcStatus.QC_PASSED
        assert forecasts[0].qc_flags == ()


class TestBuildCombinedForecastsQualityControl:
    """Plan 246 T2a — the combined ensemble is quality-controlled on the
    SAME rules and datum handling as its members, from every call site
    that stores one; a QC_FAILED combination is stored marked failed
    (OD-1), never dropped."""

    def test_qc_passed_over_clean_members(self) -> None:
        result_a = _make_result(_MODEL_A, n_members=5)
        result_b = _make_result(_MODEL_B, n_members=3)
        multi = _make_multi({_MODEL_A: result_a, _MODEL_B: result_b})

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_discharge_range_qc_rules(value_min=0.0, value_max=1000.0),
            qc_overrides=[],
            baselines=[],
        )

        assert len(forecasts) == 1
        assert forecasts[0].qc_status == QcStatus.QC_PASSED
        assert forecasts[0].qc_flags == ()

    def test_qc_failed_stored_marked_failed_not_dropped(self) -> None:
        """OD-1: a combined forecast that trips a REAL QC rule is STORED,
        marked QC_FAILED, with flags -- not dropped and not silently
        passed."""
        vts = [ensure_utc(_NOW + timedelta(hours=h)) for h in (1, 2)]
        ens_a = _members_ensemble_at(model_id=_MODEL_A, valid_times=vts, n_members=5)
        ens_b = _members_ensemble_at(model_id=_MODEL_B, valid_times=vts, n_members=5)
        multi = _make_multi(
            {
                _MODEL_A: _result_with_ensemble(_MODEL_A, ens_a),
                _MODEL_B: _result_with_ensemble(_MODEL_B, ens_b),
            }
        )

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            # member values are 1.0..5.0 per contributor (float(m)+1.0);
            # pooled median across both is 3.0 -- value_max=2.0 trips
            # range_check for real (median-based, not per-value).
            qc_rules=_discharge_range_qc_rules(value_min=0.0, value_max=2.0),
            qc_overrides=[],
            baselines=[],
        )

        assert len(forecasts) == 1
        fc = forecasts[0]
        assert fc.qc_status == QcStatus.QC_FAILED
        assert fc.qc_flags != ()
        assert fc.status == ForecastStatus.RAW  # stored, not dropped (OD-1)

    def test_no_stored_row_carries_raw_status(self) -> None:
        """Neither QC_PASSED nor QC_FAILED is ever `RAW` -- the hardcoded
        literal this task removes."""
        result_a = _make_result(_MODEL_A, n_members=5)
        result_b = _make_result(_MODEL_B, n_members=3)
        multi = _make_multi({_MODEL_A: result_a, _MODEL_B: result_b})

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_discharge_range_qc_rules(),
            qc_overrides=[],
            baselines=[],
        )

        assert all(fc.qc_status != QcStatus.RAW for fc in forecasts)

    def test_water_level_datum_present_avoids_false_range_failure(self) -> None:
        """Without the datum shift, raw metres-above-sea-level values would
        fail the -2..20m relative bounds -- the Plan 101 defect this task
        must not reintroduce."""
        vts = [ensure_utc(_NOW + timedelta(hours=h)) for h in (1, 2)]
        ens_a = _members_ensemble_at(
            model_id=_MODEL_A, valid_times=vts, n_members=5, parameter="water_level"
        )
        ens_b = _members_ensemble_at(
            model_id=_MODEL_B, valid_times=vts, n_members=5, parameter="water_level"
        )
        datum = 450.0
        ens_a = replace(
            ens_a, values=ens_a.values.with_columns(pl.col("value") + datum)
        )
        ens_b = replace(
            ens_b, values=ens_b.values.with_columns(pl.col("value") + datum)
        )
        multi = _make_multi(
            {
                _MODEL_A: _result_with_ensemble(_MODEL_A, ens_a, "water_level"),
                _MODEL_B: _result_with_ensemble(_MODEL_B, ens_b, "water_level"),
            }
        )

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_water_level_range_qc_rules(),
            qc_overrides=[],
            baselines=[],
            water_level_datum_masl=datum,
        )

        assert len(forecasts) == 1
        assert forecasts[0].qc_status == QcStatus.QC_PASSED

    def test_water_level_datum_present_out_of_range_values_fail_range_check(
        self,
    ) -> None:
        """The datum-present case must prove the rule still RUNS, and that it
        runs on DATUM-RELATIVE values -- not only that valid shifted values
        pass: an implementation that skipped `range_check` for every
        water_level combination -- datum present or absent -- passes the
        clean-shift test above. Here the datum-relative values
        (101.0..105.0) sit outside the -2..20 m bounds
        (`config/forecast_qc_rules.py:149-176`), so the combination must be
        QC_FAILED with a `range_check` flag -- and the datum provenance
        `add_forecast_datum_details` stamps on that flag must show the
        relative values the rule saw, so a datum-ignoring implementation
        (which would also fail these raw 551.0..555.0 masl values) cannot
        pass this test."""
        vts = [ensure_utc(_NOW + timedelta(hours=h)) for h in (1, 2)]
        ens_a = _members_ensemble_at(
            model_id=_MODEL_A, valid_times=vts, n_members=5, parameter="water_level"
        )
        ens_b = _members_ensemble_at(
            model_id=_MODEL_B, valid_times=vts, n_members=5, parameter="water_level"
        )
        datum = 450.0
        # Raw masl values whose datum-relative counterparts (101.0..105.0)
        # are far above the 20 m upper bound -- a real stage this gauge
        # could never reach, not a masl/relative mix-up.
        above_bounds = 100.0
        ens_a = replace(
            ens_a,
            values=ens_a.values.with_columns(pl.col("value") + datum + above_bounds),
        )
        ens_b = replace(
            ens_b,
            values=ens_b.values.with_columns(pl.col("value") + datum + above_bounds),
        )
        multi = _make_multi(
            {
                _MODEL_A: _result_with_ensemble(_MODEL_A, ens_a, "water_level"),
                _MODEL_B: _result_with_ensemble(_MODEL_B, ens_b, "water_level"),
            }
        )

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_water_level_range_qc_rules(),
            qc_overrides=[],
            baselines=[],
            water_level_datum_masl=datum,
        )

        assert len(forecasts) == 1
        assert forecasts[0].qc_status == QcStatus.QC_FAILED
        assert [f.rule_id for f in forecasts[0].qc_flags] == ["range_check"]

        # QC_FAILED plus a `range_check` flag would ALSO be produced by an
        # implementation that ignored the datum and range-checked the raw
        # masl values (551.0..555.0, likewise outside -2..20). The datum
        # provenance `add_forecast_datum_details` appends to the flag detail
        # is what separates the two: it reports the relative values the rule
        # actually saw next to the raw ones they came from. Read the fields,
        # not the whole message, so rewording the flag text stays free.
        detail = forecasts[0].qc_flags[0].detail
        assert detail is not None
        reported = {
            field: float(value)
            for field, value in re.findall(
                r"(raw_min|relative_min|raw_median|relative_median|datum_masl)"
                r"=(-?\d+(?:\.\d+)?)",
                detail,
            )
        }
        assert reported == {
            "raw_min": 551.0,
            "relative_min": 101.0,
            "raw_median": 553.0,
            "relative_median": 103.0,
            "datum_masl": 450.0,
        }

    def test_water_level_datum_absent_skips_the_datum_dependent_rule(self) -> None:
        """No datum on file: `forecast_skipped_rules` skips `range_check`
        for water_level rather than running it against meaningless raw
        masl values (the Plan 101 false failure) -- proven by the rule NOT
        firing despite raw values that would trip it if it ran."""
        vts = [ensure_utc(_NOW + timedelta(hours=h)) for h in (1, 2)]
        ens_a = _members_ensemble_at(
            model_id=_MODEL_A, valid_times=vts, n_members=5, parameter="water_level"
        )
        ens_b = _members_ensemble_at(
            model_id=_MODEL_B, valid_times=vts, n_members=5, parameter="water_level"
        )
        datum = 450.0
        ens_a = replace(
            ens_a, values=ens_a.values.with_columns(pl.col("value") + datum)
        )
        ens_b = replace(
            ens_b, values=ens_b.values.with_columns(pl.col("value") + datum)
        )
        multi = _make_multi(
            {
                _MODEL_A: _result_with_ensemble(_MODEL_A, ens_a, "water_level"),
                _MODEL_B: _result_with_ensemble(_MODEL_B, ens_b, "water_level"),
            }
        )

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_water_level_range_qc_rules(),
            qc_overrides=[],
            baselines=[],
            water_level_datum_masl=None,
        )

        assert len(forecasts) == 1
        assert forecasts[0].qc_status == QcStatus.QC_PASSED


class TestBuildCombinedForecastsInputQuality:
    """Plan 246 T2b — the combination reports the aggregate input quality
    of the models that actually contributed to THAT parameter."""

    def test_full_plus_degraded_contributor_yields_degraded(self) -> None:
        ens_a = make_forecast_ensemble(
            station_id=_STATION,
            n_members=5,
            n_steps=10,
            parameter="discharge",
            model_id=_MODEL_A,
        )
        ens_b = make_forecast_ensemble(
            station_id=_STATION,
            n_members=5,
            n_steps=10,
            parameter="discharge",
            model_id=_MODEL_B,
            rng=random.Random(9),
        )
        degraded_flag = InputQualityFlag(
            category=InputQualityCategory.OBSERVATION,
            level=InputQualityLevel.DEGRADED,
            detail="Observations 48.0h stale",
        )
        result_a = _result_with_ensemble(
            _MODEL_A, ens_a, input_quality=InputQualityLevel.FULL
        )
        result_b = _result_with_ensemble(
            _MODEL_B,
            ens_b,
            input_quality=InputQualityLevel.DEGRADED,
            input_quality_flags=(degraded_flag,),
        )
        multi = _make_multi({_MODEL_A: result_a, _MODEL_B: result_b})

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_discharge_range_qc_rules(),
            qc_overrides=[],
            baselines=[],
        )

        assert len(forecasts) == 1
        fc = forecasts[0]
        assert fc.input_quality == InputQualityLevel.DEGRADED
        assert degraded_flag in fc.input_quality_flags

    def test_all_full_contributors_yields_full(self) -> None:
        ens_a = make_forecast_ensemble(
            station_id=_STATION,
            n_members=5,
            n_steps=10,
            parameter="discharge",
            model_id=_MODEL_A,
        )
        ens_b = make_forecast_ensemble(
            station_id=_STATION,
            n_members=5,
            n_steps=10,
            parameter="discharge",
            model_id=_MODEL_B,
            rng=random.Random(9),
        )
        result_a = _result_with_ensemble(
            _MODEL_A, ens_a, input_quality=InputQualityLevel.FULL
        )
        result_b = _result_with_ensemble(
            _MODEL_B, ens_b, input_quality=InputQualityLevel.FULL
        )
        multi = _make_multi({_MODEL_A: result_a, _MODEL_B: result_b})

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_discharge_range_qc_rules(),
            qc_overrides=[],
            baselines=[],
        )

        assert len(forecasts) == 1
        assert forecasts[0].input_quality == InputQualityLevel.FULL
        assert forecasts[0].input_quality_flags == ()

    def test_degraded_model_absent_from_parameter_does_not_degrade_product(
        self,
    ) -> None:
        """model_b is DEGRADED but contributes only water_level, never
        discharge -- the discharge product must read FULL, not degraded by
        a model that never fed it."""
        ens_a = make_forecast_ensemble(
            station_id=_STATION,
            n_members=5,
            n_steps=10,
            parameter="discharge",
            model_id=_MODEL_A,
        )
        ens_c = make_forecast_ensemble(
            station_id=_STATION,
            n_members=5,
            n_steps=10,
            parameter="discharge",
            model_id=_MODEL_C,
            rng=random.Random(11),
        )
        ens_b_wl = make_forecast_ensemble(
            station_id=_STATION,
            n_members=5,
            n_steps=10,
            parameter="water_level",
            model_id=_MODEL_B,
            rng=random.Random(12),
        )
        degraded_flag = InputQualityFlag(
            category=InputQualityCategory.NWP,
            level=InputQualityLevel.DEGRADED,
            detail="NWP 10.0h stale",
        )
        result_a = _result_with_ensemble(
            _MODEL_A, ens_a, "discharge", input_quality=InputQualityLevel.FULL
        )
        result_c = _result_with_ensemble(
            _MODEL_C, ens_c, "discharge", input_quality=InputQualityLevel.FULL
        )
        result_b = _result_with_ensemble(
            _MODEL_B,
            ens_b_wl,
            "water_level",
            input_quality=InputQualityLevel.DEGRADED,
            input_quality_flags=(degraded_flag,),
        )
        multi = _make_multi(
            {_MODEL_A: result_a, _MODEL_B: result_b, _MODEL_C: result_c}
        )

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_discharge_range_qc_rules(),
            qc_overrides=[],
            baselines=[],
        )

        # Only "discharge" combines (water_level has a single contributor,
        # below the 2-contributor pooling floor).
        assert {fc.ensemble.parameter for fc in forecasts} == {"discharge"}
        discharge_fc = next(
            fc for fc in forecasts if fc.ensemble.parameter == "discharge"
        )
        assert discharge_fc.input_quality == InputQualityLevel.FULL
        assert discharge_fc.input_quality_flags == ()

    def test_degraded_model_quantile_represented_does_not_degrade_product(
        self,
    ) -> None:
        """model_b is DEGRADED and DOES contribute a `discharge` ensemble,
        but as QUANTILES -- excluded from MEMBERS-only pooling, so it must
        not degrade the discharge product it was never actually pooled
        into."""
        ens_a = make_forecast_ensemble(
            station_id=_STATION,
            n_members=5,
            n_steps=10,
            parameter="discharge",
            model_id=_MODEL_A,
        )
        ens_c = make_forecast_ensemble(
            station_id=_STATION,
            n_members=5,
            n_steps=10,
            parameter="discharge",
            model_id=_MODEL_C,
            rng=random.Random(13),
        )
        ens_b_quantile = make_forecast_ensemble(
            station_id=_STATION,
            representation=EnsembleRepresentation.QUANTILES,
            n_steps=10,
            parameter="discharge",
            model_id=_MODEL_B,
            rng=random.Random(14),
        )
        degraded_flag = InputQualityFlag(
            category=InputQualityCategory.WARM_UP,
            level=InputQualityLevel.DEGRADED,
            detail="Cold start",
        )
        result_a = _result_with_ensemble(
            _MODEL_A, ens_a, "discharge", input_quality=InputQualityLevel.FULL
        )
        result_c = _result_with_ensemble(
            _MODEL_C, ens_c, "discharge", input_quality=InputQualityLevel.FULL
        )
        result_b = _result_with_ensemble(
            _MODEL_B,
            ens_b_quantile,
            "discharge",
            input_quality=InputQualityLevel.DEGRADED,
            input_quality_flags=(degraded_flag,),
        )
        multi = _make_multi(
            {_MODEL_A: result_a, _MODEL_B: result_b, _MODEL_C: result_c}
        )

        forecasts = build_combined_forecasts(
            station_id=_STATION,
            multi_result=multi,
            strategy=ModelCombinationStrategy.POOLED,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            clock=_clock,  # type: ignore[arg-type]
            uuid_factory=_uuid_seq(),  # type: ignore[arg-type]
            qc_checker=_qc_checker(),
            qc_rules=_discharge_range_qc_rules(),
            qc_overrides=[],
            baselines=[],
        )

        assert len(forecasts) == 1
        assert forecasts[0].input_quality == InputQualityLevel.FULL
        assert forecasts[0].input_quality_flags == ()
