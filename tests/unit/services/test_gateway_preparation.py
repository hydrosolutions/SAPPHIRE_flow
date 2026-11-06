from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd
import pytest

from sapphire_flow.adapters.recap_registration import RecapRegistration
from sapphire_flow.basin_operator.handover import export_handover
from sapphire_flow.basin_operator.inventory import (
    Coordinate,
    StationInput,
    parse_inventory,
)
from sapphire_flow.basin_operator.review import (
    ReviewStatus,
    WarningAcknowledgment,
    decide,
    file_hash,
    open_batch,
)
from sapphire_flow.services.basin_package_loader import load_basin_package
from sapphire_flow.services.gateway_preparation import (
    admit_handover,
    apply_subscriptions,
    confirm_proposal,
    confirm_subscriptions,
    history_instructions,
    load_preparation_state,
    preparation_report,
    preparation_workspace,
    preview_subscriptions,
    proposal_revision,
    propose_data,
    reconcile_registration,
    register_handover,
    resume_coverage,
    retain_proposal,
    save_preparation_state,
    subscription_revision,
)
from sapphire_flow.types.basin_package import compute_package_fingerprint
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.gateway_preparation import (
    DataStream,
    DateEndConvention,
    ForecastScope,
    HistoryScope,
    PreparationState,
    ProductContract,
    RegistrationStatus,
    SourceRunContract,
    SourceRunSemantics,
    SubscriptionParameter,
)
from sapphire_flow.types.model import ModelDataRequirements
from tests.unit.adapters.test_recap_registration import config
from tests.unit.basin_operator.conftest import INVENTORY
from tests.unit.basin_operator.conftest import (
    synthetic_package as synthetic_package_fixture,
)

synthetic_package = synthetic_package_fixture


@pytest.fixture
def approved(tmp_path: Path, synthetic_package: Path) -> Path:
    batch = open_batch(synthetic_package, parse_inventory(INVENTORY, ".csv"))
    decisions = decide(
        batch,
        codes=("001.0",),
        status=ReviewStatus.APPROVED,
        reviewer="Synthetic reviewer",
        reason="",
        now="2026-11-06T00:00:00Z",
        acknowledgment=WarningAcknowledgment.NOT_ACKNOWLEDGED,
    )
    return export_handover(
        batch,
        decisions,
        tmp_path / "approved",
        package_id="approved-synthetic",
        now="2026-11-06T00:00:00Z",
        map_html="",
    )


def test_pending_package_is_not_handover(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="handover"):
        admit_handover(tmp_path)


def test_fixed_existing_fingerprints() -> None:
    path = Path("tests/fixtures/basin_static/nepal-dhm-basins")
    loaded = load_basin_package(path)
    stations = tuple(
        StationInput(
            code=b.station_code,
            display_name=b.display_name,
            original=Coordinate(latitude=b.latitude, longitude=b.longitude),
            source="fixed fingerprint regression",
        )
        for b in loaded.basins
    )
    batch = open_batch(path, stations)
    assert (
        compute_package_fingerprint(loaded)
        == "sha256:6cad3ceba6cca1bdb2f750737f652d72bf6644c08d0238bb91f2e521c166887e"
    )
    assert (
        batch.content_fingerprint
        == "sha256:7cb1966c2b9dde9d2ba70f1ef53457422fd465daaf4fd35b3587019f1bd2775c"
    )
    assert (
        batch.fingerprint
        == "sha256:3df6893cee72d8fbce2fd9792e6119c58fa8c73c5ecf625d6c065ce5409ffd34"
    )


def test_attribute_only_admission(
    approved: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("geometry review is forbidden")

    monkeypatch.setattr("geopandas.read_file", forbidden)
    monkeypatch.setattr("sapphire_flow.basin_operator.review.open_batch", forbidden)
    before = {str(p): file_hash(p) for p in approved.rglob("*") if p.is_file()}
    result = admit_handover(approved)
    assert result.features[0].station.code == "001.0"
    assert result.source_fingerprint != result.derived_fingerprint
    assert before == {str(p): file_hash(p) for p in approved.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "mutation",
    ["../outside", "/absolute", "package/../approval.json", "duplicate", "omitted"],
)
def test_checksum_paths(approved: Path, mutation: str) -> None:
    sidecar = approved / "checksums.sha256"
    original = sidecar.read_text()
    if mutation == "duplicate":
        sidecar.write_text(original + original.splitlines()[0] + "\n")
    elif mutation == "omitted":
        sidecar.write_text("\n".join(original.splitlines()[1:]) + "\n")
    else:
        sidecar.write_text("0" * 64 + "  " + mutation + "\n")
    with pytest.raises(ValueError, match="checksum"):
        admit_handover(approved)


def test_changed_source_rejected(approved: Path) -> None:
    (approved / "package/basins.gpkg").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        admit_handover(approved)


def test_state_roundtrip_and_exclusive_lock(approved: Path, tmp_path: Path) -> None:
    state = PreparationState(handover=admit_handover(approved))
    directory = tmp_path / "evidence"
    with preparation_workspace(directory, handover=approved):
        save_preparation_state(directory, state)
        assert load_preparation_state(directory) == state
        assert (directory / "state.json").stat().st_mode & 0o077 == 0
        with (
            pytest.raises(ValueError, match="another process"),
            preparation_workspace(directory, handover=approved),
        ):
            pass
    raw = json.loads((directory / "state.json").read_text())
    raw["schema_version"] = 999
    (directory / "state.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="unsupported"):
        load_preparation_state(directory)


def requirements() -> ModelDataRequirements:
    return ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        required_past_targets=frozenset({"discharge"}),
        past_dynamic_features=frozenset({"precipitation", "temperature"}),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        static_features=frozenset({"area"}),
        supported_time_steps=frozenset({timedelta(days=1)}),
        lookback_steps=30,
        forecast_horizon_steps=10,
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
    )


def proposal():
    cycle = datetime(2024, 2, 11, tzinfo=UTC)
    return propose_data(
        model_id="synthetic_cmal_contract",
        requirements=requirements(),
        time_step=timedelta(days=1),
        history=HistoryScope(
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 2, 10, tzinfo=UTC),
            purpose="synthetic evaluation",
        ),
        forecast=ForecastScope(
            cycles=(cycle,),
            first_lead=timedelta(0),
            end_lead=timedelta(days=10),
            members=(0,),
            level_type="sfc",
            level=None,
            control_grid="0p1",
            product_grid_reference="synthetic contract only",
            source_runs=(
                (
                    cycle.isoformat(),
                    SourceRunContract(
                        semantics=SourceRunSemantics.EXACT, value="synthetic_run"
                    ),
                ),
            ),
        ),
        contracts=tuple(
            ProductContract(
                stream=stream,
                subscription_source=source,
                cadence=timedelta(days=1),
                provenance=provenance,
                reference="synthetic fixture, not Vatro",
                date_end=DateEndConvention.INCLUSIVE,
                publication_lag=timedelta(days=5),
                historical_source_run=SourceRunContract(
                    semantics=SourceRunSemantics.EXACT, value="synthetic_run"
                ),
                subscription_parameters=tuple(
                    SubscriptionParameter(
                        canonical=canonical,
                        parameter_name=parameter,
                        forecast_type=mode,
                    )
                    for canonical, parameter, mode in (
                        (
                            ("temperature", "2m_temperature", None),
                            ("precipitation", "total_precipitation", None),
                        )
                        if stream == DataStream.ERA5_HISTORY
                        else (
                            ("temperature", "2t:fc:sfc", "fc"),
                            ("precipitation", "tp:fc:sfc", "fc"),
                        )
                    )
                ),
            )
            for stream, source, provenance in (
                (DataStream.ERA5_HISTORY, "synthetic_era5", "synthetic_history"),
                (DataStream.IFS_FORECAST, "synthetic_ifs", "synthetic_forecast"),
            )
        ),
    )


class SyntheticGateway:
    def __init__(self) -> None:
        self.rows = []
        self.requests = []
        self.lose_response = False

    def transport(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/upload"):
            return httpx.Response(
                200, json={"message": "uploaded", "detail": "synthetic"}
            )
        if "/parameters/" in path:
            if path.endswith("era5"):
                rows = [
                    {"name": name} for name in ("total_precipitation", "2m_temperature")
                ]
            else:
                rows = [
                    {
                        "name": name,
                        "type": mode,
                        "grid_resolution": grid,
                        "level_type": "sfc",
                        "level": None,
                    }
                    for name in ("tp", "2t")
                    for mode, grid in (("fc", "0p1"),)
                ]
            return httpx.Response(200, json=rows)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    r
                    for r in self.rows
                    if r["source"] == request.url.params["source"]
                    and r["hru_code"] == request.url.params["hru_code"]
                ],
            )
        if request.method == "POST":
            row = json.loads(request.content)
            self.rows.append(row)
            if self.lose_response:
                self.lose_response = False
                raise httpx.ReadTimeout("synthetic lost reply", request=request)
            return httpx.Response(200, json=row)
        raise AssertionError("unexpected mutation")


class SyntheticRetrieval:
    def __init__(self, polygons: list[str]) -> None:
        self.ecmwf = self
        self.snow = self
        self.polygons = polygons
        self.calls = []
        self.missing = True

    def era5_land_reanalysis(self, **kwargs: object) -> pd.DataFrame:
        self.calls.append(kwargs)
        index = pd.date_range(kwargs["start_date"], kwargs["end_date"], tz="UTC")
        result = pd.DataFrame(
            {p: [0.0] * len(index) for p in self.polygons}, index=index
        )
        result["source"] = "synthetic_history"
        result["source_run"] = "synthetic_run"
        if self.missing and len(self.polygons) > 1:
            result.loc[index[1], self.polygons[1]] = float("nan")
        return result

    def ifs_forecast(self, **kwargs: object) -> pd.DataFrame:
        self.calls.append(kwargs)
        index = pd.date_range(
            kwargs["run_date"], periods=kwargs["horizon_days"], tz="UTC"
        )
        result = pd.DataFrame(
            {p: [0.0] * len(index) for p in self.polygons}, index=index
        )
        result["source"] = "synthetic_forecast"
        result["source_run"] = "synthetic_run"
        return result


def test_model_proposal_and_unresolved_contracts() -> None:
    planned = proposal()
    assert planned.holds == ()
    assert {v.stream for v in planned.variables} == {
        DataStream.ERA5_HISTORY,
        DataStream.IFS_FORECAST,
    }
    assert "past_target:discharge" in planned.non_gateway_prerequisites
    assert planned.history.job_years == (2024, 2024)
    unknown = propose_data(
        model_id="unknown", requirements=None, time_step=timedelta(days=1), history=None
    )
    assert "installed_model_declaration_unavailable" in unknown.holds
    assert "history_period_and_purpose_required" in unknown.holds


@pytest.mark.parametrize(
    "past,future,streams",
    [
        (True, False, {DataStream.SNOW_HISTORY}),
        (False, True, {DataStream.SNOW_FORECAST}),
        (True, True, {DataStream.SNOW_HISTORY, DataStream.SNOW_FORECAST}),
    ],
)
def test_snow_streams_remain_distinct(
    past: bool, future: bool, streams: set[DataStream]
) -> None:
    req = replace(
        requirements(),
        past_dynamic_features=frozenset({"swe"}) if past else frozenset(),
        future_dynamic_features=frozenset({"swe"}) if future else frozenset(),
    )
    planned = propose_data(
        model_id="synthetic",
        requirements=req,
        time_step=timedelta(days=1),
        history=None,
    )
    assert {v.stream for v in planned.variables} == streams
    assert planned.holds


def test_full_journey_partial_batch_resume_no_duplicate_writes(
    tmp_path: Path,
    synthetic_package: Path,
) -> None:
    batch = open_batch(synthetic_package, parse_inventory(INVENTORY, ".csv"))
    decisions = decide(
        batch,
        codes=("001.0", "002"),
        status=ReviewStatus.APPROVED,
        reviewer="Synthetic",
        reason="",
        acknowledgment=WarningAcknowledgment.ACKNOWLEDGED,
        now="2026-11-06T00:00:00Z",
    )
    handover = export_handover(
        batch,
        decisions,
        tmp_path / "batch",
        package_id="synthetic-batch",
        now="2026-11-06T00:00:00Z",
        map_html="",
    )
    gateway = SyntheticGateway()
    adapter = RecapRegistration(
        config(), api_key="synthetic", transport=httpx.MockTransport(gateway.transport)
    )
    workspace = tmp_path / "evidence"
    now = "2026-11-06T00:00:00Z"
    with preparation_workspace(workspace, handover=handover):
        state = PreparationState(
            handover=admit_handover(handover), gateway_review_reference="synthetic-only"
        )
        state = retain_proposal(workspace, state, proposal())
        state = confirm_proposal(workspace, state, proposal_revision(proposal()))
        state = register_handover(
            workspace,
            state,
            adapter,
            requested_hru="new_synthetic",
            attempt_id="a",
            now=now,
        )
        with pytest.raises(ValueError, match="reconciliation"):
            register_handover(
                workspace,
                load_preparation_state(workspace),
                adapter,
                requested_hru="new_synthetic",
                attempt_id="b",
                now=now,
            )
        state = reconcile_registration(
            workspace,
            state,
            status=RegistrationStatus.CONFIRMED,
            actor="Synthetic",
            evidence_reference="synthetic upload-to-feature receipt",
            now=now,
        )
        state = register_handover(
            workspace,
            state,
            adapter,
            requested_hru="new_synthetic",
            attempt_id="c",
            now=now,
        )
        state = preview_subscriptions(workspace, state, adapter)
        state = confirm_subscriptions(
            workspace, state, subscription_revision(state.subscription_plans[-1])
        )
        gateway.lose_response = True
        state = apply_subscriptions(workspace, state, adapter, now=now)
        write_count = sum(r.method != "GET" for r in gateway.requests)
        state = apply_subscriptions(workspace, state, adapter, now=now)
        assert sum(r.method != "GET" for r in gateway.requests) == write_count == 5
        assert "era5_land_backfill__targeted" in history_instructions(state)[0]
        assert all(
            s["outcome"] == "held" for s in preparation_report(state)["stations"]
        )
        client = SyntheticRetrieval([f.polygon_name for f in state.handover.features])
        state = resume_coverage(workspace, state, client, now=now)
        outcomes = preparation_report(state)["stations"]
        assert [s["outcome"] for s in outcomes] == ["eligible_for_preparation", "held"]
        assert len(client.calls) == 6
        client.missing = False
        state = resume_coverage(
            workspace, load_preparation_state(workspace), client, now=now
        )
        assert all(
            s["outcome"] == "eligible_for_preparation"
            for s in preparation_report(state)["stations"]
        )
        assert sum(r.method != "GET" for r in gateway.requests) == 5
    adapter.close()


@pytest.mark.parametrize("mutation", ["omit", "unknown", "corrupt", "boolean_version"])
def test_state_never_resets_silently(
    approved: Path, tmp_path: Path, mutation: str
) -> None:
    workspace = tmp_path / "evidence"
    with preparation_workspace(workspace, handover=approved):
        save_preparation_state(
            workspace, PreparationState(handover=admit_handover(approved))
        )
    path = workspace / "state.json"
    raw = json.loads(path.read_bytes())
    if mutation == "omit":
        del raw["attempts"]
    elif mutation == "unknown":
        raw["future_schema_field"] = 3
    elif mutation == "boolean_version":
        raw["schema_version"] = True
    path.write_text("{broken" if mutation == "corrupt" else json.dumps(raw))
    with pytest.raises(ValueError, match="invalid or unsupported"):
        load_preparation_state(workspace)


def test_atomic_save_preserves_previous_state(
    approved: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "evidence"
    with preparation_workspace(workspace, handover=approved):
        state = PreparationState(handover=admit_handover(approved))
        save_preparation_state(workspace, state)

        def fail(*args: object) -> None:
            raise OSError("synthetic crash before replace")

        monkeypatch.setattr(
            "sapphire_flow.services.gateway_preparation.os.replace", fail
        )
        with pytest.raises(OSError, match="synthetic crash"):
            save_preparation_state(
                workspace, replace(state, gateway_review_reference="new")
            )
        assert load_preparation_state(workspace) == state
        assert not list(workspace.glob(".state-*"))


@pytest.mark.parametrize(
    "field,value", [("status", "pending"), ("fingerprint", "foreign"), ("reviewer", "")]
)
def test_foreign_or_pending_decisions_fail_after_checksum_validation(
    approved: Path, field: str, value: str
) -> None:
    approval_path = approved / "approval.json"
    document = json.loads(approval_path.read_text())
    document["decisions"][0][field] = value
    approval_path.write_text(json.dumps(document))
    sidecar = approved / "checksums.sha256"
    sidecar.write_text(
        "".join(
            f"{file_hash(p).removeprefix('sha256:')}  {p.relative_to(approved)}\n"
            for p in sorted(approved.rglob("*"))
            if p.is_file() and p != sidecar
        )
    )
    with pytest.raises(ValueError, match="applicable approvals"):
        admit_handover(approved)


def test_interrupt_after_intent_never_resends_upload(
    approved: Path, tmp_path: Path
) -> None:
    gateway = SyntheticGateway()

    def interrupted(request: httpx.Request) -> httpx.Response:
        gateway.requests.append(request)
        raise KeyboardInterrupt("synthetic interrupted upload")

    adapter = RecapRegistration(
        config(), api_key="synthetic", transport=httpx.MockTransport(interrupted)
    )
    workspace = tmp_path / "evidence"
    with preparation_workspace(workspace, handover=approved):
        state = PreparationState(
            handover=admit_handover(approved), gateway_review_reference="synthetic"
        )
        state = retain_proposal(workspace, state, proposal())
        state = confirm_proposal(workspace, state, proposal_revision(proposal()))
        with pytest.raises(KeyboardInterrupt, match="synthetic"):
            register_handover(
                workspace,
                state,
                adapter,
                requested_hru="new_synthetic",
                attempt_id="a",
                now="2026-11-06",
            )
        with pytest.raises(ValueError, match="reconciliation"):
            register_handover(
                workspace,
                load_preparation_state(workspace),
                adapter,
                requested_hru="new_synthetic",
                attempt_id="b",
                now="2026-11-06",
            )
        assert len(gateway.requests) == 1
    adapter.close()


def test_scope_edit_invalidates_confirmations(approved: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "evidence"
    with preparation_workspace(workspace, handover=approved):
        state = PreparationState(handover=admit_handover(approved))
        state = retain_proposal(workspace, state, proposal())
        state = confirm_proposal(workspace, state, proposal_revision(proposal()))
        revised = replace(proposal(), model_id="changed-model")
        state = retain_proposal(workspace, state, revised)
        assert state.confirmed_proposal is None
        assert len(state.proposals) == 2
        with pytest.raises(ValueError, match="changed"):
            confirm_proposal(workspace, state, proposal_revision(proposal()))


def test_research_text_code_00001_survives_admission(
    synthetic_package: Path, tmp_path: Path
) -> None:
    import geopandas as gpd

    path = synthetic_package / "basins.gpkg"
    frame = gpd.read_file(path)
    frame["network"] = "research"
    frame.loc[0, "station_code"] = "00001"
    frame.loc[0, "name"] = "g_00001"
    path.unlink()
    frame.to_file(path, layer="polygons", driver="GPKG")
    manifest_path = synthetic_package / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["network"] = "research"
    manifest_path.write_text(json.dumps(manifest))
    report_path = synthetic_package / "validation_report.json"
    report = json.loads(report_path.read_text())
    for row in report["basins"]:
        row["network"] = "research"
        if row["station_code"] == "001.0":
            row["station_code"] = "00001"
            row["name"] = "g_00001"
    report_path.write_text(json.dumps(report))
    batch = open_batch(
        synthetic_package,
        parse_inventory(INVENTORY.replace(b"001.0", b"00001"), ".csv"),
    )
    decisions = decide(
        batch,
        codes=("00001",),
        status=ReviewStatus.APPROVED,
        reviewer="Synthetic",
        reason="",
        acknowledgment=WarningAcknowledgment.NOT_ACKNOWLEDGED,
        now="2026-11-06T00:00:00Z",
    )
    handover = export_handover(
        batch,
        decisions,
        tmp_path / "research-approved",
        package_id="research-synthetic",
        now="2026-11-06T00:00:00Z",
        map_html="",
    )
    feature = admit_handover(handover).features[0]
    assert (feature.network, feature.station.code, feature.polygon_name) == (
        "research",
        "00001",
        "g_00001",
    )


def test_invalid_hru_never_persists_external_intent(
    approved: Path, tmp_path: Path
) -> None:
    gateway = SyntheticGateway()
    adapter = RecapRegistration(
        config(), api_key="synthetic", transport=httpx.MockTransport(gateway.transport)
    )
    workspace = tmp_path / "evidence"
    with preparation_workspace(workspace, handover=approved):
        state = PreparationState(
            handover=admit_handover(approved), gateway_review_reference="synthetic"
        )
        state = retain_proposal(workspace, state, proposal())
        state = confirm_proposal(workspace, state, proposal_revision(proposal()))
        with pytest.raises(ValueError, match="identifier"):
            register_handover(
                workspace,
                state,
                adapter,
                requested_hru="Bad/name",
                attempt_id="a",
                now="synthetic",
            )
        state = load_preparation_state(workspace)
        assert state.attempts == ()
        assert gateway.requests == []
        state = register_handover(
            workspace,
            state,
            adapter,
            requested_hru="corrected_name",
            attempt_id="b",
            now="synthetic",
        )
        assert len(gateway.requests) == 1
        assert state.attempts[-1].status == RegistrationStatus.RECONCILIATION_REQUIRED
    adapter.close()


def test_previous_state_version_is_preserved_and_never_reinterpreted(
    approved: Path, tmp_path: Path
) -> None:
    workspace = tmp_path / "evidence"
    with preparation_workspace(workspace, handover=approved):
        save_preparation_state(
            workspace, PreparationState(handover=admit_handover(approved))
        )
    path = workspace / "state.json"
    raw = json.loads(path.read_text())
    raw["schema_version"] = 1
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="unsupported preparation state; do not reset"):
        load_preparation_state(workspace)
    assert path.read_bytes() == before


def test_unknown_historical_run_blocks_proposal_confirmation() -> None:
    known = proposal()
    unresolved = tuple(
        replace(contract, historical_source_run=SourceRunContract())
        for contract in known.contracts
    )
    planned = propose_data(
        model_id=known.model_id,
        requirements=requirements(),
        time_step=known.time_step,
        history=known.history,
        forecast=known.forecast,
        contracts=unresolved,
    )
    assert "historical_source_run_unconfirmed:era5_history" in planned.holds
