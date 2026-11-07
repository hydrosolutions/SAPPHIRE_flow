from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.types.enums import EnsembleRepresentation
from tests.conftest import make_forecast_ensemble
from tests.integration.api.test_dashboard_forecasts import (
    _client,
    app_overrides_clear,
)
from tests.integration.api.test_dashboard_forecasts import (
    _reset_reflected as _reset_reflected,
)
from tests.integration.store.test_forecast_store import savepoint_factory
from tests.integration.store.test_native_rating_forecasts import native_pair
from tests.integration.store.test_selected_weather_evidence import selected

if TYPE_CHECKING:
    import sqlalchemy as sa


@pytest.mark.parametrize("purpose", ["standard", "provisional_rating"])
@pytest.mark.parametrize(
    "representation", [EnsembleRepresentation.MEMBERS, EnsembleRepresentation.QUANTILES]
)
def test_authorized_selected_data_and_html_preserve_purpose(
    db_connection: sa.Connection,
    purpose: str,
    representation: EnsembleRepresentation,
) -> None:
    ordinary, provisional = native_pair(db_connection)
    base = ordinary if purpose == "standard" else provisional
    base = replace(
        base,
        representation=representation,
        ensemble=make_forecast_ensemble(
            station_id=base.station_id, representation=representation
        ),
    )
    forecast = selected(base)
    PgForecastStore(
        db_connection,
        data_use=forecast.data_use,
        transaction_factory=savepoint_factory(db_connection),
    ).store_forecast(forecast)
    client = _client(db_connection)
    try:
        response = client.get(
            f"/api/v1/forecasts/{forecast.id}/data.json?data_use={purpose}"
        )
        html = client.get(f"/forecasts/{forecast.id}/?data_use={purpose}")
        wrong_purpose = (
            "standard" if purpose == "provisional_rating" else "provisional_rating"
        )
        mismatch = client.get(
            f"/api/v1/forecasts/{forecast.id}/data.json?data_use={wrong_purpose}"
        )
    finally:
        app_overrides_clear()
    assert response.status_code == 200
    body = response.json()
    assert body["weather_use"]["station_id"] == str(forecast.station_id)
    assert body["weather_use"]["past"]["freshness"] == "unused"
    assert "input_quality" in body and "input_quality_flags" in body
    assert html.status_code == 200
    assert mismatch.json() == {"lead_times": [], "members": {}}
    if purpose == "provisional_rating":
        assert body["badge"] == "PROVISIONAL" and body["rating_input"]
        assert "PROVISIONAL" in html.text


@pytest.mark.parametrize(
    "path", ["/api/v1/forecasts/{id}/data.json", "/forecasts/{id}/"]
)
def test_selected_missing_evidence_returns_safe_unavailable(
    db_connection: sa.Connection,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    ordinary, _ = native_pair(db_connection)
    forecast = selected(ordinary)
    PgForecastStore(
        db_connection, transaction_factory=savepoint_factory(db_connection)
    ).store_forecast(forecast)
    monkeypatch.setattr(PgForecastStore, "fetch_evidence", lambda self, fid: None)
    client = _client(db_connection)
    try:
        response = client.get(path.format(id=forecast.id))
    finally:
        app_overrides_clear()
    assert response.status_code == 503
    assert "Selected weather evidence is unavailable" in response.text


def test_legacy_does_not_read_weather_evidence(
    db_connection: sa.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ordinary, _ = native_pair(db_connection)
    PgForecastStore(
        db_connection, transaction_factory=savepoint_factory(db_connection)
    ).store_forecast(ordinary)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("legacy weather read must not inspect evidence")

    monkeypatch.setattr(PgForecastStore, "fetch_evidence", forbidden)
    client = _client(db_connection)
    try:
        response = client.get(f"/api/v1/forecasts/{ordinary.id}/data.json")
    finally:
        app_overrides_clear()
    assert response.status_code == 200 and response.json()["weather_use"] is None


@pytest.mark.parametrize(
    "fault", ["missing", "hash", "truncated", "oversized", "station", "purpose"]
)
def test_standard_selected_corruption_is_unavailable(
    db_connection: sa.Connection,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    import hashlib
    import json
    import zlib
    from uuid import uuid4

    from fastapi import HTTPException

    from sapphire_flow.api.publication_gate import PublicationGate
    from sapphire_flow.api.routes.api_forecasts import get_forecast
    from sapphire_flow.api.security import Principal
    from sapphire_flow.types.enums import AccessTokenRole
    from sapphire_flow.types.ids import AccessTokenId

    ordinary, _ = native_pair(db_connection)
    forecast = selected(ordinary)
    store = PgForecastStore(
        db_connection, transaction_factory=savepoint_factory(db_connection)
    )
    store.store_forecast(forecast)
    evidence = store.fetch_evidence(forecast.id)
    assert evidence is not None
    if fault == "missing":
        evidence = None
    elif fault == "hash":
        evidence = replace(evidence, snapshot_sha256="0" * 64)
    elif fault in ("truncated", "oversized"):
        snapshot = (
            b"invalid"
            if fault == "truncated"
            else zlib.compress(b" " * (32 * 1024 * 1024 + 1))
        )
        evidence = replace(
            evidence,
            snapshot=snapshot,
            snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
        )
    else:
        manifest = json.loads(evidence.manifest_json)
        manifest["station_id" if fault == "station" else "data_use"] = (
            str(uuid4()) if fault == "station" else "provisional_rating"
        )
        evidence = replace(evidence, manifest_json=json.dumps(manifest))
    monkeypatch.setattr(store, "fetch_evidence", lambda fid: evidence)
    principal = Principal(
        token_id=AccessTokenId(uuid4()),
        role=AccessTokenRole.ADMIN,
        tenant_id=None,
        station_ids=frozenset(),
    )
    with pytest.raises(
        HTTPException, match="Selected weather evidence is unavailable"
    ) as caught:
        get_forecast(
            str(forecast.id),
            {"forecast_store": store},
            principal,
            PublicationGate(),
        )
    assert caught.value.status_code == 503


def test_standard_selected_scope_denial_precedes_weather_read(
    db_connection: sa.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uuid import uuid4

    from fastapi import HTTPException

    from sapphire_flow.api.publication_gate import PublicationGate
    from sapphire_flow.api.routes.api_forecasts import get_forecast
    from sapphire_flow.api.security import Principal
    from sapphire_flow.types.enums import AccessTokenRole
    from sapphire_flow.types.ids import AccessTokenId, StationId

    ordinary, _ = native_pair(db_connection)
    forecast = selected(ordinary)
    store = PgForecastStore(
        db_connection, transaction_factory=savepoint_factory(db_connection)
    )
    store.store_forecast(forecast)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("out-of-scope weather evidence must not be read")

    monkeypatch.setattr(store, "fetch_evidence", forbidden)
    principal = Principal(
        token_id=AccessTokenId(uuid4()),
        role=AccessTokenRole.CONSUMER,
        tenant_id=None,
        station_ids=frozenset({StationId(uuid4())}),
    )
    with pytest.raises(HTTPException, match="Forecast not found") as caught:
        get_forecast(
            str(forecast.id),
            {"forecast_store": store},
            principal,
            PublicationGate(),
        )
    assert caught.value.status_code == 404
