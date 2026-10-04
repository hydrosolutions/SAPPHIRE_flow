from __future__ import annotations

import dataclasses
import json
import math
import random
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from fractions import Fraction
from itertools import product
from typing import TYPE_CHECKING, Literal, Protocol, cast
from uuid import UUID

import polars as pl
import pytest
import sqlalchemy as sa
from sqlalchemy import event as sa_event
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.db import metadata as db
from sapphire_flow.flows._db import make_pg_stores
from sapphire_flow.store.hindcast_store import PgHindcastStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcFlag, StationThreshold
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    FlowRegime,
    ForcingType,
    InterpolationMethod,
    ObservationSource,
    QcStatus,
    ThresholdSource,
)
from sapphire_flow.types.forecast import HindcastForecast
from sapphire_flow.types.ids import (
    BMA_MODEL_ID,
    POOLED_MODEL_ID,
    ArtifactId,
    HindcastForecastId,
    MeasurementFeedEvidenceId,
    ModelId,
    ObservationId,
    RatingCurveId,
    RatingReferenceProofId,
)
from sapphire_flow.types.observation import Observation
from sapphire_flow.types.rating_curve import RatingCurve
from sapphire_flow.types.rating_reference import (
    CurveSnapshot,
    MeasurementFeedEvidence,
    MeasurementSnapshot,
    RatingReferenceProof,
)
from sapphire_flow.types.skill import FlowRegimeConfig
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
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
    from collections.abc import Callable, Iterator

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


PROTECTED_CURVE_CONTENT = (
    '{"created_at":"2026-01-09T00:00:00+00:00'
    '","delivery_id":null,"id":"00000000-0000'
    '-0000-0000-0000000124f9","interpolation"'
    ':"linear","points":[{"discharge":7.0,"wa'
    'ter_level":1.0},{"discharge":10000.0,"wa'
    'ter_level":2.0}],"rating_type_label":nul'
    'l,"station_id":"00000000-0000-0000-0000-'
    '00000000ee48","uploaded_by":null,"valid_'
    'from":"2025-01-10T00:00:00+00:00","valid'
    '_to":"2026-01-10T01:05:00+00:00","versio'
    'n":1}'
)
PROTECTED_PROOF_CONTENT = (
    '{"api_station_id":61000,"curve":{"conten'
    't":"{\\"created_at\\":\\"2026-01-09T00:00:0'
    '0+00:00\\",\\"delivery_id\\":null,\\"id\\":\\"'
    '00000000-0000-0000-0000-0000000124f9\\",\\'
    '"interpolation\\":\\"linear\\",\\"points\\":['
    '{\\"discharge\\":7.0,\\"water_level\\":1.0},'
    '{\\"discharge\\":10000.0,\\"water_level\\":2'
    '.0}],\\"rating_type_label\\":null,\\"statio'
    'n_id\\":\\"00000000-0000-0000-0000-0000000'
    '0ee48\\",\\"uploaded_by\\":null,\\"valid_fro'
    'm\\":\\"2025-01-10T00:00:00+00:00\\",\\"vali'
    'd_to\\":\\"2026-01-10T01:05:00+00:00\\",\\"v'
    'ersion\\":1}"},"curve_reference":"gauge_z'
    'ero","curve_unit":"m","endpoint":"https:'
    '//example.invalid/levels/","evidence_ref'
    'erence":"skill-p2-january-fixture","id":'
    '"00000000-0000-0000-0000-0000000124fa","'
    'level_reference":"gauge_zero","level_uni'
    't":"m","offset_m":0.0,"rating_curve_id":'
    '"00000000-0000-0000-0000-0000000124f9","'
    'station_id":"00000000-0000-0000-0000-000'
    '00000ee48","tenant_id":"00000000-0000-00'
    '00-0000-000000000001","verified_at":"202'
    '6-01-10T01:10:00+00:00","verified_by":"f'
    'ixture-reviewer"}'
)
PROTECTED_PROOF_HASH = (
    "b43427894efdb6875015a29a80ea8acbd0fe7dd18d14d9555b134a2245a15160"
)
PROTECTED_FEED_HASHES = (
    "cd8d6fc14930b1f9dccf0a1cc095a099c940d3796fe2d024940955984a8a5655",
    "e3f54098c8fbfd607d314318b20f3a7eff515c78f5b657a7b4e5a3d5745ff0e9",
    "85a13b619359a121af5a6f1a3cea1664a0dc2565e16048a96959bc7d016732a9",
    "75ee9adc7e74be3ecc0092f663e51d7850699d5bba0b9f751c49b7ec4d1decdc",
    "6dfb35ab9536472f953c4fef651c73b8104ef7395c09debb0c55d94b820649fd",
    "6587228e8771cb602bfdbe744e18441a146c3df012ab6a7dd89ccbb73ceebfa1",
    "41c123bed02d29878be0e391078b4f017361de7bc72890aa1231d8c5f65a48fc",
    "4fc31f8128005967854a8d52428bb4c30e6b3473bf0fc312058e8c3151ee515d",
    "d3059cb93da31e2d6df20cd8dbc62b4b081efc3a5cf77a351fea2a1ea8ad8fc4",
    "f60c359bff4a92568cc4ac24af8f810ff2385393371fa4ca17211e5684bd71c7",
    "9b7b23ef90afdb9134d13e7cca8523018e06efbb24b081e96569ce77b1b8b964",
    "24255a3d1d80f7c7e9b9fa4410a8b2b4ed4b70c7683ce9ca1de4a5222e97c97f",
    "ee2f8d325854a41c06814ad43ccda4418ef30a3d89b87d73feee7c0857109b3d",
    "1b2a5eb0c114e0ca34de82133f88a6937ec03c8a6fe03c7a94c8686b7ba08b63",
    "247d0df61c57f33a6355a06bdb0a85d956e7278eb9f45e070b658efff51b3a5f",
    "5b1c4c2f7d28d631b24f26344b87c8a6f703c276ece53d2f8497b98de8ad0d49",
    "5f1eb9760e8c812df8078cd4a57143e6791b515d54274504f2ec58fae37a85ac",
    "a559444a3a51d9a45dd2ad6121d9d19ffc2dd194cfe75c9fffe6b8079222b937",
    "e730b878fc0a733d80e700af21e219c863f58d6fe946814fcd862c35d0fbea79",
    "a34e0941e7ddd77976c67435a45803cd6d7d39fc22d97fae82428f058d270042",
    "20ee3c3c153301047f96f227209da97e5d877fd00eccf05af9b3f89e0409555f",
    "d51dec7d983138520354140de1e1ff96a46372caec31136030ab64207e946e33",
    "4077fa8f8d6c1a05b5e89bff0abd361fddfb7526faee1d32df245a28ce236891",
    "d52813bfe2aeb5872adab8c72851a957fb3c5d4bc57b460f983efb82ebefb3a9",
    "b8afb02c44369e3f8f8154d836460d1ace96108f6f2c4a4d0daf7098585ba250",
    "579f6a8ad72a9c531e95990911d31233e90eeff601914362bc3e28827a407278",
    "08f799d6893efa7ba79ee5615aa3f8f8c68bc21d218befcbd28a14f12fd26131",
    "e11084028a67d9a1f29a6904ae4760865ca33ceca677c07204573d2cc56961ca",
    "73fb9ccdf05cac34730f63414f6ae4df217402ef14119cd6d3db4e5507e0f560",
    "a7a8236df853cbe8ac6a78a07b817c1a712023b8bebddd3f29f51eb3610f89fa",
    "0f297bc0ac6842dc8bf8aa5bc3bfec994e4a39aa72a54a08d471630bf3d1dafc",
    "a4fbf90c78f007dd3671bd3d7d7beb9c3c380fead22d0bd52fe89a29b76201b0",
    "e98ab313b33488b24164486a3f13f9cc56ea66afaa70cf884f5750959c09514b",
    "49fdbe4d897ace847094a58c621b7f2cd4a454f6794e36e9d59d88285b873627",
    "ab2d228437afa73fda14721080f0ccaf6bae060ba2e09dc2de0d733ab55e11c1",
    "a8831915f05d31f3d72ae7c4aaa1733b7c7eaf9952e0c15033876e3cd4086381",
    "9b71e67ef7abf6cafdb2d495d925c490f1c7b4d87d0240aff42d2ad2fbe2fbdc",
    "de76489bbed5b37dc858b354ffab331a6c3cf56b25e715d1c1160ae5628bd8e0",
    "4ef100e70aae69658f5165905bb2166e4a82733325eb3c90202870ff912b9a27",
    "c1fa44f2b208df47365f49ab118159df2cbf9574445027cae3b4b8168a44f133",
    "871c8046b2d9b38da85f96fcb54eed6a93ec3b564519dc8729842ba9c1e3c9cc",
    "2f01e0ad1a5a34eb9bd3a17c444492e35891e665bced94e1ec59023cdbe155f4",
    "8c656a7beff27e43fcf73d1fdaa212db11e93ce46c31c00734ef7188b78a684e",
    "084f40983d5317ad6530b04335bc2c94083042f7f5f4ed3427e4d136395abd1d",
    "4f2ea1baafef3edcbdc02cd63b7ef01d045574cb36e182ffac142727e85897e1",
    "57ed5ae8839301cb7256196cb58a5f1af018f43f4e51adf17943eb5ff3268b7f",
    "5b540f82225fd8c9ec44f74caffc61d14c472804c3ab0268f81f910d5d551be6",
    "72994b562c35782afcd0bc4ef3bfa82118fb7e8f64bdcd4105f6090f62ec9819",
)
PROTECTED_CONTENT_HASHES = (
    "2c17de4df6d4235b28f43c8770e9c0ba45e0a6c90d37c89ed9670b539964e1f1",
    "48344a66b275bc7586d0092b1d8398f5926641aa1d992d34107dcf6788dba0c7",
    "cdb9ca44bf22aa7a5fd7fc0a1c7957c6951efa2ef3a422f0d022bad19139a362",
    "2e1f8189d04491c6a3f01f3af1a5e29bed15f528c26fff42f865c78eabc9fc5e",
    "0925b5ab7ef51f22343d887f3d072e7070458cbeba5efeb4bc31fcfc024a7216",
    "26760750d62b852f9f7e9718a2d8454615a7956ed83128a26941bfec9d5b7899",
    "7b97923c57879c2127c27c08d4d2484df03ff0bfc37bf98e53a1142c40eee362",
    "4554930370d6f0e067546be0238489575997f9ae5c241ac666367d98ef9e7607",
    "bea111553fb0221ec1cbb68a4218510c676808345d2be51a854c1bdf3577b4b8",
    "7ca15625da7005100b25114bdf17ac137234ff9a0acb4c7ee2a69dff3c7d8469",
    "cf98d94533de95a680bed534a87762c9d6137df621fe5900079e6ecf46e7eecf",
    "9d59d766b10e3490082556b45fa494ccd7d6f23f9e00724f38360a9e7df1eec2",
    "56db18660d2cbe033ca53acd2f46b59d4715a6b7edb2e2a23407bfa19d035657",
    "fb9bb932532bec5a3e9e2ca4f0a57de9bcbc62523799fbaf7a3fdc7e4faa121f",
    "04f905b2bf7d0556fd04fc29b6e1c080f18d1be2f2f2b538635bd4d9d36a5f91",
    "9653649a4f6f949ba1c04117a724f1ae3e428d59142ed1989c53bb02d69f8aba",
    "4b3bc7388231d5c4e93e8d3887ce3c1d22d81411c9ea9f422793598dc360b1d0",
    "370792de432abdaaf76d700ac261c2955335d0e8c524b7e0c93d068ca5459ed6",
    "fec1fb8af9e93f2bc1967b2b87ce89fb88f9bac56d0a25f5dc9b17a3e9402bad",
    "891a41037e4c846c9abf51ca03ab33e40c1b5864edbaf7802b41efe745e4481e",
    "6f4b987e091256090741844f487f1de433a4afbfe92256073a3f20bd33c1c0c2",
    "06d392c36adebf8f51780c9d3f4d8331e8a59850a6e10e7acd7bd1c097873c69",
    "1f7b8118d05bc563c18b4be515b42ec4176efe3fd8f8a4a2bb33294cd7959a21",
    "c9a50e7a55c1794b764764e95eee1a8c8ea99e8d1dafdbc3954a498ad9b09987",
    "60bdb9c1e71847bc275e0fc2495920f4c5cbf71d426ce6d417a56cc34204df21",
    "441b25fe84c59b849d48281cea9023f54e77d4d56fcac97bb40c4b9881f42a57",
    "47b7f782b4088343993bb648067638a568c6646f9341adbd833c20934fe7bb81",
    "98986fc6c196c88a319d36d1320d7f9e6da008bc0c66a28d44ebff5503883209",
    "4a3b6e32116dffad1e3f33a05df69781763a6950191f51b8622be2904c27502d",
    "bde85f343668b387f1a857a819f56acdc1667672b28e3750bb102d7bec9e53ae",
    "418ed81a3bf13c90d5619316c74ea3ec8559867539518307ccce66e04732b128",
    "6d9ae61327d22e3bf8e1cd9e46a5bc7c19b2626263a2242501a24e14a918b236",
    "dc59c5d3c2fe793c77c11476248027eee9b20ca1c140a803c6512f8f918d99c4",
    "68e1c23a26b4a166223ca5c68024c2a2a984e618bd8ec8e1986a7b314a1c65b3",
    "833fb484e7c282f769d7072e8a284f2bb4435040ab7bcb9676df2358c792a372",
    "c7ac864ce7b916c5bace48cc366f55d3eaf5e47765da787b223db05b0eae1137",
    "d980f6aa1bd191255e9287f41ae670adafb23b5adeb920e3ae6fd53250a9cd63",
    "265d5b584322d0e856ac0fcceb7311e11eeb2be1dec32a0e3a18c731cdd2bb72",
    "905e7d26d6de658fe5b4403429139d34e5d5ac039f570d24ff53fe6a9ed31c19",
    "b65b929e28e040a4111a09717c2a74b7a6fc90877eae02f39ec2a3958a02fb47",
    "19799a9278b9ee165dc416f03ace17097319965391d03996354d7c715fe1ebeb",
    "3641c56c4df5c42bd7cd79d9df30a37146d4e6958ab2fa5c1c80de91bbcf1bd0",
    "13b59e3c2394b5ae57b402689ca48f2a02999f118daa4a79e0701b81ec590ea4",
    "b676f0fb71745fdab5cb3f64829bba6253b626a85d0bee3725f7f83307a762c4",
    "0349c2ee8077b425f2fbdc24ebedb1eb2dfcd02a4a59d22a38f0c3ec921e4d55",
    "a711b8026cd29263a646c17c1e6906a89b5ab82d0bfa22117dc471bd27824d77",
    "2d24c0e6780def562bbfb8a14e24a4a64c6b4c1507dbadb4771f8877a10797f2",
    "676c36d3ce35eeba55557837f79677e5c5f239e1a1d3a5594ff9243565c37da0",
)


def fixture_json(value: object) -> str:
    def encode(item: object) -> str:
        if isinstance(item, (datetime, UUID)):
            return item.isoformat() if isinstance(item, datetime) else str(item)
        if isinstance(item, Enum):
            return str(item.value)
        raise TypeError(f"Unsupported fixture value: {type(item).__name__}")

    return json.dumps(
        value, default=encode, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def ordinary_manifest(perturbation: Perturbation | None = None) -> list[Observation]:
    return [
        Observation(
            id=ObservationId(
                UUID(int=random.Random(100 * i + j).getrandbits(128), version=4)
            ),
            station_id=SID,
            timestamp=ensure_utc(T + timedelta(hours=2 * i + 1, minutes=15 * j)),
            parameter="discharge",
            value=float(
                q
                + offset
                + (
                    24
                    if perturbation is not None
                    and i == perturbation_index(perturbation)
                    and j == 1
                    else 0
                )
            ),
            source=ObservationSource.RATING_CURVE_DERIVED
            if i == j == 0
            else ObservationSource.MANUAL_IMPORT,
            rating_curve_id=RatingCurveId(UUID(int=75001)) if i == j == 0 else None,
            rating_curve_correction_version="skill-p2-valid-v1"
            if i == j == 0
            else None,
            qc_status=QcStatus.QC_PASSED,
            qc_flags=[],
            qc_rule_version=None,
            created_at=C,
            delivery_id=None,
        )
        for i, q in enumerate(Q)
        for j, offset in enumerate((-3, -1, 1, 3))
    ]


def protected_measurement(index: int) -> Observation:
    bucket, position = divmod(index, 3)
    timestamp = ensure_utc(
        T + timedelta(hours=2 * bucket + 1, seconds=(900, 1350, 3150)[position])
    )
    return Observation(
        id=ObservationId(UUID(int=76000 + index)),
        station_id=SID,
        timestamp=timestamp,
        parameter="water_level",
        value=2.0,
        source=ObservationSource.MEASURED,
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=QcStatus.QC_PASSED,
        qc_flags=[
            QcFlag(rule_id="range_check", rule_version="1", status=QcStatus.QC_PASSED)
        ],
        qc_rule_version="skill-p2-level-v1",
        created_at=ensure_utc(timestamp + timedelta(seconds=1)),
        delivery_id=None,
    )


def seed_protected_lineage(
    conn: sa.Connection,
    presence: Literal["absent", "present"],
    perturbation: Perturbation | None = None,
) -> None:
    from sapphire_flow.services.provisional_discharge import (
        convert_provisional_discharge,
    )
    from sapphire_flow.store.provisional_discharge_store import (
        PgProvisionalDischargeStore,
    )
    from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
    from tests.integration.store.test_provisional_discharge_store import (
        permit_fixture,
        seed_reference,
    )

    seed_clean(conn)
    permit_fixture(conn, DEFAULT_TENANT_ID)
    curve = RatingCurve(
        id=RatingCurveId(UUID(int=75001)),
        station_id=SID,
        version=1,
        valid_from=ensure_utc(T - timedelta(days=365)),
        valid_to=ensure_utc(T + timedelta(hours=1, minutes=5)),
        points=[
            {"water_level": 1.0, "discharge": 7.0},
            {"water_level": 2.0, "discharge": 10000.0},
        ],
        interpolation=InterpolationMethod.LINEAR,
        uploaded_by=None,
        created_at=ensure_utc(T - timedelta(days=1)),
    )
    assert fixture_json(dataclasses.asdict(curve)) == PROTECTED_CURVE_CONTENT
    curves = PgRatingCurveStore(conn)
    curves.store_rating_curve(curve)
    assert [
        fixture_json(dataclasses.asdict(c))
        for c in curves.fetch_all_curves_for_station(SID)
    ] == [PROTECTED_CURVE_CONTENT]
    proof = RatingReferenceProof(
        id=RatingReferenceProofId(UUID(int=75002)),
        tenant_id=DEFAULT_TENANT_ID,
        station_id=SID,
        rating_curve_id=curve.id,
        curve=CurveSnapshot(content=PROTECTED_CURVE_CONTENT),
        endpoint="https://example.invalid/levels/",
        api_station_id=61000,
        level_unit="m",
        curve_unit="m",
        level_reference="gauge_zero",
        curve_reference="gauge_zero",
        offset_m=0.0,
        evidence_reference="skill-p2-january-fixture",
        verified_by="fixture-reviewer",
        verified_at=ensure_utc(T + timedelta(hours=1, minutes=10)),
    )
    assert fixture_json(dataclasses.asdict(proof)) == PROTECTED_PROOF_CONTENT
    seed_reference(conn, db.rating_reference_proofs, proof)
    first = ordinary_manifest()[0]
    changed = conn.execute(
        sa.update(db.observations)
        .where(db.observations.c.id == first.id)
        .values(
            source=first.source.value,
            rating_curve_id=first.rating_curve_id,
            rating_curve_correction_version=first.rating_curve_correction_version,
        )
    )
    assert changed.rowcount == 1
    assert curve.valid_from <= first.timestamp < cast("datetime", curve.valid_to)
    stores = make_pg_stores(conn)
    station = cast("PgStationStore", stores["station_store"]).fetch_station(SID)
    assert station is not None
    assert station.tenant_id == DEFAULT_TENANT_ID == UUID(int=1) == proof.tenant_id
    permission = (
        conn.execute(sa.select(db.provisional_discharge_permissions)).mappings().one()
    )
    assert permission["tenant_id"] == station.tenant_id
    assert permission["state"] == "enabled"
    assert permission["permission_reference"] == "fixture-only"
    assert permission["inventory_digest"] == "a" * 64
    proof_row = conn.execute(sa.select(db.rating_reference_proofs)).mappings().one()
    assert {
        k: proof_row[k]
        for k in (
            "id",
            "tenant_id",
            "station_id",
            "rating_curve_id",
            "content",
            "fingerprint",
        )
    } == {
        "id": proof.id,
        "tenant_id": DEFAULT_TENANT_ID,
        "station_id": SID,
        "rating_curve_id": curve.id,
        "content": PROTECTED_PROOF_CONTENT,
        "fingerprint": PROTECTED_PROOF_HASH,
    }
    obs_store = cast("PgObservationStore", stores["obs_store"])
    baseline = assert_ordinary_manifest(obs_store)
    if perturbation is not None:
        target = baseline[4 * perturbation_index(perturbation) + 1]
        modified = conn.execute(
            sa.update(db.observations)
            .where(db.observations.c.id == target.id)
            .values(value=db.observations.c.value + 24.0)
        )
        assert modified.rowcount == 1
        after = assert_ordinary_manifest(obs_store, perturbation)
        changed_fields = {
            before.id: {
                key
                for key, value in dataclasses.asdict(before).items()
                if dataclasses.asdict(current)[key] != value
            }
            for before, current in zip(baseline, after, strict=True)
        }
        assert {key: fields for key, fields in changed_fields.items() if fields} == {
            target.id: {"value"}
        }
    protected_store = PgProvisionalDischargeStore(conn)
    for index in range(48 if presence == "present" else 0):
        measurement = protected_measurement(index)
        obs_store.store_observations([measurement])
        snapshot_fields = dataclasses.asdict(measurement)
        qc = {
            key: snapshot_fields.pop(key)
            for key in ("qc_status", "qc_flags", "qc_rule_version")
        }
        snapshot_text = fixture_json(snapshot_fields)
        feed = MeasurementFeedEvidence(
            id=MeasurementFeedEvidenceId(UUID(int=77000 + index)),
            tenant_id=DEFAULT_TENANT_ID,
            station_id=SID,
            observation_id=measurement.id,
            measurement=MeasurementSnapshot(content=snapshot_text),
            endpoint="https://example.invalid/levels/",
            api_station_id=61000,
            evidence_reference="skill-p2-january-fixture",
            verified_by="fixture-reviewer",
            verified_at=ensure_utc(measurement.timestamp + timedelta(seconds=2)),
        )
        assert station.tenant_id == feed.tenant_id
        assert (
            cast("datetime", curve.valid_to)
            < measurement.timestamp
            < feed.verified_at
            <= C
        )
        assert proof.verified_at <= C
        seed_reference(conn, db.measurement_feed_evidence, feed)
        converted = convert_provisional_discharge(
            observation=measurement,
            curves=[curve],
            feed_evidence=feed,
            reference_proof=proof,
            at=C,
        )
        expected_content = fixture_json(
            {
                "measurement": json.loads(snapshot_text),
                "qc": qc,
                "curve": json.loads(PROTECTED_CURVE_CONTENT),
                "feed_evidence": dataclasses.asdict(feed),
                "reference_proof": json.loads(PROTECTED_PROOF_CONTENT),
                "conversion_version": "expired-rating-linear-reference-v1",
                "discharge": 10000.0,
            }
        )
        assert converted.content == expected_content
        assert converted.fingerprint == PROTECTED_CONTENT_HASHES[index]
        assert converted.discharge == 10000.0
        assert converted.measurement.content == snapshot_text
        assert converted.curve.content == PROTECTED_CURVE_CONTENT
        assert (
            converted.tenant_id,
            converted.station_id,
            converted.observation_id,
            converted.rating_curve_id,
            converted.feed_evidence_id,
            converted.reference_proof_id,
        ) == (
            DEFAULT_TENANT_ID,
            SID,
            measurement.id,
            curve.id,
            feed.id,
            proof.id,
        )
        assert (
            protected_store.store_provisional_discharge(converted, captured_at=C)
            == PROTECTED_CONTENT_HASHES[index]
        )
        feed_row = (
            conn.execute(
                sa.select(db.measurement_feed_evidence).where(
                    db.measurement_feed_evidence.c.id == feed.id
                )
            )
            .mappings()
            .one()
        )
        assert {
            k: feed_row[k]
            for k in (
                "id",
                "tenant_id",
                "station_id",
                "observation_id",
                "content",
                "fingerprint",
            )
        } == {
            "id": feed.id,
            "tenant_id": DEFAULT_TENANT_ID,
            "station_id": SID,
            "observation_id": measurement.id,
            "content": fixture_json(dataclasses.asdict(feed)),
            "fingerprint": PROTECTED_FEED_HASHES[index],
        }
        stored = (
            conn.execute(
                sa.select(db.provisional_discharges).where(
                    db.provisional_discharges.c.fingerprint
                    == PROTECTED_CONTENT_HASHES[index]
                )
            )
            .mappings()
            .one()
        )
        assert dict(stored) == {
            "fingerprint": PROTECTED_CONTENT_HASHES[index],
            "tenant_id": DEFAULT_TENANT_ID,
            "station_id": SID,
            "observation_id": measurement.id,
            "rating_curve_id": curve.id,
            "feed_evidence_id": feed.id,
            "reference_proof_id": proof.id,
            "discharge": 10000.0,
            "content": expected_content,
            "captured_at": C,
        }
    expected_count = 48 if presence == "present" else 0
    persisted_levels = obs_store.fetch_observations(SID, "water_level", T, C)
    assert len(persisted_levels) == expected_count
    assert {row.id: dataclasses.asdict(row) for row in persisted_levels} == {
        row.id: dataclasses.asdict(row)
        for row in [protected_measurement(i) for i in range(expected_count)]
    }
    assert (
        conn.scalar(sa.select(sa.func.count()).select_from(db.provisional_discharges))
        == expected_count
    )
    assert (
        conn.scalar(
            sa.select(sa.func.count()).select_from(db.measurement_feed_evidence)
        )
        == expected_count
    )
    assert (
        conn.scalar(sa.select(sa.func.count()).select_from(db.observations))
        == 64 + expected_count
    )


def assert_ordinary_manifest(
    store: PgObservationStore, perturbation: Perturbation | None = None
) -> list[Observation]:
    actual = store.fetch_observations(SID, "discharge", T, C)
    expected = ordinary_manifest(perturbation)
    assert len(actual) == len({row.id for row in actual}) == 64
    assert {row.id: dataclasses.asdict(row) for row in actual} == {
        row.id: dataclasses.asdict(row) for row in expected
    }
    assert all(len(dataclasses.asdict(row)) == 13 for row in actual)
    return actual


def assert_protected_buckets(conn: sa.Connection, ordinary: list[Observation]) -> None:
    p, o, f, r = (
        db.provisional_discharges,
        db.observations,
        db.measurement_feed_evidence,
        db.rating_reference_proofs,
    )
    joined = (
        conn.execute(
            sa.select(
                o.c.id,
                o.c.timestamp,
                p.c.fingerprint,
                f.c.id.label("feed"),
                r.c.id.label("proof"),
            )
            .select_from(
                p.join(o, p.c.observation_id == o.c.id)
                .join(f, p.c.feed_evidence_id == f.c.id)
                .join(r, p.c.reference_proof_id == r.c.id)
            )
            .order_by(o.c.timestamp)
        )
        .mappings()
        .all()
    )
    assert len(joined) == 48
    for bucket in range(16):
        start = T + timedelta(hours=2 * bucket + 1)
        end = start + timedelta(hours=1)
        # Query actual stored discharge rows, never the water-level parent maximum.
        actual = conn.execute(
            sa.select(o.c.id, o.c.timestamp)
            .where(
                o.c.station_id == SID,
                o.c.parameter == "discharge",
                o.c.qc_status == "qc_passed",
                o.c.timestamp >= start,
                o.c.timestamp < end,
            )
            .order_by(o.c.timestamp)
        ).all()
        assert [(row.id, row.timestamp) for row in actual] == [
            (row.id, row.timestamp) for row in ordinary[4 * bucket : 4 * bucket + 4]
        ]
        latest = max(row.timestamp for row in actual)
        assert latest == start + timedelta(minutes=45)
        within = [row for row in joined if start <= row["timestamp"] < end]
        assert len(within) == 3
        assert (
            start
            <= within[0]["timestamp"]
            == actual[1].timestamp
            < within[1]["timestamp"]
            < latest
            < within[2]["timestamp"]
            < end
        )
        for offset, row in enumerate(within):
            index = 3 * bucket + offset
            assert dict(row) == {
                "id": UUID(int=76000 + index),
                "timestamp": protected_measurement(index).timestamp,
                "fingerprint": PROTECTED_CONTENT_HASHES[index],
                "feed": UUID(int=77000 + index),
                "proof": UUID(int=75002),
            }


@pytest.fixture
def protected_pair_connection(
    skill_persistence_engine: sa.Engine,
) -> Iterator[sa.Connection]:
    with skill_persistence_engine.connect() as conn:
        outer = conn.begin()
        try:
            assert conn.scalar(sa.text("SELECT current_user")) == "test"
            yield conn
        finally:
            outer.rollback()


@contextmanager
def protected_scenario(
    conn: sa.Connection,
    presence: Literal["absent", "present"],
    perturbation: Perturbation | None = None,
) -> Iterator[tuple[CleanCase, list[Observation]]]:
    assert conn.scalar(sa.text("SELECT current_user")) == "test"
    scenario = conn.begin_nested()
    try:
        seed_protected_lineage(conn, presence, perturbation)
        stores = make_pg_stores(conn)
        obs_store = cast("PgObservationStore", stores["obs_store"])
        ordinary = assert_ordinary_manifest(obs_store, perturbation)
        if presence == "present":
            assert_protected_buckets(conn, ordinary)
        conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
        assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
        with pytest.raises(DBAPIError, match="permission denied"), conn.begin_nested():
            conn.execute(sa.select(db.provisional_discharges))
        levels = obs_store.fetch_observations(SID, "water_level", T, C)
        assert {row.id: dataclasses.asdict(row) for row in levels} == {
            row.id: dataclasses.asdict(row)
            for row in [
                protected_measurement(i)
                for i in range(48 if presence == "present" else 0)
            ]
        }
        assert assert_ordinary_manifest(obs_store, perturbation) == ordinary
        yield (
            CleanCase(
                connection=conn,
                skill_store=cast("PgSkillStore", stores["skill_store"]),
                hindcast_store=cast("PgHindcastStore", stores["hindcast_store"]),
                obs_store=obs_store,
                station_store=cast("PgStationStore", stores["station_store"]),
                flow_regime_store=cast(
                    "PgFlowRegimeConfigStore", stores["flow_regime_store"]
                ),
                owner_role="test",
            ),
            ordinary,
        )
        assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
        assert assert_ordinary_manifest(obs_store, perturbation) == ordinary
    finally:
        scenario.rollback()
    assert conn.scalar(sa.text("SELECT current_user")) == "test"


PERTURBED_SCALARS: dict[str, dict[str, dict[str, dict[str, str]]]] = {
    "P_FIRST": {
        "SINGLE": {
            "ALL": {"crps": "5/4", "mae": "5/4", "pbias": "500/139"},
            "LOW": {"crps": "1", "mae": "1", "pbias": "25/3"},
            "HIGH": {"crps": "1", "mae": "1", "pbias": "125/23"},
            "FLOOD": {"crps": "9/5", "mae": "9/5", "pbias": "-50/57"},
        },
        "POOLED": {
            "ALL": {"crps": "2", "mae": "3", "pbias": "2100/139"},
            "LOW": {"crps": "2", "mae": "3", "pbias": "25"},
            "HIGH": {"crps": "2", "mae": "3", "pbias": "375/23"},
            "FLOOD": {"crps": "2", "mae": "3", "pbias": "150/19"},
        },
        "BMA": {
            "ALL": {"crps": "6739/5000", "mae": "5/4", "pbias": "4400/1207"},
            "LOW": {"crps": "2957/2500", "mae": "1", "pbias": "25/3"},
            "HIGH": {"crps": "2957/2500", "mae": "1", "pbias": "925/171"},
            "FLOOD": {"crps": "4057/2500", "mae": "5/3", "pbias": "-25/782"},
        },
    },
    "P_SECOND": {
        "SINGLE": {
            "ALL": {"crps": "5/4", "mae": "5/4", "pbias": "500/139"},
            "LOW": {"crps": "1", "mae": "1", "pbias": "25/3"},
            "HIGH": {"crps": "1", "mae": "1", "pbias": "125/23"},
            "FLOOD": {"crps": "9/5", "mae": "9/5", "pbias": "-50/57"},
        },
        "POOLED": {
            "ALL": {"crps": "2", "mae": "3", "pbias": "2100/139"},
            "LOW": {"crps": "2", "mae": "3", "pbias": "25"},
            "HIGH": {"crps": "2", "mae": "3", "pbias": "375/23"},
            "FLOOD": {"crps": "2", "mae": "3", "pbias": "150/19"},
        },
        "BMA": {
            "ALL": {"crps": "6739/5000", "mae": "5/4", "pbias": "4400/1207"},
            "LOW": {"crps": "2957/2500", "mae": "1", "pbias": "25/3"},
            "HIGH": {"crps": "2957/2500", "mae": "1", "pbias": "925/171"},
            "FLOOD": {"crps": "4057/2500", "mae": "5/3", "pbias": "-25/782"},
        },
    },
}

PERTURBED_PROBABILITIES: dict[str, dict[str, tuple[int, ...]]] = {
    "P_FIRST": {
        "SINGLE": (0, 0, 0, 0, 100, 100, 0, 100, 0, 0, 0, 0, 100, 100, 0, 100),
        "POOLED": (0, 0, 50, 50, 100, 100, 0, 100, 0, 0, 50, 50, 100, 100, 0, 100),
        "BMA": (0, 0, 17, 17, 100, 100, 0, 100, 0, 0, 25, 25, 100, 100, 0, 100),
    },
    "P_SECOND": {
        "SINGLE": (0, 0, 0, 0, 100, 100, 0, 100, 0, 0, 0, 0, 100, 100, 0, 100),
        "POOLED": (0, 0, 50, 50, 100, 100, 0, 100, 0, 0, 50, 50, 100, 100, 0, 100),
        "BMA": (0, 0, 25, 25, 100, 100, 0, 100, 0, 0, 17, 17, 100, 100, 0, 100),
    },
}

Perturbation = Literal["P_FIRST", "P_SECOND"]


def perturbation_index(perturbation: Perturbation) -> int:
    return {"P_FIRST": 2, "P_SECOND": 10}[perturbation]


def perturbed_indices(regime: RegimeKey, perturbation: Perturbation) -> list[int]:
    changed = perturbation_index(perturbation)
    truth = [q + (6 if i == changed else 0) for i, q in enumerate(Q)]
    return [
        i
        for i, q in enumerate(truth)
        if regime == "ALL"
        or ("LOW" if q <= 15 else "FLOOD" if q > 21 else "HIGH") == regime
    ]


def perturbed_payload(
    strategy: Strategy,
    regime: RegimeKey,
    kind: DiagramKind,
    perturbation: Perturbation,
    layer: Literal["domain", "json"],
) -> dict[str, object]:
    selected = perturbed_indices(regime, perturbation)
    changed = perturbation_index(perturbation)
    truth = [q + (6 if i == changed else 0) for i, q in enumerate(Q)]
    probabilities = PERTURBED_PROBABILITIES[perturbation][strategy]
    undefined = float("nan") if layer == "domain" else None
    if kind == "rank_histogram":
        members = {"SINGLE": 2, "POOLED": 4, "BMA": 100}[strategy]
        counts = [0] * (members + 1)
        for index in selected:
            counts[members if index == changed else 0] += 1
        return {"ranks": list(range(members + 1)), "counts": counts}
    if kind == "reliability":
        counts = [0] * 10
        events = [0] * 10
        for index in selected:
            bin_index = min(probabilities[index] // 10, 9)
            counts[bin_index] += 1
            events[bin_index] += int(truth[index] > 19)
        centers = [float(Fraction(2 * i + 1, 20)) for i in range(10)]
        return {
            "bins": centers,
            "forecast_freq": centers,
            "sample_counts": counts,
            "observed_freq": [
                float(Fraction(e, c)) if c else undefined
                for e, c in zip(events, counts, strict=True)
            ],
        }
    n_events = sum(truth[index] > 19 for index in selected)
    n_non_events = len(selected) - n_events
    return {
        "thresholds": [float(Fraction(j, 100)) for j in range(101)],
        "n_events": n_events,
        "n_non_events": n_non_events,
        "hit_rate": [
            float(
                Fraction(
                    sum(probabilities[i] >= j and truth[i] > 19 for i in selected),
                    n_events,
                )
            )
            if n_events
            else undefined
            for j in range(101)
        ],
        "false_alarm_rate": [
            float(
                Fraction(
                    sum(probabilities[i] >= j and truth[i] <= 19 for i in selected),
                    n_non_events,
                )
            )
            if n_non_events
            else undefined
            for j in range(101)
        ],
    }


ReplayRows = dict[UUID, dict[str, object]]


@dataclass(frozen=True, kw_only=True, slots=True)
class ReplaySnapshot:
    rows: dict[str, ReplayRows]
    scores: ReplayRows
    diagrams: ReplayRows


@dataclass(frozen=True, kw_only=True, slots=True)
class ReplayWrite:
    table: str
    submitted: ReplayRows
    before: ReplaySnapshot
    after: ReplaySnapshot

    def inserted_ids(self) -> set[UUID]:
        before = set(self.before.rows[self.table])
        after = set(self.after.rows[self.table])
        assert not before - after
        return after - before


def replay_snapshot(case: CleanCase, strategy: Strategy) -> ReplaySnapshot:
    conn = case.connection
    assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
    rows: dict[str, ReplayRows] = {}
    for table in (db.skill_scores, db.skill_diagrams, db.skill_generations):
        scope = (
            table.c.station_id == SID,
            table.c.model_id == MODEL_IDS[strategy],
            table.c.model_artifact_id.is_(None)
            if strategy != "SINGLE"
            else table.c.model_artifact_id == AID,
            table.c.parameter == "discharge",
            sa.select(db.stations.c.id)
            .where(
                db.stations.c.id == table.c.station_id,
                db.stations.c.tenant_id == DEFAULT_TENANT_ID,
            )
            .exists(),
        )
        result = conn.execute(sa.select(table).where(*scope)).mappings().all()
        rows[table.name] = {cast("UUID", row["id"]): dict(row) for row in result}
    scores = case.skill_store.fetch_latest_scores(
        SID, MODEL_IDS[strategy], parameter="discharge"
    )
    diagrams = case.skill_store.fetch_latest_diagrams(
        SID, MODEL_IDS[strategy], parameter="discharge"
    )
    assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
    return ReplaySnapshot(
        rows=rows,
        scores={row.id: dataclasses.asdict(row) for row in scores},
        diagrams={row.id: dataclasses.asdict(row) for row in diagrams},
    )


class ReplayParameterContext(Protocol):
    compiled_parameters: list[dict[str, object]]


class ReplayObserver:
    def __init__(
        self,
        case: CleanCase,
        strategy: Strategy,
        record: Callable[[str, object], None],
    ) -> None:
        self.case = case
        self.record = record
        self.strategy: Strategy = strategy
        self.events: list[ReplayWrite] = []
        self.pending: tuple[object, str, ReplayRows, ReplaySnapshot] | None = None
        self.busy = False

    def before(
        self,
        conn: sa.Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: sa.engine.ExecutionContext,
        executemany: bool,
    ) -> None:
        from copy import deepcopy

        from sqlalchemy.sql.dml import Insert

        if self.busy or context.compiled is None:
            return
        clause = context.compiled.statement
        if not isinstance(clause, Insert):
            return
        table = cast("sa.Table", clause.table)
        if table.name not in {"skill_scores", "skill_diagrams", "skill_generations"}:
            return
        assert conn is self.case.connection
        assert not executemany and self.pending is None
        self.busy = True
        try:
            parameter_sets = cast("ReplayParameterContext", context).compiled_parameters
            assert len(parameter_sets) == 1, "Unexpected skill INSERT parameter sets"
            bound = deepcopy(dict(parameter_sets[0]))
            if "id" in bound:
                payloads = [bound]
            else:
                id_keys = [key for key in bound if key.startswith("id_m")]
                assert id_keys, "Unexpected skill INSERT identity parameters"
                assert set(id_keys) == {f"id_m{i}" for i in range(len(id_keys))}
                payloads = [
                    {
                        key[: -len(f"_m{i}")]: value
                        for key, value in bound.items()
                        if key.endswith(f"_m{i}")
                    }
                    for i in range(len(id_keys))
                ]
                assert sum(map(len, payloads)) == len(bound)
            submitted: ReplayRows = {}
            for payload in payloads:
                row_id = payload["id"]
                assert isinstance(row_id, UUID) and row_id not in submitted
                submitted[row_id] = payload
            self.pending = (
                context,
                table.name,
                submitted,
                replay_snapshot(self.case, self.strategy),
            )
        finally:
            self.busy = False

    def after(
        self,
        conn: sa.Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: sa.engine.ExecutionContext,
        executemany: bool,
    ) -> None:
        if self.busy or self.pending is None or self.pending[0] is not context:
            return
        assert conn is self.case.connection
        self.busy = True
        try:
            _, table, submitted, before = self.pending
            self.events.append(
                ReplayWrite(
                    table=table,
                    submitted=submitted,
                    before=before,
                    after=replay_snapshot(self.case, self.strategy),
                )
            )
            self.pending = None
        finally:
            self.busy = False

    @contextmanager
    def observe(self) -> Iterator[None]:
        conn = self.case.connection
        before, after = self.before, self.after
        sa_event.listen(conn, "before_cursor_execute", before)
        try:
            sa_event.listen(conn, "after_cursor_execute", after)
            try:
                yield
            finally:
                sa_event.remove(conn, "after_cursor_execute", after)
        finally:
            sa_event.remove(conn, "before_cursor_execute", before)
            assert not sa_event.contains(conn, "before_cursor_execute", before)
            assert not sa_event.contains(conn, "after_cursor_execute", after)
            self.pending = None
            self.busy = False
            self.record("listeners_removed", True)


@contextmanager
def replay_scenario(conn: sa.Connection) -> Iterator[CleanCase]:
    assert conn.scalar(sa.text("SELECT current_user")) == "test"
    scenario = conn.begin_nested()
    try:
        seed_protected_lineage(conn, "present")
        stores = make_pg_stores(conn)
        case = CleanCase(
            connection=conn,
            skill_store=cast("PgSkillStore", stores["skill_store"]),
            hindcast_store=cast("PgHindcastStore", stores["hindcast_store"]),
            obs_store=cast("PgObservationStore", stores["obs_store"]),
            station_store=cast("PgStationStore", stores["station_store"]),
            flow_regime_store=cast(
                "PgFlowRegimeConfigStore", stores["flow_regime_store"]
            ),
            owner_role="test",
        )
        ordinary = assert_ordinary_manifest(case.obs_store)
        assert_protected_buckets(conn, ordinary)
        conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
        assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
        with pytest.raises(DBAPIError, match="permission denied"), conn.begin_nested():
            conn.execute(sa.select(db.provisional_discharges))
        levels = case.obs_store.fetch_observations(SID, "water_level", T, C)
        assert_same_source(
            {row.id: dataclasses.asdict(row) for row in levels},
            {
                row.id: dataclasses.asdict(row)
                for row in map(protected_measurement, range(48))
            },
        )
        assert assert_ordinary_manifest(case.obs_store) == ordinary
        yield case
        assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
        assert_ordinary_manifest(case.obs_store, "P_FIRST")
    finally:
        scenario.rollback()
    assert conn.scalar(sa.text("SELECT current_user")) == "test"


def correct_replay_observation(case: CleanCase) -> None:
    conn = case.connection
    before = {
        row.id: dataclasses.asdict(row)
        for row in assert_ordinary_manifest(case.obs_store)
    }
    target = ObservationId(UUID("6b9bb2f6-535a-4e07-b6df-fce8112d9d11"))
    assert before[target]["value"] == 15.0
    assert before[target]["timestamp"] == T + timedelta(hours=5, minutes=15)
    conn.execute(sa.text("SET LOCAL ROLE test"))
    try:
        assert conn.scalar(sa.text("SELECT current_user")) == "test"
        conn.execute(
            sa.update(db.observations)
            .where(db.observations.c.id == target)
            .values(value=39.0)
        )
        ordinary = assert_ordinary_manifest(case.obs_store, "P_FIRST")
        after = {row.id: dataclasses.asdict(row) for row in ordinary}
        expected = {key: dict(row) for key, row in before.items()}
        expected[target]["value"] = 39.0
        assert_same_source(after, expected)
        assert_protected_buckets(conn, ordinary)
    finally:
        conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
    assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
    assert_ordinary_manifest(case.obs_store, "P_FIRST")
