from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows import historical_ingest as historical
from sapphire_flow.flows.ingest_observations import ingest_observations_flow
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.historical_ingest import HistoricalCaptureRequest

NOW = ensure_utc(datetime(2026, 11, 3, tzinfo=UTC))
FIELDS = dict(
    historical_tenant_code="CHWRR",
    historical_station_ids=[str(uuid4())],
    historical_start=NOW - timedelta(days=2),
    historical_end=NOW - timedelta(days=1),
)
DEPENDENCIES = (
    "station_store",
    "obs_store",
    "baseline_store",
    "alert_store",
    "adapter",
    "qc_rules",
    "deployment_config",
    "clock",
    "formula_store",
    "pipeline_health_store",
    "tenant_store",
    "qc_obs_store",
    "bafu_unit_registry",
    "bafu_unit_writer",
    "rating_derived_store",
    "native_capture_store",
)


class TestHistoricalBoundary:
    @pytest.mark.parametrize("name", DEPENDENCIES)
    @pytest.mark.parametrize(
        "value", [{}, [], "override", 1, True, SimpleNamespace(), lambda: NOW]
    )
    def test_all_public_dependency_overrides_fail_before_bootstrap(
        self,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        value: object,
    ) -> None:
        def forbidden() -> None:
            pytest.fail("bootstrap must not run")

        monkeypatch.setattr(historical, "_bootstrap", forbidden)
        parameters = {**FIELDS, name: value}
        with pytest.raises(ConfigurationError, match="native-only"):
            ingest_observations_flow.fn(**parameters)
        validated = ingest_observations_flow.validate_parameters(parameters)
        with pytest.raises(ConfigurationError, match="native-only"):
            ingest_observations_flow.fn(**validated)

    @pytest.mark.parametrize("missing", tuple(FIELDS))
    def test_partial_fields_fail(self, missing: str) -> None:
        fields = dict(FIELDS)
        fields.pop(missing)
        with pytest.raises(ConfigurationError, match="native-only"):
            ingest_observations_flow.fn(**fields)

    @pytest.mark.parametrize(
        "extra",
        [
            {"context_window_hours": 1.0},
            {"rating_backfill_station_ids": []},
            {"rating_backfill_start": NOW},
            {"rating_backfill_end": NOW},
        ],
    )
    def test_conflicting_controls_fail(self, extra: dict[str, object]) -> None:
        with pytest.raises(ConfigurationError, match="native-only"):
            ingest_observations_flow.fn(**FIELDS, **extra)

    def test_bafu_config_refused_before_bootstrap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_BAFU_UNIT_INGEST_CONFIG", "unused")
        with pytest.raises(ConfigurationError, match="native-only"):
            ingest_observations_flow.fn(**FIELDS)

    @pytest.mark.parametrize(
        "section",
        [
            [],
            {"writable_tenants": "CHWRR"},
            {"writable_tenants": {}},
            {"writable_tenants": [1]},
            {"writable_tenants": [" "]},
            {"writable_tenants": ["CHWRR"], "global_admin": "false"},
            {"writable_tenants": ["CHWRR"], "global_admin": 0},
        ],
    )
    def test_original_identity_types_are_not_coerced(
        self, monkeypatch: pytest.MonkeyPatch, section: object
    ) -> None:
        from sapphire_flow.config import _overlay

        monkeypatch.setenv("SAPPHIRE_CONFIG", "unused")
        monkeypatch.setattr(
            _overlay, "load_merged_toml", lambda *args: {"deployment": section}
        )
        with pytest.raises(ConfigurationError, match="identity"):
            historical._load_identity_config()

    def test_aware_offsets_normalize_without_changing_instants(self) -> None:
        start = datetime.fromisoformat("2026-11-01T05:45:00+05:45")
        request = HistoricalCaptureRequest.parse(
            tenant_code="CHWRR",
            station_ids=[str(uuid4())],
            start=start,
            end=start + timedelta(days=1),
            now=NOW,
        )
        assert request.start == NOW - timedelta(days=2)

    def test_naive_bounds_are_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="Invalid historical"):
            HistoricalCaptureRequest.parse(
                tenant_code="CHWRR",
                station_ids=[str(uuid4())],
                start=datetime(2026, 11, 1),
                end=NOW,
                now=NOW,
            )


@pytest.fixture
def native_preflight(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    from dataclasses import replace
    from uuid import UUID

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
    from sapphire_flow.types.domain import QcRuleSet
    from sapphire_flow.types.ids import StationId
    from tests.unit.adapters.test_dhm import parse_dhm_config, settings, station

    configured_station = replace(
        station(), id=StationId(UUID(FIELDS["historical_station_ids"][0]))
    )
    qc = SimpleNamespace(
        fetch_capture_qc_window=lambda *args, **kwargs: None,
        update_qc_batch_if_current=lambda *args, **kwargs: True,
    )
    stores = {
        "station_store": SimpleNamespace(
            fetch_station=lambda identity: configured_station,
            fetch_all_stations=lambda **kwargs: [],
        ),
        "obs_store": SimpleNamespace(
            qc_context_reader=lambda: qc,
            fetch_latest_capture_timestamp=lambda *args: NOW,
        ),
        "tenant_store": SimpleNamespace(
            fetch_tenant_by_code=lambda code: SimpleNamespace(
                id=configured_station.tenant_id
            )
        ),
        "baseline_store": SimpleNamespace(fetch_baselines=lambda *args: []),
        "native_capture_store": SimpleNamespace(
            commit_batch=lambda *args: None,
            resolve=lambda *args: (),
            pending_qc=lambda *args, **kwargs: None,
        ),
    }
    monkeypatch.setattr(historical, "_bootstrap", lambda: (None, stores))
    monkeypatch.setattr(historical, "_historical_now", lambda: NOW)
    monkeypatch.setattr(
        historical,
        "_load_identity_config",
        lambda: DeploymentIdentityConfig(
            writable_tenants=frozenset({"CHWRR"}), global_admin=False
        ),
    )
    monkeypatch.setattr(
        historical,
        "_load_operational_config",
        lambda: DeploymentConfig(max_retention_days=3650),
    )
    monkeypatch.setattr(
        historical, "_load_rules", lambda: QcRuleSet(version="none", rules=())
    )
    monkeypatch.setattr(
        historical, "_load_dhm_config", lambda: parse_dhm_config(settings())
    )
    monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("HTTP must not run after invalid preflight")

    from sapphire_flow.flows import ingest_observations as flow

    monkeypatch.setattr(flow, "_dhm_http_client", forbidden)
    return stores


class TestHistoricalPreflight:
    @pytest.mark.parametrize(
        "store",
        [
            "station_store",
            "obs_store",
            "tenant_store",
            "baseline_store",
            "native_capture_store",
        ],
    )
    def test_missing_store_fails_before_http(
        self, native_preflight: dict[str, object], store: str
    ) -> None:
        native_preflight[store] = None
        with pytest.raises(ConfigurationError, match="preflight failed"):
            ingest_observations_flow.fn(**FIELDS)

    @pytest.mark.parametrize("method", ["commit_batch", "resolve", "pending_qc"])
    def test_each_capture_capability_is_required(
        self, native_preflight: dict[str, object], method: str
    ) -> None:
        delattr(native_preflight["native_capture_store"], method)
        with pytest.raises(ConfigurationError, match="preflight failed"):
            ingest_observations_flow.fn(**FIELDS)

    @pytest.mark.parametrize(
        "loader", ["_load_rules", "_load_dhm_config", "_load_operational_config"]
    )
    def test_loaded_native_types_required(
        self,
        native_preflight: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
        loader: str,
    ) -> None:
        monkeypatch.setattr(historical, loader, lambda: None)
        with pytest.raises(ConfigurationError, match="preflight failed"):
            ingest_observations_flow.fn(**FIELDS)

    @pytest.mark.parametrize("cursor", [None, NOW - timedelta(days=3)])
    def test_gap_cursor_is_required(
        self, native_preflight: dict[str, object], cursor: object
    ) -> None:
        native_preflight["obs_store"].fetch_latest_capture_timestamp = lambda *args: (
            cursor
        )
        with pytest.raises(ConfigurationError, match="preflight failed"):
            ingest_observations_flow.fn(**FIELDS)

    def test_global_admin_has_no_bypass(
        self, native_preflight: dict[str, object], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig

        monkeypatch.setattr(
            historical,
            "_load_identity_config",
            lambda: DeploymentIdentityConfig(
                writable_tenants=frozenset(), global_admin=True
            ),
        )
        with pytest.raises(ConfigurationError, match="preflight failed"):
            ingest_observations_flow.fn(**FIELDS)

    def test_qc_purpose_capability_is_required(
        self, native_preflight: dict[str, object]
    ) -> None:
        native_preflight["obs_store"].qc_context_reader = lambda: SimpleNamespace()
        with pytest.raises(ConfigurationError, match="preflight failed"):
            ingest_observations_flow.fn(**FIELDS)

    def test_unsupported_public_identity_parameter(self) -> None:
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            ingest_observations_flow.fn(
                **FIELDS, identity_config={"global_admin": True}
            )

    def test_requested_provider_alias_is_rejected(
        self, native_preflight: dict[str, object], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dataclasses import replace

        from sapphire_flow.config.dhm import DhmBinding

        original = historical._load_dhm_config()
        ambiguous = replace(
            original,
            bindings=(
                *original.bindings,
                DhmBinding(
                    network="dhm",
                    station_code="unrequested",
                    api_station_id=168,
                    level_reference="unknown",
                ),
            ),
        )
        monkeypatch.setattr(historical, "_load_dhm_config", lambda: ambiguous)
        with pytest.raises(ConfigurationError, match="preflight failed"):
            ingest_observations_flow.fn(**FIELDS)

    def test_foreign_station_is_rejected(
        self, native_preflight: dict[str, object]
    ) -> None:
        from dataclasses import replace

        from sapphire_flow.types.ids import TenantId

        store = native_preflight["station_store"]
        station = store.fetch_station(None)
        store.fetch_station = lambda identity: replace(
            station, tenant_id=TenantId(uuid4())
        )
        with pytest.raises(ConfigurationError, match="preflight failed"):
            ingest_observations_flow.fn(**FIELDS)

    def test_override_resolution_failure_is_normalized_before_http(
        self, native_preflight: dict[str, object], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.flows import ingest_observations as flow

        def broken(*args: object, **kwargs: object) -> None:
            raise ValueError("private configuration text")

        monkeypatch.setattr(flow, "resolve_configured_station_qc", broken)
        with pytest.raises(
            ConfigurationError, match="^Historical capture preflight failed$"
        ) as caught:
            ingest_observations_flow.fn(**FIELDS)
        assert caught.value.__suppress_context__


class TestHistoricalRequestContract:
    def test_every_public_object_dependency_is_covered(self) -> None:
        import inspect

        dependencies = {
            name
            for name, parameter in inspect.signature(
                ingest_observations_flow.fn
            ).parameters.items()
            if parameter.annotation in (object, "object")
        }
        assert dependencies == set(DEPENDENCIES)

    @pytest.mark.parametrize(
        "change", ["duplicate", "empty", "future", "oversize", "reversed"]
    )
    def test_invalid_request_is_refused(self, change: str) -> None:
        identity = str(uuid4())
        ids = [identity]
        start = NOW - timedelta(days=2)
        end = NOW - timedelta(days=1)
        if change == "duplicate":
            ids = [identity, identity.upper()]
        elif change == "empty":
            ids = []
        elif change == "future":
            end = NOW + timedelta(seconds=1)
        elif change == "oversize":
            start = NOW - timedelta(days=33)
        elif change == "reversed":
            start = end
        with pytest.raises(ConfigurationError, match="Invalid historical"):
            HistoricalCaptureRequest.parse(
                tenant_code="CHWRR", station_ids=ids, start=start, end=end, now=NOW
            )
