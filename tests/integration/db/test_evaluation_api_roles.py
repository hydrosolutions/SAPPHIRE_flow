from __future__ import annotations

# Shared integration seed helpers intentionally cross test-module boundaries.
# pyright: reportPrivateUsage=false
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import polars as pl
import pytest
import sqlalchemy as sa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import insert

from sapphire_flow.api import deps
from sapphire_flow.api.errors import http_exception_handler, unhandled_exception_handler
from sapphire_flow.api.publication_cache import PublicationCacheMiddleware
from sapphire_flow.api.publication_gate import PublicationGate
from sapphire_flow.api.review_auth import require_reviewer_or_human
from sapphire_flow.api.routes.api_evaluation_forecasts import router
from sapphire_flow.api.security import Principal
from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.db.metadata import model_artifacts, model_assignments, models
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.daily_forecast_claim import DailyClaim
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberReason,
    DailyMemberState,
    DailyOutputSource,
)
from sapphire_flow.types.daily_input_provenance import DailyInputProvenance
from sapphire_flow.types.domain import InputQualityFlag, QcFlag
from sapphire_flow.types.enums import (
    AccessTokenRole,
    ForecastDataUse,
    InputQualityCategory,
    InputQualityLevel,
    NwpCycleSource,
    QcStatus,
)
from sapphire_flow.types.ids import AccessTokenId, ModelId, StationId
from tests.conftest import make_station_config
from tests.integration.db.test_daily_forecast_roles import (
    isolated_committed_db_engine as isolated_committed_db_engine,
)
from tests.integration.db.test_daily_forecast_roles import (
    worker_engine as worker_engine,
)
from tests.integration.db.test_role_bootstrap import bootstrapped as bootstrapped
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.integration.store.test_daily_forecast_claim_store import NOW, _later
from tests.integration.store.test_daily_forecast_claim_store import (
    assignment as assignment,
)
from tests.integration.store.test_daily_forecast_finalization import _prepare
from tests.integration.store.test_forecast_store import (
    _make_forecast,
    _seed_artifact,
    _seed_model,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.types.daily_forecast_claim import (
        DailyClaimKey,
        FrozenDailyAssignment,
    )
    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness


@pytest.fixture
def api_engine(bootstrapped: _RoleBootstrapHarness) -> Iterator[sa.Engine]:
    engine = sa.create_engine(bootstrapped.role_url("sapphire_api", "api-pw-initial"))
    with engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_api"
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def scenario(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> tuple[DailyClaimKey, FrozenDailyAssignment]:
    key, frozen = assignment
    with isolated_committed_db_engine.begin() as conn:
        conn.execute(
            insert(models)
            .values(
                id="cmal_small",
                display_name="Synthetic daily",
                artifact_scope="group",
                description="No real artifact",
            )
            .on_conflict_do_nothing()
        )
        conn.execute(
            sa.update(model_artifacts)
            .where(model_artifacts.c.id == frozen.artifact_id)
            .values(model_id="cmal_small")
        )
        other = StationId(uuid4())
        PgStationStore(conn).store_station(
            make_station_config(
                station_id=other, code=str(other), tenant_id=key.tenant_id
            )
        )
        conn.execute(
            sa.insert(model_assignments).values(
                station_id=frozen.members[0].station_id,
                model_id="cmal_small",
                time_step=timedelta(hours=1),
                status="active",
                priority=1,
                created_at=NOW,
            )
        )
    first = replace(frozen.members[0], required_parameters=("discharge",))
    second = replace(first, station_id=other)
    return replace(key, model_id=ModelId("cmal_small")), replace(
        frozen, members=(first, second)
    )


def _app(engine: sa.Engine, claim: DailyClaim) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(PublicationCacheMiddleware)
    app.state.engine = engine
    app.state.deployment_config = DeploymentConfig.model_validate(
        {
            "max_retention_days": 2192,
            "evaluation_forecasts": {
                "tenant_id": str(claim.key.tenant_id),
                "deployment": "test",
                "group_ids": [str(claim.key.group_id)],
                "plausibility_models": ["cmal_small"],
            },
        }
    )
    principal = Principal(
        token_id=AccessTokenId(uuid4()),
        role=AccessTokenRole.REVIEWER,
        tenant_id=claim.key.tenant_id,
        station_ids=frozenset({claim.frozen.members[0].station_id}),
    )
    # Inject authorization only; use real DB credentials and read dependencies.
    app.dependency_overrides[require_reviewer_or_human] = lambda: principal
    app.dependency_overrides[deps.get_evaluation_clock] = lambda: (
        NOW + timedelta(hours=1)
    )
    return app


def _body(app: FastAPI, claim: DailyClaim) -> dict[str, Any]:
    client: Any
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/stations/{claim.frozen.members[0].station_id}/evaluation-forecasts"
        )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def _seed_summary(
    worker_engine: sa.Engine,
    scenario: tuple[DailyClaimKey, FrozenDailyAssignment],
    *,
    accepted: bool,
) -> DailyClaim:
    claim, outcome, evidence = _prepare(worker_engine, scenario)
    if accepted:
        assert claim.frozen.artifact_id is not None
        item = evidence[0]
        forecast = _make_forecast(
            item.reference.key.station_id,
            claim.key.model_id,
            claim.frozen.artifact_id,
            issued_at=NOW,
            n_steps=2,
            n_members=2,
            nwp_cycle_reference_time=NOW,
        )
        flag = QcFlag(
            rule_id="range_check",
            rule_version="1",
            status=QcStatus.QC_PASSED,
            detail="private accepted QC",
        )
        input_flag = InputQualityFlag(
            category=next(iter(InputQualityCategory)),
            level=InputQualityLevel.DEGRADED,
            detail="private input detail",
        )
        forecast = replace(
            forecast,
            ensemble=item.ensemble,
            qc_status=QcStatus.QC_PASSED,
            qc_flags=(flag,),
            input_quality=InputQualityLevel.DEGRADED,
            input_quality_flags=(input_flag,),
        )
        with worker_engine.connect() as conn:
            output_id = PgForecastStore(
                conn, transaction_factory=worker_engine.begin
            ).store_forecast(forecast)
        item = replace(
            item,
            reference=replace(
                item.reference, source=DailyOutputSource.FORECAST, output_id=output_id
            ),
            qc_status=QcStatus.QC_PASSED,
            qc_flags=(flag,),
            provenance=DailyInputProvenance(
                nwp_source=NwpCycleSource.PRIMARY,
                nwp_reference_time=NOW,
                nwp_age_hours=0.0,
                input_quality=InputQualityLevel.DEGRADED,
                input_quality_flags=(input_flag,),
            ),
        )
        evidence = (item,)
        outcome = replace(
            outcome, references=(item.reference,), provenance=item.provenance
        )
    excluded = replace(
        claim.frozen.members[1],
        state=DailyMemberState.MODEL_FAILED,
        reason=DailyMemberReason.MODEL_FAILURE,
    )
    PgDailyForecastStore(worker_engine.begin).finalize(
        claim, outcomes=(outcome, excluded), evidence=evidence, finished_at=NOW
    )
    return claim


@pytest.mark.parametrize("accepted", [False, True])
def test_current_exact_records_and_publication_redaction(
    worker_engine: sa.Engine,
    api_engine: sa.Engine,
    scenario: tuple[DailyClaimKey, FrozenDailyAssignment],
    accepted: bool,
) -> None:
    claim = _seed_summary(worker_engine, scenario, accepted=accepted)
    app = _app(api_engine, claim)
    body = _body(app, claim)
    selected = body["parameters"][0]
    assert selected["selection"] == "current"
    row = selected["selected"]
    assert row["source"] == ("forecast" if accepted else "rejected")
    assert row["output_id"] == body["daily"][0]["references"][0]["output_id"]
    assert row["values"] and row["qc_flags"][0]["detail"]
    if accepted:
        assert row["input_quality_flags"][0]["detail"] == "private input detail"
    assert body["models"][0]["records"][0] == row
    assert body["daily"][0]["provenance"]["nwp_reference_time"]
    app.state.publication_gate = PublicationGate(
        active_tenant_ids=frozenset({claim.key.tenant_id})
    )
    hidden = _body(app, claim)
    for record in (
        hidden["parameters"][0]["selected"],
        hidden["models"][0]["records"][0],
    ):
        assert record["withheld"] is True
        assert record["values"] is None
        assert record["qc_flags"][0]["detail"] is None
        assert record["provenance"] is None
        if accepted:
            assert record["input_quality_flags"][0]["detail"] is None
    assert hidden["daily"][0]["provenance"] is None


@pytest.mark.parametrize("fallback", [False, True])
def test_retry_superseded_exact_reference_and_whole_summary(
    worker_engine: sa.Engine,
    api_engine: sa.Engine,
    isolated_committed_db_engine: sa.Engine,
    scenario: tuple[DailyClaimKey, FrozenDailyAssignment],
    fallback: bool,
) -> None:
    claim = _seed_summary(worker_engine, scenario, accepted=True)
    app = _app(api_engine, claim)
    original = _body(app, claim)["parameters"][0]["selected"]["output_id"]
    store = PgDailyForecastStore(worker_engine.begin)
    retry = store.claim(
        _later(claim.key, 1),
        claim.frozen,
        attempt_id=uuid4(),
        owner_id=uuid4(),
        started_at=NOW + timedelta(hours=1),
    )
    assert isinstance(retry, DailyClaim)
    active = _body(app, claim)
    assert active["daily"][0]["latest_attempt_id"] == str(retry.id)
    assert active["daily"][0]["authoritative_attempt_id"] == str(claim.id)
    assert active["parameters"][0]["selected"]["output_id"] == original
    # A new ordinary version is not a finalized daily summary.
    with worker_engine.connect() as conn:
        forecasts = PgForecastStore(conn, transaction_factory=worker_engine.begin)
        from uuid import UUID

        from sapphire_flow.types.ids import ForecastId

        old = forecasts.fetch_forecast(ForecastId(UUID(original)))
        assert old is not None
        changed = replace(
            old,
            id=ForecastId(uuid4()),
            ensemble=replace(
                old.ensemble,
                values=old.ensemble.values.with_columns(
                    (pl.col("value") + 1).alias("value")
                ),
            ),
        )
        replacement = forecasts.store_forecast(changed)
    assert str(replacement) != original
    fallback_id = None
    if fallback:
        sid = claim.frozen.members[0].station_id
        with isolated_committed_db_engine.begin() as conn:
            model = _seed_model(conn, f"fallback-{uuid4()}")
            artifact = _seed_artifact(conn, sid, model)
            conn.execute(
                sa.insert(model_assignments).values(
                    station_id=sid,
                    model_id=model,
                    time_step=timedelta(days=1),
                    status="active",
                    priority=5,
                    created_at=NOW,
                )
            )
        ensemble = replace(
            old.ensemble,
            model_id=model,
            time_step=timedelta(days=1),
            values=old.ensemble.values.with_columns(
                pl.when(pl.col("valid_time") == NOW + timedelta(hours=1))
                .then(pl.lit(NOW))
                .otherwise(pl.lit(NOW + timedelta(days=1)))
                .alias("valid_time")
            ),
        )
        ordinary = replace(
            old,
            id=ForecastId(uuid4()),
            model_id=model,
            model_artifact_id=artifact,
            ensemble=ensemble,
        )
        with worker_engine.connect() as conn:
            fallback_id = PgForecastStore(
                conn, transaction_factory=worker_engine.begin
            ).store_forecast(ordinary)
        raw = app.state.deployment_config.model_dump()
        raw["evaluation_forecasts"]["fallback_contracts"] = {
            model: [
                {
                    "parameter": "discharge",
                    "units": "m³/s",
                    "representation": "members",
                    "first_lead_hours": 0,
                    "horizon_steps": 2,
                    "series_keys": [0.0, 1.0],
                }
            ]
        }
        app.state.deployment_config = DeploymentConfig.model_validate(raw)
    body = _body(app, claim)
    assert body["daily"][0]["latest_attempt_id"] == str(retry.id)
    assert body["daily"][0]["authoritative_attempt_id"] == str(claim.id)
    assert body["parameters"][0]["selection"] == ("fallback" if fallback else "absent")
    if fallback:
        assert body["parameters"][0]["selected"]["output_id"] == str(fallback_id)
        assert body["assignments"][1]["declared_parameters"] == ["discharge"]
    excluded = tuple(
        replace(
            m,
            state=DailyMemberState.INPUT_EXCLUDED,
            reason=DailyMemberReason.MISSING_REQUIRED_TARGET,
        )
        for m in retry.frozen.members
    )
    store.finalize(
        retry, outcomes=excluded, evidence=(), finished_at=NOW + timedelta(hours=1)
    )
    body = _body(app, claim)
    assert body["daily"][0]["authoritative_attempt_id"] == str(retry.id)
    assert body["daily"][0]["references"] == []
    assert body["models"][0]["records"] == []
    assert body["parameters"][0]["selection"] == ("fallback" if fallback else "absent")


@pytest.mark.parametrize("store_name", ["forecast_store", "rejected_forecast_store"])
def test_nonstandard_real_store_refused(
    worker_engine: sa.Engine,
    api_engine: sa.Engine,
    scenario: tuple[DailyClaimKey, FrozenDailyAssignment],
    store_name: str,
) -> None:
    claim = _seed_summary(worker_engine, scenario, accepted=True)
    app = _app(api_engine, claim)
    with api_engine.begin() as conn:
        stores = deps.get_stores(conn, app.state.deployment_config)
        from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore

        stores[store_name] = (
            PgForecastStore(conn, data_use=ForecastDataUse.EXPIRED_RATING_TEST)
            if store_name == "forecast_store"
            else PgRejectedForecastStore(
                conn,
                transaction_factory=None,
                data_use=ForecastDataUse.EXPIRED_RATING_TEST,
            )
        )
        app.dependency_overrides[deps.get_stores] = lambda: stores
        client: Any
        with TestClient(app) as client:
            response = client.get(
                f"/api/v1/stations/{claim.frozen.members[0].station_id}/evaluation-forecasts"
            )
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
