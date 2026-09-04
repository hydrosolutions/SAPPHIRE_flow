"""Plan 218: the runtime image must ship a CURATED set of operator scripts
(D2) at ``/app/scripts`` — no more (image hygiene, D1) and no less (the
whole point of the plan: a deployment must be reproducible from the image).

These tests parse the Dockerfile into logical instructions (folding
backslash line-continuations, matching Docker's own instruction grammar)
and reason about the SINGLE COPY instruction that targets ``/app/scripts``
— rather than searching curated/excluded names independently across the
entire file text. A prose substring search would pass a Dockerfile that
copies only one curated script while mentioning the others in a comment,
or that ships an extra directory (e.g. ``scripts/dhm_precip/``) alongside
the curated COPY; anchoring on the parsed instruction's actual source and
destination arguments closes both gaps.
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
# writes the read-only source tree). Includes directory names (not just
# files) so a COPY of a whole excluded subtree (e.g. ``scripts/dhm_precip/``)
# is caught too.
_EXCLUDED_NAMES = frozenset(
    {
        "bootstrap-mac-mini.sh",
        "check_readiness.py",
        "regenerate_icon_grid_asset.py",
        "audit_distribution_shift.py",
        "063_e2e_verify.py",
        "plan100_forecast_feed_resilience.py",
        "nepal_forcing_run.py",
        "nepal_forcing_seed.sql",
        "recap_probe_loop.py",
        "restore-rehearsal.sh",
        "dhm_precip",
        "launchd",
    }
)


def _dockerfile_text() -> str:
    return (_REPO_ROOT / "Dockerfile").read_text()


def _dockerfile_lines() -> list[str]:
    return _dockerfile_text().splitlines()


def _logical_instructions() -> list[tuple[int, int, str]]:
    """Fold backslash-continued lines into single logical instructions.

    Returns a list of ``(start_line_idx, end_line_idx_inclusive, joined_text)``
    — indices into ``_dockerfile_lines()`` (0-based) — for every
    non-blank, non-comment logical instruction in the Dockerfile.
    """
    lines = _dockerfile_lines()
    instructions: list[tuple[int, int, str]] = []
    buf: list[str] = []
    start = 0
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not buf:
            if not stripped or stripped.startswith("#"):
                continue
            start = i
        buf.append(raw)
        if stripped.endswith("\\"):
            continue
        instructions.append((start, i, "\n".join(buf)))
        buf = []
    return instructions


def _copy_instructions() -> list[tuple[int, int, list[str]]]:
    """Every logical ``COPY`` instruction as ``(start, end, args)`` where
    ``args`` is the source(s)-then-destination token list with flags
    (``--chown=...``, ``--from=...``) stripped out."""
    result: list[tuple[int, int, list[str]]] = []
    for start, end, text in _logical_instructions():
        first_token = text.strip().split(None, 1)[0] if text.strip() else ""
        if first_token != "COPY":
            continue
        joined = text.replace("\\\n", " ")
        tokens = joined.split()
        args = [t for t in tokens[1:] if not t.startswith("--")]
        result.append((start, end, args))
    return result


def _scripts_dir_copies() -> list[tuple[int, int, list[str]]]:
    """COPY instructions whose destination is under ``/app/scripts``."""
    return [
        (start, end, args)
        for start, end, args in _copy_instructions()
        if args and args[-1].startswith("/app/scripts")
    ]


class TestRuntimeStageShipsCuratedOperatorScripts:
    def test_exactly_one_copy_instruction_targets_app_scripts(self) -> None:
        copies = _scripts_dir_copies()
        assert len(copies) == 1, (
            f"expected exactly one COPY instruction targeting /app/scripts, "
            f"found {len(copies)}: {[args for _, _, args in copies]}"
        )

    def test_the_scripts_copy_ships_exactly_the_curated_set_no_more_no_less(
        self,
    ) -> None:
        [(_, _, args)] = _scripts_dir_copies()
        assert len(args) >= 2, f"COPY has no sources: {args}"
        sources, dest = args[:-1], args[-1]
        assert dest == "/app/scripts/", f"unexpected destination: {dest!r}"

        # Every source must be a plain top-level file directly under
        # scripts/ — not a subdirectory (e.g. scripts/dhm_precip/) and not
        # nested (e.g. scripts/sub/foo.py).
        for src in sources:
            assert src.startswith("scripts/") and src.count("/") == 1, (
                f"curated COPY source must be a top-level scripts/<file>, got {src!r}"
            )

        shipped = {src.removeprefix("scripts/") for src in sources}
        assert shipped == _CURATED_SCRIPTS, (
            f"curated COPY ships {sorted(shipped)}, expected exactly "
            f"{sorted(_CURATED_SCRIPTS)}\n"
            f"missing: {sorted(_CURATED_SCRIPTS - shipped)}\n"
            f"extra: {sorted(shipped - _CURATED_SCRIPTS)}"
        )

    def test_the_scripts_copy_is_chowned_to_the_app_user(self) -> None:
        [(start, _, _)] = _scripts_dir_copies()
        first_line = _dockerfile_lines()[start]
        assert first_line.strip().startswith("COPY"), (
            f"expected COPY, got {first_line!r}"
        )
        assert "--chown=app:app" in first_line, (
            f"curated-scripts COPY must be --chown=app:app: {first_line!r}"
        )

    def test_the_scripts_copy_is_in_the_runtime_stage_not_the_builder(self) -> None:
        text = _dockerfile_text()
        from_indices = [
            i for i, line in enumerate(text.splitlines()) if line.startswith("FROM ")
        ]
        assert len(from_indices) == 2, (
            "expected exactly one builder stage + one runtime stage"
        )
        runtime_stage_start = from_indices[1]

        [(start, _, _)] = _scripts_dir_copies()
        assert start > runtime_stage_start, (
            "curated-scripts COPY must be in the runtime stage, not the builder stage"
        )

    def test_no_copy_instruction_anywhere_ships_an_excluded_script_or_directory(
        self,
    ) -> None:
        """D1/D2's exclusions must not appear as a COPY *source* anywhere in
        the Dockerfile — not just within the curated /app/scripts COPY —
        guarding against a second, differently-destined COPY smuggling an
        excluded script or subtree (e.g. ``scripts/dhm_precip/``) in."""
        offending: list[str] = []
        for _, _, args in _copy_instructions():
            sources = args[:-1] if len(args) > 1 else []
            for src in sources:
                segments = {p for p in src.strip("/").split("/") if p}
                if segments & _EXCLUDED_NAMES:
                    offending.append(src)
        assert not offending, (
            f"COPY sources reference excluded scripts/directories: {offending}"
        )
