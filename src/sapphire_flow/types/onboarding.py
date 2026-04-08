from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True, slots=True)
class OnboardingResult:
    stations_created: int
    stations_skipped: int
    basins_created: int
    basins_skipped: int
    observations_imported: int
    forcing_records_imported: int
    observations_qc_passed: int
    observations_qc_failed: int
    observations_qc_suspect: int
    baselines_computed: int
    flow_regimes_computed: int
    errors: list[str]
    model_assignments_created: int = 0
    models_trained: int = 0
    stations_marked_operational: int = 0
    stations_updated: int = 0
