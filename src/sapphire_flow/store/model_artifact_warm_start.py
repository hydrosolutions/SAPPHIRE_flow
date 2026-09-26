# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Plan 399 T4 — warm-start provenance: what a retrained artifact came FROM.

A standalone helper + thin reader, mirroring `store/model_artifact_lineage.py`
and `store/model_artifact_provenance.py`: NOT a widening of the cross-cutting
`ModelArtifactStore` Protocol. Called right after the artifact is stored, on the
connection the calling flow task already has.

Two things this records that nothing else can:

* **the donor** — SAP3 chooses it (Plan 399 D1), so SAP3 knows it. FI cannot
  tell us, and for an artifact we did not produce it is unrecoverable after the
  fact.
* **the config THIS run used** — opaque to SAP3 by decision, but stored
  verbatim, so "which fine-tuning strategy produced this artifact?" is
  answerable later. Without it, "opaque for v1" becomes "unknowable forever",
  which is the failure already on `cmal_small`'s record.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
import structlog

from sapphire_flow.db.metadata import model_artifact_warm_start

if TYPE_CHECKING:
    from sapphire_flow.types.ids import ArtifactId

log = structlog.get_logger()


@dataclass(frozen=True, kw_only=True, slots=True)
class WarmStartRecord:
    """What a retrained artifact was derived from.

    Every `*_unknown_reason` field exists because UNKNOWN and known-absent are
    different states, and conflating them is how provenance quietly becomes
    unrecoverable. A NULL path with no reason is not acceptable.
    """

    artifact_id: ArtifactId
    base_artifact_id: ArtifactId
    run_config: dict[str, Any]
    base_config_path: str | None = None
    base_config_sha256: str | None = None
    base_config_unknown_reason: str | None = None
    base_params_path: str | None = None
    base_params_unknown_reason: str | None = None

    def __post_init__(self) -> None:
        if self.base_config_path is None and not self.base_config_unknown_reason:
            raise ValueError(
                "base_config_path is NULL without a reason — record WHY it is "
                "unknown (Plan 399 § 13); an unexplained NULL is how provenance "
                "becomes unrecoverable"
            )
        if self.base_params_path is None and not self.base_params_unknown_reason:
            raise ValueError(
                "base_params_path is NULL without a reason — record WHY it is "
                "unknown (Plan 399 § 5a); UNKNOWN and known-absent are "
                "different states"
            )


def record_warm_start(conn: sa.Connection, record: WarmStartRecord) -> None:
    conn.execute(
        sa.insert(model_artifact_warm_start).values(
            model_artifact_id=record.artifact_id,
            base_artifact_id=record.base_artifact_id,
            base_config_path=record.base_config_path,
            base_config_sha256=record.base_config_sha256,
            base_config_unknown_reason=record.base_config_unknown_reason,
            base_params_path=record.base_params_path,
            base_params_unknown_reason=record.base_params_unknown_reason,
            run_config=record.run_config,
        )
    )
    log.info(
        "warm_start.recorded",
        artifact_id=str(record.artifact_id),
        base_artifact_id=str(record.base_artifact_id),
        base_config_known=record.base_config_path is not None,
        base_params_known=record.base_params_path is not None,
    )


def fetch_warm_start(
    conn: sa.Connection, artifact_id: ArtifactId
) -> WarmStartRecord | None:
    """`None` means this artifact was trained fresh, not fine-tuned."""
    row = (
        conn.execute(
            sa.select(model_artifact_warm_start).where(
                model_artifact_warm_start.c.model_artifact_id == artifact_id
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return WarmStartRecord(
        artifact_id=row["model_artifact_id"],
        base_artifact_id=row["base_artifact_id"],
        run_config=dict(row["run_config"]),
        base_config_path=row["base_config_path"],
        base_config_sha256=row["base_config_sha256"],
        base_config_unknown_reason=row["base_config_unknown_reason"],
        base_params_path=row["base_params_path"],
        base_params_unknown_reason=row["base_params_unknown_reason"],
    )


def resolve_donor_config(
    conn: sa.Connection,
    base_artifact_id: ArtifactId,
    *,
    installed_config_path: str | None = None,
    installed_config_sha256: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Resolve the DONOR's config identity. Returns `(path, sha256, reason)`.

    Plan 399 § 13's decision table, in three cases:

    * **imported donor** — its `model_artifact_provenance` row carries the
      `config_hash` captured at import. Authoritative.
    * **produced by SAP3 with a warm-start record** — that record's own donor
      config carries forward.
    * **anything else** — ``(None, None, reason)``.

    ⛔ It NEVER falls back to `installed_config_*`, and that is the whole point.
    Hashing whatever template is on disk today satisfies a naive "the hash
    matches the file it names" check while naming the WRONG configuration
    whenever the template has changed since the donor was built. The installed
    values are accepted only to be COMPARED against the donor's recorded hash,
    and a mismatch is a refusal the caller makes — not something this resolver
    papers over.
    """
    from sapphire_flow.store.model_artifact_provenance import fetch_artifact_provenance

    provenance = fetch_artifact_provenance(conn, base_artifact_id)
    if provenance is not None and provenance.config_hash:
        # An imported donor: its hash is recorded. The path is the vendored
        # config's, but the HASH is the donor's own — the caller compares them.
        return installed_config_path, provenance.config_hash, None

    inherited = fetch_warm_start(conn, base_artifact_id)
    if inherited is not None and inherited.base_config_sha256:
        return (
            inherited.base_config_path,
            inherited.base_config_sha256,
            None,
        )

    return (
        None,
        None,
        "donor has no recorded config identity: it was neither imported (no "
        "provenance row) nor produced with a warm-start record, so it pre-dates "
        "Plan 399 T4. Not inferred from the installed template — that would name "
        "a configuration the donor may never have used.",
    )
