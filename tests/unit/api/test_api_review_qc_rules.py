"""Plan 402 T1 — `GET /api/v1/qc/rules[?station_id=]`."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from sapphire_flow.types.enums import AccessTokenRole
from sapphire_flow.types.ids import StationId
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from fastapi.testclient import TestClient


def _set_principal(role: AccessTokenRole, station_ids: frozenset[StationId]) -> None:
    from sapphire_flow.api import app
    from sapphire_flow.api.security import Principal, require_principal
    from sapphire_flow.types.ids import AccessTokenId

    app.dependency_overrides[require_principal] = lambda: Principal(
        token_id=AccessTokenId(uuid4()),
        role=role,
        tenant_id=None,
        station_ids=station_ids,
    )


class TestDeploymentScopeResolution:
    def test_no_station_id_is_deployment_scoped_with_builtin_default(
        self, client: TestClient
    ) -> None:
        resp = client.get("/api/v1/qc/rules")
        assert resp.status_code == 200
        body = resp.json()
        assert body["scope"] == "deployment"
        assert body["observation"]["source"] == "builtin_default"
        assert body["forecast"]["source"] == "builtin_default"
        assert "station_id" not in body
        assert "station_qc" not in body
        assert body["observation"]["selection"] == "parameter_cadence_network"
        assert body["forecast"]["selection"] == "parameter_and_cadence"

    def test_config_file_with_qc_rules_section_is_source_config(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = tmp_path / "config.toml"
        config.write_text(
            "max_retention_days = 730\n"
            "[qc_rules]\n"
            'version = "9.9.9"\n'
            "[[qc_rules.rules]]\n"
            'rule_id = "range_check"\nrule_version = "1.0"\n'
            'parameter = "discharge"\ntime_step_seconds = 600\n'
            "thresholds = { value_min = 0.0, value_max = 123.0 }\n"
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))

        resp = client.get("/api/v1/qc/rules")
        assert resp.status_code == 200
        body = resp.json()
        assert body["observation"]["source"] == "config"
        assert body["observation"]["version"] == "9.9.9"
        assert len(body["observation"]["rules"]) == 1
        assert body["observation"]["rules"][0]["thresholds"]["value_max"] == 123.0

    def test_severity_comes_from_the_code_constant(self, client: TestClient) -> None:
        resp = client.get("/api/v1/qc/rules")
        body = resp.json()
        by_rule_id = {r["rule_id"]: r for r in body["observation"]["rules"]}
        assert by_rule_id["range_check"]["severity"] == "qc_failed"
        assert by_rule_id["rate_of_change"]["severity"] == "qc_suspect"
        forecast_by_rule_id = {r["rule_id"]: r for r in body["forecast"]["rules"]}
        assert forecast_by_rule_id["negative_value"]["severity"] == "qc_failed"
        assert forecast_by_rule_id["flat_ensemble"]["severity"] == "qc_suspect"

    def test_same_rule_id_at_two_cadences_returns_both_rows(
        self, client: TestClient
    ) -> None:
        resp = client.get("/api/v1/qc/rules")
        rows = [
            r
            for r in resp.json()["observation"]["rules"]
            if r["rule_id"] == "range_check" and r["parameter"] == "discharge"
        ]
        assert {r["time_step_seconds"] for r in rows} == {600, 86400}

    def test_nan_and_inf_thresholds_served_as_null(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = tmp_path / "config.toml"
        config.write_text(
            "max_retention_days = 730\n"
            "[qc_rules]\n"
            'version = "nan-test"\n'
            "[[qc_rules.rules]]\n"
            'rule_id = "range_check"\nrule_version = "1.0"\n'
            'parameter = "discharge"\ntime_step_seconds = 600\n'
            "thresholds = { value_min = nan, value_max = inf }\n"
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))

        resp = client.get("/api/v1/qc/rules")
        assert resp.status_code == 200
        thresholds = resp.json()["observation"]["rules"][0]["thresholds"]
        assert thresholds == {"value_min": None, "value_max": None}

    def test_observation_block_never_holds_a_forecast_only_rule_id(
        self, client: TestClient
    ) -> None:
        resp = client.get("/api/v1/qc/rules")
        body = resp.json()
        observation_rule_ids = {r["rule_id"] for r in body["observation"]["rules"]}
        # `climatology_outlier`/`quantile_crossing` exist only on the forecast
        # side (Repository facts) — never a valid observation rule_id.
        assert "climatology_outlier" not in observation_rule_ids
        assert "quantile_crossing" not in observation_rule_ids


class TestAuth:
    def test_consumer_gets_403(self, client: TestClient) -> None:
        _set_principal(AccessTokenRole.CONSUMER, frozenset())
        try:
            resp = client.get("/api/v1/qc/rules")
        finally:
            from sapphire_flow.api import app
            from sapphire_flow.api.security import require_principal

            app.dependency_overrides.pop(require_principal, None)
        assert resp.status_code == 403

    def test_reviewer_scoped_to_the_station_gets_200(
        self, client: TestClient, fake_stores: dict
    ) -> None:
        station = make_station_config(code="REV-1")
        fake_stores["station_store"].store_station(station)
        _set_principal(AccessTokenRole.REVIEWER, frozenset({station.id}))
        try:
            resp = client.get(
                "/api/v1/qc/rules", params={"station_id": str(station.id)}
            )
        finally:
            from sapphire_flow.api import app
            from sapphire_flow.api.security import require_principal

            app.dependency_overrides.pop(require_principal, None)
        assert resp.status_code == 200
        assert resp.json()["scope"] == "station"


class TestStationScope:
    def test_malformed_station_id_is_400(self, client: TestClient) -> None:
        resp = client.get("/api/v1/qc/rules", params={"station_id": "not-a-uuid"})
        assert resp.status_code == 400
        assert resp.json()["error"] is not None

    def test_unknown_station_is_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/qc/rules", params={"station_id": str(uuid4())})
        assert resp.status_code == 404

    def test_out_of_scope_station_is_404_for_reviewer(
        self, client: TestClient, fake_stores: dict
    ) -> None:
        station = make_station_config(code="OOS-1")
        fake_stores["station_store"].store_station(station)
        _set_principal(AccessTokenRole.REVIEWER, frozenset({StationId(uuid4())}))
        try:
            resp = client.get(
                "/api/v1/qc/rules", params={"station_id": str(station.id)}
            )
        finally:
            from sapphire_flow.api import app
            from sapphire_flow.api.security import require_principal

            app.dependency_overrides.pop(require_principal, None)
        assert resp.status_code == 404

    def test_declared_override_merges_and_names_station_thresholds(
        self, client: TestClient, fake_stores: dict, tmp_path: Path, monkeypatch
    ) -> None:
        station = make_station_config(code="2135", network="bafu")
        fake_stores["station_store"].store_station(station)
        config = tmp_path / "config.toml"
        config.write_text(
            "max_retention_days = 730\n[onboarding]\n"
            "[[onboarding.station_qc_thresholds]]\n"
            'tenant_code = "sapphire"\ncode = "2135"\nnetwork = "bafu"\n'
            'rule_id = "range_check"\nparameter = "discharge"\n'
            "time_step_seconds = 600\nthresholds = { value_max = 10.0 }\n"
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))

        resp = client.get("/api/v1/qc/rules", params={"station_id": str(station.id)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["scope"] == "station"
        assert body["station_qc"] == "judged"
        assert body["water_level_datum_masl"] is None
        row = next(
            r
            for r in body["observation"]["rules"]
            if r["rule_id"] == "range_check"
            and r["parameter"] == "discharge"
            and r["time_step_seconds"] == 600
        )
        assert row["thresholds"]["value_max"] == 10.0
        assert row["thresholds"]["value_min"] == 0.0  # unchanged
        assert row["station_thresholds"] == ["value_max"]
        assert row["skipped"] is None
        # A different cadence's row is untouched by the 600s-only override.
        daily_row = next(
            r
            for r in body["observation"]["rules"]
            if r["rule_id"] == "range_check"
            and r["parameter"] == "discharge"
            and r["time_step_seconds"] == 86400
        )
        assert daily_row["station_thresholds"] == []
        assert daily_row["thresholds"]["value_max"] == 100000.0

    def test_undeclared_station_gets_network_rules_with_no_station_thresholds(
        self, client: TestClient, fake_stores: dict, tmp_path: Path, monkeypatch
    ) -> None:
        declared = make_station_config(code="2135", network="bafu")
        undeclared = make_station_config(code="2136", network="bafu")
        fake_stores["station_store"].store_station(declared)
        fake_stores["station_store"].store_station(undeclared)
        config = tmp_path / "config.toml"
        config.write_text(
            "max_retention_days = 730\n[onboarding]\n"
            "[[onboarding.station_qc_thresholds]]\n"
            'tenant_code = "sapphire"\ncode = "2135"\nnetwork = "bafu"\n'
            'rule_id = "range_check"\nparameter = "discharge"\n'
            "time_step_seconds = 600\nthresholds = { value_max = 10.0 }\n"
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))

        resp = client.get("/api/v1/qc/rules", params={"station_id": str(undeclared.id)})
        assert resp.status_code == 200
        body = resp.json()
        row = next(
            r
            for r in body["observation"]["rules"]
            if r["rule_id"] == "range_check"
            and r["parameter"] == "discharge"
            and r["time_step_seconds"] == 600
        )
        assert row["station_thresholds"] == []
        assert row["thresholds"]["value_max"] == 100000.0

    def test_not_judged_station_is_not_judged_and_has_no_station_thresholds(
        self, client: TestClient, fake_stores: dict, tmp_path: Path, monkeypatch
    ) -> None:
        from sapphire_flow.types.enums import StationStatus

        station = make_station_config(
            code="ONB-1", network="bafu", station_status=StationStatus.ONBOARDING
        )
        fake_stores["station_store"].store_station(station)
        config = tmp_path / "config.toml"
        config.write_text(
            "max_retention_days = 730\n[onboarding]\n"
            "[[onboarding.station_qc_thresholds]]\n"
            'tenant_code = "sapphire"\ncode = "ONB-1"\nnetwork = "bafu"\n'
            'rule_id = "range_check"\nparameter = "discharge"\n'
            "time_step_seconds = 600\nthresholds = { value_max = 10.0 }\n"
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))

        resp = client.get("/api/v1/qc/rules", params={"station_id": str(station.id)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["station_qc"] == "not_judged"
        row = next(
            r
            for r in body["observation"]["rules"]
            if r["rule_id"] == "range_check"
            and r["parameter"] == "discharge"
            and r["time_step_seconds"] == 600
        )
        assert row["station_thresholds"] == []

    def test_water_level_station_without_datum_marks_skipped_no_datum(
        self, client: TestClient, fake_stores: dict
    ) -> None:
        from sapphire_flow.types.enums import GaugingStatus, StationKind

        station = make_station_config(
            code="LAKE-1",
            station_kind=StationKind.LAKE,
            measured_parameters=frozenset({"water_level"}),
            gauging_status=GaugingStatus.GAUGED,
            water_level_datum_masl=None,
        )
        fake_stores["station_store"].store_station(station)

        resp = client.get("/api/v1/qc/rules", params={"station_id": str(station.id)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["water_level_datum_masl"] is None
        row = next(
            r
            for r in body["observation"]["rules"]
            if r["rule_id"] == "range_check" and r["parameter"] == "water_level"
        )
        assert row["skipped"] == "no_datum"
        rate_row = next(
            r
            for r in body["observation"]["rules"]
            if r["rule_id"] == "rate_of_change" and r["parameter"] == "water_level"
        )
        assert rate_row["skipped"] is None

    def test_duplicate_declared_block_is_500_on_station_variant_only(
        self, client: TestClient, fake_stores: dict, tmp_path: Path, monkeypatch
    ) -> None:
        station = make_station_config(code="2135", network="bafu")
        fake_stores["station_store"].store_station(station)
        config = tmp_path / "config.toml"
        config.write_text(
            "max_retention_days = 730\n[onboarding]\n"
            "[[onboarding.station_qc_thresholds]]\n"
            'tenant_code = "sapphire"\ncode = "2135"\nnetwork = "bafu"\n'
            'rule_id = "range_check"\nparameter = "discharge"\n'
            "time_step_seconds = 600\nthresholds = { value_max = 10.0 }\n"
            "[[onboarding.station_qc_thresholds]]\n"
            'tenant_code = "sapphire"\ncode = "2135"\nnetwork = "bafu"\n'
            'rule_id = "range_check"\nparameter = "discharge"\n'
            "time_step_seconds = 600\nthresholds = { value_max = 11.0 }\n"
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))

        # `client`'s dependency overrides live on the shared `app` singleton;
        # a second TestClient over the same `app`, with
        # `raise_server_exceptions=False`, observes the 500 response instead
        # of the exception propagating into the test.
        from fastapi.testclient import TestClient as _TestClient

        from sapphire_flow.api import app

        with _TestClient(app, raise_server_exceptions=False) as raw_client:
            deployment_resp = raw_client.get("/api/v1/qc/rules")
            station_resp = raw_client.get(
                "/api/v1/qc/rules", params={"station_id": str(station.id)}
            )
        assert deployment_resp.status_code == 200
        assert station_resp.status_code == 500
        assert station_resp.json() == {
            "error": "internal_server_error",
            "detail": None,
        }
