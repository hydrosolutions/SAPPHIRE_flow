from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sapphire_flow.config.qc_rules import load_qc_rules
from sapphire_flow.flows.ingest_observations import _aggregate_qc_status
from sapphire_flow.services.qc import Stage1QualityChecker
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import ClimBaseline
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation import Observation

_ROOT = Path(__file__).parents[3]
_FIXTURE = _ROOT / "tests/fixtures/qc/swiss_daily_discharge_v1.json"


def test_swiss_daily_discharge_matches_pre_change_golden(
    monkeypatch,
) -> None:
    monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
    fixture = json.loads(_FIXTURE.read_text())
    station_id = StationId(UUID(fixture["station_id"]))
    created_at = ensure_utc(datetime.fromisoformat(fixture["created_at"]))
    observations = [
        Observation(
            id=ObservationId(UUID(row["id"])),
            station_id=station_id,
            timestamp=ensure_utc(datetime.fromisoformat(row["timestamp"])),
            parameter=fixture["parameter"],
            value=row["value"],
            source=ObservationSource.MEASURED,
            rating_curve_id=None,
            rating_curve_correction_version=None,
            qc_status=QcStatus.RAW,
            qc_flags=[],
            qc_rule_version=None,
            created_at=created_at,
        )
        for row in fixture["observations"]
    ]
    target = observations[2]
    baseline = ClimBaseline(
        station_id=station_id,
        parameter=fixture["parameter"],
        day_of_year=fixture["baseline"]["day_of_year"],
        rolling_mean=fixture["baseline"]["rolling_mean"],
        rolling_std=fixture["baseline"]["rolling_std"],
        sample_count=fixture["baseline"]["sample_count"],
    )

    flags = Stage1QualityChecker().check(
        observations,
        load_qc_rules(_ROOT / "config.toml"),
        [],
        [baseline],
        station_networks={station_id: "bafu"},
    )
    actual = [
        {
            "status": _aggregate_qc_status(flags[obs.id], rules_ran=True).value,
            "flags": [
                {
                    "rule_id": flag.rule_id,
                    "status": flag.status.value,
                    "detail": flag.detail,
                }
                for flag in flags[obs.id]
            ],
        }
        for obs in observations
    ]
    expected = [
        {"status": row["status"], "flags": row["flags"]}
        for row in fixture["observations"]
    ]

    assert target.timestamp == ensure_utc(
        datetime.fromisoformat(fixture["observations"][2]["timestamp"])
    )
    assert actual == expected
