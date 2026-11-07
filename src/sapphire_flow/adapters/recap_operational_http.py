# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from urllib.parse import urljoin

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests

from sapphire_flow.adapters.recap_gateway import (
    RecapAuthError,
    RecapConfigurationError,
    RecapDataUnavailableError,
    RecapPayloadIntegrityError,
    RecapTransientError,
)

if TYPE_CHECKING:
    from recap_client import ApiClientConfig
    from recap_client.http import ParquetHttpClient

    from sapphire_flow.adapters.recap_gateway import EcmwfApiLike, SnowApiLike


_STRING_LIMITS = {"source": 32, "source_meta": 512, "source_run": 64}


def _expanded_row_budget(schema: pa.Schema) -> int:  # pyright: ignore[reportUnknownParameterType]
    if len(schema) > 64 or len(set(schema.names)) != len(schema):
        raise RecapPayloadIntegrityError("unsupported operational parquet schema")
    # Index, validity buffers, Arrow/pandas copies and Python string overhead.
    width = 32
    for field in schema:
        dtype = field.type
        if pa.types.is_dictionary(dtype):
            dtype = dtype.value_type
        if pa.types.is_string(dtype) or pa.types.is_large_string(dtype):
            limit = _STRING_LIMITS.get(field.name)
            if limit is None:
                raise RecapPayloadIntegrityError(
                    "unsupported operational string column"
                )
            width += 128 + 5 * limit
        elif pa.types.is_integer(dtype) or pa.types.is_floating(dtype):
            width += 2 * (dtype.bit_width // 8) + 2
        elif pa.types.is_timestamp(dtype):
            width += 18
        else:
            raise RecapPayloadIntegrityError("unsupported operational parquet type")
    return width


def _validate_string_batch(
    batch: pa.RecordBatch,  # pyright: ignore[reportUnknownParameterType]
    *,
    max_bytes: int,
    row_budget: int,
) -> None:
    expanded = batch.num_rows * row_budget
    for field, column in zip(batch.schema, batch.columns, strict=True):
        values = column.dictionary if pa.types.is_dictionary(column.type) else column
        if not (
            pa.types.is_string(values.type) or pa.types.is_large_string(values.type)
        ):
            continue
        if pa.types.is_dictionary(column.type):
            # Dictionaries can contain values not referenced by any output row.
            expanded += len(values) * 128 + 5 * values.nbytes
            if expanded > max_bytes:
                raise RecapPayloadIntegrityError(
                    "operational dictionary exceeds decoded limit"
                )
        longest = pc.call_function(
            "max", [pc.call_function("binary_length", [values])]
        ).as_py()
        if longest is not None and longest > _STRING_LIMITS[field.name]:
            raise RecapPayloadIntegrityError(
                "operational provenance string exceeds limit"
            )


def decode_bounded_parquet(payload: bytes, *, max_bytes: int) -> pd.DataFrame:
    if len(payload) > max_bytes:
        raise RecapPayloadIntegrityError("operational response exceeds transfer limit")
    try:
        parquet = pq.ParquetFile(
            io.BytesIO(payload),
            thrift_string_size_limit=min(max_bytes, 64 * 1024),
            thrift_container_size_limit=128,
        )
        metadata = parquet.metadata
        row_budget = _expanded_row_budget(parquet.schema_arrow)
        encoded_pages = sum(
            metadata.row_group(index).total_byte_size
            for index in range(metadata.num_row_groups)
        )
        if encoded_pages > max_bytes or metadata.num_rows * row_budget > max_bytes:
            raise RecapPayloadIntegrityError(
                "operational parquet exceeds decoded limit"
            )
        dictionary_columns = [
            field.name
            for field in parquet.schema_arrow
            if field.name in _STRING_LIMITS
            and (pa.types.is_string(field.type) or pa.types.is_large_string(field.type))
        ]
        if dictionary_columns:
            parquet = pq.ParquetFile(
                io.BytesIO(payload),
                read_dictionary=dictionary_columns,
                thrift_string_size_limit=min(max_bytes, 64 * 1024),
                thrift_container_size_limit=128,
            )
        frames: list[pd.DataFrame] = []
        total_bytes = 0
        total_rows = 0
        for batch in parquet.iter_batches(
            batch_size=max(1, min(1024, max_bytes // row_budget)), use_threads=False
        ):
            total_rows += batch.num_rows
            if total_rows > metadata.num_rows or batch.nbytes > max_bytes:
                raise RecapPayloadIntegrityError(
                    "operational batch exceeds decoded limit"
                )
            _validate_string_batch(batch, max_bytes=max_bytes, row_budget=row_budget)
            frame = cast("pd.DataFrame", batch.to_pandas())
            total_bytes += sum(
                cast("list[int]", frame.memory_usage(index=True, deep=True).tolist())
            )
            if total_bytes > max_bytes:
                raise RecapPayloadIntegrityError(
                    "operational dataframe exceeds decoded limit"
                )
            frames.append(frame)
        if total_rows != metadata.num_rows:
            raise RecapPayloadIntegrityError("operational parquet row count differs")
        if not frames:
            raise RecapPayloadIntegrityError("empty operational parquet")
        return pd.concat(frames) if len(frames) > 1 else frames[0]
    except RecapPayloadIntegrityError:
        raise
    except (ValueError, OSError, TypeError) as exc:
        raise RecapPayloadIntegrityError("invalid operational parquet") from exc


class BoundedOperationalTransport:
    def __init__(
        self,
        *,
        config: ApiClientConfig,
        max_bytes: int,
        session: requests.Session,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("operational response limit must be positive")
        self._config = config
        self._max_bytes = max_bytes
        self._session = session

    def get_parquet_df(self, path: str, params: dict[str, object]) -> pd.DataFrame:
        if path not in ("exports/ecmwf/operational", "exports/ifs/forecast"):
            raise RecapConfigurationError("unsupported bounded operational endpoint")
        if any(
            not isinstance(value, (str, int, float, bool)) for value in params.values()
        ):
            raise RecapConfigurationError("invalid operational request parameter type")
        query = {key: str(value) for key, value in params.items()}
        query["output_format"] = "parquet"
        if not self._config.api_key:
            raise RecapConfigurationError("operational weather API key is missing")
        query["api_key"] = self._config.api_key
        url = urljoin(self._config.base_url.rstrip("/") + "/", path)
        try:
            with self._session.get(
                url,
                params=query,
                stream=True,
                timeout=self._config.timeout_s,
                verify=self._config.verify_tls,
                allow_redirects=False,
            ) as response:
                if (
                    response.status_code in (401, 403)
                    or 300 <= response.status_code < 400
                ):
                    self._raise_status(response.status_code, b"")
                limit = self._max_bytes if response.status_code == 200 else 64 * 1024
                payload = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if response.status_code != 200:
                        payload.extend(chunk[: limit - len(payload)])
                        if len(payload) == limit:
                            break
                    else:
                        if len(payload) + len(chunk) > limit:
                            raise RecapPayloadIntegrityError(
                                "operational response exceeds transfer limit"
                            )
                        payload.extend(chunk)
                if response.status_code != 200:
                    self._raise_status(response.status_code, bytes(payload))
                return decode_bounded_parquet(bytes(payload), max_bytes=self._max_bytes)
        except requests.RequestException:
            raise RecapTransientError("operational weather transport failed") from None

    @staticmethod
    def _raise_status(status: int, payload: bytes) -> None:
        if status in (401, 403):
            raise RecapAuthError(
                "operational weather authorization failed", status_code=status
            )
        if 300 <= status < 400:
            raise RecapConfigurationError("operational weather redirect rejected")
        if status in (408, 425, 429) or status >= 500:
            raise RecapTransientError("operational weather upstream unavailable")
        try:
            body = json.loads(payload)
        except (ValueError, UnicodeError, RecursionError):
            body = None
        if isinstance(body, dict):
            if body.get("code") == "source_data_missing":
                raise RecapDataUnavailableError(
                    "operational weather unavailable", code="source_data_missing"
                )
            if (
                body.get("supported_values") is not None
                or body.get("field") == "hru_code"
            ):
                raise RecapConfigurationError("operational weather request rejected")
        raise RecapTransientError("operational weather upstream unavailable")


def build_bounded_operational_api(
    *, config: ApiClientConfig, max_bytes: int, session: requests.Session
) -> EcmwfApiLike:
    from recap_client.ecmwf import EcmwfApi

    transport = BoundedOperationalTransport(
        config=config, max_bytes=max_bytes, session=session
    )
    return cast("EcmwfApiLike", EcmwfApi(cast("ParquetHttpClient", transport)))


@dataclass(kw_only=True, slots=True)
class BoundedOperationalRecapClient:
    ecmwf: EcmwfApiLike
    snow: SnowApiLike
