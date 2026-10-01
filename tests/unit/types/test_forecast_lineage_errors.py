from __future__ import annotations

import traceback

import pytest

from sapphire_flow.types.forecast_lineage import ForecastInputSnapshot


def test_snapshot_validation_traceback_withholds_payload() -> None:
    marker = "protected-synthetic-lineage-canary"
    with pytest.raises(ValueError, match="consumed input snapshot") as caught:
        ForecastInputSnapshot(
            kind="observation", units="m", content='{"id":"' + marker + '"}'
        )
    rendered = "".join(traceback.format_exception(caught.value))
    assert marker not in rendered
