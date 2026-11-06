from __future__ import annotations

import io
import logging
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


def scope() -> CoverageScope:
    return CoverageScope(
        polygon="g_synthetic",
        start=datetime(2024, 2, 28, tzinfo=UTC),
        end=datetime(2024, 3, 2, tzinfo=UTC),
        cadence=timedelta(days=1),
        source="synthetic_history",
        contract_reference="synthetic-only contract",
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
