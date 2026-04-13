---
status: DONE
created: 2026-04-04
completed: 2026-04-13
scope: implementation — SHA-256 hash verification for model artifacts
depends_on: []
---

# 018 — Model Artifact SHA-256 Verification

## Problem

`docs/standards/security.md` makes two concrete claims:

- OWASP A08 row: "Model artifacts verified by SHA-256 hash."
- Threat model table: "Tampered model files won't match their stored hash. Detected on next model load."

Neither is implemented. There is no `artifact_hash` column in `model_artifacts`, no
`hashlib` import anywhere in `src/`, and no hash verification in `store_artifact` or
`fetch_artifact`. The documented security control does not exist.

## Scope

Add SHA-256 hash-on-write / verify-on-read to the model artifact storage path.

### What was implemented

1. **Alembic migration 0022**: added `sha256_hash TEXT NOT NULL` column to `model_artifacts`.
2. **`PgModelArtifactStore.store_artifact`**: computes `hashlib.sha256(artifact_bytes).hexdigest()` before writing to disk, stores in DB alongside the artifact record.
3. **`PgModelArtifactStore._read_and_verify`**: private helper extracts the hash-on-read pattern. Used by all three fetch methods: `fetch_artifact`, `fetch_active_artifact`, and `fetch_active_artifact_for_station`. Raises `ArtifactIntegrityError` (subclass of `SapphireError`) on mismatch.
4. **`ModelArtifactRecord`**: `sha256_hash: str` field.
5. **`FakeModelArtifactStore`**: `fetch_active_artifact` delegates to `fetch_artifact` (which verifies). `fetch_active_artifact_for_station` delegates transitively.
6. **Tests**: `test_tampered_artifact_raises_integrity_error` (fetch_artifact), `test_tampered_active_artifact_raises_integrity_error` (fetch_active_artifact), `test_tampered_active_artifact_for_station_raises_integrity_error` (direct path), `test_tampered_active_artifact_for_station_group_raises_integrity_error` (group fallback path).

### What NOT to implement

- No changes to `serialize_artifact` / `deserialize_artifact` Protocols (format-agnostic, model-owned).
- No change to the pickle trust model — that is already covered by the Model Code Trust Boundary (security.md).
- No retroactive hashing of existing artifacts (migration sets column NOT NULL with a sentinel for existing rows, or backfills from disk).

## Design notes

- The hash protects against filesystem-level tampering between training and next load (the threat scenario security.md describes). It does not protect against in-process attacks (covered by the entry-point trust model).
- Cost: one `hashlib.sha256()` call per store and per fetch. Artifact sizes are small (< 100 KB for linear regression, < 50 MB for large ML models). Negligible overhead.
- `ArtifactIntegrityError` is defined in `exceptions.py` and documented in `conventions.md`.
