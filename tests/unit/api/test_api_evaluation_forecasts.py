# ruff: noqa: F811 -- Reuse the selector scenario fixture.
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from sapphire_flow.api import deps
from sapphire_flow.api.cors import ScopedCorsMiddleware
from sapphire_flow.api.errors import http_exception_handler, unhandled_exception_handler
from sapphire_flow.api.publication_cache import PublicationCacheMiddleware
from sapphire_flow.api.publication_gate import get_publication_gate
from sapphire_flow.api.review_auth import require_reviewer_or_human
from sapphire_flow.api.routes.api_evaluation_forecasts import router
from sapphire_flow.api.security import Principal
from sapphire_flow.config.evaluation_forecasts import EvaluationForecastsConfig
from sapphire_flow.types.daily_forecast_outcome import DailyOutputSource
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import AccessTokenRole, ForecastDataUse, QcStatus
from sapphire_flow.types.human_auth import HumanPermission, HumanPrincipal, StationGrant
from sapphire_flow.types.ids import AccessTokenId, UserId
from sapphire_flow.types.rejected_forecast import PersistedRejectedForecast
from tests.unit.services.test_evaluation_forecasts import case  # noqa: F401


@pytest.fixture
def api(case: SimpleNamespace) -> SimpleNamespace:  # noqa: F811
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(PublicationCacheMiddleware)
    app.add_middleware(
        ScopedCorsMiddleware,
        consumer_origins=[],
        human_dashboard_origin="https://review.example",
    )
    principal = Principal(
        token_id=AccessTokenId(UUID(int=90)),
        role=AccessTokenRole.REVIEWER,
        tenant_id=case.station.tenant_id,
        station_ids=frozenset({case.station.id}),
    )
    group_store = SimpleNamespace(
        fetch_group=case.stores.groups.fetch_group,
        fetch_groups_for_station=lambda _: [],
    )
    stations = SimpleNamespace(
        fetch_station=lambda _: case.station, fetch_model_assignments=lambda _: []
    )
    config = SimpleNamespace(
        evaluation_forecasts=EvaluationForecastsConfig(
            tenant_id=case.station.tenant_id,
            deployment="bafu",
            group_ids=case.settings.group_ids,
            plausibility_models=("cmal_small",),
        )
    )
    state = SimpleNamespace(principal=principal, active=False, config=config)
    app.dependency_overrides.update(
        {
            require_reviewer_or_human: lambda: state.principal,
            deps.get_stores: lambda: dict(
                station_store=stations,
                group_store=group_store,
                forecast_store=case.records,
                rejected_forecast_store=case.stores.rejected,
            ),
            deps.get_daily_evaluation_reader: lambda: case.days,
            deps.get_deployment_config: lambda: state.config,
            deps.get_evaluation_clock: lambda: case.issue + timedelta(hours=1),
            deps.get_evaluation_contracts: lambda: {},
            get_publication_gate: lambda: SimpleNamespace(
                active=lambda _: state.active
            ),
        }
    )
    state.stations = stations
    state.app = app
    state.client = TestClient(app, raise_server_exceptions=False)
    state.url = f"/api/v1/stations/{case.station.id}/evaluation-forecasts"
    return state


class TestEvaluationRoute:
    def test_actual_selector_exact_reference(
        self, api: SimpleNamespace, case: SimpleNamespace
    ) -> None:
        response = api.client.get(api.url)
        assert response.status_code == 200
        body = response.json()
        selected = body["parameters"][0]
        assert selected["selection"] == "current"
        assert selected["selected"]["output_id"] == str(case.record.id)
        assert selected["selected"]["values"]
        assert response.headers["cache-control"] == "no-store"
        assert "roster" not in response.text
        assert "url" not in response.text

    @pytest.mark.parametrize("days", [0, 3])
    @pytest.mark.parametrize("rejected", [False, True])
    @pytest.mark.parametrize("active", [False, True])
    def test_publication_redaction_both_sources(
        self,
        api: SimpleNamespace,
        case: SimpleNamespace,
        rejected: bool,
        active: bool,
        days: int,
    ) -> None:
        flag = QcFlag(
            rule_id="range_check",
            rule_version="1",
            status=QcStatus.QC_FAILED if rejected else QcStatus.QC_PASSED,
            detail="sensitive QC",
        )
        case.records.records = [replace(case.record, qc_flags=(flag,))]
        if rejected:
            ref = replace(
                case.summary.outcomes[0].references[0],
                source=DailyOutputSource.REJECTED,
            )
            member = replace(case.summary.outcomes[0], references=(ref,))
            summary = replace(case.summary, outcomes=(member,))
            case.days.day = replace(
                case.days.day, latest=summary, authoritative=summary
            )
            case.stores.rejected.records = [
                PersistedRejectedForecast(
                    id=case.record.id,
                    attempt_id=case.summary.claim.id,
                    recorded_at=case.issue,
                    station_id=case.station.id,
                    model_id=case.record.model_id,
                    model_artifact_id=case.record.model_artifact_id,
                    group_id=case.settings.group_ids[0],
                    issued_at=case.issue,
                    parameter="discharge",
                    representation=case.record.representation,
                    units=case.record.ensemble.units,
                    time_step_seconds=3600,
                    qc_status=QcStatus.QC_FAILED,
                    qc_flags=(flag,),
                    values={
                        str(k): tuple(
                            (r["valid_time"], r["value"])
                            for r in case.record.ensemble.values.to_dicts()
                            if r["member_id"] == k
                        )
                        for k in (0, 1)
                    },
                )
            ]
        from sapphire_flow.types.enums import ModelAssignmentStatus

        api.stations.fetch_model_assignments = lambda _: [
            SimpleNamespace(
                station_id=case.station.id,
                model_id=case.record.model_id,
                priority=1,
                status=ModelAssignmentStatus.ACTIVE,
            )
        ]
        api.active = active
        api.app.dependency_overrides[deps.get_evaluation_clock] = lambda: (
            case.issue + timedelta(days=days, hours=1)
        )
        response = api.client.get(api.url)
        assert response.status_code == 200
        selected = response.json()["parameters"][0]["selected"]
        diagnostic = response.json()["models"][0]["records"][0]
        if days:
            assert selected is None
            assert (
                response.json()["parameters"][0]["daily_absence"] == "no_usable_output"
            )
            selected = diagnostic
        else:
            assert diagnostic == selected
        assert response.headers["cache-control"] == "no-store"
        assert selected["withheld"] is active
        assert (selected["values"] is None) is active
        assert (selected["qc_flags"][0]["detail"] is None) is active
        if active:
            assert selected["provenance"] is None
            assert response.json()["daily"][0]["provenance"] is None
            assert "sensitive QC" not in response.text

    @pytest.mark.parametrize("time", ["2026-01-01T00:00:00", "not-time"])
    def test_bad_time_is_no_store_before_reads(
        self, api: SimpleNamespace, case: SimpleNamespace, time: str
    ) -> None:
        response = api.client.get(api.url, params={"valid_at": time})
        assert response.status_code == 400
        assert response.headers["cache-control"] == "no-store"
        assert case.records.reads == []

    def test_future_valid_time_does_not_move_day(
        self, api: SimpleNamespace, case: SimpleNamespace
    ) -> None:
        response = api.client.get(
            api.url, params={"valid_at": (case.issue + timedelta(days=2)).isoformat()}
        )
        body = response.json()
        assert body["issue_time"].startswith(case.issue.date().isoformat())
        assert body["parameters"][0]["absence"] == "no_valid_time_coverage"

    @pytest.mark.parametrize("failure", ["off", "scope", "tenant", "purpose"])
    def test_fail_closed_before_value_reads(
        self, api: SimpleNamespace, case: SimpleNamespace, failure: str
    ) -> None:
        if failure == "off":
            api.config = None
        elif failure == "scope":
            api.principal = replace(api.principal, station_ids=frozenset())
        elif failure == "tenant":
            api.config = SimpleNamespace(
                evaluation_forecasts=api.config.evaluation_forecasts.model_copy(
                    update={"tenant_id": UUID(int=88)}
                )
            )
        else:
            case.records.data_use = ForecastDataUse.EXPIRED_RATING_TEST
        response = api.client.get(api.url)
        assert response.status_code == (503 if failure == "purpose" else 404)
        assert response.headers["cache-control"] == "no-store"
        assert case.records.reads == []

    @pytest.mark.parametrize("status", [401, 403, 404])
    def test_auth_errors_no_store(self, api: SimpleNamespace, status: int) -> None:
        def refuse() -> None:
            raise HTTPException(status, "Denied")

        api.app.dependency_overrides[require_reviewer_or_human] = refuse
        response = api.client.get(api.url)
        assert response.status_code == status
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.parametrize(
        "method,origin,allowed",
        [
            ("GET", "https://review.example", True),
            ("POST", "https://review.example", False),
            ("GET", "https://evil.example", False),
        ],
    )
    def test_cors_get_only_and_no_store(
        self, api: SimpleNamespace, method: str, origin: str, allowed: bool
    ) -> None:
        response = api.client.options(
            api.url,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        assert response.status_code == (200 if allowed else 400)
        assert response.headers["cache-control"] == "no-store"
        if allowed:
            assert response.headers["access-control-allow-credentials"] == "true"

    @pytest.mark.parametrize("granted", [False, True])
    def test_human_current_grant(
        self, api: SimpleNamespace, case: SimpleNamespace, granted: bool
    ) -> None:
        api.active = True
        api.principal = HumanPrincipal(
            user_id=UserId(UUID(int=91)),
            tenant_id=case.station.tenant_id,
            grants=frozenset(
                {
                    StationGrant(
                        station_id=case.station.id, permission=HumanPermission.REVIEW
                    )
                }
            )
            if granted
            else frozenset(),
        )
        response = api.client.get(api.url)
        assert response.status_code == (200 if granted else 404)
        if granted:
            assert response.json()["parameters"][0]["selected"]["values"]
        else:
            assert case.records.reads == []

    def test_admin_not_withheld(self, api: SimpleNamespace) -> None:
        api.active = True
        api.principal = replace(api.principal, role=AccessTokenRole.ADMIN)
        assert api.client.get(api.url).json()["parameters"][0]["selected"]["values"]

    def test_unhandled_error_no_store(self, api: SimpleNamespace) -> None:
        def broken() -> None:
            raise RuntimeError("private failure")

        api.app.dependency_overrides[deps.get_daily_evaluation_reader] = broken
        response = api.client.get(api.url)
        assert response.status_code == 500
        assert response.headers["cache-control"] == "no-store"
        assert "private failure" not in response.text

    def test_input_details_withheld(
        self, api: SimpleNamespace, case: SimpleNamespace
    ) -> None:
        from sapphire_flow.types.domain import InputQualityFlag
        from sapphire_flow.types.enums import InputQualityCategory, InputQualityLevel

        flag = InputQualityFlag(
            category=next(iter(InputQualityCategory)),
            level=InputQualityLevel.DEGRADED,
            detail="sensitive input",
        )
        case.records.records = [
            replace(
                case.record,
                input_quality=InputQualityLevel.DEGRADED,
                input_quality_flags=(flag,),
            )
        ]
        api.active = True
        response = api.client.get(api.url)
        assert (
            response.json()["parameters"][0]["selected"]["input_quality_flags"][0][
                "detail"
            ]
            is None
        )
        assert "sensitive input" not in response.text

    def test_no_cors_without_human_origin(self, api: SimpleNamespace) -> None:
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides.update(api.app.dependency_overrides)
        app.add_middleware(
            ScopedCorsMiddleware,
            consumer_origins=["https://review.example"],
            human_dashboard_origin=None,
        )
        response = TestClient(app).get(
            api.url, headers={"Origin": "https://review.example"}
        )
        assert "access-control-allow-origin" not in response.headers

    def test_request_ledger_reader_is_read_only(self) -> None:
        reader = deps.get_daily_evaluation_reader(conn=object())
        assert callable(reader.read_day)
        assert not hasattr(reader, "claim")
        assert not hasattr(reader, "finalize")

    def test_declared_fallback_dependency(
        self, api: SimpleNamespace, case: SimpleNamespace
    ) -> None:
        from sapphire_flow.types.enums import ModelAssignmentStatus
        from sapphire_flow.types.ids import ModelId

        model = ModelId("persistence")
        record = replace(
            case.record,
            model_id=model,
            ensemble=replace(case.record.ensemble, model_id=model),
        )
        case.records.records = [record]
        case.days.day = replace(case.days.day, latest=None, authoritative=None)
        api.stations.fetch_model_assignments = lambda _: [
            SimpleNamespace(
                station_id=case.station.id,
                model_id=model,
                priority=5,
                status=ModelAssignmentStatus.ACTIVE,
            )
        ]
        body = api.client.get(api.url).json()
        assert body["parameters"][0]["selection"] == "absent"
        assert body["assignments"][0]["declared_parameters"] == []
        api.app.dependency_overrides[deps.get_evaluation_contracts] = lambda: {
            model: (case.contract,)
        }
        body = api.client.get(api.url).json()
        assert body["parameters"][0]["selection"] == "fallback"
        assert body["parameters"][0]["selected"]["model_id"] == model

    def test_cross_tenant_principal_cannot_use_stale_scope(
        self, api: SimpleNamespace, case: SimpleNamespace
    ) -> None:
        api.principal = replace(api.principal, tenant_id=UUID(int=75))
        assert api.client.get(api.url).status_code == 404
        assert case.records.reads == []

    def test_daily_reader_borrows_existing_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore

        connection = object()
        seen = []

        class BorrowedStore:
            def __init__(self, factory: Any) -> None:
                self.factory = factory

            def read_day(self, key: Any) -> str:
                with self.factory() as borrowed:
                    seen.append((borrowed, key))
                return "snapshot"

        monkeypatch.setattr(
            "sapphire_flow.store.daily_forecast_store.PgDailyForecastStore",
            BorrowedStore,
        )
        assert PgDailyForecastStore is not BorrowedStore
        reader = deps.get_daily_evaluation_reader(conn=connection)
        assert reader.read_day("key") == "snapshot"
        assert seen == [(connection, "key")]


class TestUnselectedModelDiagnostics:
    @pytest.mark.parametrize("active", [False, True])
    def test_stale_ordinary_record_is_visible_but_redacted(
        self, api: SimpleNamespace, case: SimpleNamespace, active: bool
    ) -> None:
        from sapphire_flow.types.enums import ModelAssignmentStatus
        from sapphire_flow.types.ids import ModelId

        model = ModelId("other")
        issue = case.issue - timedelta(days=10)
        flag = QcFlag(
            rule_id="range_check",
            rule_version="1",
            status=QcStatus.QC_PASSED,
            detail="private diagnostic",
        )
        old = replace(
            case.record,
            model_id=model,
            issued_at=issue,
            qc_flags=(flag,),
            ensemble=replace(case.record.ensemble, model_id=model, issued_at=issue),
        )
        case.records.records.append(old)
        api.stations.fetch_model_assignments = lambda _: [
            SimpleNamespace(
                station_id=case.station.id,
                model_id=model,
                priority=100,
                status=ModelAssignmentStatus.ACTIVE,
            )
        ]
        api.active = active
        response = api.client.get(api.url)
        assert response.status_code == 200
        body = response.json()
        diagnostic = body["models"][0]
        assert diagnostic["freshness"] == "stale"
        assert diagnostic["age_seconds"] == timedelta(days=10, hours=1).total_seconds()
        assert (diagnostic["records"][0]["values"] is None) is active
        assert (diagnostic["records"][0]["qc_flags"][0]["detail"] is None) is active
        assert body["parameters"][0]["selected"]["model_id"] == case.record.model_id
        if active:
            assert "private diagnostic" not in response.text
            assert diagnostic["records"][0]["provenance"] is None


class TestBoundedDiagnostics:
    @pytest.mark.parametrize("withheld", [False, True])
    def test_superseded_reference_reason_is_withheld(
        self, api: SimpleNamespace, case: SimpleNamespace, withheld: bool
    ) -> None:
        from sapphire_flow.types.daily_forecast import DailyAttemptState
        from sapphire_flow.types.enums import ForecastStatus
        from sapphire_flow.types.ids import ForecastId

        retry = replace(
            case.summary,
            state=DailyAttemptState.ACTIVE,
            claim=replace(case.summary.claim, id=UUID(int=99)),
            finished_at=None,
            outcomes=None,
        )
        case.days.day = replace(case.days.day, latest=retry)
        case.records.records = [
            replace(case.record, status=ForecastStatus.SUPERSEDED),
            replace(case.record, id=ForecastId(UUID(int=100))),
        ]
        api.active = withheld
        response = api.client.get(api.url)
        assert response.status_code == 200
        body = response.json()
        assert body["parameters"][0]["selected"] is None
        reference = body["daily"][0]["references"][0]
        assert reference["output_id"] == str(case.record.id)
        assert reference["usability"] == (None if withheld else "superseded")

    def test_post_clock_transition_returns_unavailable_not_no_attempt(
        self, api: SimpleNamespace, case: SimpleNamespace
    ) -> None:
        from sapphire_flow.types.enums import ModelAssignmentStatus
        from sapphire_flow.types.ids import ModelId

        future = replace(case.summary, finished_at=case.issue + timedelta(hours=2))
        case.days.day = replace(case.days.day, latest=future, authoritative=future)
        model = ModelId("ordinary")
        ordinary = replace(
            case.record,
            model_id=model,
            ensemble=replace(case.record.ensemble, model_id=model),
        )
        case.records.records.append(ordinary)
        api.stations.fetch_model_assignments = lambda _: [
            SimpleNamespace(
                station_id=case.station.id,
                model_id=model,
                priority=1,
                status=ModelAssignmentStatus.ACTIVE,
            )
        ]
        api.app.dependency_overrides[deps.get_evaluation_contracts] = lambda: {
            model: (case.contract,)
        }
        response = api.client.get(api.url)
        assert response.status_code == 200
        body = response.json()
        assert body["parameters"][0]["selection"] == "fallback"
        assert body["daily"][0]["availability"] == "post_as_of"
        assert body["daily"][0]["latest_state"] == "unavailable"
        assert body["daily"][0]["references"] == []
        assert body["daily"][0]["recovery_status"] is None


class TestFailedDiagnosticAttributionRoute:
    @pytest.mark.parametrize("unrelated_summary", [False, True])
    @pytest.mark.parametrize("active", [False, True])
    @pytest.mark.parametrize("authorized", [False, True])
    def test_failed_attempt_identity_without_values_or_cross_group_authority(
        self,
        case: SimpleNamespace,
        unrelated_summary: bool,
        active: bool,
        authorized: bool,
        request: pytest.FixtureRequest,
    ) -> None:
        from sapphire_flow.types.enums import ModelAssignmentStatus
        from tests.unit.services.test_evaluation_forecasts import failed_diagnostic_case

        failed_diagnostic_case(case, unrelated_summary)
        api = request.getfixturevalue("api")
        api.active = active
        api.stations.fetch_model_assignments = lambda _: [
            SimpleNamespace(
                station_id=case.station.id,
                model_id=case.record.model_id,
                priority=1,
                status=ModelAssignmentStatus.ACTIVE,
            )
        ]
        if not authorized:
            api.principal = replace(api.principal, station_ids=frozenset())
        response = api.client.get(api.url)
        assert response.headers["cache-control"] == "no-store"
        assert "private failed evidence" not in response.text
        assert "-123" not in response.text
        if not authorized:
            assert response.status_code == 404
            assert case.days.reads == []
            return
        assert response.status_code == 200
        diagnostic = response.json()["models"][0]
        assert diagnostic["evidence_authority"] == "stored_only_unverified"
        assert diagnostic["authoritative_attempt_id"] is None
        assert diagnostic["latest_attempt_id"] == str(case.summary.claim.id)
        assert diagnostic["latest_state"] == "failed"
        assert diagnostic["group_id"] == str(case.settings.group_ids[0])
        assert diagnostic["records"] == []
        assert diagnostic["stored_result"]["output_id"] == str(UUID(int=888))
        assert response.json()["parameters"][0]["selected"] is None
