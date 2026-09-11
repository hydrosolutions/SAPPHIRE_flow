"""Plan 261 T2 — forecast values must never reach training or hindcast.

Training on forecast values teaches a model the forecast's biases rather than
the weather's, and a hindcast filled with forecasts issued after its own issue
time is look-ahead leakage that flatters every skill score derived from it.

D2 makes both impossible BY CONSTRUCTION rather than by a runtime guard: the
filled rows exist only inside an operational assembler's frame and are never
persisted. That is a structural property, so this is a structural test — it
fails the moment the fill spreads to a historical path, which no value-based
assertion on today's frames would catch.

KNOWN LIMIT, recorded so this file is not read as more than it proves
(independent Codex review 2026-09-11): both checks are structural — a
reference scan and a signature scan — so they catch DIRECT wiring only. A
historical assembler that reached the fill INDIRECTLY, through some
intermediate wrapper carrying the forecast store on a context object, would
leave both green. That is accepted rather than fixed: the alternative is a
runtime provenance guard, which D6 deliberately declined ("we ship what data
we have"), and the plan's DO-NOT-OVER-ENGINEER rule binds here. What keeps
the indirect route closed today is that neither historical assembler takes a
`WeatherForecastStore` at all — which is exactly what the second test pins.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from sapphire_flow.services import (
    hindcast,
    operational_inputs,
    track_assembly,
    training_data,
)

_FILL = "fill_past_forcing_tail"

# The fill belongs to the two OPERATIONAL assemblers and nowhere else.
_PERMITTED_MODULES = {
    "sapphire_flow/services/operational_inputs.py",  # defines it
    "sapphire_flow/services/track_assembly.py",  # the per-track operational route
}


def test_only_the_operational_assemblers_reference_the_fill() -> None:
    src = Path(operational_inputs.__file__).parents[1]
    referencing = {
        str(path.relative_to(src.parent))
        for path in src.rglob("*.py")
        if _FILL in path.read_text()
    }

    assert referencing == _PERMITTED_MODULES


def test_historical_assemblers_take_no_weather_forecast_store() -> None:
    """A historical assembler that cannot be HANDED the forecast store cannot
    read from it, whatever a future edit does inside the function body."""
    for func in (
        hindcast._assemble_hindcast_inputs,
        training_data.assemble_station_training_data,
    ):
        annotations = [
            str(p.annotation) for p in inspect.signature(func).parameters.values()
        ]
        assert not any("WeatherForecastStore" in a for a in annotations), func.__name__


def test_the_operational_assemblers_do_take_one() -> None:
    """The mirror of the assertion above — it is what makes that one meaningful
    rather than vacuously true of every function in the repo."""
    for func in (
        operational_inputs.assemble_station_operational_inputs,
        track_assembly.assemble_assignment_inputs,
    ):
        annotations = [
            str(p.annotation) for p in inspect.signature(func).parameters.values()
        ]
        assert any("WeatherForecastStore" in a for a in annotations), func.__name__
