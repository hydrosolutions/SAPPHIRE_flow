from __future__ import annotations

from pathlib import Path

CONSUMER_FILES = {
    "src/sapphire_flow/services/training_data.py": [
        "fetch_accepted_historical_observations"
    ],
    "src/sapphire_flow/services/hindcast.py": [
        "fetch_accepted_historical_observations"
    ],
    "src/sapphire_flow/services/operational_inputs.py": [
        "fetch_accepted_historical_observations"
    ],
    "src/sapphire_flow/services/forecast_lab/db_sources.py": [
        "fetch_accepted_historical_observations"
    ],
}


def test_plan520_accepted_history_consumers_use_restricted_reader() -> None:
    for path, required in CONSUMER_FILES.items():
        source = Path(path).read_text()
        for name in required:
            assert name in source
