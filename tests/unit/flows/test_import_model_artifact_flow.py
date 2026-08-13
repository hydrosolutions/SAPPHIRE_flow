"""Plan 157 T3 fixer round — the deployed parameter boundary.

`import_model_artifact_flow` is a Prefect deployment: its top-level
parameters cross the wire as JSON. A `bytes`-typed parameter does NOT
base64-decode a JSON string (pydantic's JSON-mode `bytes` validation does
`str.encode()`), so arbitrary binary (a `.pt` checkpoint) cannot round-trip
through it. `artifact_base64: str`, decoded inside the flow, is the fix —
these tests reproduce the exact deployment-parameter transport, not just the
decode helper in isolation.
"""

from __future__ import annotations

import base64

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.import_model_artifact import (
    _decode_artifact_base64,
    import_model_artifact_flow,
)

# Deliberately non-UTF-8 — a real checkpoint is arbitrary binary, and a
# base64 round trip that only survives ASCII/UTF-8 text is not proof of
# anything.
_RAW_ARTIFACT_BYTES = bytes([0, 1, 2, 3, 0xFF, 0xFE, 0x80, 0x81])


class TestDeploymentParameterBoundaryRoundTrips:
    def test_validate_parameters_preserves_artifact_base64_through_json(
        self,
    ) -> None:
        """Simulates exactly what a Prefect deployment run does: JSON-shaped
        parameters go through `Flow.validate_parameters` before `.fn` ever
        runs. The base64 STRING must survive that boundary unchanged."""
        encoded = base64.b64encode(_RAW_ARTIFACT_BYTES).decode()

        validated = import_model_artifact_flow.validate_parameters(
            {
                "model_id": "some_model",
                "artifact_base64": encoded,
                "trained_at": "2025-01-01T00:00:00+00:00",
            }
        )

        assert validated["artifact_base64"] == encoded
        assert (
            base64.b64decode(validated["artifact_base64"], validate=True)
            == _RAW_ARTIFACT_BYTES
        )

    def test_decode_artifact_base64_reproduces_the_original_bytes(self) -> None:
        encoded = base64.b64encode(_RAW_ARTIFACT_BYTES).decode()

        assert _decode_artifact_base64(encoded) == _RAW_ARTIFACT_BYTES

    def test_decode_artifact_base64_rejects_invalid_base64(self) -> None:
        with pytest.raises(ConfigurationError, match="not valid base64"):
            _decode_artifact_base64("not-valid-base64!!! not even close")
