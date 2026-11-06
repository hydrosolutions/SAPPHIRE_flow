from __future__ import annotations

import io
import json
import logging
import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, quote, quote_plus, urlsplit

import httpx
import pandas as pd
import pytest
import requests
from pydantic import TypeAdapter

from sapphire_flow.basin_operator.handover import export_handover
from sapphire_flow.basin_operator.inventory import parse_inventory
from sapphire_flow.basin_operator.review import (
    ReviewStatus,
    WarningAcknowledgment,
    decide,
    open_batch,
)
from sapphire_flow.cli import prepare_gateway
from sapphire_flow.services.gateway_preparation import proposal_revision
from sapphire_flow.types.gateway_preparation import (
    DataProposal,
    DataStream,
    ForecastLeadBand,
    SourceRunContract,
    SourceRunSemantics,
)
from tests.unit.basin_operator.conftest import INVENTORY
from tests.unit.basin_operator.conftest import (
    synthetic_package as synthetic_package_fixture,
)
from tests.unit.services.test_gateway_preparation import (
    SyntheticGateway,
    SyntheticRetrieval,
    proposal,
    requirements,
)
from tests.unit.services.test_gateway_preparation import approved as approved_fixture

approved = approved_fixture
synthetic_package = synthetic_package_fixture


def arguments(workspace: Path, approved: Path) -> list[str]:
    return ["--workspace", str(workspace), "--handover", str(approved)]


def test_help_and_scope_prepare_without_remote_construction(
    approved: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec = proposal()
    payload = TypeAdapter(DataProposal).dump_python(spec, mode="json")
    source = {
        key: payload[key] for key in ("model_id", "history", "forecast", "contracts")
    }
    source["time_step_seconds"] = 86400
    path = tmp_path / "scope.json"
    path.write_text(json.dumps(source))
    monkeypatch.setattr(
        prepare_gateway,
        "discover_models",
        lambda: {
            "synthetic_cmal_contract": SimpleNamespace(data_requirements=requirements())
        },
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("no Gateway or database construction allowed")

    monkeypatch.setattr(prepare_gateway, "load_recap_api_key", forbidden)
    workspace = tmp_path / "evidence"
    argv = arguments(workspace, approved)
    assert prepare_gateway.run([*argv, "prepare", "--scope", str(path)]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["proposal_revision"] == proposal_revision(spec)
    assert output["stations"][0]["outcome"] == "held"
    assert (
        prepare_gateway.run(
            [
                *argv,
                "prepare",
                "--scope",
                str(path),
                "--confirm",
                proposal_revision(spec),
            ]
        )
        == 2
    )
    capsys.readouterr()
    assert prepare_gateway.run([*argv, "status"]) == 2
    assert json.loads(capsys.readouterr().out)["stations"][0]["station_code"] == "001.0"


def test_bad_scope_never_prints_validation_input(
    approved: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "bad.json"
    source.write_text('{"time_step_seconds": "SENTINEL-SECRET"}')
    assert (
        prepare_gateway.run(
            [
                *arguments(tmp_path / "evidence", approved),
                "prepare",
                "--scope",
                str(source),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "SENTINEL-SECRET" not in captured.out + captured.err


@pytest.fixture
def gateway_cli(
    tmp_path: Path, synthetic_package: Path, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
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
        tmp_path / "approved-batch",
        package_id="synthetic-cli",
        now="2026-11-06T00:00:00Z",
        map_html="",
    )
    spec = proposal()
    assert spec.forecast is not None
    cycle = spec.forecast.cycles[0]
    spec = replace(
        spec,
        forecast=replace(
            spec.forecast,
            source_runs=(
                (
                    cycle.isoformat(),
                    SourceRunContract(
                        semantics=SourceRunSemantics.UTC_INSTANT, timestamp=cycle
                    ),
                ),
            ),
        ),
        contracts=tuple(
            replace(
                contract,
                historical_source_run=SourceRunContract(
                    semantics=SourceRunSemantics.UTC_DATE_MIDNIGHT
                ),
            )
            if contract.stream == DataStream.ERA5_HISTORY
            else replace(
                contract,
                forecast_lead_bands=(
                    ForecastLeadBand(
                        start=timedelta(0),
                        end=timedelta(hours=144),
                        cadence=timedelta(hours=3),
                    ),
                    ForecastLeadBand(
                        start=timedelta(hours=144),
                        end=timedelta(hours=240),
                        cadence=timedelta(hours=6),
                    ),
                ),
            )
            for contract in spec.contracts
        ),
    )
    payload = TypeAdapter(DataProposal).dump_python(spec, mode="json")
    source = {
        key: payload[key] for key in ("model_id", "history", "forecast", "contracts")
    }
    source["time_step_seconds"] = 86400
    scope_file = tmp_path / "scope.json"
    scope_file.write_text(json.dumps(source))
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[adapters.recap_gateway]\nbase_url="https://example.invalid/sdk"\ntimeout_s=5\nverify_tls=true\nstaleness_threshold_hours=12\nhru_metadata_source="synthetic"\nmax_retries=0\n'
    )
    secret = "aaaa/aaaa+aaaa aaaa%aaaa?"
    key_file = tmp_path / "dummy-secret"
    key_file.write_text(secret)
    forms = {secret, quote(secret), quote(secret, safe=""), quote_plus(secret)}
    forms |= {
        form.replace("%2F", "%2f").replace("%2B", "%2b").replace("%3F", "%3f")
        for form in forms
    }
    case = SimpleNamespace(
        argv=arguments(tmp_path / "evidence", handover),
        workspace=tmp_path / "evidence",
        scope=scope_file,
        config=config_file,
        key_file=key_file,
        forms=forms,
        secret=secret,
        gateway=SyntheticGateway(),
        retrieval=SyntheticRetrieval(["g_001_0", "g_002"]),
        upload_error=None,
        subscription_fault=None,
        readback_fault=None,
        subscription_fault_seen=False,
        commit_failed_write=False,
        wrong_run=False,
        forecast_fault=None,
        download_error=None,
        retrieval_response=None,
        native_requests=[],
    )
    monkeypatch.setattr(
        prepare_gateway,
        "discover_models",
        lambda: {
            "synthetic_cmal_contract": SimpleNamespace(data_requirements=requirements())
        },
    )

    def httpx_send(self: object, request: httpx.Request) -> httpx.Response:
        request.read()
        logging.getLogger("httpx").critical("synthetic unsafe %s", request.url)
        if request.url.path.endswith("/upload"):
            case.gateway.requests.append(request)
            if case.upload_error is not None:
                raise case.upload_error("synthetic failure " + str(request.url))
            echo = " credential ".join(sorted(forms))
            return httpx.Response(
                200, json={"message": echo, "detail": echo, "warnings": echo}
            )
        if "/catalogs/subscriptions" in request.url.path:
            if (
                request.method == "GET"
                and case.subscription_fault_seen
                and case.readback_fault
            ):
                if case.readback_fault == "interrupt":
                    raise KeyboardInterrupt()
                raise httpx.ReadTimeout(
                    "synthetic " + secret + str(request.url), request=request
                )
            if (
                request.method in ("POST", "PUT")
                and case.subscription_fault is not None
            ):
                case.gateway.requests.append(request)
                status = case.subscription_fault
                case.subscription_fault = None
                case.subscription_fault_seen = True
                if case.commit_failed_write:
                    case.gateway.rows.append(json.loads(request.content))
                if status == 0:
                    raise httpx.ReadTimeout(
                        "synthetic " + secret + str(request.url), request=request
                    )
                return httpx.Response(
                    status, json={"detail": secret + str(request.url)}
                )
        if request.method == "PUT":
            case.gateway.requests.append(request)
            source, hru, parameter = request.url.path.split("/")[-3:]
            row = next(
                r
                for r in case.gateway.rows
                if (r["source"], r["hru_code"], r["parameter_name"])
                == (source, hru, parameter)
            )
            row.update(json.loads(request.content))
            return httpx.Response(200, json=row)
        return case.gateway.transport(request)

    def requests_send(
        self: object, request: requests.PreparedRequest, **kwargs: object
    ) -> requests.Response:
        assert request.url is not None
        if case.download_error is not None:
            raise ValueError("synthetic failure " + request.url)
        if case.retrieval_response is not None:
            status, code = case.retrieval_response
            response = requests.Response()
            response.status_code = status
            response.url = request.url
            response.request = request
            response.raw = io.BytesIO(
                json.dumps({"code": code, "message": secret + request.url}).encode()
            )
            return response
        query = {
            key: values[0]
            for key, values in parse_qs(urlsplit(request.url).query).items()
        }
        assert query["api_key"] == secret
        assert query["hru_code"] == "new_synthetic"
        case.native_requests.append(request.url)
        logging.getLogger("urllib3.connectionpool").critical(
            "synthetic unsafe %s", request.url
        )
        assert Path.cwd().parent == case.workspace
        assert Path.cwd().stat().st_mode & 0o077 == 0
        if "/era5-land/" in request.url:
            data = case.retrieval.era5_land_reanalysis(**query)
            data["source_run"] = data.index.normalize()
            if case.wrong_run:
                data["source_run"] = data["source_run"] - pd.Timedelta(days=1)
        else:
            assert query["ifs_type"] == "fc" and "member" not in query
            query["horizon_days"] = int(query["horizon_days"])
            base = pd.Timestamp(query["run_date"], tz="UTC") + pd.Timedelta(
                hours=int(query["run_hour"])
            )
            times = [
                base + pd.Timedelta(hours=lead)
                for lead in (*range(0, 144, 3), *range(144, 240, 6))
            ]
            data = pd.DataFrame(
                {
                    "g_001_0": 1.0,
                    "g_002": 1.0,
                    "source": "synthetic_forecast",
                    "source_run": base,
                },
                index=pd.DatetimeIndex(times),
            )
            missing_leads = {"missing_3h": 3, "missing_6h": 150, "join": 144}
            if case.forecast_fault in missing_leads:
                data = data.drop(
                    base + pd.Timedelta(hours=missing_leads[case.forecast_fault])
                )
            elif case.forecast_fault == "wrong_cycle":
                data["source_run"] = base + pd.Timedelta(hours=6)
            elif case.forecast_fault in ("outside", "end_boundary", "duplicate_join"):
                lead = {"outside": 145, "end_boundary": 240, "duplicate_join": 144}[
                    case.forecast_fault
                ]
                extra = data.iloc[[0]].copy()
                extra.index = pd.DatetimeIndex([base + pd.Timedelta(hours=lead)])
                data = pd.concat([data, extra])

        content = io.BytesIO()
        data.to_parquet(content)
        response = requests.Response()
        response.status_code = 200
        response.request = request
        response.url = request.url
        response.raw = io.BytesIO(content.getvalue())
        return response

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", httpx_send)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", requests_send)
    return case


def invoke_cli(
    case: SimpleNamespace,
    command: list[str],
    capsys: pytest.CaptureFixture[str],
    expected: int = 2,
) -> dict[str, Any]:
    assert prepare_gateway.run([*case.argv, *command]) == expected
    captured = capsys.readouterr()
    state_text = (case.workspace / "state.json").read_text()
    for form in case.forms:
        assert form not in captured.out + captured.err + state_text
    return json.loads(captured.out)


def connection_args(case: SimpleNamespace) -> list[str]:
    return ["--config", str(case.config), "--secret-file", str(case.key_file)]


def prepare_cli(case: SimpleNamespace, capsys: pytest.CaptureFixture[str]) -> None:
    result = invoke_cli(case, ["prepare", "--scope", str(case.scope)], capsys)
    invoke_cli(
        case,
        [
            "prepare",
            "--scope",
            str(case.scope),
            "--confirm",
            result["proposal_revision"],
            "--vatro-review",
            "synthetic-reference-not-live-approval",
        ],
        capsys,
    )


def test_all_cli_dispatch_with_real_facade_and_fake_transports(
    gateway_cli: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    case = gateway_cli
    caplog.set_level(logging.DEBUG)
    prepare_cli(case, capsys)
    connection = connection_args(case)
    invoke_cli(case, ["register", "--hru", "new_synthetic", *connection], capsys)
    invoke_cli(case, ["register", "--hru", "new_synthetic", *connection], capsys)
    assert len(case.gateway.requests) == 1  # uncertain upload never repeats
    invoke_cli(
        case,
        [
            "reconcile",
            "--outcome",
            "confirmed",
            "--actor",
            "Synthetic operator",
            "--evidence",
            "synthetic upload/feature receipt",
        ],
        capsys,
    )
    result = invoke_cli(case, ["subscribe", *connection], capsys)
    # Wrong confirmation stays local; only the exact preview revision permits writes.
    invoke_cli(case, ["subscribe", "--confirm", "stale", *connection], capsys)
    assert sum(r.method != "GET" for r in case.gateway.requests) == 1
    case.gateway.lose_response = True
    invoke_cli(
        case,
        ["subscribe", "--confirm", result["subscription_revision"], *connection],
        capsys,
    )
    invoke_cli(
        case,
        ["subscribe", "--confirm", result["subscription_revision"], *connection],
        capsys,
    )
    assert sum(r.method != "GET" for r in case.gateway.requests) == 5
    instructions = invoke_cli(case, ["history"], capsys)
    assert "era5_land_backfill__targeted" in instructions["manual_history"][0]
    invoke_cli(
        case,
        [
            "history",
            "--stream",
            "era5_history",
            "--actor",
            "Synthetic operator",
            "--job-reference",
            "synthetic-job",
        ],
        capsys,
    )
    case.wrong_run = True
    wrong = invoke_cli(case, ["resume", *connection], capsys)
    assert all(s["outcome"] == "held" for s in wrong["stations"])
    case.wrong_run = False
    partial = invoke_cli(case, ["resume", *connection], capsys)
    assert [s["outcome"] for s in partial["stations"]] == [
        "eligible_for_preparation",
        "held",
    ]
    partial_chunks = partial["stations"][1]["coverage"]
    missing = next(chunk for chunk in partial_chunks if chunk["missing_count"])
    assert (
        missing["missing_intervals"]
        and missing["usable_count"] < missing["requested_count"]
    )
    assert missing["observed_count"] == missing["requested_count"]
    assert "evidence_provenance_mismatch" not in missing["holds"]
    case.retrieval.missing = False
    invoke_cli(case, ["resume", *connection], capsys, expected=0)
    invoke_cli(case, ["status"], capsys, expected=0)
    invoke_cli(
        case, ["register", "--hru", "new_synthetic", *connection], capsys, expected=0
    )
    assert sum(r.method != "GET" for r in case.gateway.requests) == 5
    raw = json.loads((case.workspace / "state.json").read_text())
    assert raw["history_jobs"] == [
        ["era5_history", "Synthetic operator", "synthetic-job"]
    ]
    complete_history = [
        e for e in raw["coverage"] if e["stream"] == "era5_history" and not e["holds"]
    ]
    assert complete_history and all(
        e["validated_source"] == "synthetic_history"
        and e["validated_source_run"]
        == {"semantics": "utc_date_midnight", "value": None, "timestamp": None}
        for e in complete_history
    )
    complete_forecast = [
        e for e in raw["coverage"] if e["stream"] == "ifs_forecast" and not e["holds"]
    ]
    assert complete_forecast and all(
        e["expected_count"] == e["usable_count"] == 64 for e in complete_forecast
    )
    assert all(
        e["validated_run_identities"] == ["2024-02-11T00:00:00+00:00"]
        for e in complete_forecast
    )
    assert len(complete_history[-1]["validated_run_identities"]) == 9
    assert len(case.native_requests) == 18
    assert not list(case.workspace.glob(".recap-download-*"))
    for form in case.forms:
        assert form not in caplog.text


@pytest.mark.parametrize("exception_type", [httpx.InvalidURL, httpx.StreamError])
def test_cli_transport_errors_retain_safe_intent(
    gateway_cli: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    exception_type: type[Exception],
) -> None:
    case = gateway_cli
    prepare_cli(case, capsys)
    case.upload_error = exception_type
    result = invoke_cli(
        case, ["register", "--hru", "new_synthetic", *connection_args(case)], capsys
    )
    assert result["registration_status"] == "reconciliation_required"
    invoke_cli(
        case, ["register", "--hru", "new_synthetic", *connection_args(case)], capsys
    )
    assert len(case.gateway.requests) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "missing_3h",
        "missing_6h",
        "join",
        "wrong_cycle",
        "outside",
        "end_boundary",
        "duplicate_join",
    ],
)
def test_cli_declared_control_schedule_rejects_incomplete_or_wrong_support(
    gateway_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str], fault: str
) -> None:
    case = gateway_cli
    prepare_cli(case, capsys)
    connection = connection_args(case)
    invoke_cli(case, ["register", "--hru", "new_synthetic", *connection], capsys)
    invoke_cli(
        case,
        [
            "reconcile",
            "--outcome",
            "confirmed",
            "--actor",
            "Synthetic",
            "--evidence",
            "synthetic-receipt",
        ],
        capsys,
    )
    preview = invoke_cli(case, ["subscribe", *connection], capsys)
    invoke_cli(
        case,
        ["subscribe", "--confirm", preview["subscription_revision"], *connection],
        capsys,
    )
    case.retrieval.missing = False
    case.forecast_fault = fault
    result = invoke_cli(case, ["resume", *connection], capsys)
    assert all(station["outcome"] == "held" for station in result["stations"])
    raw = json.loads((case.workspace / "state.json").read_text())
    evidence = [chunk for chunk in raw["coverage"] if chunk["stream"] == "ifs_forecast"]
    assert evidence and all(
        chunk["expected_count"] == 64 and chunk["holds"] for chunk in evidence
    )
    if fault in ("missing_3h", "missing_6h", "join", "duplicate_join"):
        assert all(chunk["usable_count"] == 63 for chunk in evidence)
    elif fault == "wrong_cycle":
        assert all(
            chunk["usable_count"] == 0 and chunk["validated_run_identities"] == []
            for chunk in evidence
        )
    else:
        assert all(
            chunk["usable_count"] == 64 and "unexpected_time_support" in chunk["holds"]
            for chunk in evidence
        )


def test_cli_observed_subscription_drift_survives_restart_until_reconfirmed(
    gateway_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    case = gateway_cli
    prepare_cli(case, capsys)
    connection = connection_args(case)
    invoke_cli(case, ["register", "--hru", "new_synthetic", *connection], capsys)
    invoke_cli(
        case,
        [
            "reconcile",
            "--outcome",
            "confirmed",
            "--actor",
            "Synthetic",
            "--evidence",
            "synthetic-receipt",
        ],
        capsys,
    )
    preview = invoke_cli(case, ["subscribe", *connection], capsys)
    revision = preview["subscription_revision"]
    invoke_cli(case, ["subscribe", "--confirm", revision, *connection], capsys)
    case.retrieval.missing = False
    invoke_cli(case, ["resume", *connection], capsys, expected=0)
    native_count = len(case.native_requests)
    case.gateway.rows[0]["enabled"] = False
    observed = invoke_cli(
        case, ["subscribe", "--confirm", revision, *connection], capsys
    )
    raw = json.loads((case.workspace / "state.json").read_text())
    assert raw["subscription_actions"][-1]["outcome"] == "uncertain"
    assert any(a["outcome"] == "verified" for a in raw["subscription_actions"])
    assert all(s["outcome"] == "held" for s in observed["stations"])
    invoke_cli(case, ["status"], capsys)
    invoke_cli(case, ["resume", *connection], capsys)
    assert len(case.native_requests) == native_count
    assert sum(r.method != "GET" for r in case.gateway.requests) == 5
    renewed = invoke_cli(case, ["subscribe", *connection], capsys)
    assert renewed["subscription_revision"] != revision
    invoke_cli(case, ["subscribe", "--confirm", revision, *connection], capsys)
    assert case.gateway.rows[0]["enabled"] is False
    assert sum(r.method != "GET" for r in case.gateway.requests) == 5
    invoke_cli(
        case,
        ["subscribe", "--confirm", renewed["subscription_revision"], *connection],
        capsys,
        expected=0,
    )
    invoke_cli(case, ["status"], capsys, expected=0)
    invoke_cli(case, ["resume", *connection], capsys, expected=0)
    assert sum(r.method != "GET" for r in case.gateway.requests) == 6
    assert len([r for r in case.gateway.requests if r.method == "PUT"]) == 1


@pytest.mark.parametrize("declaration", ["missing", "unresolved", "partial"])
def test_cli_unresolved_forecast_cycles_cannot_confirm_or_write(
    gateway_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str], declaration: str
) -> None:
    case = gateway_cli
    source = json.loads(case.scope.read_text())
    forecast = source["forecast"]
    if declaration == "partial":
        forecast["cycles"].append("2024-02-12T00:00:00Z")
    elif declaration == "missing":
        forecast["source_runs"] = []
    else:
        forecast["cycles"].append("2024-02-12T00:00:00Z")
        forecast["source_runs"] = [
            [cycle.replace("Z", "+00:00"), {"semantics": "unresolved"}]
            for cycle in forecast["cycles"]
        ]
    case.scope.write_text(json.dumps(source))
    preview = invoke_cli(case, ["prepare", "--scope", str(case.scope)], capsys)
    assert all(
        any(
            h.startswith("forecast_source_run_contract_unconfirmed:")
            for h in s["holds"]
        )
        for s in preview["stations"]
    )
    invoke_cli(
        case,
        [
            "prepare",
            "--scope",
            str(case.scope),
            "--confirm",
            preview["proposal_revision"],
            "--vatro-review",
            "synthetic",
        ],
        capsys,
    )
    invoke_cli(
        case, ["register", "--hru", "new_synthetic", *connection_args(case)], capsys
    )
    invoke_cli(case, ["subscribe", *connection_args(case)], capsys)
    raw = json.loads((case.workspace / "state.json").read_text())
    assert raw["confirmed_proposal"] is None and raw["attempts"] == []
    assert case.gateway.requests == []


def complete_cli_case(
    case: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> dict[str, Any]:
    prepare_cli(case, capsys)
    connection = connection_args(case)
    invoke_cli(case, ["register", "--hru", "new_synthetic", *connection], capsys)
    invoke_cli(
        case,
        [
            "reconcile",
            "--outcome",
            "confirmed",
            "--actor",
            "Synthetic",
            "--evidence",
            "synthetic",
        ],
        capsys,
    )
    preview = invoke_cli(case, ["subscribe", *connection], capsys)
    invoke_cli(
        case,
        ["subscribe", "--confirm", preview["subscription_revision"], *connection],
        capsys,
    )
    case.retrieval.missing = False
    return invoke_cli(case, ["resume", *connection], capsys, expected=0)


@pytest.mark.parametrize(
    "field,value",
    [
        ("variable", "wrong_variable"),
        ("expected_count", 1),
        ("validated_source", "wrong_source"),
        ("start", "2024-01-01T01:00:00+00:00"),
        ("station_code", "unmapped"),
        (
            "validated_source_run",
            {"semantics": "null", "value": None, "timestamp": None},
        ),
    ],
)
def test_cli_wrong_evidence_cannot_establish_eligibility_or_skip_fetch(
    gateway_cli: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    field: str,
    value: object,
) -> None:
    case = gateway_cli
    complete_cli_case(case, capsys)
    path = case.workspace / "state.json"
    raw = json.loads(path.read_text())
    chunk = raw["coverage"][0]
    chunk[field] = value
    if field == "validated_source_run":
        chunk["validated_run_identities"] = []
    if field == "expected_count":
        chunk["usable_count"] = value
        chunk["validated_run_identities"] = chunk["validated_run_identities"][:1]
        if "observed_count" in chunk:
            chunk["observed_count"] = value
    path.write_text(json.dumps(raw))
    output = invoke_cli(case, ["status"], capsys)
    assert output["stations"][0]["outcome"] == "held"
    if field in ("variable", "start"):
        assert output["stations"][1]["outcome"] == "eligible_for_preparation"
    before = len(case.native_requests)
    output = invoke_cli(
        case,
        ["resume", *connection_args(case)],
        capsys,
        expected=2 if field in ("variable", "start", "station_code") else 0,
    )
    assert (
        len(case.native_requests) > before + 2
    )  # refreshed history, not just forecast
    summaries = output["stations"][0]["coverage"]
    if field in ("variable", "start", "station_code"):
        assert any(c["applicability"] == "unexpected" for c in summaries)


@pytest.mark.parametrize("stream", ["era5_history", "ifs_forecast"])
def test_cli_conflicting_subscription_identities_never_confirm_or_write(
    gateway_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str], stream: str
) -> None:
    case = gateway_cli
    source = json.loads(case.scope.read_text())
    contract = next(c for c in source["contracts"] if c["stream"] == stream)
    contract["subscription_parameters"][1]["parameter_name"] = contract[
        "subscription_parameters"
    ][0]["parameter_name"]
    case.scope.write_text(json.dumps(source))
    preview = invoke_cli(case, ["prepare", "--scope", str(case.scope)], capsys)
    assert "conflicting_subscription_identity" in preview["stations"][0]["holds"]
    invoke_cli(
        case,
        [
            "prepare",
            "--scope",
            str(case.scope),
            "--confirm",
            preview["proposal_revision"],
            "--vatro-review",
            "synthetic",
        ],
        capsys,
    )
    invoke_cli(
        case, ["register", "--hru", "new_synthetic", *connection_args(case)], capsys
    )
    invoke_cli(case, ["subscribe", *connection_args(case)], capsys)
    assert case.gateway.requests == []


def test_cli_reports_aggregate_coverage_without_raw_measurements(
    gateway_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    output = complete_cli_case(gateway_cli, capsys)
    chunks = output["stations"][0]["coverage"]
    forecast = next(c for c in chunks if c["stream"] == "ifs_forecast")
    assert (
        forecast["requested_count"]
        == forecast["usable_count"]
        == forecast["observed_count"]
        == 64
    )
    assert forecast["missing_count"] == 0 and forecast["missing_intervals"] == []
    assert forecast["validated_source"] == "synthetic_forecast"
    assert forecast["validated_run_identities"] == ["2024-02-11T00:00:00+00:00"]
    assert forecast["publication_lag_seconds"] >= 0
    assert forecast["response_sha256"].startswith("sha256:")
    assert "values" not in forecast and "raw" not in forecast


def test_cli_failed_fetch_preserves_honest_aggregate_evidence(
    gateway_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    case = gateway_cli
    complete_cli_case(case, capsys)
    case.download_error = "synthetic"
    output = invoke_cli(case, ["resume", *connection_args(case)], capsys)
    forecasts = [
        c for c in output["stations"][0]["coverage"] if c["stream"] == "ifs_forecast"
    ]
    assert forecasts and all(
        c["observed_count"] == c["usable_count"] == 0 for c in forecasts
    )
    assert all(
        c["validated_source"] is None and c["validated_source_run"] is None
        for c in forecasts
    )
    assert all(
        c["requested_count"] == c["missing_count"] == 64 and c["missing_intervals"]
        for c in forecasts
    )


def test_cli_legacy_observed_count_is_unknown_not_fabricated(
    gateway_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    case = gateway_cli
    complete_cli_case(case, capsys)
    path = case.workspace / "state.json"
    raw = json.loads(path.read_text())
    for chunk in raw["coverage"]:
        del chunk["observed_count"]
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    output = invoke_cli(case, ["status"], capsys, expected=0)
    assert all(c["observed_count"] is None for c in output["stations"][0]["coverage"])
    assert path.read_bytes() == before


def test_cli_malformed_saved_counts_fail_without_reset(
    gateway_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    case = gateway_cli
    complete_cli_case(case, capsys)
    path = case.workspace / "state.json"
    raw = json.loads(path.read_text())
    raw["coverage"][0]["expected_count"] = -1
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    assert prepare_gateway.run([*case.argv, "status"]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert path.read_bytes() == before


@pytest.mark.parametrize("command", ["register", "subscribe", "resume"])
@pytest.mark.parametrize(
    "failure",
    ["missing", "empty", "whitespace", "unreadable", "disappeared", "emptied_on_read"],
)
def test_explicit_secret_file_fails_before_clients_without_environment_fallback(
    gateway_cli: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    failure: str,
) -> None:
    import recap_client

    case = gateway_cli
    prepare_cli(case, capsys)
    monkeypatch.setenv("RECAP_API_KEY", "aaaa-ambient-aaaa")
    if failure == "missing":
        case.key_file.unlink()
    elif failure in ("empty", "whitespace"):
        case.key_file.write_text("" if failure == "empty" else " \n\t")
    else:
        original = Path.read_text

        def unreadable(path: Path, *args: object, **kwargs: object) -> str:
            if path == case.key_file:
                if failure == "disappeared":
                    raise FileNotFoundError("synthetic disappeared aaaa-ambient-aaaa")
                if failure == "emptied_on_read":
                    return ""
                raise PermissionError("synthetic raw read error aaaa-ambient-aaaa")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", unreadable)
    clients: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        clients.append("constructed")
        raise AssertionError(
            "client construction must follow successful credential selection"
        )

    monkeypatch.setattr(prepare_gateway, "RecapRegistration", forbidden)
    monkeypatch.setattr(recap_client, "RecapClient", forbidden)
    arguments = [command, *connection_args(case)]
    if command == "register":
        arguments.extend(["--hru", "new_synthetic"])
    assert prepare_gateway.run([*case.argv, *arguments]) == 2
    captured = capsys.readouterr()
    assert "aaaa-ambient-aaaa" not in captured.out + captured.err
    assert "synthetic raw read error" not in captured.out + captured.err
    assert clients == [] and case.gateway.requests == [] and case.native_requests == []
    assert os.environ.get("RECAP_API_KEY") == "aaaa-ambient-aaaa"


def test_valid_explicit_secret_precedes_ambient_key(
    gateway_cli: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = gateway_cli
    prepare_cli(case, capsys)
    monkeypatch.setenv("RECAP_API_KEY", "aaaa-ambient-aaaa")
    case.key_file.write_text("  " + case.secret + "\n")
    invoke_cli(
        case, ["register", "--hru", "new_synthetic", *connection_args(case)], capsys
    )
    assert case.gateway.requests[0].url.params["api_key"] == case.secret


@pytest.mark.parametrize("selection", ["file", "environment"])
def test_omitted_secret_option_keeps_supported_environment_selection(
    gateway_cli: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    from sapphire_flow.config import recap_gateway

    case = gateway_cli
    prepare_cli(case, capsys)
    monkeypatch.setattr(
        recap_gateway,
        "DEFAULT_RECAP_API_KEY_SECRET_PATH",
        case.key_file
        if selection == "file"
        else case.key_file.parent / "missing-default",
    )
    monkeypatch.setenv(
        "RECAP_API_KEY", "aaaa-ambient-aaaa" if selection == "file" else case.secret
    )
    invoke_cli(
        case,
        ["register", "--hru", "new_synthetic", "--config", str(case.config)],
        capsys,
    )
    assert case.gateway.requests[0].url.params["api_key"] == case.secret


@pytest.mark.parametrize("method", ["POST", "PUT"])
@pytest.mark.parametrize(
    "status,reason",
    [
        (400, "invalid_request"),
        (422, "invalid_request"),
        (409, "reconciliation_required"),
        (401, "authorization_failed"),
        (403, "authorization_failed"),
        (0, "outcome_uncertain"),
        (200, "malformed_response"),
    ],
)
def test_cli_subscription_failure_reason_is_durable_and_never_retries_write(
    gateway_cli: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    status: int,
    reason: str,
    method: str,
) -> None:
    case = gateway_cli
    prepare_cli(case, capsys)
    connection = connection_args(case)
    invoke_cli(case, ["register", "--hru", "new_synthetic", *connection], capsys)
    invoke_cli(
        case,
        [
            "reconcile",
            "--outcome",
            "confirmed",
            "--actor",
            "Synthetic",
            "--evidence",
            "synthetic",
        ],
        capsys,
    )
    if method == "PUT":
        case.gateway.rows.append(
            {
                "source": "synthetic_era5",
                "hru_code": "new_synthetic",
                "parameter_name": "2m_temperature",
                "enabled": False,
                "forecast_enabled": None,
                "reanalysis_enabled": None,
            }
        )
    preview = invoke_cli(case, ["subscribe", *connection], capsys)
    case.subscription_fault = status
    invoke_cli(
        case,
        ["subscribe", "--confirm", preview["subscription_revision"], *connection],
        capsys,
    )
    raw = json.loads((case.workspace / "state.json").read_text())
    assert any(
        action.get("failure_reason") == reason for action in raw["subscription_actions"]
    )
    result = invoke_cli(case, ["status"], capsys)
    assert any(
        failure["reason"] == reason
        for row in result["subscription_diagnostics"]
        for failure in row["failure_history"]
    )
    invoke_cli(
        case,
        ["subscribe", "--confirm", preview["subscription_revision"], *connection],
        capsys,
    )
    assert sum(request.method != "GET" for request in case.gateway.requests) == 2
    assert [
        request.method for request in case.gateway.requests if request.method != "GET"
    ][-1] == method
    result = invoke_cli(case, ["status"], capsys)
    assert any(
        failure["reason"] == reason
        for row in result["subscription_diagnostics"]
        for failure in row["failure_history"]
    )


@pytest.mark.parametrize("readback", ["transport", "interrupt"])
def test_cli_write_failure_is_saved_before_readback_can_fail(
    gateway_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str], readback: str
) -> None:
    case = gateway_cli
    prepare_cli(case, capsys)
    connection = connection_args(case)
    invoke_cli(case, ["register", "--hru", "new_synthetic", *connection], capsys)
    invoke_cli(
        case,
        [
            "reconcile",
            "--outcome",
            "confirmed",
            "--actor",
            "Synthetic",
            "--evidence",
            "synthetic",
        ],
        capsys,
    )
    preview = invoke_cli(case, ["subscribe", *connection], capsys)
    case.subscription_fault = 409
    case.readback_fault = readback
    command = ["subscribe", "--confirm", preview["subscription_revision"], *connection]
    if readback == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            prepare_gateway.run([*case.argv, *command])
        capsys.readouterr()
    else:
        invoke_cli(case, command, capsys)
    raw = json.loads((case.workspace / "state.json").read_text())
    assert (
        raw["subscription_actions"][-1]["failure_reason"] == "reconciliation_required"
    )
    case.readback_fault = None
    result = invoke_cli(case, ["status"], capsys)
    assert (
        result["subscription_diagnostics"][0]["failure_history"][-1]["reason"]
        == "reconciliation_required"
    )
    invoke_cli(case, command, capsys)
    assert sum(request.method != "GET" for request in case.gateway.requests) == 2


@pytest.mark.parametrize(
    "status,reason",
    [
        (0, "outcome_uncertain"),
        (409, "reconciliation_required"),
        (400, "invalid_request"),
    ],
)
def test_cli_verified_readback_retains_failure_history_without_duplicate_write(
    gateway_cli: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    status: int,
    reason: str,
) -> None:
    case = gateway_cli
    case.subscription_fault = status
    case.commit_failed_write = True
    output = complete_cli_case(case, capsys)
    diagnostic = next(
        row for row in output["subscription_diagnostics"] if row["failure_history"]
    )
    assert diagnostic["current_outcome"] == "verified"
    assert diagnostic["failure_reason"] is None
    assert diagnostic["failure_history"][0]["reason"] == reason
    invoke_cli(
        case,
        [
            "subscribe",
            "--confirm",
            output["subscription_revision"],
            *connection_args(case),
        ],
        capsys,
        expected=0,
    )
    assert sum(request.method != "GET" for request in case.gateway.requests) == 5


@pytest.mark.parametrize("failure", ["contract", "checksum", "path", "geopackage"])
@pytest.mark.parametrize("entry", ["prepare", "status", "recovery"])
def test_known_package_failures_exit_held_without_mutation_or_clients(
    gateway_cli: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    entry: str,
) -> None:
    import recap_client

    from sapphire_flow.exceptions import ConfigurationError
    from tests.unit.services.test_gateway_preparation import (
        build_invalid_handover_fixture,
    )

    case = gateway_cli
    source = Path(case.argv[3])
    if entry != "prepare":
        prepare_cli(case, capsys)
    else:
        case.workspace.mkdir(mode=0o700)
        (case.workspace / ".preparation.lock").write_bytes(b"")
        (case.workspace / ".preparation.lock").chmod(0o600)
    workspace_before = {
        str(path.relative_to(case.workspace)): path.read_bytes()
        for path in case.workspace.rglob("*")
        if path.is_file()
    }
    source_before: dict[str, bytes] = {}

    def corrupt_fixture() -> None:
        build_invalid_handover_fixture(source, failure)
        source_before.update(
            {
                str(path.relative_to(source)): path.read_bytes()
                for path in source.rglob("*")
                if path.is_file()
            }
        )

    if entry == "recovery":

        def fail_config(path: Path) -> None:
            corrupt_fixture()
            raise ConfigurationError("synthetic config failure")

        monkeypatch.setattr(prepare_gateway, "load_recap_gateway_config", fail_config)
        command = ["register", "--hru", "new_synthetic", *connection_args(case)]
    else:
        corrupt_fixture()
        command = (
            ["prepare", "--scope", str(case.scope)]
            if entry == "prepare"
            else ["status"]
        )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("known package failure must not construct a client")

    monkeypatch.setattr(prepare_gateway, "RecapRegistration", forbidden)
    monkeypatch.setattr(recap_client, "RecapClient", forbidden)
    assert prepare_gateway.run([*case.argv, *command]) == 2
    captured = capsys.readouterr()
    assert "held or failed" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert "synthetic-unsupported-contract" not in captured.out + captured.err
    assert "synthetic-escape" not in captured.out + captured.err
    assert (
        "not recognized as being in a supported file format"
        not in captured.out + captured.err
    )
    assert case.gateway.requests == [] and case.native_requests == []
    assert {
        str(path.relative_to(case.workspace)): path.read_bytes()
        for path in case.workspace.rglob("*")
        if path.is_file()
    } == workspace_before
    assert {
        str(path.relative_to(source)): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    } == source_before


def test_cli_z_spelled_scope_preserves_revision_and_completes(
    gateway_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    case = gateway_cli
    original = invoke_cli(case, ["prepare", "--scope", str(case.scope)], capsys)
    source = json.loads(case.scope.read_text())
    source["forecast"]["source_runs"][0][0] = source["forecast"]["source_runs"][0][
        0
    ].replace("+00:00", "Z")
    assert source["forecast"]["cycles"][0].endswith("Z")
    case.scope.write_text(json.dumps(source))
    assert prepare_gateway.run([*case.argv, "prepare", "--scope", str(case.scope)]) == 2
    captured = capsys.readouterr()
    assert "held or failed" not in captured.err
    equivalent = json.loads(captured.out)
    assert equivalent["proposal_revision"] == original["proposal_revision"]
    assert "forecast_source_run_contract_unconfirmed" not in str(equivalent["stations"])
    complete = complete_cli_case(case, capsys)
    assert complete["proposal_revision"] == original["proposal_revision"]
    assert all(
        station["outcome"] == "eligible_for_preparation"
        for station in complete["stations"]
    )
    path = case.workspace / "state.json"
    before = path.read_bytes()
    raw = json.loads(before)
    assert raw["proposals"][-1]["forecast"]["source_runs"][0][0].endswith("+00:00")
    invoke_cli(case, ["status"], capsys, expected=0)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "status,code,reason",
    [
        (401, "synthetic_unknown", "authorization_failed"),
        (403, "synthetic_unknown", "authorization_failed"),
        (404, "source_data_missing", "source_unavailable"),
        (404, "subscription_not_found", "outcome_uncertain"),
    ],
)
def test_cli_native_retrieval_error_category_is_safe_and_durable(
    gateway_cli: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    status: int,
    code: str,
    reason: str,
) -> None:
    case = gateway_cli
    complete_cli_case(case, capsys)
    writes = sum(request.method != "GET" for request in case.gateway.requests)
    case.retrieval_response = (status, code)
    output = invoke_cli(case, ["resume", *connection_args(case)], capsys)
    forecasts = [
        chunk
        for chunk in output["stations"][0]["coverage"]
        if chunk["stream"] == "ifs_forecast"
    ]
    assert forecasts and all(reason in chunk["holds"] for chunk in forecasts)
    assert all(
        chunk["usable_count"] == 0 and chunk["validated_source"] is None
        for chunk in forecasts
    )
    restored = invoke_cli(case, ["status"], capsys)
    assert reason in restored["stations"][0]["holds"]
    assert sum(request.method != "GET" for request in case.gateway.requests) == writes
