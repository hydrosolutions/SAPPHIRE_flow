from __future__ import annotations

import io
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
import requests
from recap_client import ApiClientConfig, RecapClient

from sapphire_flow.adapters.recap_registration import GatewayAccessError
from sapphire_flow.services.gateway_data_coverage import (
    CoverageScope,
    assess_coverage,
    private_gateway_fetch,
    recover_downloads,
)
from sapphire_flow.types.gateway_preparation import (
    SourceRunContract,
    SourceRunSemantics,
)


def scope() -> CoverageScope:
    return CoverageScope(
        polygon="g_synthetic",
        start=datetime(2024, 2, 28, tzinfo=UTC),
        end=datetime(2024, 3, 2, tzinfo=UTC),
        cadence=timedelta(days=1),
        source="synthetic_history",
        contract_reference="synthetic-only contract",
        source_run=SourceRunContract(semantics=SourceRunSemantics.NULL),
    )


def frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "g_synthetic": [0.0, 1.0, 2.0],
            "source": ["synthetic_history"] * 3,
            "source_run": [None] * 3,
        },
        index=pd.date_range("2024-02-28", periods=3, tz="UTC"),
    )


def test_complete_zero_and_leap_day() -> None:
    result = assess_coverage(frame(), scope())
    assert result.holds == ()
    assert result.usable_count == result.expected_count == 3


@pytest.mark.parametrize(
    "problem",
    ["interior", "nan", "inf", "source", "duplicate", "offgrid", "column", "empty"],
)
def test_full_scope_not_minmax(problem: str) -> None:
    data = frame()
    if problem == "interior":
        data = data.iloc[[0, 2]]
    elif problem == "nan":
        data.iloc[1, 0] = float("nan")
    elif problem == "inf":
        data.iloc[1, 0] = float("inf")
    elif problem == "source":
        data.iloc[1, 1] = "forecast_fill"
    elif problem == "duplicate":
        data = pd.concat([data, data.iloc[[1]]])
    elif problem == "offgrid":
        data.index = data.index + timedelta(hours=1)
    elif problem == "column":
        data = data.drop(columns="g_synthetic")
    elif problem == "empty":
        data = data.iloc[:0]
    result = assess_coverage(data, scope())
    assert result.holds
    assert result.usable_count < result.expected_count


def test_native_facade_private_download_and_redirect_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "synthetic-sentinel-secret"
    buffer = io.BytesIO()
    frame().to_parquet(buffer)
    calls = []
    permissions = []

    def send(
        self: object, request: requests.PreparedRequest, **kwargs: object
    ) -> requests.Response:
        calls.append(request.url)
        logging.getLogger("urllib3.connectionpool").critical("unsafe %s", request.url)
        assert Path.cwd().parent == tmp_path
        assert Path.cwd().stat().st_mode & 0o077 == 0
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 302 if len(calls) == 1 else 200
        if len(calls) == 1:
            response.headers["location"] = "https://example.invalid/redirected"
            response.raw = io.BytesIO(b"")
        else:

            class Raw(io.BytesIO):
                def read(self, *args: object, **kwargs: object) -> bytes:
                    paths = list(Path.cwd().glob(".tmp_recap_*.parquet"))
                    permissions.extend(p.stat().st_mode & 0o077 for p in paths)
                    return super().read(*args, **kwargs)

            response.raw = Raw(buffer.getvalue())
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    caplog.set_level(logging.DEBUG)
    client = RecapClient(
        ApiClientConfig(base_url="https://example.invalid/sdk", api_key=secret)
    )
    original = Path.cwd()
    result = private_gateway_fetch(
        tmp_path,
        lambda: client.ecmwf.era5_land_reanalysis(
            variable="synthetic",
            start_date="2024-02-28",
            end_date="2024-03-01",
            hru_code="new_synthetic",
        ),
    )
    assert result.equals(frame())
    assert len(calls) == 2  # Native Session.get still follows redirects.
    assert permissions and set(permissions) == {0}
    assert Path.cwd() == original and not list(tmp_path.iterdir())
    assert secret not in caplog.text


def test_native_failure_has_no_chain_or_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "synthetic-sentinel-secret"

    def send(*args: object, **kwargs: object) -> requests.Response:
        raise requests.ConnectionError("https://example.invalid/?api_key=" + secret)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    client = RecapClient(
        ApiClientConfig(base_url="https://example.invalid/sdk", api_key=secret)
    )
    with pytest.raises(GatewayAccessError, match="uncertain") as error:
        private_gateway_fetch(
            tmp_path,
            lambda: client.ecmwf.era5_land_reanalysis(
                variable="synthetic", start_date="2024-02-28", hru_code="new_synthetic"
            ),
        )
    assert secret not in str(error.value)
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert not list(tmp_path.iterdir())


def test_only_owned_orphans_cleaned(tmp_path: Path) -> None:
    owned = tmp_path / ".recap-download-owned"
    owned.mkdir(mode=0o700)
    (owned / ".owned").write_text("SAP3 private Gateway download attempt v1\n")
    (owned / ".tmp_recap_123.parquet").write_bytes(b"synthetic")
    other = owned / "keep.txt"
    other.write_text("keep")
    unrelated = tmp_path / ".tmp_recap_234.parquet"
    unrelated.write_bytes(b"unrelated")
    assert recover_downloads(tmp_path) == 1
    assert other.exists() and unrelated.exists()


def test_unconfirmed_run_contract_never_completes_history() -> None:
    data = frame()
    data["source_run"] = "unexpected-run"
    result = assess_coverage(data, replace(scope(), source_run=SourceRunContract()))
    assert result.holds
    assert result.usable_count == 0


@pytest.mark.parametrize(
    "semantics", [SourceRunSemantics.EXACT, SourceRunSemantics.NULL]
)
def test_wrong_historical_source_run_holds_each_row(
    semantics: SourceRunSemantics,
) -> None:
    contract = SourceRunContract(
        semantics=semantics,
        value="confirmed-synthetic" if semantics == SourceRunSemantics.EXACT else None,
    )
    data = frame()
    data["source_run"] = "wrong-run"
    result = assess_coverage(data, replace(scope(), source_run=contract))
    assert "wrong_source_provenance" in result.holds
    assert result.usable_count == 0
    assert result.validated_source is None and result.validated_source_run is None


@pytest.mark.parametrize(
    "semantics",
    [SourceRunSemantics.EXACT, SourceRunSemantics.NULL, SourceRunSemantics.ABSENT],
)
def test_explicit_run_semantics_retained_as_validated_evidence(
    semantics: SourceRunSemantics,
) -> None:
    contract = SourceRunContract(
        semantics=semantics,
        value="confirmed-synthetic" if semantics == SourceRunSemantics.EXACT else None,
    )
    data = frame()
    if semantics == SourceRunSemantics.ABSENT:
        data = data.drop(columns="source_run")
    elif semantics == SourceRunSemantics.EXACT:
        data["source_run"] = "confirmed-synthetic"
    result = assess_coverage(data, replace(scope(), source_run=contract))
    assert result.holds == ()
    assert result.validated_source == "synthetic_history"
    assert result.validated_source_run == contract


def test_null_run_requires_a_column_and_absent_run_forbids_it() -> None:
    assert assess_coverage(frame().drop(columns="source_run"), scope()).holds
    assert assess_coverage(
        frame(),
        replace(
            scope(), source_run=SourceRunContract(semantics=SourceRunSemantics.ABSENT)
        ),
    ).holds


@pytest.mark.parametrize(
    "semantics", [SourceRunSemantics.UTC_INSTANT, SourceRunSemantics.UTC_DATE_MIDNIGHT]
)
def test_datetime_run_contracts_normalize_offset_and_preserve_observed_identity(
    semantics: SourceRunSemantics,
) -> None:
    data = frame()
    expected = pd.Timestamp(scope().start).normalize()
    data["source_run"] = (
        data.index.normalize().tz_convert("Asia/Kathmandu")
        if semantics == SourceRunSemantics.UTC_DATE_MIDNIGHT
        else expected.tz_convert("Asia/Kathmandu")
    )
    run = SourceRunContract(
        semantics=semantics,
        timestamp=expected.to_pydatetime()
        if semantics == SourceRunSemantics.UTC_INSTANT
        else None,
    )
    result = assess_coverage(data, replace(scope(), source_run=run))
    assert result.holds == ()
    expected_runs = (
        tuple(time.isoformat() for time in data.index.normalize())
        if semantics == SourceRunSemantics.UTC_DATE_MIDNIGHT
        else (expected.isoformat(),)
    )
    assert result.validated_run_identities == expected_runs


@pytest.mark.parametrize(
    "bad_run", [pd.Timestamp("2024-01-01"), pd.NaT, "2024-01-01T00:00:00+00:00"]
)
def test_typed_datetime_run_rejects_naive_missing_or_text(bad_run: object) -> None:
    data = frame()
    data["source_run"] = bad_run
    run = SourceRunContract(semantics=SourceRunSemantics.UTC_DATE_MIDNIGHT)
    result = assess_coverage(data, replace(scope(), source_run=run))
    assert result.usable_count == 0
    assert "wrong_source_provenance" in result.holds


def test_declared_daily_run_policy_validates_every_date_not_a_global_constant() -> None:
    times = pd.date_range("2024-01-01", periods=3, freq="D", tz="UTC")
    data = pd.DataFrame(
        {"g_synthetic": 1.0, "source": "synthetic_history", "source_run": times},
        index=times,
    )
    declared = replace(
        scope(),
        start=times[0].to_pydatetime(),
        end=(times[-1] + pd.Timedelta(days=1)).to_pydatetime(),
        cadence=timedelta(days=1),
        source_run=SourceRunContract(semantics=SourceRunSemantics.UTC_DATE_MIDNIGHT),
    )
    result = assess_coverage(data, declared)
    assert result.holds == ()
    assert len(result.validated_run_identities) == 3
    data.loc[times[-1], "source_run"] = times[0]
    result = assess_coverage(data, declared)
    assert result.usable_count == 2
    assert "wrong_source_provenance" in result.holds


@pytest.mark.parametrize(
    "status,code,reason",
    [
        (401, "synthetic_unknown", "authorization_failed"),
        (403, "synthetic_unknown", "authorization_failed"),
        (404, "source_data_missing", "source_unavailable"),
        (404, "subscription_not_found", "outcome_uncertain"),
        (500, "synthetic_unknown", "outcome_uncertain"),
    ],
)
def test_native_client_error_shapes_are_safely_classified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, code: str, reason: str
) -> None:
    import json

    secret = "aaaa-native-aaaa"

    def send(
        self: object, request: requests.PreparedRequest, **kwargs: object
    ) -> requests.Response:
        response = requests.Response()
        response.status_code = status
        response.url = request.url
        response.request = request
        response.raw = io.BytesIO(
            json.dumps({"code": code, "message": secret + str(request.url)}).encode()
        )
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    client = RecapClient(
        ApiClientConfig(base_url="https://example.invalid/sdk", api_key=secret)
    )
    with pytest.raises(GatewayAccessError, match=reason) as error:
        private_gateway_fetch(
            tmp_path,
            lambda: client.ecmwf.era5_land_reanalysis(
                variable="synthetic", start_date="2024-02-28", hru_code="new_synthetic"
            ),
        )
    assert error.value.reason.value == reason
    assert secret not in str(error.value)
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert not list(tmp_path.iterdir())


def test_malformed_response_digests_cover_structure_and_available_relevant_cells() -> (
    None
):
    missing = frame().drop(columns="source")
    changed_value = missing.copy()
    changed_value.iloc[0, 0] = 99.0
    duplicate = pd.concat([frame(), frame().iloc[:, [0]]], axis=1)
    unknown_axis = frame().reset_index(drop=True)
    changed_axis = unknown_axis.copy()
    changed_axis.index = pd.Index([10, 11, 12])
    responses = [
        missing,
        changed_value,
        duplicate,
        unknown_axis,
        changed_axis,
        pd.DataFrame(),
        pd.DataFrame(columns=["other"]),
        pd.DataFrame(columns=["junk"]),
    ]
    digests = [
        assess_coverage(response, scope()).response_sha256 for response in responses
    ]
    assert len(set(digests)) == len(responses)
    assert all(
        assess_coverage(response.copy(), scope()).response_sha256 == digest
        for response, digest in zip(responses, digests, strict=True)
    )


def test_absent_tabular_response_is_not_hashed_as_an_empty_response() -> None:
    absent = assess_coverage(None, scope())
    empty = assess_coverage(pd.DataFrame(), scope())
    assert absent.response_sha256 != empty.response_sha256
    assert absent.observed_count == absent.usable_count == 0
    assert absent.validated_source is None
    assert absent.response_sha256 == assess_coverage(None, scope()).response_sha256


def test_projection_hash_avoids_arbitrary_reprs_and_unrequested_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Unsupported:
        def __repr__(self) -> str:
            raise AssertionError("arbitrary repr must never run")

    data = frame().drop(columns="source")
    data["g_synthetic"] = Unsupported()
    data["unrequested"] = "aaaa-unrequested-aaaa"
    first = assess_coverage(data, scope())
    data["unrequested"] = "changed but excluded"
    assert assess_coverage(data, scope()).response_sha256 == first.response_sha256
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_malformed_duplicate_and_timezone_metadata_affect_projection() -> None:
    data = pd.concat([frame(), frame().iloc[:, [0]]], axis=1)
    changed = data.copy()
    changed.iloc[0, -1] = 42.0
    naive = frame().drop(columns="source")
    naive.index = naive.index.tz_localize(None)
    assert (
        assess_coverage(data, scope()).response_sha256
        != assess_coverage(changed, scope()).response_sha256
    )
    assert (
        assess_coverage(naive, scope()).response_sha256
        != assess_coverage(frame().drop(columns="source"), scope()).response_sha256
    )
