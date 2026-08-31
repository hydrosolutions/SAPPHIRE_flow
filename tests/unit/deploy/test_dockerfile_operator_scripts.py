"""Plan 218: the runtime image must ship a CURATED set of operator scripts
(D2) at ``/app/scripts`` — no more (image hygiene, D1) and no less (the
whole point of the plan: a deployment must be reproducible from the image).

Prose assertions on the real ``Dockerfile`` text, matching the existing
Plan 082 pattern (``tests/unit/tooling/test_recap_wheel_guard.py``) — a
single selector each, not a bespoke Dockerfile parser.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

# D2's curated set — the scripts an operator runs AGAINST a deployment.
_CURATED_SCRIPTS = frozenset(
    {
        "import_caravan_attributes.py",
        "onboard.py",
        "backfill_meteoswiss_history.py",
        "backfill_era5_land_history.py",
        "validate_forcing_reference.py",
    }
)

# D1/D2 — explicitly excluded: host provisioning, dev tooling, research,
# Nepal-stack-specific, and the two entries D3a proved can't work in the
# image (check_readiness.py needs docs/; regenerate_icon_grid_asset.py
# writes the read-only source tree).
_EXCLUDED_SCRIPTS = frozenset(
    {
        "bootstrap-mac-mini.sh",
        "codex-review.sh",
        "check_readiness.py",
        "regenerate_icon_grid_asset.py",
        "audit_distribution_shift.py",
        "063_e2e_verify.py",
        "plan100_forecast_feed_resilience.py",
        "nepal_forcing_run.py",
        "nepal_forcing_seed.sql",
        "recap_probe_loop.py",
        "restore-rehearsal.sh",
    }
)


def _dockerfile_text() -> str:
    return (_REPO_ROOT / "Dockerfile").read_text()


def _dockerfile_lines() -> list[str]:
    return _dockerfile_text().splitlines()


class TestRuntimeStageShipsCuratedOperatorScripts:
    def test_every_curated_script_is_copied_into_the_runtime_stage(self) -> None:
        text = _dockerfile_text()
        missing = {name for name in _CURATED_SCRIPTS if f"scripts/{name}" not in text}
        assert not missing, (
            f"curated operator scripts missing from Dockerfile: {missing}"
        )

    def test_curated_scripts_land_at_app_scripts(self) -> None:
        text = _dockerfile_text()
        assert (
            "/app/scripts/" in text
            or "/app/scripts\n" in text
            or "/app/scripts " in text
        )

    def test_curated_copy_is_chowned_to_the_app_user(self) -> None:
        """The COPY instruction that ships the curated scripts (which may
        span multiple backslash-continued lines) must be --chown=app:app —
        matching every other runtime-stage COPY (Dockerfile:130-133)."""
        lines = _dockerfile_lines()
        target_idx = next(
            (
                i
                for i, line in enumerate(lines)
                if "scripts/import_caravan_attributes.py" in line
            ),
            None,
        )
        assert target_idx is not None, "no line ships import_caravan_attributes.py"

        # Walk back to the start of this COPY instruction (backslash continuations).
        start_idx = target_idx
        while start_idx > 0 and lines[start_idx - 1].rstrip().endswith("\\"):
            start_idx -= 1
        found = lines[start_idx]
        assert found.strip().startswith("COPY"), f"expected COPY, got {found!r}"
        assert "--chown=app:app" in lines[start_idx]

    def test_excluded_scripts_are_not_shipped(self) -> None:
        text = _dockerfile_text()
        shipped = {name for name in _EXCLUDED_SCRIPTS if f"scripts/{name}" in text}
        assert not shipped, (
            f"scripts that must NOT ship are referenced in Dockerfile: {shipped}"
        )

    def test_operator_scripts_copy_is_in_the_runtime_stage_not_the_builder(
        self,
    ) -> None:
        text = _dockerfile_text()
        # The runtime stage begins at the second `FROM python:` (multi-stage
        # build: builder, then runtime). The curated-scripts COPY must live
        # after that second FROM, alongside the other runtime `COPY --from=builder`
        # lines it was added near (Dockerfile:130-133 pre-change).
        from_indices = [
            i for i, line in enumerate(text.splitlines()) if line.startswith("FROM ")
        ]
        assert len(from_indices) == 2, (
            "expected exactly one builder stage + one runtime stage"
        )
        runtime_stage_start = from_indices[1]
        lines = text.splitlines()
        copy_line_idx = next(
            (
                i
                for i, line in enumerate(lines)
                if "scripts/import_caravan_attributes.py" in line
            ),
            None,
        )
        assert copy_line_idx is not None, "no line ships import_caravan_attributes.py"
        assert copy_line_idx > runtime_stage_start, (
            "curated-scripts COPY must be in the runtime stage, not the builder stage"
        )
