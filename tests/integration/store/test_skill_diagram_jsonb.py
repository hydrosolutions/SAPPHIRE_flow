from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID

import numpy as np
import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import skill_diagrams
from sapphire_flow.services.skill import combined_skill
from sapphire_flow.services.skill import diagrams as diagram_producers
from sapphire_flow.services.skill.service import compute_generation_fingerprint
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SkillSource
from sapphire_flow.types.skill import SkillDiagram

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from tests.integration.services.skill_persistence_fixture import (
        SkillPersistenceCase,
    )

pytest_plugins = ("tests.integration.services.skill_persistence_fixture",)

DiagramKind = Literal["reliability", "roc", "rank_histogram"]
CaseName = Literal[
    "reliability_nan",
    "roc_no_events",
    "roc_all_events",
    "merged_reliability",
    "merged_roc_no_events",
    "merged_roc_all_events",
    "finite_rank",
    "finite_reliability",
    "finite_roc",
    "legacy_reliability",
    "legacy_roc",
]


def scientific_function(module: object, name: str) -> Callable[..., dict[str, object]]:
    # Existing producers expose unparameterized dicts; these tests check real outputs.
    return cast("Callable[..., dict[str, object]]", getattr(module, name))


def producer_payload(case: CaseName) -> tuple[DiagramKind, dict[str, object]]:
    if case == "legacy_reliability":
        return "reliability", {"bins": [0.1, 0.5, 0.9], "values": [0.08, 0.48, 0.91]}
    if case == "legacy_roc":
        return "roc", {"x": [0.0, 1.0], "y": [0.0, 1.0]}
    observed = np.array([10.0, 22.0])
    if "no_events" in case:
        observed = np.array([10.0, 12.0])
    elif "all_events" in case:
        observed = np.array([22.0, 24.0])
    ensemble = np.repeat((observed + 1.0)[:, None], 2, axis=1)
    kind: DiagramKind
    payload: dict[str, object]
    rank = scientific_function(diagram_producers, "compute_rank_histogram")
    reliability = scientific_function(diagram_producers, "compute_reliability_diagram")
    roc = scientific_function(diagram_producers, "compute_roc_curve")
    merge = scientific_function(combined_skill, "_merge_diagram_data")
    if case == "finite_rank":
        kind = "rank_histogram"
        payload = rank(ensemble, observed)
    elif "roc" in case:
        kind = "roc"
        payload = roc(ensemble, observed, 19.0)
    else:
        kind = "reliability"
        payload = reliability(
            ensemble, observed, 19.0, n_bins=2 if case == "finite_reliability" else 10
        )
    if case.startswith("merged_"):
        # Exercise actual merge output shape, not a reconstructed fixture.
        payload = merge(
            kind, cast("list[dict[str, Sequence[float]]]", [payload, payload])
        )
    return kind, payload


def comparison_value(value: object, *, nan_as_null: bool = False) -> object:
    # Comparison only; never passed to the persistence writer.
    if isinstance(value, float) and math.isnan(value):
        return None if nan_as_null else "<float-NaN>"
    if isinstance(value, dict):
        return {
            key: comparison_value(item, nan_as_null=nan_as_null)
            for key, item in cast("dict[object, object]", value).items()
        }
    if isinstance(value, list):
        return [
            comparison_value(item, nan_as_null=nan_as_null)
            for item in cast("list[object]", value)
        ]
    return value


class TestSkillDiagramJsonb:
    @pytest.mark.parametrize(
        "case",
        [
            "reliability_nan",
            "roc_no_events",
            "roc_all_events",
            "merged_reliability",
            "merged_roc_no_events",
            "merged_roc_all_events",
            "finite_rank",
            "finite_reliability",
            "finite_roc",
            "legacy_reliability",
            "legacy_roc",
        ],
    )
    def test_public_worker_store_roundtrips_actual_diagrams(
        self, skill_persistence_case: SkillPersistenceCase, case: CaseName
    ) -> None:
        from tests.integration.services.skill_persistence_fixture import (
            AID,
            MID,
            SID,
            C,
            T,
        )

        kind, payload = producer_payload(case)
        if case.startswith("merged_roc"):
            assert isinstance(payload["n_events"], float)
            assert isinstance(payload["n_non_events"], float)
        diagram = SkillDiagram(
            id=UUID(int=66001),
            station_id=SID,
            model_id=MID,
            parameter="discharge",
            model_artifact_id=AID,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            computation_version=2,
            lead_time_hours=1,
            season=None,
            flow_regime=None,
            flow_regime_config_id=None,
            diagram_type=kind,
            threshold_level=None if kind == "rank_histogram" else "2",
            data=payload,
            eval_period_start=T,
            eval_period_end=ensure_utc(T + timedelta(hours=2)),
            created_at=C,
            time_step_seconds=3600,
            phase_offset_seconds=0,
            generation_id=None,
        )
        original = comparison_value(payload)
        fingerprint = compute_generation_fingerprint([], [diagram])
        store = skill_persistence_case.skill_store

        # Public regression: baseline must fail here for genuine NaN JSONB rejection.
        assert store.store_skill_diagrams([diagram]) == 1

        raw = skill_persistence_case.connection.execute(
            sa.select(skill_diagrams.c.data)
        ).scalar_one()
        assert raw == comparison_value(payload, nan_as_null=True)
        json.dumps(raw, allow_nan=False)
        fetched = store.fetch_latest_diagrams(SID, MID, parameter="discharge")
        assert len(fetched) == 1
        assert comparison_value(asdict(fetched[0])["data"]) == original
        assert comparison_value(payload) == original
        assert compute_generation_fingerprint([], fetched) == fingerprint
        assert store.store_skill_diagrams([diagram]) == 0
        assert (
            skill_persistence_case.connection.scalar(
                sa.select(sa.func.count()).select_from(skill_diagrams)
            )
            == 1
        )


def make_public_diagram(kind: DiagramKind, payload: dict[str, object]) -> SkillDiagram:
    from tests.integration.services.skill_persistence_fixture import AID, MID, SID, C, T

    return SkillDiagram(
        id=UUID(int=66001),
        station_id=SID,
        model_id=MID,
        parameter="discharge",
        model_artifact_id=AID,
        skill_source=SkillSource.HINDCAST_REANALYSIS,
        computation_version=2,
        lead_time_hours=1,
        season=None,
        flow_regime=None,
        flow_regime_config_id=None,
        diagram_type=kind,
        threshold_level=None if kind == "rank_histogram" else "2",
        data=payload,
        eval_period_start=T,
        eval_period_end=ensure_utc(T + timedelta(hours=2)),
        created_at=C,
        time_step_seconds=3600,
        phase_offset_seconds=0,
        generation_id=None,
    )


class TestSkillDiagramBoundaryControls:
    @pytest.mark.parametrize(
        "payload",
        [
            {1: (True, None), "1": "last", None: [0.5]},
            {float("nan"): 1, float("inf"): 2},
            {"nested": ({"value": np.float64(0.5)},)},
        ],
    )
    def test_finite_native_jsonb_matches_direct_control(
        self, skill_persistence_case: SkillPersistenceCase, payload: dict[str, object]
    ) -> None:
        from tests.integration.services.skill_persistence_fixture import MID, SID

        expected = skill_persistence_case.connection.execute(
            sa.text("SELECT CAST(:payload AS JSONB)"), {"payload": json.dumps(payload)}
        ).scalar_one()
        diagram = make_public_diagram("reliability", payload)
        assert skill_persistence_case.skill_store.store_skill_diagrams([diagram]) == 1
        fetched = skill_persistence_case.skill_store.fetch_latest_diagrams(SID, MID)[0]
        assert asdict(fetched)["data"] == expected

    @pytest.mark.parametrize("shape", ["canonical", "noncanonical"])
    def test_native_subclass_becomes_plain_jsonb_before_decode(
        self, skill_persistence_case: SkillPersistenceCase, shape: str
    ) -> None:
        from collections import OrderedDict

        from tests.integration.services.skill_persistence_fixture import MID, SID

        data: dict[str, Any] = (
            OrderedDict[str, Any](
                bins=[0.5], forecast_freq=[0.5], sample_counts=[0], observed_freq=[None]
            )
            if shape == "canonical"
            else OrderedDict[str, Any](x=[None], y=[1.0])
        )
        diagram = make_public_diagram("reliability", data)
        skill_persistence_case.skill_store.store_skill_diagrams([diagram])
        fetched = skill_persistence_case.skill_store.fetch_latest_diagrams(SID, MID)[0]
        if shape == "canonical":
            assert math.isnan(asdict(fetched)["data"]["observed_freq"][0])
        else:
            assert asdict(fetched)["data"] == data
        assert data.get("observed_freq", data.get("x")) == [None]

    def test_plain_invalid_batch_is_rejected_before_insert(
        self, skill_persistence_case: SkillPersistenceCase
    ) -> None:
        from dataclasses import replace

        from sapphire_flow.exceptions import SkillDiagramEncodingError

        good = make_public_diagram("rank_histogram", {"ranks": [0], "counts": [1]})
        invalid = replace(good, id=UUID(int=66002), data={"secret": [float("inf")]})
        with pytest.raises(SkillDiagramEncodingError, match="unsupported_infinity"):
            skill_persistence_case.skill_store.store_skill_diagrams([good, invalid])
        assert (
            skill_persistence_case.connection.scalar(
                sa.select(sa.func.count()).select_from(skill_diagrams)
            )
            == 0
        )

    def test_external_none_identity_normalization_is_explicit(
        self, skill_persistence_case: SkillPersistenceCase
    ) -> None:
        from tests.integration.services.skill_persistence_fixture import MID, SID

        diagram = make_public_diagram(
            "reliability",
            {
                "bins": [0.5],
                "forecast_freq": [0.5],
                "sample_counts": [0],
                "observed_freq": [None],
            },
        )
        before = compute_generation_fingerprint([], [diagram])
        skill_persistence_case.skill_store.store_skill_diagrams([diagram])
        fetched = skill_persistence_case.skill_store.fetch_latest_diagrams(SID, MID)
        assert math.isnan(asdict(fetched[0])["data"]["observed_freq"][0])
        assert compute_generation_fingerprint([], fetched) != before

    @pytest.mark.parametrize("shape", ["complete", "partial"])
    def test_owner_seeded_legacy_null_has_conservative_worker_readback(
        self, skill_persistence_case: SkillPersistenceCase, shape: str
    ) -> None:
        from tests.integration.services.skill_persistence_fixture import MID, SID

        payload: dict[str, object] = {
            "bins": [0.5],
            "forecast_freq": [0.5],
            "sample_counts": [0],
            "observed_freq": [None],
        }
        if shape == "partial":
            payload.pop("bins")
        diagram = make_public_diagram("reliability", payload)
        row = asdict(diagram)
        row["skill_source"] = diagram.skill_source.value
        conn = skill_persistence_case.connection
        # Explicit owner historical-fixture write, never a worker delete/update.
        conn.execute(sa.text("RESET ROLE"))
        try:
            conn.execute(sa.insert(skill_diagrams).values(row))
        finally:
            conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
        assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
        fetched = skill_persistence_case.skill_store.fetch_latest_diagrams(SID, MID)[0]
        value = asdict(fetched)["data"]["observed_freq"][0]
        if shape == "complete":
            assert math.isnan(value)
        else:
            assert value is None
