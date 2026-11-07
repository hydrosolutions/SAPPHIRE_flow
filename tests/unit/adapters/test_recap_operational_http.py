import io
from pathlib import Path
from typing import Literal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from sapphire_flow.adapters.recap_gateway import RecapPayloadIntegrityError
from sapphire_flow.adapters.recap_operational_http import decode_bounded_parquet

FIXTURE = Path(__file__).parents[2] / "fixtures/recap/operational/precipitation.parquet"


class TestDecodeBoundedParquet:
    def test_qualified_payload_decodes_with_preserved_utc_index(self) -> None:
        frame = decode_bounded_parquet(FIXTURE.read_bytes(), max_bytes=64 * 1024 * 1024)
        assert len(frame) == 708
        assert str(frame.index.tz) == "UTC"

    def test_rejects_transfer_overflow_before_decode(self) -> None:
        with pytest.raises(RecapPayloadIntegrityError, match="transfer limit"):
            decode_bounded_parquet(FIXTURE.read_bytes(), max_bytes=100)

    def test_rejects_decoded_overflow(self) -> None:
        payload = FIXTURE.read_bytes()
        with pytest.raises(RecapPayloadIntegrityError, match="decoded limit"):
            decode_bounded_parquet(payload, max_bytes=len(payload))

    def test_rejects_invalid_parquet_without_raw_error(self) -> None:
        with pytest.raises(
            RecapPayloadIntegrityError, match="invalid operational parquet"
        ):
            decode_bounded_parquet(b"not parquet", max_bytes=1024)


class TestExpandedAllocationPreflight:
    def test_dictionary_numeric_expansion_refused_before_batch_decode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sink = io.BytesIO()
        pq.write_table(pa.table({"value": [1.0] * 100_000}), sink, compression="snappy")
        payload = sink.getvalue()
        assert len(payload) < 4096

        def forbidden_decode(*args: object, **kwargs: object) -> None:
            raise AssertionError("decoder must not run before expanded-size refusal")

        monkeypatch.setattr(pq.ParquetFile, "iter_batches", forbidden_decode)
        with pytest.raises(RecapPayloadIntegrityError, match="decoded limit"):
            decode_bounded_parquet(payload, max_bytes=4096)

    def test_oversized_dictionary_provenance_is_refused(self) -> None:
        sink = io.BytesIO()
        pq.write_table(pa.table({"source_meta": ["x" * 1024] * 2}), sink)
        with pytest.raises(RecapPayloadIntegrityError, match="string exceeds limit"):
            decode_bounded_parquet(sink.getvalue(), max_bytes=64 * 1024)

    def test_nested_schema_is_refused(self) -> None:
        sink = io.BytesIO()
        pq.write_table(pa.table({"value": [[1.0, 2.0]]}), sink)
        with pytest.raises(RecapPayloadIntegrityError, match="unsupported.*type"):
            decode_bounded_parquet(sink.getvalue(), max_bytes=64 * 1024)

    def test_unreferenced_dictionary_values_are_budgeted_before_conversion(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import pyarrow.compute as pc

        values = pa.array(["x" * 256 + str(index) for index in range(1000)])
        column = pa.DictionaryArray.from_arrays(pa.array([0]), values)
        sink = io.BytesIO()
        pq.write_table(pa.table({"source_meta": column}), sink)

        def forbidden_length_scan(*args: object, **kwargs: object) -> None:
            raise AssertionError(
                "dictionary budget must precede string scan/conversion"
            )

        monkeypatch.setattr(pc, "call_function", forbidden_length_scan)
        with pytest.raises(
            RecapPayloadIntegrityError, match="dictionary exceeds decoded limit"
        ):
            decode_bounded_parquet(sink.getvalue(), max_bytes=512 * 1024)


@pytest.mark.parametrize("variable", ["tp", "t2m"])
@pytest.mark.parametrize("hour,horizon", [(0, 15), (6, 3)])
def test_pinned_future_export_layout_through_actual_sdk(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    hour: int,
    horizon: int,
) -> None:
    # Contract-generated layout from pinned raw-export postprocessing; NOT a
    # captured deployed future-endpoint response or producer attestation.
    import pandas as pd
    import requests
    from recap_client import ApiClientConfig

    from sapphire_flow.adapters.recap_operational_http import (
        build_bounded_operational_api,
    )

    index = pd.date_range(
        f"2026-11-06 {hour:02}:00",
        periods=24 * horizon,
        freq="h",
        tz="UTC",
        name="time",
    )
    expected = pd.DataFrame(
        {f"g_{n:05}": [float(n)] * len(index) for n in range(21)}, index=index
    )
    expected["source"] = "ifs"
    expected["source_run"] = pd.Timestamp(index[0]).tz_localize(None)
    expected["source_meta"] = '{"grid_resolution":0.1}'
    payload = expected.to_parquet(engine="pyarrow", index=True)
    session = requests.Session()
    queries: list[dict[str, object]] = []

    def get(url: str, **kwargs: object) -> requests.Response:
        assert url == "https://fixture.invalid/exports/ifs/forecast"
        queries.append(kwargs)
        response = requests.Response()
        response.status_code = 200
        response._content = payload
        response._content_consumed = True
        return response

    monkeypatch.setattr(session, "get", get)
    api = build_bounded_operational_api(
        config=ApiClientConfig(
            base_url="https://fixture.invalid", api_key="fixture-key", timeout_s=7
        ),
        max_bytes=64 * 1024 * 1024,
        session=session,
    )
    result = api.ifs_forecast(
        variable=variable,
        run_date=index[0].date(),
        hru_code="nepal21",
        run_hour=hour,
        ifs_type="fc",
        member=None,
        horizon_days=horizon,
        include_provenance=True,
    )
    pd.testing.assert_frame_equal(
        result.astype(expected.dtypes.to_dict()), expected, check_freq=False
    )
    assert queries == [
        {
            "params": {
                "variable": variable,
                "run_date": "2026-11-06",
                "hru_code": "nepal21",
                "ifs_type": "fc",
                "run_hour": str(hour),
                "level_type": "sfc",
                "horizon_days": str(horizon),
                "include_provenance": "True",
                "output_format": "parquet",
                "api_key": "fixture-key",
            },
            "stream": True,
            "timeout": 7,
            "verify": True,
            "allow_redirects": False,
        }
    ]


def test_multiple_batches_preserve_provenance_index_and_all_values() -> None:
    import pandas as pd

    index = pd.date_range("2026-01-01", periods=2500, freq="h", tz="UTC", name="time")
    expected = pd.DataFrame(
        {
            "g_00001": range(len(index)),
            "source": "ifs",
            "source_run": "2026-01-01T00:00:00Z",
            "source_meta": "{}",
        },
        index=index,
    )
    decoded = decode_bounded_parquet(
        expected.to_parquet(index=True), max_bytes=16 * 1024 * 1024
    )
    pd.testing.assert_frame_equal(
        decoded.astype(expected.dtypes.to_dict()), expected, check_freq=False
    )


def test_request_failure_traceback_does_not_expose_credential_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import traceback

    import requests
    from recap_client import ApiClientConfig

    from sapphire_flow.adapters.recap_gateway import RecapTransientError
    from sapphire_flow.adapters.recap_operational_http import (
        BoundedOperationalTransport,
    )

    canary = "fixture-key-must-not-appear"
    session = requests.Session()

    def get(url: str, **kwargs: object) -> requests.Response:
        raise requests.ConnectionError(f"connection failed: {url}?api_key={canary}")

    monkeypatch.setattr(session, "get", get)
    transport = BoundedOperationalTransport(
        config=ApiClientConfig(base_url="https://fixture.invalid", api_key=canary),
        max_bytes=4096,
        session=session,
    )
    with pytest.raises(RecapTransientError, match="transport failed") as raised:
        transport.get_parquet_df("exports/ifs/forecast", {})
    assert canary not in "".join(traceback.format_exception(raised.value))
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (401, b"credential-canary", "auth"),
        (403, b"credential-canary", "auth"),
        (408, b"", "transient"),
        (425, b"", "transient"),
        (429, b"", "transient"),
        (502, b"", "transient"),
        (503, b"x" * 70000, "transient"),
        (404, b'{"code":"source_data_missing"}', "unavailable"),
        (400, b'{"supported_values":["credential-canary"]}', "configuration"),
        (422, b'{"field":"hru_code","hint":"credential-canary"}', "configuration"),
        (400, b"unstructured", "transient"),
        (404, b"unstructured", "transient"),
        (422, b"unstructured", "transient"),
        (302, b"credential-canary", "configuration"),
    ],
)
def test_error_status_taxonomy_is_bounded_and_credential_safe(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    body: bytes,
    expected: str,
) -> None:
    import traceback

    import requests
    from recap_client import ApiClientConfig

    from sapphire_flow.adapters.recap_gateway import (
        RecapAuthError,
        RecapConfigurationError,
        RecapDataUnavailableError,
        RecapTransientError,
    )
    from sapphire_flow.adapters.recap_operational_http import (
        BoundedOperationalTransport,
    )

    response = requests.Response()
    response.status_code = status
    response._content = body
    response._content_consumed = True
    session = requests.Session()
    monkeypatch.setattr(session, "get", lambda *args, **kwargs: response)
    transport = BoundedOperationalTransport(
        config=ApiClientConfig(
            base_url="https://fixture.invalid", api_key="credential-canary"
        ),
        max_bytes=1024,
        session=session,
    )
    expected_type = {
        "auth": RecapAuthError,
        "configuration": RecapConfigurationError,
        "unavailable": RecapDataUnavailableError,
        "transient": RecapTransientError,
    }[expected]
    with pytest.raises(expected_type, match="operational weather") as raised:
        transport.get_parquet_df("exports/ecmwf/operational", {})
    assert "credential-canary" not in "".join(traceback.format_exception(raised.value))


def test_error_prefix_stops_reading_and_closes_without_draining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import Iterator

    import requests
    from recap_client import ApiClientConfig

    from sapphire_flow.adapters.recap_gateway import RecapTransientError
    from sapphire_flow.adapters.recap_operational_http import (
        BoundedOperationalTransport,
    )

    response = requests.Response()
    response.status_code = 503
    closed: list[bool] = []

    def chunks(chunk_size: int) -> Iterator[bytes]:
        assert chunk_size == 64 * 1024
        yield b"x" * (64 * 1024)
        raise AssertionError("must not drain the remaining error body")

    monkeypatch.setattr(response, "iter_content", chunks)
    monkeypatch.setattr(response, "close", lambda: closed.append(True))
    session = requests.Session()
    monkeypatch.setattr(session, "get", lambda *args, **kwargs: response)
    transport = BoundedOperationalTransport(
        config=ApiClientConfig(
            base_url="https://fixture.invalid", api_key="fixture-key"
        ),
        max_bytes=1024,
        session=session,
    )
    with pytest.raises(RecapTransientError, match="upstream unavailable"):
        transport.get_parquet_df("exports/ifs/forecast", {})
    assert closed == [True]


def test_success_body_still_refuses_transfer_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import requests
    from recap_client import ApiClientConfig

    from sapphire_flow.adapters.recap_operational_http import (
        BoundedOperationalTransport,
    )

    response = requests.Response()
    response.status_code = 200
    response._content = b"x" * 1025
    response._content_consumed = True
    session = requests.Session()
    monkeypatch.setattr(session, "get", lambda *args, **kwargs: response)
    transport = BoundedOperationalTransport(
        config=ApiClientConfig(
            base_url="https://fixture.invalid", api_key="fixture-key"
        ),
        max_bytes=1024,
        session=session,
    )
    with pytest.raises(RecapPayloadIntegrityError, match="transfer limit"):
        transport.get_parquet_df("exports/ifs/forecast", {})


@pytest.mark.parametrize("status", [401, 403, 302, 307, 429, 503])
@pytest.mark.parametrize("body_failure", ["immediate", "after_prefix"])
def test_known_fatal_status_precedes_broken_error_body(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    body_failure: Literal["immediate", "after_prefix"],
) -> None:
    import traceback
    from collections.abc import Iterator

    import requests
    from recap_client import ApiClientConfig

    from sapphire_flow.adapters.recap_gateway import (
        RecapAuthError,
        RecapConfigurationError,
        RecapTransientError,
    )
    from sapphire_flow.adapters.recap_operational_http import (
        BoundedOperationalTransport,
    )

    response = requests.Response()
    response.status_code = status
    reads: list[int] = []
    closed: list[bool] = []

    def chunks(chunk_size: int) -> Iterator[bytes]:
        reads.append(chunk_size)
        if body_failure == "after_prefix":
            yield b"proxy error prefix"
        raise requests.exceptions.ChunkedEncodingError("credential-canary")

    monkeypatch.setattr(response, "iter_content", chunks)
    monkeypatch.setattr(response, "close", lambda: closed.append(True))
    session = requests.Session()
    monkeypatch.setattr(session, "get", lambda *args, **kwargs: response)
    transport = BoundedOperationalTransport(
        config=ApiClientConfig(
            base_url="https://fixture.invalid", api_key="credential-canary"
        ),
        max_bytes=1024,
        session=session,
    )
    expected = (
        RecapAuthError
        if status in (401, 403)
        else RecapConfigurationError
        if status < 400
        else RecapTransientError
    )
    with pytest.raises(expected, match="operational weather") as raised:
        transport.get_parquet_df("exports/ecmwf/operational", {})
    if status in (401, 403):
        assert raised.value.status_code == status
    assert reads == ([64 * 1024] if status in (429, 503) else [])
    assert closed == [True]
    assert "credential-canary" not in "".join(traceback.format_exception(raised.value))
