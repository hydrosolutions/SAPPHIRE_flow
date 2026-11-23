from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import polars as pl
import pytest
import sqlalchemy as sa

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.config.fine_tuning import parse_fine_tuning
from sapphire_flow.flows import _db, fine_tuning_evaluation, train_models
from sapphire_flow.store.fine_tuning import PgFineTuningReceipts
from sapphire_flow.store.model_artifact_lineage import PgArtifactLineageWriter
from sapphire_flow.store.model_artifact_warm_start import fetch_warm_start
from sapphire_flow.types.enums import (
    ArtifactScope,
    ModelArtifactStatus,
    ModelAssignmentStatus,
)
from sapphire_flow.types.ids import ModelId, StationGroupId, StationId
from sapphire_flow.types.model import GroupTrainingData, ModelParams, ModelRecord
from sapphire_flow.types.station import GroupModelAssignment, StationGroup
from sapphire_flow.types.training import TrainingResult
from tests.conftest import make_station_config
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_models import FakeGroupForecastModel
from tests.unit.config.test_fine_tuning import request_document

if TYPE_CHECKING:
    import random
    from pathlib import Path


class RetrainableGroup(FakeGroupForecastModel):
    supports_warm_start = True

    def __init__(self) -> None:
        self.fits = 0

    def retrain(
        self,
        base: bytes,
        data: GroupTrainingData,
        params: ModelParams,
        rng: random.Random,
    ) -> bytes:
        self.fits += 1
        assert base == b"donor"
        return b"fine-tuned"


@pytest.mark.parametrize("failure", ["none", "evaluation", "lineage", "race"])
def test_configured_flow_preserves_candidate_and_resumes_without_refitting(
    isolated_committed_db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    db_engine = isolated_committed_db_engine
    monkeypatch.setenv(
        "DATABASE_URL", db_engine.url.render_as_string(hide_password=False)
    )
    document = request_document()
    request = replace(
        parse_fine_tuning(document),
        request_id=uuid4(),
        tenant_code="sapphire",
        model_id=ModelId(f"fine-tune-{uuid4()}"),
        group_id=StationGroupId(uuid4()),
    )
    monkeypatch.setenv("SAPPHIRE_DATA_DIR", str(tmp_path / "data"))
    config = tmp_path / "config.toml"
    config.write_text(
        '[deployment]\nwritable_tenants = ["sapphire"]\noperator = "test"\n'
    )
    monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))
    monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
    with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        stores = _db.make_pg_stores(conn)
        station = make_station_config(
            station_id=StationId(uuid4()), code=f"test-{uuid4()}"
        )
        stores["station_store"].store_station(station)
        group = StationGroup(
            id=request.group_id,
            name=f"fine-tune-{uuid4()}",
            station_ids=frozenset({station.id}),
            tenant_id=station.tenant_id,
            created_at=request.fitting_start,
        )
        stores["group_store"].store_group(group)
        stores["model_store"].register_model(
            ModelRecord(
                id=request.model_id,
                display_name="test fine-tune",
                artifact_scope=ArtifactScope.GROUP,
                description="test",
                created_at=request.fitting_start,
            )
        )
        stores["group_store"].store_group_model_assignment(
            GroupModelAssignment(
                group_id=group.id,
                model_id=request.model_id,
                time_step=timedelta(days=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=1,
                created_at=request.fitting_start,
            )
        )
        donor, _ = stores["artifact_store"].store_artifact(
            request.model_id,
            b"donor",
            request.fitting_start,
            request.fitting_end,
            request.fitting_end,
            group_id=group.id,
            status=ModelArtifactStatus.ACTIVE,
        )
        request = replace(request, base_artifact_id=donor)
        model = RetrainableGroup()
        monkeypatch.setattr(_db, "setup_production_stores", lambda url: (conn, stores))
        monkeypatch.setattr(
            train_models,
            "_resolve_donor_config_or_refuse",
            lambda **kwargs: (
                None,
                None,
                "test donor config unavailable",
                None,
                "test donor params unavailable",
            ),
        )
        for name in (
            "_determine_scope_task",
            "_train_model_task",
            "_store_artifact_task",
        ):
            monkeypatch.setattr(train_models, name, getattr(train_models, name).fn)
        data = GroupTrainingData(
            group_id=group.id,
            station_ids=(station.id,),
            past_targets=pl.DataFrame(),
            past_dynamic=pl.DataFrame(),
            future_dynamic=pl.DataFrame(),
            static=None,
            time_step=timedelta(days=1),
            val_start=None,
        )
        monkeypatch.setattr(train_models, "_assemble_data_task", lambda **kwargs: data)
        from sapphire_flow.adapters import hybrid_reanalysis_factories

        monkeypatch.setattr(
            hybrid_reanalysis_factories,
            "select_reanalysis_source",
            lambda **kwargs: FakeWeatherReanalysisSource([]),
        )
        evaluations = 0

        def evaluate(receipt: object, **kwargs: object) -> tuple:
            nonlocal evaluations
            evaluations += 1
            if failure == "evaluation" and evaluations == 1:
                raise RuntimeError("evaluation unavailable")
            return replace(
                receipt, evaluation_json='{"state":"complete"}'
            ), TrainingResult(
                training_unit=kwargs["unit"],
                artifact_id=receipt.artifact_id,
                hindcast_steps=[],
                skill_computed=True,
            )

        monkeypatch.setattr(fine_tuning_evaluation, "evaluate_candidate", evaluate)
        if failure == "lineage":

            def fail_lineage(self: object, *args: object) -> None:
                raise RuntimeError("lineage unavailable")

            monkeypatch.setattr(PgArtifactLineageWriter, "record", fail_lineage)
        kwargs = dict(
            fine_tuning=request.payload(),
            models={request.model_id: model},
            clock=lambda: request.evaluation_end,
            deployment_config=DeploymentConfig(max_retention_days=600),
        )
        if failure in {"evaluation", "lineage"}:
            with pytest.raises(RuntimeError, match=f"{failure} unavailable"):
                train_models.train_models_flow.fn(**kwargs)
        else:
            train_models.train_models_flow.fn(**kwargs)
        assert model.fits == 1
        assert (
            stores["artifact_store"].fetch_artifact_record(donor).status
            is ModelArtifactStatus.ACTIVE
        )
        inactive = stores["artifact_store"].fetch_artifacts_by_status(
            request.model_id,
            ModelArtifactStatus.TRAINING,
            group_id=group.id,
        )
        if failure == "lineage":
            assert inactive == []
            PgFineTuningReceipts(conn).refuse_retained_candidate(request)
            assert evaluations == 0
            return
        if failure == "evaluation":
            assert len(inactive) == 1
            assert fetch_warm_start(conn, inactive[0]).base_artifact_id == donor
            train_models.train_models_flow.fn(
                **kwargs, fine_tuning_candidate_id=str(inactive[0])
            )
            assert model.fits == 1
        pending = stores["artifact_store"].fetch_artifacts_by_status(
            request.model_id,
            ModelArtifactStatus.PENDING_APPROVAL,
            group_id=group.id,
        )
        assert len(pending) == 1
        assert fetch_warm_start(conn, pending[0]).base_artifact_id == donor
        assert (
            PgFineTuningReceipts(conn).latest(pending[0]).evaluation_json
            == '{"state":"complete"}'
        )

        if failure == "race":
            from sapphire_flow.config.paths import resolve_artifact_dir
            from sapphire_flow.services.training import promote_artifact
            from sapphire_flow.store.model_artifact_store import PgModelArtifactStore

            competitor, _ = stores["artifact_store"].store_artifact(
                request.model_id,
                b"competing-model",
                request.fitting_start,
                request.fitting_end,
                request.fitting_end,
                group_id=group.id,
            )
            with (
                pytest.raises(sa.exc.IntegrityError, match="duplicate key"),
                db_engine.begin() as approval_conn,
            ):
                approval_store = PgModelArtifactStore(
                    approval_conn, resolve_artifact_dir()
                )
                read_active = approval_store.fetch_artifacts_by_status

                def promote_competitor_after_read(*args: object, **kw: object) -> list:
                    checked = read_active(*args, **kw)
                    with db_engine.begin() as competitor_conn:
                        promote_artifact(
                            PgModelArtifactStore(
                                competitor_conn, resolve_artifact_dir()
                            ),
                            request.model_id,
                            competitor,
                            group_id=group.id,
                        )
                    return checked

                monkeypatch.setattr(
                    approval_store,
                    "fetch_artifacts_by_status",
                    promote_competitor_after_read,
                )
                promote_artifact(
                    approval_store,
                    request.model_id,
                    pending[0],
                    group_id=group.id,
                    expected_active_ids=frozenset({donor}),
                )
            assert (
                stores["artifact_store"].fetch_artifact_record(competitor).status
                is ModelArtifactStatus.ACTIVE
            )
            assert (
                stores["artifact_store"].fetch_artifact_record(pending[0]).status
                is ModelArtifactStatus.PENDING_APPROVAL
            )
