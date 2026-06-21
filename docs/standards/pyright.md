# Pyright type-checking — configuration policy

## What's configured

`pyrightconfig.json` at the repo root is authoritative. `typeCheckingMode`
is `"strict"` globally. An `executionEnvironments` carve-out silences
the six Unknown-cluster rules (`reportUnknownVariableType`,
`reportUnknownMemberType`, `reportUnknownArgumentType`,
`reportUnknownParameterType`, `reportMissingTypeArgument`,
`reportUnknownLambdaType`) inside `src/sapphire_flow/flows/`.

`[tool.pyright]` in `pyproject.toml` is NOT used — pyright gives the
JSON config precedence and silently ignores the TOML block when both
exist. Do not add a `_comment` key to `pyrightconfig.json`; pyright
1.1.408 rejects unknown top-level keys. This file is the documentation
surface instead.

## Why the flows/ carve-out

Prefect `@flow`/`@task` decorators erase types in ways pyright cannot
follow. Combined with pandas/xarray/numpy stub propagation noise, this
produces ~400 spurious errors that do not indicate real bugs. The
carve-out silences only the Unknown cluster — real-bug rules like
`reportArgumentType`, `reportAttributeAccessIssue`, `reportCallIssue`
still fire at full strength inside `flows/`. See Plan 069 §Context for
the empirical justification (1078 → 675 errors when the carve-out
landed; the 403-error reduction was all Unknown noise).

## Ratchet

CI runs `uv run pyright --outputjson src/` and pipes the result through
`tools/pyright_ratchet.py`, which compares per-file and total counts
against `tools/pyright_baseline.json`. A PR that raises any count above
the baseline fails CI. The baseline is shrink-only and updated by
deliberate commits.

## How to update the baseline

After fixing pyright errors, run:

```bash
uv run python tools/pyright_baseline.py
```

This regenerates `tools/pyright_baseline.json` with the live count.
Commit the updated baseline alongside the fix.

## References

- `docs/plans/069-pyright-backlog-cleanup.md` — the plan that
  established this configuration and ratchet.
- `pyrightconfig.json` — the authoritative config.
- `tools/pyright_ratchet.py` — the CI gating script.
