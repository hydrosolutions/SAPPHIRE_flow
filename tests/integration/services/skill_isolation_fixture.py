from __future__ import annotations

import dataclasses
import math
import random
from dataclasses import dataclass
from datetime import timedelta
from fractions import Fraction
from itertools import product
from typing import TYPE_CHECKING, Literal, cast
from uuid import UUID

import polars as pl
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.db import metadata as db
from sapphire_flow.flows._db import make_pg_stores
from sapphire_flow.store.hindcast_store import PgHindcastStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import StationThreshold
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    FlowRegime,
    ForcingType,
    ObservationSource,
    ThresholdSource,
)
from sapphire_flow.types.forecast import HindcastForecast
from sapphire_flow.types.ids import (
    BMA_MODEL_ID,
    POOLED_MODEL_ID,
    ArtifactId,
    HindcastForecastId,
    ModelId,
)
from sapphire_flow.types.skill import FlowRegimeConfig
from tests.conftest import make_observation
from tests.integration.services.skill_persistence_fixture import (
    AID,
    MID,
    RUN,
    SID,
    C,
    T,
    seed_base,
    seed_transaction,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.store.flow_regime_config_store import PgFlowRegimeConfigStore
    from sapphire_flow.store.observation_store import PgObservationStore
    from sapphire_flow.store.skill_store import PgSkillStore
    from sapphire_flow.store.station_store import PgStationStore

Strategy = Literal["SINGLE", "POOLED", "BMA"]
GenerationMode = Literal["OFF", "ON"]
DiagramKind = Literal["rank_histogram", "reliability", "roc"]
RegimeKey = Literal["ALL", "LOW", "HIGH", "FLOOD"]
KINDS: tuple[DiagramKind, ...] = ("rank_histogram", "reliability", "roc")
Q = (10, 12, 16, 18, 22, 24, 14, 20) * 2
MID_B = ModelId("skill_isolation_b")
AID_B = ArtifactId(UUID(int=62002))
RUN_B = UUID(int=63002)
REGIME = UUID(int=64000)
MODEL_IDS = {"SINGLE": MID, "POOLED": POOLED_MODEL_ID, "BMA": BMA_MODEL_ID}
INVOCATIONS = {
    "SINGLE": UUID(int=65001),
    "POOLED": UUID(int=65002),
    "BMA": UUID(int=65003),
}
REGIMES: tuple[RegimeKey, ...] = ("ALL", "LOW", "HIGH", "FLOOD")
METRICS = (
    "crps",
    "nse",
    "kge",
    "pbias",
    "mae",
    "sharpness_p10_p90",
    "sharpness_p25_p75",
    "ensemble_range",
    "bss_danger_2",
    "pod_danger_2",
    "far_danger_2",
    "csi_danger_2",
    "peak_timing_error",
)

# Reviewed clean scalar table: exact fractions; None means numeric NaN, never SQL NULL.
SCALAR_VALUES: dict[Strategy, dict[str, tuple[str | None, ...]]] = {
    "SINGLE": {
        "ALL": (
            "1",
            "20/21",
            "16/17",
            "100/17",
            "1",
            "0",
            "0",
            "0",
            "1",
            "1",
            "0",
            "1",
            "0",
        ),
        "LOW": (
            "1",
            "5/8",
            "11/12",
            "25/3",
            "1",
            "0",
            "0",
            "0",
            None,
            None,
            None,
            None,
        ),
        "HIGH": ("1", "5/8", "17/18", "50/9", "1", "0", "0", "0", "1", "1", "0", "1"),
        "FLOOD": ("1", "0", "22/23", "100/23", "1", "0", "0", "0", None, "1", "0", "1"),
    },
    "POOLED": {
        "ALL": (
            "2",
            "4/7",
            "14/17",
            "300/17",
            "3",
            "4",
            "4",
            "4",
            "11/15",
            "1",
            "2/5",
            "3/5",
            "0",
        ),
        "LOW": ("2", "-19/8", "3/4", "25", "3", "4", "4", "4", None, None, None, None),
        "HIGH": (
            "2",
            "-19/8",
            "5/6",
            "50/3",
            "3",
            "4",
            "4",
            "4",
            "1/4",
            "1",
            "2/3",
            "1/3",
        ),
        "FLOOD": (
            "2",
            "-8",
            "20/23",
            "300/23",
            "3",
            "4",
            "4",
            "4",
            None,
            "1",
            "0",
            "1",
        ),
    },
    "BMA": {
        "ALL": (
            "2789/2500",
            "20/21",
            "16/17",
            "100/17",
            "1",
            "4",
            "0",
            "4",
            "9086/9375",
            "1",
            "0",
            "1",
            "0",
        ),
        "LOW": (
            "2789/2500",
            "5/8",
            "11/12",
            "25/3",
            "1",
            "4",
            "0",
            "4",
            None,
            None,
            None,
            None,
            "0",
        ),
        "HIGH": (
            "2789/2500",
            "5/8",
            "17/18",
            "50/9",
            "1",
            "4",
            "0",
            "4",
            "9133/10000",
            "1",
            "0",
            "1",
            "0",
        ),
        "FLOOD": (
            "2789/2500",
            "0",
            "22/23",
            "100/23",
            "1",
            "4",
            "0",
            "4",
            None,
            "1",
            "0",
            "1",
            "0",
        ),
    },
}


@dataclass(frozen=True, kw_only=True, slots=True)
class CleanCase:
    connection: sa.Connection
    skill_store: PgSkillStore
    hindcast_store: PgHindcastStore
    obs_store: PgObservationStore
    station_store: PgStationStore
    flow_regime_store: PgFlowRegimeConfigStore
    owner_role: str


def deployment(mode: GenerationMode) -> DeploymentConfig:
    return DeploymentConfig.model_validate(
        {
            "max_retention_days": 2192,
            "enable_skill_generations": mode == "ON",
            "seasons": [{"name": "winter", "months": [1]}],
        }
    )


def seed_clean(conn: sa.Connection) -> None:
    stores = seed_base(conn)
    conn.execute(
        insert(db.models)
        .values(
            [
                {
                    "id": model,
                    "display_name": str(model),
                    "artifact_scope": "station",
                    "description": "Synthetic stored hindcasts; no model execution",
                }
                for model in (MID_B, POOLED_MODEL_ID, BMA_MODEL_ID)
            ]
        )
        .on_conflict_do_nothing(index_elements=[db.models.c.id])
    )
    conn.execute(
        sa.insert(db.model_artifacts).values(
            id=AID_B,
            model_id=MID_B,
            station_id=SID,
            group_id=None,
            status="active",
            artifact_path="unused-synthetic-artifact",
            sha256_hash="",
            training_period_start=T - timedelta(days=2),
            training_period_end=T - timedelta(days=1),
            trained_at=T - timedelta(days=1),
        )
    )
    station = cast("PgStationStore", stores["station_store"])
    station.store_thresholds(
        [
            StationThreshold(
                station_id=SID,
                danger_level="2",
                parameter="discharge",
                value=19.0,
                source=ThresholdSource.INFERRED,
                created_at=C,
                updated_at=C,
            )
        ]
    )
    regime = cast("PgFlowRegimeConfigStore", stores["flow_regime_store"])
    regime.store_config(
        FlowRegimeConfig(
            id=REGIME,
            station_id=SID,
            parameter="discharge",
            p50=15.0,
            p90=21.0,
            computed_at=C,
            observation_count=365,
            version=1,
            created_at=C,
        )
    )
    writer = PgHindcastStore(conn, transaction_factory=lambda: seed_transaction(conn))
    for i, model_index in product(range(16), range(2)):
        model, artifact, run, offset = ((MID, AID, RUN, 1), (MID_B, AID_B, RUN_B, 5))[
            model_index
        ]
        issue = ensure_utc(T + timedelta(hours=2 * i))
        valid = ensure_utc(issue + timedelta(hours=1))
        ensemble = ForecastEnsemble.from_members(
            station_id=SID,
            issued_at=issue,
            parameter="discharge",
            units="m³/s",
            time_step=timedelta(hours=1),
            values=pl.DataFrame(
                {
                    "valid_time": [valid, valid],
                    "member_id": [0, 1],
                    "value": [float(Q[i] + offset)] * 2,
                }
            ),
            model_id=model,
        )
        writer.store_hindcast(
            HindcastForecast(
                id=HindcastForecastId(UUID(int=67000 + 2 * i + model_index)),
                station_id=SID,
                model_id=model,
                model_artifact_id=artifact,
                hindcast_step=issue,
                forcing_type=ForcingType.REANALYSIS,
                representation=EnsembleRepresentation.MEMBERS,
                hindcast_run_id=run,
                ensemble=ensemble,
                created_at=C,
            )
        )
    observations = [
        dataclasses.replace(
            make_observation(
                station_id=SID,
                timestamp=ensure_utc(T + timedelta(hours=2 * i + 1, minutes=15 * j)),
                value=float(q + offset),
                rng=random.Random(100 * i + j),
            ),
            source=ObservationSource.MANUAL_IMPORT,
            created_at=C,
        )
        for i, q in enumerate(Q)
        for j, offset in enumerate((-3, -1, 1, 3))
    ]
    cast("PgObservationStore", stores["obs_store"]).store_observations(observations)
    assert (
        conn.scalar(sa.select(sa.func.count()).select_from(db.hindcast_forecasts)) == 32
    )
    assert conn.scalar(sa.select(sa.func.count()).select_from(db.hindcast_values)) == 64
    assert conn.scalar(sa.select(sa.func.count()).select_from(db.observations)) == 64
    stored = conn.execute(
        sa.select(
            db.observations.c.timestamp,
            db.observations.c.value,
            db.observations.c.source,
            db.observations.c.qc_status,
        ).order_by(db.observations.c.timestamp)
    ).all()
    expected = [
        (
            T + timedelta(hours=2 * i + 1, minutes=15 * j),
            float(q + offset),
            "manual_import",
            "qc_passed",
        )
        for i, q in enumerate(Q)
        for j, offset in enumerate((-3, -1, 1, 3))
    ]
    assert [tuple(row) for row in stored] == expected


@pytest.fixture
def clean_skill_case(skill_persistence_engine: sa.Engine) -> Iterator[CleanCase]:
    with skill_persistence_engine.connect() as conn:
        outer = conn.begin()
        try:
            owner = str(conn.scalar(sa.text("SELECT current_user")))
            assert owner == "test"
            seed_clean(conn)
            conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
            assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
            with (
                pytest.raises(DBAPIError, match="permission denied"),
                conn.begin_nested(),
            ):
                conn.execute(sa.select(db.provisional_discharges))
            stores = make_pg_stores(conn)
            yield CleanCase(
                connection=conn,
                skill_store=cast("PgSkillStore", stores["skill_store"]),
                hindcast_store=cast("PgHindcastStore", stores["hindcast_store"]),
                obs_store=cast("PgObservationStore", stores["obs_store"]),
                station_store=cast("PgStationStore", stores["station_store"]),
                flow_regime_store=cast(
                    "PgFlowRegimeConfigStore", stores["flow_regime_store"]
                ),
                owner_role=owner,
            )
        finally:
            outer.rollback()


def regime_key(regime: FlowRegime | None) -> RegimeKey:
    labels: dict[FlowRegime | None, RegimeKey] = {
        None: "ALL",
        FlowRegime.LOW: "LOW",
        FlowRegime.HIGH: "HIGH",
        FlowRegime.FLOOD: "FLOOD",
    }
    return labels[regime]


def indices(regime: RegimeKey) -> list[int]:
    return [
        i
        for i, q in enumerate(Q)
        if regime == "ALL"
        or regime == "LOW"
        and q <= 15
        or regime == "HIGH"
        and 15 < q <= 21
        or regime == "FLOOD"
        and q > 21
    ]


def expected_payload(
    strategy: Strategy,
    regime: RegimeKey,
    kind: DiagramKind,
    layer: Literal["domain", "json"],
) -> dict[str, object]:
    selected = indices(regime)
    if kind == "rank_histogram":
        members = {"SINGLE": 2, "POOLED": 4, "BMA": 100}[strategy]
        return {
            "ranks": list(range(members + 1)),
            "counts": [len(selected)] + [0] * members,
        }
    # Fixed CLEAN probability numerators, not production forecast output.
    half = {
        "SINGLE": (0, 0, 0, 0, 100, 100, 0, 100),
        "POOLED": (0, 0, 50, 50, 100, 100, 0, 100),
        "BMA": (0, 0, 17, 17, 100, 100, 0, 100),
    }[strategy]
    probabilities = half * 2
    undefined = float("nan") if layer == "domain" else None
    if kind == "reliability":
        groups = [
            [i for i in selected if min(9, probabilities[i] // 10) == b]
            for b in range(10)
        ]
        centers = [float(Fraction(2 * b + 1, 20)) for b in range(10)]
        return {
            "bins": centers,
            "forecast_freq": centers,
            "sample_counts": [len(group) for group in groups],
            "observed_freq": [
                sum(Q[i] > 19 for i in group) / len(group) if group else undefined
                for group in groups
            ],
        }
    assert kind == "roc"
    events = sum(Q[i] > 19 for i in selected)
    non_events = len(selected) - events
    return {
        "thresholds": [float(Fraction(j, 100)) for j in range(101)],
        "n_events": events,
        "n_non_events": non_events,
        "hit_rate": [
            sum(probabilities[i] >= j and Q[i] > 19 for i in selected) / events
            if events
            else undefined
            for j in range(101)
        ],
        "false_alarm_rate": [
            sum(probabilities[i] >= j and Q[i] <= 19 for i in selected) / non_events
            if non_events
            else undefined
            for j in range(101)
        ],
    }


def assert_value(actual: object, expected: object) -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        expected_map = cast("dict[object, object]", expected)
        actual_map = cast("dict[object, object]", actual)
        assert actual_map.keys() == expected_map.keys()
        for key, value in expected_map.items():
            assert_value(actual_map[key], value)
    elif isinstance(expected, list):
        assert isinstance(actual, list)
        actual_list = cast("list[object]", actual)
        expected_list = cast("list[object]", expected)
        assert len(actual_list) == len(expected_list)
        for value, reference in zip(actual_list, expected_list, strict=True):
            assert_value(value, reference)
    elif isinstance(expected, (int, float)):
        assert isinstance(actual, (int, float)) and not isinstance(actual, bool)
        if isinstance(expected, float) and math.isnan(expected):
            assert math.isnan(actual)
        elif isinstance(expected, float):
            assert math.isfinite(actual)
            assert abs(actual - expected) <= max(1e-12, 1e-12 * abs(expected))
        else:
            assert actual == expected
    else:
        assert actual == expected


def null_count(value: object) -> int:
    if value is None:
        return 1
    if isinstance(value, dict):
        return sum(
            null_count(item) for item in cast("dict[str, object]", value).values()
        )
    if isinstance(value, list):
        return sum(null_count(item) for item in cast("list[object]", value))
    return 0


def assert_same_source(actual: object, expected: object) -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        expected_map = cast("dict[object, object]", expected)
        actual_map = cast("dict[object, object]", actual)
        assert actual_map.keys() == expected_map.keys()
        for key, value in expected_map.items():
            assert_same_source(actual_map[key], value)
    elif isinstance(expected, list):
        assert isinstance(actual, list)
        actual_list = cast("list[object]", actual)
        expected_list = cast("list[object]", expected)
        assert len(actual_list) == len(expected_list)
        for value, reference in zip(actual_list, expected_list, strict=True):
            assert_same_source(value, reference)
    elif isinstance(expected, (int, float)):
        assert isinstance(actual, (int, float)) and not isinstance(actual, bool)
        if isinstance(expected, float) and math.isnan(expected):
            assert isinstance(actual, float) and math.isnan(actual)
        else:
            assert math.isfinite(actual) and math.isfinite(expected)
            assert actual == expected
    else:
        assert actual == expected


def wire_from_domain(value: object, null_pattern: object) -> object:
    # Only independently expected zero-support rate positions may change NaN to None.
    if null_pattern is None:
        assert isinstance(value, float) and math.isnan(value)
        return None
    if isinstance(value, dict):
        assert isinstance(null_pattern, dict)
        mapping = cast("dict[str, object]", value)
        pattern = cast("dict[str, object]", null_pattern)
        assert mapping.keys() == pattern.keys()
        return {
            key: wire_from_domain(item, pattern[key]) for key, item in mapping.items()
        }
    if isinstance(value, list):
        assert isinstance(null_pattern, list)
        values = cast("list[object]", value)
        positions = cast("list[object]", null_pattern)
        assert len(values) == len(positions)
        return [
            wire_from_domain(item, position)
            for item, position in zip(values, positions, strict=True)
        ]
    if isinstance(value, float):
        assert math.isfinite(value)
    return value
