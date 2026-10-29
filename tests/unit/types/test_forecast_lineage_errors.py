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


@pytest.mark.parametrize("with_qc_context", [False, True])
def test_consumed_observation_snapshot_excludes_ephemeral_qc_context(
    with_qc_context: bool,
) -> None:
    import json
    from dataclasses import asdict, replace

    from sapphire_flow.types.forecast_lineage import snapshot_consumed_input
    from sapphire_flow.types.observation_read import QcContextRequirement, QcReadToken
    from sapphire_flow.types.rating_reference import canonical_content
    from tests.conftest import make_observation

    observation = make_observation()
    expected = asdict(observation)
    for field in (
        "created_at",
        "qc_read_token",
        "qc_context_segment",
        "qc_context_requirement",
    ):
        expected.pop(field)
    if with_qc_context:
        observation = replace(
            observation,
            qc_read_token=QcReadToken(state_sha256="a" * 64, context_sha256="b" * 64),
            qc_context_segment=7,
            qc_context_requirement=QcContextRequirement.UNIT_GOVERNED,
        )
    snapshot = snapshot_consumed_input(observation, units="m")
    assert snapshot.content == canonical_content(expected)
    assert set(json.loads(snapshot.content)) == {
        "id",
        "station_id",
        "timestamp",
        "parameter",
        "value",
        "source",
        "rating_curve_id",
        "rating_curve_correction_version",
        "qc_status",
        "qc_flags",
        "qc_rule_version",
        "delivery_id",
    }
