# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnusedFunction=false, reportUnknownArgumentType=false, reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownLambdaType=false
"""Plan 404 T3 — `GET /api/v1/stations/{id}/rejected-forecasts`."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from sapphire_flow.api import review_auth
from sapphire_flow.api.cors import ScopedCorsMiddleware
from sapphire_flow.api.review_auth import require_reviewer_or_human
from sapphire_flow.api.security import Principal, generate_raw_token
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import AccessTokenRole, ForecastDataUse, QcStatus
from sapphire_flow.types.human_auth import HumanPermission, HumanPrincipal, StationGrant
from sapphire_flow.types.ids import (
    AccessTokenId,
    ModelId,
    StationId,
    TenantId,
    UserId,
)
from sapphire_flow.types.rejected_forecast import (
    RejectedAssignmentPayload,
    RejectedForecastEntry,
    RejectedParameterPayload,
)
from tests.conftest import make_forecast_ensemble, make_station_config
from tests.fakes.fake_stores import FakeRejectedForecastStore

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import tzinfo

_NOW = ensure_utc(datetime(2026, 9, 28, 12, tzinfo=UTC))
_DEFAULT_MODEL_ID = ModelId("test-model")


def _service_token() -> tuple[str, str]:
    """A well-shaped (one dot) service-token bearer, for shape tests that
    never need it to actually authenticate."""
    raw_key, _, _ = generate_raw_token()
    return raw_key, raw_key.split(".")[0]


def _oidc_shaped_bearer() -> str:
    return "a.b.c"  # three JWT segments, two dots


def _request(bearer: str | None, *, human_verifier: object | None = "unset") -> Request:
    app = FastAPI()
    if human_verifier != "unset":
        app.state.human_oidc_verifier = human_verifier
    headers = (
        [] if bearer is None else [(b"authorization", f"Bearer {bearer}".encode())]
    )
    return Request({"type": "http", "app": app, "headers": headers})


class TestRequireReviewerOrHuman:
    """Unit-level: the bearer's shape decides which verifier runs, with no
    fallback, and every failure collapses to the SAME 401 body."""

    def test_no_bearer_is_401(self) -> None:
        with pytest.raises(HTTPException) as exc:
            require_reviewer_or_human(_request(None), conn=None)  # type: ignore[arg-type]
        assert exc.value.status_code == 401

    def test_malformed_shape_zero_dots_401_neither_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            review_auth, "require_principal", lambda *a, **kw: calls.append("service")
        )
        monkeypatch.setattr(
            review_auth,
            "require_human_principal",
            lambda *a, **kw: calls.append("human"),
        )
        with pytest.raises(HTTPException) as exc:
            require_reviewer_or_human(_request("no-dot-here"), conn=None)  # type: ignore[arg-type]
        assert exc.value.status_code == 401
        assert calls == []

    def test_malformed_shape_three_dots_401_neither_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            review_auth, "require_principal", lambda *a, **kw: calls.append("service")
        )
        monkeypatch.setattr(
            review_auth,
            "require_human_principal",
            lambda *a, **kw: calls.append("human"),
        )
        with pytest.raises(HTTPException) as exc:
            require_reviewer_or_human(_request("a.b.c.d"), conn=None)  # type: ignore[arg-type]
        assert exc.value.status_code == 401
        assert calls == []

    def test_service_token_never_reaches_oidc_verifier(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        raw_key, _ = _service_token()
        principal = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.REVIEWER,
            tenant_id=None,
            station_ids=frozenset(),
        )

        def _boom(*a: object, **kw: object) -> object:
            raise AssertionError("OIDC verifier must not be called for a service token")

        monkeypatch.setattr(
            review_auth, "require_principal", lambda *a, **kw: principal
        )
        monkeypatch.setattr(review_auth, "require_human_principal", _boom)
        result = require_reviewer_or_human(_request(raw_key), conn=None)  # type: ignore[arg-type]
        assert result is principal

    def test_oidc_token_never_looked_up_as_access_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        human = HumanPrincipal(
            user_id=UserId(uuid4()), tenant_id=TenantId(uuid4()), grants=frozenset()
        )

        def _boom(*a: object, **kw: object) -> object:
            raise AssertionError(
                "access-token lookup must not run for an OIDC-shaped bearer"
            )

        monkeypatch.setattr(review_auth, "require_principal", _boom)
        monkeypatch.setattr(
            review_auth, "require_human_principal", lambda *a, **kw: human
        )
        result = require_reviewer_or_human(
            _request(_oidc_shaped_bearer()),
            conn=None,  # type: ignore[arg-type]
        )
        assert result is human

    def test_service_token_still_succeeds_with_human_auth_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        raw_key, _ = _service_token()
        principal = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        monkeypatch.setattr(
            review_auth, "require_principal", lambda *a, **kw: principal
        )
        # human auth disabled -> require_human_principal would 503, but the
        # service branch never calls it.
        monkeypatch.setattr(
            review_auth,
            "require_human_principal",
            lambda *a, **kw: (_ for _ in ()).throw(
                HTTPException(
                    status_code=503, detail="Human authentication is disabled"
                )
            ),
        )
        result = require_reviewer_or_human(_request(raw_key), conn=None)  # type: ignore[arg-type]
        assert result is principal

    def test_oidc_shaped_bearer_with_human_auth_disabled_is_401_not_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            review_auth,
            "require_human_principal",
            lambda *a, **kw: (_ for _ in ()).throw(
                HTTPException(
                    status_code=503, detail="Human authentication is disabled"
                )
            ),
        )
        with pytest.raises(HTTPException) as exc:
            require_reviewer_or_human(
                _request(_oidc_shaped_bearer()),
                conn=None,  # type: ignore[arg-type]
            )
        assert exc.value.status_code == 401
        assert exc.value.detail == "Missing or invalid access token"

    def test_deactivated_human_is_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            review_auth,
            "require_human_principal",
            lambda *a, **kw: (_ for _ in ()).throw(
                HTTPException(
                    status_code=401, detail="Missing or invalid human access token"
                )
            ),
        )
        with pytest.raises(HTTPException) as exc:
            require_reviewer_or_human(
                _request(_oidc_shaped_bearer()),
                conn=None,  # type: ignore[arg-type]
            )
        assert exc.value.status_code == 401

    def test_identical_401_bodies_across_failure_cases(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bodies: list[tuple[int, str | None]] = []

        # No bearer at all.
        with pytest.raises(HTTPException) as exc:
            require_reviewer_or_human(_request(None), conn=None)  # type: ignore[arg-type]
        bodies.append((exc.value.status_code, exc.value.detail))

        # Malformed shape.
        with pytest.raises(HTTPException) as exc:
            require_reviewer_or_human(_request("no-dot"), conn=None)  # type: ignore[arg-type]
        bodies.append((exc.value.status_code, exc.value.detail))

        # Human auth disabled, OIDC-shaped bearer.
        monkeypatch.setattr(
            review_auth,
            "require_human_principal",
            lambda *a, **kw: (_ for _ in ()).throw(
                HTTPException(
                    status_code=503, detail="Human authentication is disabled"
                )
            ),
        )
        with pytest.raises(HTTPException) as exc:
            require_reviewer_or_human(
                _request(_oidc_shaped_bearer()),
                conn=None,  # type: ignore[arg-type]
            )
        bodies.append((exc.value.status_code, exc.value.detail))

        assert len(set(bodies)) == 1

    def test_consumer_service_token_is_403(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        raw_key, _ = _service_token()
        consumer = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.CONSUMER,
            tenant_id=None,
            station_ids=frozenset(),
        )
        monkeypatch.setattr(review_auth, "require_principal", lambda *a, **kw: consumer)
        with pytest.raises(HTTPException) as exc:
            require_reviewer_or_human(_request(raw_key), conn=None)  # type: ignore[arg-type]
        assert exc.value.status_code == 403


def _seed_rejected_entry(
    store: FakeRejectedForecastStore,
    *,
    station_id: StationId,
    model_id: ModelId = _DEFAULT_MODEL_ID,
    issued_at: UtcDatetime = _NOW,
    attempt_id: UUID | None = None,
    n_steps: int = 2,
    n_members: int = 2,
    qc_status: QcStatus = QcStatus.QC_FAILED,
    with_nonfinite: bool = False,
) -> None:
    ensemble = make_forecast_ensemble(
        station_id=station_id, n_steps=n_steps, n_members=n_members
    )
    if with_nonfinite:
        import polars as pl

        df = ensemble.values.with_columns(
            pl.when(pl.int_range(pl.len()) == 0)
            .then(float("nan"))
            .otherwise(pl.col("value"))
            .alias("value")
        )
        ensemble = type(ensemble)(
            representation=ensemble.representation,
            values=df,
            station_id=ensemble.station_id,
            issued_at=ensemble.issued_at,
            parameter=ensemble.parameter,
            units=ensemble.units,
            forecast_horizon_steps=ensemble.forecast_horizon_steps,
            time_step=ensemble.time_step,
        )
    payload = RejectedAssignmentPayload(
        station_id=station_id,
        model_id=model_id,
        model_artifact_id=None,
        issued_at=issued_at,
        parameters=(
            RejectedParameterPayload(
                ensemble=ensemble,
                qc_status=qc_status,
                qc_flags=(
                    QcFlag(
                        rule_id="range_check",
                        rule_version="1.0",
                        status=qc_status,
                        detail="value out of range",
                    ),
                )
                if qc_status != QcStatus.QC_PASSED
                else (),
            ),
        ),
    )
    store.write_batch(
        [RejectedForecastEntry(attempt_id=attempt_id or uuid4(), payload=payload)],
        abandon=threading.Event(),
    )


@pytest.fixture
def rejected_fake_stores(fake_stores: dict[str, Any]) -> dict[str, Any]:
    fake_stores["rejected_forecast_store"] = FakeRejectedForecastStore()
    return fake_stores


def _override_principal(app: FastAPI, principal: object) -> None:
    app.dependency_overrides[require_reviewer_or_human] = lambda: principal


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    from sapphire_flow.api import app
    from sapphire_flow.api.publication_gate import get_publication_gate

    app.dependency_overrides.pop(require_reviewer_or_human, None)
    app.dependency_overrides.pop(get_publication_gate, None)


class TestGetRejectedForecasts:
    @pytest.fixture(autouse=True)
    def _fixed_route_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from sapphire_flow.api.routes import api_rejected_forecasts

        class FixedRouteDatetime(datetime):
            @classmethod
            def now(cls, tz: tzinfo | None = None) -> datetime:
                instant = _NOW + timedelta(hours=1)
                if tz is None:
                    return instant.replace(tzinfo=None)
                return instant.astimezone(tz)

            @classmethod
            def fromisoformat(cls, date_string: str) -> datetime:
                return datetime.fromisoformat(date_string)

        monkeypatch.setattr(api_rejected_forecasts, "datetime", FixedRouteDatetime)

    def test_reviewer_in_scope_sees_values_and_flags_not_withheld(
        self, client: TestClient, rejected_fake_stores: dict
    ) -> None:
        from sapphire_flow.api import app

        station = make_station_config(code="RF-1")
        rejected_fake_stores["station_store"].store_station(station)
        _seed_rejected_entry(
            rejected_fake_stores["rejected_forecast_store"], station_id=station.id
        )
        reviewer = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.REVIEWER,
            tenant_id=None,
            station_ids=frozenset({station.id}),
        )
        _override_principal(app, reviewer)

        resp = client.get(f"/api/v1/stations/{station.id}/rejected-forecasts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["withheld"] is False
        assert item["values"] is not None
        assert item["qc_flags"][0]["detail"] == "value out of range"

    def test_reviewer_out_of_scope_station_is_404(
        self, client: TestClient, rejected_fake_stores: dict
    ) -> None:
        from sapphire_flow.api import app

        station = make_station_config(code="RF-2")
        rejected_fake_stores["station_store"].store_station(station)
        reviewer = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.REVIEWER,
            tenant_id=None,
            station_ids=frozenset(),
        )
        _override_principal(app, reviewer)
        resp = client.get(f"/api/v1/stations/{station.id}/rejected-forecasts")
        assert resp.status_code == 404

    def test_admin_sees_any_existing_station(
        self, client: TestClient, rejected_fake_stores: dict
    ) -> None:
        from sapphire_flow.api import app

        station = make_station_config(code="RF-3")
        rejected_fake_stores["station_store"].store_station(station)
        _seed_rejected_entry(
            rejected_fake_stores["rejected_forecast_store"], station_id=station.id
        )
        admin = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        _override_principal(app, admin)
        resp = client.get(f"/api/v1/stations/{station.id}/rejected-forecasts")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_unknown_station_is_404_for_reviewer_and_admin(
        self, client: TestClient, rejected_fake_stores: dict
    ) -> None:
        from sapphire_flow.api import app

        for role in (AccessTokenRole.REVIEWER, AccessTokenRole.ADMIN):
            principal = Principal(
                token_id=AccessTokenId(uuid4()),
                role=role,
                tenant_id=None,
                station_ids=frozenset(),
            )
            _override_principal(app, principal)
            resp = client.get(f"/api/v1/stations/{uuid4()}/rejected-forecasts")
            assert resp.status_code == 404, role

    def test_malformed_station_id_is_400(
        self, client: TestClient, rejected_fake_stores: dict
    ) -> None:
        from sapphire_flow.api import app

        admin = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        _override_principal(app, admin)
        resp = client.get("/api/v1/stations/not-a-uuid/rejected-forecasts")
        assert resp.status_code == 400

    def test_model_id_and_date_filters_and_default_window(
        self, client: TestClient, rejected_fake_stores: dict
    ) -> None:
        from sapphire_flow.api import app

        station = make_station_config(code="RF-4")
        rejected_fake_stores["station_store"].store_station(station)
        store = rejected_fake_stores["rejected_forecast_store"]
        # The route clock is fixed one hour after this row. Explicit end=_NOW
        # therefore checks the exclusive endpoint against a row exactly on it.
        _seed_rejected_entry(
            store,
            station_id=station.id,
            model_id=ModelId("model-a"),
            issued_at=_NOW,
        )
        _seed_rejected_entry(
            store,
            station_id=station.id,
            model_id=ModelId("model-b"),
            issued_at=_NOW - timedelta(days=30),
        )
        admin = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        _override_principal(app, admin)

        # Default window (last 7 days): the 30-day-old row is excluded.
        resp = client.get(f"/api/v1/stations/{station.id}/rejected-forecasts")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["model_id"] == "model-a"

        # model_id filter.
        resp = client.get(
            f"/api/v1/stations/{station.id}/rejected-forecasts",
            params={
                "model_id": "model-b",
                "start": (_NOW - timedelta(days=40)).isoformat(),
                "end": (_NOW + timedelta(days=1)).isoformat(),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["model_id"] == "model-b"

        # end is exclusive.
        resp = client.get(
            f"/api/v1/stations/{station.id}/rejected-forecasts",
            params={
                "start": (_NOW - timedelta(days=1)).isoformat(),
                "end": _NOW.isoformat(),
            },
        )
        assert resp.json()["total"] == 0

    def test_default_window_includes_start_and_excludes_end(
        self, client: TestClient, rejected_fake_stores: dict
    ) -> None:
        from sapphire_flow.api import app

        station = make_station_config(code="RF-BOUNDARY")
        rejected_fake_stores["station_store"].store_station(station)
        now = _NOW + timedelta(hours=1)
        start = now - timedelta(days=7)
        for issued_at in (
            start,
            start - timedelta(microseconds=1),
            now - timedelta(microseconds=1),
            now,
        ):
            _seed_rejected_entry(
                rejected_fake_stores["rejected_forecast_store"],
                station_id=station.id,
                issued_at=issued_at,
            )
        admin = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        _override_principal(app, admin)

        response = client.get(f"/api/v1/stations/{station.id}/rejected-forecasts")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2
        assert [datetime.fromisoformat(row["issued_at"]) for row in body["items"]] == [
            now - timedelta(microseconds=1),
            start,
        ]

    def test_gate_forced_on_withholds_values_for_reviewer_not_admin(
        self,
        client: TestClient,
        rejected_fake_stores: dict,
    ) -> None:
        from sapphire_flow.api import app
        from sapphire_flow.api.publication_gate import (
            PublicationGate,
            get_publication_gate,
        )

        station = make_station_config(code="RF-5")
        app.dependency_overrides[get_publication_gate] = lambda: PublicationGate(
            active_tenant_ids=frozenset({station.tenant_id})
        )
        rejected_fake_stores["station_store"].store_station(station)
        _seed_rejected_entry(
            rejected_fake_stores["rejected_forecast_store"], station_id=station.id
        )

        reviewer = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.REVIEWER,
            tenant_id=None,
            station_ids=frozenset({station.id}),
        )
        _override_principal(app, reviewer)
        resp = client.get(f"/api/v1/stations/{station.id}/rejected-forecasts")
        item = resp.json()["items"][0]
        assert item["withheld"] is True
        assert resp.headers["cache-control"] == "no-store"
        assert item["values"] is None
        assert item["qc_flags"][0]["detail"] is None
        # every other field is still present
        assert item["id"] and item["model_artifact_id"] is None
        assert item["qc_status"] == "qc_failed"

        admin = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        _override_principal(app, admin)
        resp = client.get(f"/api/v1/stations/{station.id}/rejected-forecasts")
        item = resp.json()["items"][0]
        assert item["withheld"] is False
        assert item["values"] is not None

    def test_items_ordered_newest_first(
        self, client: TestClient, rejected_fake_stores: dict
    ) -> None:
        from sapphire_flow.api import app

        station = make_station_config(code="RF-6")
        rejected_fake_stores["station_store"].store_station(station)
        store = rejected_fake_stores["rejected_forecast_store"]
        _seed_rejected_entry(
            store, station_id=station.id, issued_at=_NOW - timedelta(hours=2)
        )
        _seed_rejected_entry(store, station_id=station.id, issued_at=_NOW)
        admin = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        _override_principal(app, admin)
        resp = client.get(f"/api/v1/stations/{station.id}/rejected-forecasts")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2
        items = resp.json()["items"]
        assert len(items) == 2
        issued_ats = [item["issued_at"] for item in items]
        assert issued_ats == sorted(issued_ats, reverse=True)

    def test_nonfinite_values_round_trip(
        self, client: TestClient, rejected_fake_stores: dict
    ) -> None:
        from sapphire_flow.api import app

        station = make_station_config(code="RF-7")
        rejected_fake_stores["station_store"].store_station(station)
        _seed_rejected_entry(
            rejected_fake_stores["rejected_forecast_store"],
            station_id=station.id,
            with_nonfinite=True,
        )
        admin = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        _override_principal(app, admin)
        resp = client.get(f"/api/v1/stations/{station.id}/rejected-forecasts")
        item = resp.json()["items"][0]
        series = next(iter(item["values"].values()))
        assert any(
            isinstance(point["value"], dict) and point["value"]["nonfinite"] == "nan"
            for point in series
        )

    def test_limit_above_max_is_refused(
        self, client: TestClient, rejected_fake_stores: dict
    ) -> None:
        from sapphire_flow.api import app

        station = make_station_config(code="RF-8")
        rejected_fake_stores["station_store"].store_station(station)
        admin = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        _override_principal(app, admin)
        resp = client.get(
            f"/api/v1/stations/{station.id}/rejected-forecasts", params={"limit": 51}
        )
        assert resp.status_code == 422

    def test_limit_offset_paginate(
        self, client: TestClient, rejected_fake_stores: dict
    ) -> None:
        from sapphire_flow.api import app

        station = make_station_config(code="RF-9")
        rejected_fake_stores["station_store"].store_station(station)
        store = rejected_fake_stores["rejected_forecast_store"]
        for i in range(3):
            _seed_rejected_entry(
                store, station_id=station.id, issued_at=_NOW - timedelta(hours=i)
            )
        admin = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        _override_principal(app, admin)
        resp = client.get(
            f"/api/v1/stations/{station.id}/rejected-forecasts",
            params={"limit": 1, "offset": 1},
        )
        body = resp.json()
        assert body["total"] == 3
        assert len(body["items"]) == 1

    def test_human_with_current_review_grant_sees_full_record(
        self,
        client: TestClient,
        rejected_fake_stores: dict,
    ) -> None:
        from sapphire_flow.api import app
        from sapphire_flow.api.publication_gate import (
            PublicationGate,
            get_publication_gate,
        )

        # Gate forced ON: a reviewer service token would be withheld, but a
        # granted human still sees everything (D4).
        station = make_station_config(code="RF-10")
        app.dependency_overrides[get_publication_gate] = lambda: PublicationGate(
            active_tenant_ids=frozenset({station.tenant_id})
        )
        rejected_fake_stores["station_store"].store_station(station)
        _seed_rejected_entry(
            rejected_fake_stores["rejected_forecast_store"], station_id=station.id
        )
        human = HumanPrincipal(
            user_id=UserId(uuid4()),
            tenant_id=TenantId(uuid4()),
            grants=frozenset(
                {StationGrant(station_id=station.id, permission=HumanPermission.REVIEW)}
            ),
        )
        _override_principal(app, human)
        resp = client.get(f"/api/v1/stations/{station.id}/rejected-forecasts")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["withheld"] is False
        assert item["values"] is not None

    def test_human_without_grant_is_404(
        self, client: TestClient, rejected_fake_stores: dict
    ) -> None:
        from sapphire_flow.api import app

        station = make_station_config(code="RF-11")
        rejected_fake_stores["station_store"].store_station(station)
        human = HumanPrincipal(
            user_id=UserId(uuid4()), tenant_id=TenantId(uuid4()), grants=frozenset()
        )
        _override_principal(app, human)
        resp = client.get(f"/api/v1/stations/{station.id}/rejected-forecasts")
        assert resp.status_code == 404

    def test_consumer_is_403(
        self,
        client: TestClient,
        rejected_fake_stores: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from sapphire_flow.api import app

        # Route the real dependency through, only stubbing the access-token
        # lookup (this bypasses `_override_principal`, which would skip the
        # role check entirely).
        app.dependency_overrides.pop(require_reviewer_or_human, None)
        raw_key, _ = _service_token()
        consumer = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.CONSUMER,
            tenant_id=None,
            station_ids=frozenset(),
        )
        monkeypatch.setattr(review_auth, "require_principal", lambda *a, **kw: consumer)
        station = make_station_config(code="RF-12")
        rejected_fake_stores["station_store"].store_station(station)
        resp = client.get(
            f"/api/v1/stations/{station.id}/rejected-forecasts",
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert resp.status_code == 403


class TestRejectedForecastsCors:
    @pytest.fixture
    def cors_client(self) -> Iterator[TestClient]:
        app = FastAPI()

        @app.get("/api/v1/stations/one/rejected-forecasts")
        def get_rejected() -> dict[str, str]:
            return {"result": "ok"}

        @app.get("/api/v1/stations/one/other")
        def other() -> dict[str, str]:
            return {"result": "ok"}

        app.add_middleware(
            ScopedCorsMiddleware,
            consumer_origins=["https://consumer.example.org"],
            human_dashboard_origin="https://dashboard.example.org",
        )
        with TestClient(app) as client:
            yield client

    def test_dashboard_origin_allowed_on_get_and_preflight(
        self, cors_client: TestClient
    ) -> None:
        preflight = cors_client.options(
            "/api/v1/stations/one/rejected-forecasts",
            headers={
                "Origin": "https://dashboard.example.org",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert preflight.status_code == 200
        assert (
            preflight.headers["access-control-allow-origin"]
            == "https://dashboard.example.org"
        )

    def test_other_origin_refused(self, cors_client: TestClient) -> None:
        denied = cors_client.options(
            "/api/v1/stations/one/rejected-forecasts",
            headers={
                "Origin": "https://evil.example.org",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert denied.status_code == 400

    def test_another_stations_route_not_covered(self, cors_client: TestClient) -> None:
        # /rejected-forecasts gets the new policy; a sibling stations route
        # does not — it falls to the consumer policy, which does not admit
        # the dashboard origin.
        resp = cors_client.options(
            "/api/v1/stations/one/other",
            headers={
                "Origin": "https://dashboard.example.org",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 400

    def test_post_preflight_refused(self, cors_client: TestClient) -> None:
        resp = cors_client.options(
            "/api/v1/stations/one/rejected-forecasts",
            headers={
                "Origin": "https://dashboard.example.org",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert resp.status_code == 400


class TestOrdinaryRejectedReadBoundary:
    @pytest.mark.parametrize(
        "purpose", [None, "standard", ForecastDataUse.EXPIRED_RATING_TEST]
    )
    @pytest.mark.parametrize("offset", [0, 100])
    def test_wrong_purpose_never_queries_page(
        self, purpose: object, offset: int
    ) -> None:
        from sapphire_flow.api.publication_gate import PublicationGate
        from sapphire_flow.api.routes.api_rejected_forecasts import (
            get_rejected_forecasts,
        )
        from tests.fakes.fake_stores import FakeStationStore

        station = make_station_config()
        station_store = FakeStationStore()
        station_store.store_station(station)

        class Store:
            data_use = purpose

            def fetch_rejected_forecasts(
                self, *args: object, **kwargs: object
            ) -> object:
                pytest.fail("rejected count/page queried before purpose refusal")

        principal = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        with pytest.raises(
            HTTPException, match="Ordinary forecast reads are unavailable"
        ) as exc:
            get_rejected_forecasts(
                str(station.id),
                model_id=None,
                start=None,
                end=None,
                limit=20,
                offset=offset,
                stores={
                    "station_store": station_store,
                    "rejected_forecast_store": Store(),
                },
                principal=principal,
                gate=PublicationGate(),
            )
        assert exc.value.status_code == 503

    def test_rejected_serializer_refuses_even_when_values_withheld(self) -> None:
        from sapphire_flow.api.routes.api_rejected_forecasts import _to_response

        class Result:
            data_use = ForecastDataUse.EXPIRED_RATING_TEST

            def __getattr__(self, name: str) -> object:
                pytest.fail("protected rejection canary accessed: " + name)

        with pytest.raises(
            HTTPException, match="Ordinary forecast reads are unavailable"
        ) as exc:
            _to_response(Result(), withheld=True)  # type: ignore[arg-type]
        assert exc.value.status_code == 503


class TestRejectedPageResultAndAuthorization:
    @pytest.mark.parametrize("reader", ["reviewer", "admin", "human"])
    def test_whole_page_refused_before_rendering_even_when_withheld(
        self, client: TestClient, rejected_fake_stores: dict[str, Any], reader: str
    ) -> None:
        from types import SimpleNamespace

        from sapphire_flow.api import app
        from sapphire_flow.api.publication_gate import (
            PublicationGate,
            get_publication_gate,
        )

        station = make_station_config()
        rejected_fake_stores["station_store"].store_station(station)
        if reader == "human":
            principal = HumanPrincipal(
                user_id=UserId(uuid4()),
                tenant_id=station.tenant_id,
                grants=frozenset(
                    {
                        StationGrant(
                            station_id=station.id, permission=HumanPermission.REVIEW
                        )
                    }
                ),
            )
        else:
            principal = Principal(
                token_id=AccessTokenId(uuid4()),
                role=AccessTokenRole(reader),
                tenant_id=station.tenant_id,
                station_ids=frozenset({station.id}),
            )
        _override_principal(app, principal)
        app.dependency_overrides[get_publication_gate] = lambda: PublicationGate(
            active_tenant_ids=frozenset({station.tenant_id})
        )

        class Store:
            data_use = ForecastDataUse.STANDARD

            def fetch_rejected_forecasts(
                self, *args: object, **kwargs: object
            ) -> object:
                return [
                    SimpleNamespace(data_use=ForecastDataUse.STANDARD),
                    SimpleNamespace(data_use=ForecastDataUse.EXPIRED_RATING_TEST),
                ], 987654

        rejected_fake_stores["rejected_forecast_store"] = Store()
        response = client.get(f"/api/v1/stations/{station.id}/rejected-forecasts")
        assert response.status_code == 503
        assert response.json() == {
            "error": "Ordinary forecast reads are unavailable",
            "detail": None,
        }

    @pytest.mark.parametrize(
        "reader", ["foreign_reviewer", "human_no_grant", "unknown_station"]
    )
    def test_scope_and_existence_precede_wrong_purpose(
        self, client: TestClient, rejected_fake_stores: dict[str, Any], reader: str
    ) -> None:
        from sapphire_flow.api import app

        station = make_station_config()
        if reader != "unknown_station":
            rejected_fake_stores["station_store"].store_station(station)
        principal: object = (
            HumanPrincipal(
                user_id=UserId(uuid4()), tenant_id=station.tenant_id, grants=frozenset()
            )
            if reader == "human_no_grant"
            else Principal(
                token_id=AccessTokenId(uuid4()),
                role=AccessTokenRole.REVIEWER,
                tenant_id=station.tenant_id,
                station_ids=frozenset({station.id})
                if reader == "unknown_station"
                else frozenset(),
            )
        )
        _override_principal(app, principal)
        rejected_fake_stores["rejected_forecast_store"] = object()
        assert (
            client.get(f"/api/v1/stations/{station.id}/rejected-forecasts").status_code
            == 404
        )
