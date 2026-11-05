# ruff: noqa: F811 -- Shared authority scenario fixture.
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import jsonschema
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from sapphire_flow.api import deps
from sapphire_flow.api.cors import ScopedCorsMiddleware
from sapphire_flow.api.errors import http_exception_handler, unhandled_exception_handler
from sapphire_flow.api.map_contract import build_map_openapi
from sapphire_flow.api.publication_cache import PublicationCacheMiddleware
from sapphire_flow.api.publication_gate import get_publication_gate
from sapphire_flow.api.routes import api_swiss_overview as route
from sapphire_flow.api.security import Principal, require_principal
from sapphire_flow.config.swiss_overview import SwissOverviewConfig
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.dhm_delivery import DELIVERY_ID
from sapphire_flow.types.enums import AccessTokenRole, QcStatus
from sapphire_flow.types.ids import AccessTokenId, ObservationId, StationId, TenantId
from sapphire_flow.types.swiss_overview import SwissRosterEntry
from tests.conftest import make_observation
from tests.fakes.fake_stores import FakeObservationStore
from tests.unit.services.test_evaluation_forecasts import case  # noqa: F401

_PATH = "/api/v1/swiss/overview"


@pytest.fixture
def api(case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    app = FastAPI()
    app.include_router(route.router)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(PublicationCacheMiddleware)
    app.add_middleware(
        ScopedCorsMiddleware,
        consumer_origins=["https://review.example"],
        human_dashboard_origin=None,
    )
    principal = Principal(
        token_id=AccessTokenId(UUID(int=90)),
        role=AccessTokenRole.REVIEWER,
        tenant_id=case.station.tenant_id,
        station_ids=frozenset({case.station.id}),
    )
    roster = (
        SwissRosterEntry(
            station_id=case.station.id,
            network=case.station.network,
            code=case.station.code,
        ),
    )
    state = SimpleNamespace(
        principal=principal,
        active=False,
        now=ensure_utc(case.issue + timedelta(hours=1, minutes=30)),
        roster=roster,
        stations=[case.station],
        obs=FakeObservationStore(),
        obs_calls=[],
        reads=[],
    )
    state.config = SimpleNamespace(
        swiss_overview=SwissOverviewConfig(roster_count=1, roster_sha256="a" * 64),
        evaluation_forecasts=SimpleNamespace(to_domain=lambda: case.settings),
        bafu_forecast_archive_path=None,
    )
    monkeypatch.setattr(route, "load_swiss_roster", lambda _: state.roster)

    def accepted(ids: list) -> dict:
        state.reads.append(("accepted", ids))
        return {r.id: r for r in case.records.records if r.id in ids}

    def rejected(ids: list) -> dict:
        state.reads.append(("rejected", ids))
        return {r.id: r for r in case.stores.rejected.records if r.id in ids}

    case.records.fetch_forecasts_by_ids = accepted
    case.stores.rejected.fetch_rejected_forecasts_by_ids = rejected
    fetch = state.obs.fetch_latest_observations

    def observations(*args: object, **kwargs: object) -> dict:
        state.obs_calls.append((args, kwargs))
        return fetch(*args, **kwargs)

    state.obs.fetch_latest_observations = observations
    app.dependency_overrides.update(
        {
            require_principal: lambda: state.principal,
            deps.get_stores: lambda: dict(
                station_store=SimpleNamespace(
                    fetch_all_stations=lambda: state.stations
                ),
                obs_store=state.obs,
                group_store=case.stores.groups,
                forecast_store=case.records,
                rejected_forecast_store=case.stores.rejected,
            ),
            deps.get_daily_evaluation_reader: lambda: case.days,
            deps.get_deployment_config: lambda: state.config,
            deps.get_evaluation_clock: lambda: state.now,
            get_publication_gate: lambda: SimpleNamespace(
                active=lambda _: state.active
            ),
        }
    )
    state.app = app
    state.client = TestClient(app, raise_server_exceptions=False)
    return state


class TestSwissOverview:
    def test_real_authority_headline_and_schema(
        self, api: SimpleNamespace, case: SimpleNamespace
    ) -> None:
        response = api.client.get(_PATH)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        item = body["items"][0]
        assert item["forecast"]["output_id"] == str(case.record.id)
        assert item["forecast"]["state"] == "available"
        assert item["forecast"]["series"]["state"] == "available"
        assert item["forecast"]["series"]["points"]
        assert item["reported_level"]["reason"] == "not_onboarded"
        assert item["thresholds"] == []
        assert len(api.obs_calls) == 4
        assert len(api.reads) == 2
        doc = build_map_openapi()
        schema = doc["paths"][_PATH]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        jsonschema.validate(body, {**schema, "components": doc["components"]})

    @pytest.mark.parametrize("role", [AccessTokenRole.REVIEWER, AccessTokenRole.ADMIN])
    def test_exact_gate_redaction(
        self, api: SimpleNamespace, role: AccessTokenRole
    ) -> None:
        api.active = True
        api.principal = replace(api.principal, role=role)
        item = api.client.get(_PATH).json()["items"][0]
        assert item["bafu_comparison"] == {
            "state": "withheld",
            "reason": "publication_gate",
            "source": "bafu",
        }
        if role is AccessTokenRole.REVIEWER:
            assert set(item["forecast"]) == {
                "state",
                "reason",
                "output_id",
                "output_source",
                "qc_status",
                "model_id",
                "issued_at",
            }
        else:
            assert item["forecast"]["state"] == "available"

    def test_opaque_member_ids_do_not_fail_route_encoding(
        self, case: SimpleNamespace
    ) -> None:
        import polars as pl

        from sapphire_flow.api.routes.api_swiss_overview import _cmal
        from sapphire_flow.services.evaluation_forecasts import (
            read_evaluation_forecasts,
        )
        from sapphire_flow.services.swiss_overview import CurrentHeadline

        candidate = (
            read_evaluation_forecasts(
                station=case.station,
                settings=case.settings,
                assignments=(),
                stores=case.stores,
                as_of=case.issue + timedelta(hours=1),
            )
            .parameters[0]
            .candidate
        )
        candidate = replace(
            candidate,
            record=replace(
                candidate.record,
                ensemble=replace(
                    candidate.record.ensemble,
                    values=candidate.record.ensemble.values.with_columns(
                        pl.col("member_id").replace_strict(
                            {0: "control", 1: "member-a"}
                        )
                    ),
                ),
            ),
        )
        result = _cmal(
            CurrentHeadline(candidate=candidate, absence=None),
            as_of=case.issue + timedelta(hours=1),
            withheld=False,
        )
        assert result.state == "available"
        assert result.series is not None
        assert result.series.state == "available"

    def test_four_qc_windows_do_not_carry_forward(
        self, api: SimpleNamespace, case: SimpleNamespace
    ) -> None:
        hour = api.now.replace(minute=0, second=0, microsecond=0)
        observations = [
            make_observation(
                station_id=case.station.id,
                timestamp=ensure_utc(hour - timedelta(hours=2)),
                value=11,
                qc_status=QcStatus.QC_PASSED,
            ),
            make_observation(
                station_id=case.station.id,
                timestamp=ensure_utc(hour),
                value=12,
                qc_status=QcStatus.QC_PASSED,
            ),
            make_observation(
                station_id=case.station.id,
                timestamp=ensure_utc(hour + timedelta(minutes=1)),
                value=99,
                qc_status=QcStatus.QC_FAILED,
            ),
            replace(
                make_observation(
                    station_id=case.station.id,
                    timestamp=ensure_utc(hour + timedelta(minutes=2)),
                    value=98,
                    qc_status=QcStatus.QC_PASSED,
                ),
                delivery_id=DELIVERY_ID,
            ),
        ]
        api.obs.store_observations(
            [
                replace(row, id=ObservationId(UUID(int=i + 500)))
                for i, row in enumerate(observations)
            ]
        )
        api.principal = replace(api.principal, role=AccessTokenRole.ADMIN)
        body = api.client.get(_PATH).json()["items"][0]["observation"]
        assert body["latest"]["value"] == 12
        assert [
            h["sample"]["value"] if h["sample"] else None for h in body["hourly"]
        ] == [11, None, 12]

    @pytest.mark.parametrize("role", [AccessTokenRole.ADMIN, AccessTokenRole.REVIEWER])
    def test_admin_roster_and_reviewer_intersection(
        self, api: SimpleNamespace, case: SimpleNamespace, role: AccessTokenRole
    ) -> None:
        unlisted = replace(case.station, id=StationId(UUID(int=444)), code="unlisted")
        api.stations.append(unlisted)
        api.principal = replace(
            api.principal, role=role, station_ids=frozenset({unlisted.id})
        )
        body = api.client.get(_PATH).json()
        assert body["station_count"] == (1 if role is AccessTokenRole.ADMIN else 0)
        assert "unlisted" not in str(body)

    def test_foreign_tenant_grants_are_empty(self, api: SimpleNamespace) -> None:
        api.principal = replace(api.principal, tenant_id=TenantId(UUID(int=555)))
        assert api.client.get(_PATH).json()["station_count"] == 0

    @pytest.mark.parametrize("fault", ["disabled", "metadata", "missing_station"])
    def test_config_failure_before_data_reads(
        self, api: SimpleNamespace, fault: str
    ) -> None:
        if fault == "disabled":
            api.config = None
        elif fault == "metadata":
            api.stations = [replace(api.stations[0], code="drift")]
        else:
            api.stations = []
        response = api.client.get(_PATH)
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        assert not api.obs_calls and not api.reads

    def test_consumer_rejected_before_reads(self, api: SimpleNamespace) -> None:
        api.principal = replace(api.principal, role=AccessTokenRole.CONSUMER)
        response = api.client.get(_PATH)
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"
        assert not api.obs_calls

    def test_unauthenticated_no_store(self, api: SimpleNamespace) -> None:
        def unauthorized() -> Principal:
            raise HTTPException(401, "unauthorized")

        api.app.dependency_overrides[require_principal] = unauthorized
        response = api.client.get(_PATH)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.parametrize("method", ["GET", "POST"])
    def test_preflight_no_store(self, api: SimpleNamespace, method: str) -> None:
        response = api.client.options(
            _PATH,
            headers={
                "Origin": "https://review.example",
                "Access-Control-Request-Method": method,
            },
        )
        assert response.status_code == (200 if method == "GET" else 400)
        assert response.headers["cache-control"] == "no-store"

    def test_authority_integrity_is_component_failure(
        self, api: SimpleNamespace, case: SimpleNamespace
    ) -> None:
        corrupt = replace(
            case.summary,
            claim=replace(
                case.summary.claim,
                key=replace(case.summary.claim.key, deployment="wrong"),
            ),
        )
        case.days.day = replace(case.days.day, authoritative=corrupt)
        response = api.client.get(_PATH)
        assert response.status_code == 200
        assert response.json()["items"][0]["forecast"] == {
            "state": "unavailable",
            "reason": "authority_integrity_error",
        }
        assert not api.reads

    def test_unknown_error_not_mislabeled(
        self, api: SimpleNamespace, case: SimpleNamespace
    ) -> None:
        def broken(_: object) -> object:
            raise RuntimeError("unexpected")

        case.days.read_day = broken
        response = api.client.get(_PATH)
        assert response.status_code == 500
        assert response.headers["cache-control"] == "no-store"
        assert "authority_integrity_error" not in response.text

    def test_yesterday_is_never_current(self, api: SimpleNamespace) -> None:
        api.now = ensure_utc(api.now + timedelta(days=1))
        assert api.client.get(_PATH).json()["items"][0]["forecast"] == {
            "state": "unavailable",
            "reason": "pending_current_issue",
        }


class TestCorrectionBoundaries:
    def test_error_responses_are_in_contract(self) -> None:
        responses = build_map_openapi()["paths"][_PATH]["get"]["responses"]
        for status in ("500", "503"):
            assert responses[status]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }

    @pytest.mark.parametrize("failure", ["decode", "ordinary_value_error", "protected"])
    def test_decode_failure_does_not_hide_other_components(
        self, api: SimpleNamespace, case: SimpleNamespace, tmp_path, failure: str
    ) -> None:
        from sapphire_flow.exceptions import StoredForecastDecodeError
        from sapphire_flow.services.forecast_read import ForecastReadUnavailableError
        from tests.fakes.bafu_archive import write_raw_evidence
        from tests.unit.services.forecast_lab.test_bafu_archive import (
            _rows_from_fixture,
            _write_run,
        )

        rows = _rows_from_fixture(
            case.station.code, issued_at=case.issue, produced_at=case.issue
        )
        shift = case.issue - rows[0]["valid_time"]
        for row in rows:
            row["valid_time"] += shift
        _write_run(tmp_path, case.station.code, case.issue, rows)
        write_raw_evidence(tmp_path, case.station.code, case.issue, rows)
        api.config.bafu_forecast_archive_path = tmp_path
        api.obs.store_observations(
            [
                make_observation(
                    station_id=case.station.id,
                    timestamp=api.now,
                    value=12,
                    qc_status=QcStatus.QC_PASSED,
                )
            ]
        )

        def broken(ids: list) -> dict:
            if failure == "decode":
                raise StoredForecastDecodeError("invalid stored forecast record")
            if failure == "protected":
                raise ForecastReadUnavailableError()
            raise ValueError("programmer error")

        case.records.fetch_forecasts_by_ids = broken
        response = api.client.get(_PATH)
        assert response.headers["cache-control"] == "no-store"
        assert (
            response.status_code
            == {"decode": 200, "ordinary_value_error": 500, "protected": 503}[failure]
        )
        if failure == "decode":
            item = response.json()["items"][0]
            assert item["forecast"] == {
                "state": "unavailable",
                "reason": "authority_integrity_error",
            }
            assert item["observation"]["latest"]["value"] == 12
            assert item["bafu_comparison"]["state"] == "available"

    def test_malformed_archive_name_preserves_other_components(
        self, api: SimpleNamespace, case: SimpleNamespace, tmp_path: Path
    ) -> None:
        api.config.bafu_forecast_archive_path = tmp_path
        (tmp_path / "parsed").mkdir()
        (
            tmp_path
            / "parsed"
            / f"{case.station.code}_q_forecast_20251340T250000Z.parquet"
        ).touch()
        api.obs.store_observations(
            [
                make_observation(
                    station_id=case.station.id,
                    timestamp=api.now,
                    value=12,
                    qc_status=QcStatus.QC_PASSED,
                )
            ]
        )
        response = api.client.get(_PATH)
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["observation"]["latest"]["value"] == 12
        assert item["forecast"]["state"] == "available"
        assert item["bafu_comparison"] == {
            "state": "unavailable",
            "reason": "missing_archive",
        }
