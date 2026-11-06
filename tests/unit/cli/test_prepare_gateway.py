from __future__ import annotations

import io
import json
import logging
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
        wrong_run=False,
        forecast_fault=None,
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
        return case.gateway.transport(request)

    def requests_send(
        self: object, request: requests.PreparedRequest, **kwargs: object
    ) -> requests.Response:
        assert request.url is not None
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
