from __future__ import annotations

import random
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.services.forecast_evidence import (
    capture_combined_evidence,
    capture_group_evidence,
    capture_station_evidence,
    restore_frame,
    restore_snapshot,
)
from sapphire_flow.types.domain import (
    ClimBaseline,
    ForecastQcRuleParams,
    ForecastQcRuleSet,
    StationForecastQcOverride,
)
from sapphire_flow.types.forecast_evidence import EvidenceStatus, StationSourceEvidence
from sapphire_flow.types.ids import ModelId
from tests.integration.store.test_forecast_store import _make_forecast
from tests.unit.adapters.test_forecast_interface_adapter_predict import (
    _SID_A,
    _SID_B,
    _adapter,
    _group_model_inputs,
    _station_model_inputs,
    _success_result,
    _success_variable,
)

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def _config() -> DeploymentConfig:
    return DeploymentConfig(max_retention_days=1000)


def _qc_rules() -> ForecastQcRuleSet:
    return ForecastQcRuleSet(version="1", rules=())


class TestForecastEvidenceCapture:
    def test_invalid_runtime_digest_is_explicitly_incomplete(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_IMAGE_DIGEST", "not-a-digest")
        inputs = replace(
            _station_model_inputs(), source_evidence=StationSourceEvidence()
        )
        evidence = capture_station_evidence(
            inputs=inputs,
            model=object(),
            model_id=ModelId("native"),
            artifact_bytes=b"artifact",
            prior_state=None,
            rng_state=random.Random(42).getstate(),
            config=_config(),
            qc_rules=_qc_rules(),
            qc_overrides=[],
            baselines=[],
            water_level_datum_masl=None,
        )
        assert evidence.status is EvidenceStatus.INCOMPLETE
        assert evidence.reason == "runtime_image_digest_invalid"

    def test_station_snapshot_preserves_effective_qc_inputs(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_IMAGE_DIGEST", "sha256:" + "a" * 64)
        inputs = replace(
            _station_model_inputs(), source_evidence=StationSourceEvidence()
        )
        rules = ForecastQcRuleSet(
            version="1",
            rules=(
                ForecastQcRuleParams(
                    rule_id="range_check",
                    rule_version="2",
                    parameter="discharge",
                    time_step=timedelta(hours=1),
                    thresholds={"maximum": 100.0},
                ),
            ),
        )
        override = StationForecastQcOverride(
            station_id=inputs.station_id,
            rule_id="range_check",
            parameter="discharge",
            time_step=timedelta(hours=1),
            thresholds={"maximum": 90.0},
        )
        baseline = ClimBaseline(
            station_id=inputs.station_id,
            parameter="discharge",
            day_of_year=1,
            rolling_mean=40.0,
            rolling_std=3.0,
            sample_count=10,
        )
        evidence = capture_station_evidence(
            inputs=inputs,
            model=object(),
            model_id=ModelId("native"),
            artifact_bytes=b"artifact",
            prior_state=None,
            rng_state=random.Random(42).getstate(),
            config=_config(),
            qc_rules=rules,
            qc_overrides=[override],
            baselines=[baseline],
            water_level_datum_masl=123.0,
        )
        assert evidence.snapshot is not None
        snapshot = restore_snapshot(evidence.snapshot)
        assert snapshot["forecast_qc_rules"]["rules"][0]["thresholds"] == {
            "maximum": 100.0
        }
        assert snapshot["forecast_qc_overrides"][0]["thresholds"] == {"maximum": 90.0}
        assert snapshot["forecast_qc_baselines"][0]["rolling_mean"] == 40.0
        assert snapshot["water_level_datum_masl"] == 123.0

    def test_missing_runtime_digest_is_explicitly_incomplete(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_IMAGE_DIGEST", raising=False)
        inputs = replace(
            _station_model_inputs(), source_evidence=StationSourceEvidence()
        )
        evidence = capture_station_evidence(
            inputs=inputs,
            model=object(),
            model_id=ModelId("native"),
            artifact_bytes=b"artifact",
            prior_state=None,
            rng_state=random.Random(42).getstate(),
            config=_config(),
            qc_rules=_qc_rules(),
            qc_overrides=[],
            baselines=[],
            water_level_datum_masl=None,
        )
        assert evidence.status is EvidenceStatus.INCOMPLETE
        assert evidence.reason == "runtime_image_digest_unavailable"
        assert evidence.snapshot is not None

    def test_fi_snapshot_contains_exact_delivered_series(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_IMAGE_DIGEST", "sha256:" + "a" * 64)
        inputs = replace(
            _station_model_inputs(), source_evidence=StationSourceEvidence()
        )
        adapter, model = _adapter(
            _success_result(
                {"station": {"discharge": _success_variable([1.0, 2.0, 3.0])}}
            )
        )
        rng = random.Random(42)
        evidence = capture_station_evidence(
            inputs=inputs,
            model=adapter,
            model_id=ModelId("fi"),
            artifact_bytes=b"artifact",
            prior_state=None,
            rng_state=rng.getstate(),
            config=_config(),
            qc_rules=_qc_rules(),
            qc_overrides=[],
            baselines=[],
            water_level_datum_masl=None,
        )
        adapter.predict(b"artifact", inputs, rng)
        assert evidence.status is EvidenceStatus.INCOMPLETE
        assert evidence.reason == "runtime_image_bytes_unpinned"
        assert evidence.snapshot is not None
        assert model.predict_inputs is not None
        captured = restore_snapshot(evidence.snapshot)["fi_model_inputs"]
        dynamic = captured["stations"]["station"]["dynamic"]["3600.0"]["data"]
        spatial = next(iter(dynamic.values()))
        series = spatial["past_known"]["obs"]["discharge"]["data"]
        actual = model.predict_inputs.stations["station"].dynamic[inputs.time_step]
        actual_series = (
            next(iter(actual.data.values())).past_known["obs"]["discharge"].data
        )
        assert isinstance(actual_series, pl.DataFrame)
        assert restore_frame(series["polars_ipc_base64"]).equals(actual_series)

    def test_group_snapshot_preserves_member_lineage_and_frames(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_IMAGE_DIGEST", "sha256:" + "a" * 64)
        original = _group_model_inputs()
        inputs = replace(
            original,
            source_evidence=tuple(
                (sid, StationSourceEvidence()) for sid in original.station_ids
            ),
        )
        evidence = capture_group_evidence(
            inputs=inputs,
            model=object(),
            model_id=ModelId("group"),
            artifact_bytes=b"weights",
            rng_state=random.Random(3).getstate(),
            config=_config(),
            qc_rules=_qc_rules(),
            qc_overrides=[],
            baselines_by_station={},
            water_level_datums_masl={},
        )
        assert evidence.status is EvidenceStatus.INCOMPLETE
        assert evidence.snapshot is not None
        snapshot = restore_snapshot(evidence.snapshot)
        assert snapshot["station_ids"] == [str(sid) for sid in inputs.station_ids]
        assert restore_frame(snapshot["frames"]["past_targets"]).equals(
            inputs.past_targets
        )

    def test_station_fanout_snapshot_keeps_each_delivered_member_frame(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_IMAGE_DIGEST", "sha256:" + "a" * 64)
        original = _station_model_inputs()
        future = original.data.future_dynamic
        inputs = replace(
            original,
            data=replace(
                original.data,
                future_dynamic=future.select(
                    "timestamp",
                    pl.col("precipitation_forecast").alias("precipitation_forecast_0"),
                    (pl.col("precipitation_forecast") + 1).alias(
                        "precipitation_forecast_1"
                    ),
                ),
            ),
            source_evidence=StationSourceEvidence(),
        )
        evidence = capture_station_evidence(
            inputs=inputs,
            model=object(),
            model_id=ModelId("ensemble"),
            artifact_bytes=b"artifact",
            prior_state=None,
            rng_state=random.Random(42).getstate(),
            config=_config(),
            qc_rules=_qc_rules(),
            qc_overrides=[],
            baselines=[],
            water_level_datum_masl=None,
            fanout_features=frozenset({"precipitation_forecast"}),
        )
        assert evidence.status is EvidenceStatus.INCOMPLETE
        assert evidence.snapshot is not None
        members = restore_snapshot(evidence.snapshot)["fanout_inputs"]
        assert [member["member_id"] for member in members] == [0, 1]
        assert (
            restore_frame(members[0]["frames"]["future_dynamic"])[
                "precipitation_forecast"
            ].to_list()
            == future["precipitation_forecast"].to_list()
        )
        assert restore_frame(members[1]["frames"]["future_dynamic"])[
            "precipitation_forecast"
        ].to_list() == [value + 1 for value in future["precipitation_forecast"]]

    def test_fi_group_snapshot_uses_only_serviceable_members(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        from sapphire_flow.adapters import forecast_interface as fi_boundary

        monkeypatch.setenv("SAPPHIRE_IMAGE_DIGEST", "sha256:" + "a" * 64)
        original = _group_model_inputs()
        future = original.future_dynamic.with_columns(
            pl.when(pl.col("station_id") == str(_SID_B))
            .then(None)
            .otherwise(pl.col("precipitation_forecast"))
            .alias("precipitation_forecast")
        )
        inputs = replace(
            original,
            future_dynamic=future,
            source_evidence=tuple(
                (sid, StationSourceEvidence()) for sid in original.station_ids
            ),
        )
        adapter, model = _adapter(
            _success_result(
                {"gauge-a": {"discharge": _success_variable([1.0, 2.0, 3.0])}}
            ),
            artifact_scope=fi_boundary.FIArtifactScope.GROUP,
        )
        rng = random.Random(7)
        evidence = capture_group_evidence(
            inputs=inputs,
            model=adapter,
            model_id=ModelId("fi-group"),
            artifact_bytes=b"artifact",
            rng_state=rng.getstate(),
            config=_config(),
            qc_rules=_qc_rules(),
            qc_overrides=[],
            baselines_by_station={},
            water_level_datums_masl={},
        )
        result = adapter.predict_batch(b"artifact", inputs, rng)
        assert set(result) == {_SID_A}
        assert model.predict_inputs is not None
        assert evidence.snapshot is not None
        captured = restore_snapshot(evidence.snapshot)["fi_model_inputs"]
        assert set(captured["stations"]) == set(model.predict_inputs.stations)

    def test_combined_snapshot_keeps_contributor_ids_and_weights(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_IMAGE_DIGEST", "sha256:" + "a" * 64)
        from uuid import uuid4

        from sapphire_flow.types.ids import ArtifactId, StationId

        station = StationId(uuid4())
        contributors = tuple(
            _make_forecast(station, ModelId(name), ArtifactId(uuid4()))
            for name in ("a", "b")
        )
        evidence = capture_combined_evidence(
            model_id=ModelId("pooled"),
            strategy="pooled",
            contributors=contributors,
            weights={ModelId("a"): 0.6, ModelId("b"): 0.4},
            qc_rules=_qc_rules(),
            qc_overrides=[],
            baselines=[],
            water_level_datum_masl=None,
        )
        assert evidence.status is EvidenceStatus.INCOMPLETE
        assert evidence.reason == (
            "contributor_evidence_incomplete;runtime_image_bytes_unpinned"
        )
        assert evidence.snapshot is not None
        snapshot = restore_snapshot(evidence.snapshot)
        assert [item["forecast_id"] for item in snapshot["contributors"]] == [
            str(fc.id) for fc in contributors
        ]
        assert snapshot["weights"] == {"a": 0.6, "b": 0.4}

        bma = capture_combined_evidence(
            model_id=ModelId("bma"),
            strategy="bma",
            contributors=contributors,
            weights={ModelId("a"): 0.6, ModelId("b"): 0.4},
            qc_rules=_qc_rules(),
            qc_overrides=[],
            baselines=[],
            water_level_datum_masl=None,
        )
        assert bma.snapshot is not None
        seeds = restore_snapshot(bma.snapshot)["sampling_seeds"]
        assert seeds == {name: int(abs(hash(name))) % (2**31) for name in ("a", "b")}
