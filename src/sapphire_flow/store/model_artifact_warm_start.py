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

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NoReturn, assert_never

import sqlalchemy as sa
import structlog

from sapphire_flow.db.metadata import model_artifact_warm_start
from sapphire_flow.exceptions import ConfigurationError

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
        check_config_provenance(self.base_config_path, self.base_config_unknown_reason)
        check_params_provenance(self.base_params_path, self.base_params_unknown_reason)


def check_config_provenance(path: str | None, reason: str | None) -> None:
    """The config half of `WarmStartRecord`'s invariant, callable on its own.

    Plan 405 T1 — the flow validates a RESOLVED `(path, sha, reason)` triple
    BEFORE training, when no artifact has been stored yet and raising costs
    nothing. It must apply the SAME rule the record enforces, so this is the one
    definition both use: ⛔ a hand-rolled copy in the flow would drift from
    `__post_init__`, and the drift would only surface as a crash after a
    successful train.
    """
    if path is None and not reason:
        raise ValueError(
            "base_config_path is NULL without a reason — record WHY it is "
            "unknown (Plan 399 § 13); an unexplained NULL is how provenance "
            "becomes unrecoverable"
        )
    if path is not None and reason is not None and reason == "":
        raise ValueError(
            "base_config_path and base_config_unknown_reason cannot both be set — "
            "a known path is not unknown"
        )
    if path is not None and reason:
        raise ValueError(
            "base_config_path and base_config_unknown_reason cannot both be set — "
            "a known path is not unknown"
        )


def check_params_provenance(path: str | None, reason: str | None) -> None:
    """The params half of the same invariant. See `check_config_provenance`."""
    if path is None and not reason:
        raise ValueError(
            "base_params_path is NULL without a reason — record WHY it is "
            "unknown (Plan 399 § 5a); UNKNOWN and known-absent are "
            "different states"
        )
    if path is not None and reason is not None and reason == "":
        raise ValueError(
            "base_params_path and base_params_unknown_reason cannot both be set — "
            "a known path is not unknown"
        )
    if path is not None and reason:
        raise ValueError(
            "base_params_path and base_params_unknown_reason cannot both be set — "
            "a known path is not unknown"
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


_DonorKind = Literal["imported", "verified_retrain"]


def _donor_kind_prose(kind: _DonorKind) -> str:
    """Prose for a donor class, EXHAUSTIVELY — `assert_never` is the point.

    ⛔ A `dict[_DonorKind, str]` does NOT give this: a dict literal missing a
    member is still a well-typed `dict`, so pyright reports nothing and the gap
    surfaces as a runtime `KeyError` inside a `NoReturn` helper — while composing
    a refusal message, the worst possible moment. *Measured: adding a third member
    to `_DonorKind` left the dict version at "0 errors". The `match` +
    `assert_never` form fails type-checking instead, which is what the comment
    claimed and the dict did not deliver.*
    """
    match kind:
        case "imported":
            return "imported"
        case "verified_retrain":
            return "a SAP3 retrain, verified when it was produced"
    assert_never(kind)


def _refuse_changed_template(
    *,
    base_artifact_id: ArtifactId,
    donor_kind: _DonorKind,
    recorded_hash: str,
    installed_hash: str,
    installed_path: str | None,
) -> NoReturn:
    """Plan 405 T2's refusal, shared by both comparable donor classes.

    ⛔ One message, two call sites: a second copy would drift, and T2 requires
    BOTH hashes named — an operator who cannot see which side moved cannot tell
    whether the template or the donor is the surprise.
    """
    kind = _donor_kind_prose(donor_kind)
    where = (
        installed_path
        if installed_path is not None
        else "the installed config template (no path supplied)"
    )
    raise ConfigurationError(
        "refusing to retrain: the installed config template does not match the "
        f"one this donor was built from. donor artifact {base_artifact_id} "
        f"({kind}) recorded config hash {recorded_hash}, while {where} "
        f"hashes to {installed_hash}. Fine-tuning across a changed template "
        "would mix two configurations without recording that it happened."
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
    HASH is therefore only ever COMPARED against one the donor itself
    recorded — never adopted as the donor's identity. ⚠️ *The installed PATH is a
    different matter: on a verified match it IS recorded as the donor's config
    path, because that is the only path available (provenance stores none). An
    earlier wording said "the installed values are never adopted", which was true
    of the hash and false of the path.*

    ⭐ **Plan 405 T2 — a changed template is REFUSED** with `ConfigurationError`
    naming both hashes. *399 promised this comparison in a docstring and never
    made it; 405 § 1 charges that, and this is where the promise is kept.*

    **WHEN the comparison is valid** — load-bearing, and an earlier version of
    this function got it wrong in the PERMISSIVE direction:

    * **imported donor** — `model_artifact_provenance.config_hash` IS its own
      config's hash. Comparable.
    * **SAP3 retrain whose own record was VERIFIED** (`base_config_path` set and
      `base_config_unknown_reason` NULL) — comparable too. ⭐ *Because THIS check
      refused its retrain unless the template matched, its carried-forward hash
      necessarily describes the config it was actually built with.*
    * **SAP3 retrain with a NULL path** (pre-T2, or never verifiable) — ⛔ NOT
      comparable: the carried hash describes an ANCESTOR, so refusing on it would
      refuse on evidence about a different artifact. Genuinely UNKNOWN ⇒
      NULL-with-reason, which is T2's "Out" bullet.

    ⛔ *The middle case was initially exempted as well, on the premise that a
    carried hash never describes its own donor. That premise holds only for the
    last case: T2's own refusal makes it FALSE for a donor produced THROUGH T2 —
    and that is exactly the donor whose path is non-NULL. Exempting it left a
    changed template unrefused from generation 2 onward, writing a path, a hash
    the file no longer produces, and `reason=None` meaning "nothing is missing" —
    a verified-looking lie. Caught by both T2 cross-checks.*
    """
    from sapphire_flow.store.model_artifact_provenance import fetch_artifact_provenance

    provenance = fetch_artifact_provenance(conn, base_artifact_id)
    if provenance is not None and provenance.config_hash:
        # An imported donor: its HASH is recorded, but provenance stores no
        # PATH (`model_artifact_provenance` carries source_repository,
        # source_commit, config_hash, imported_at, imported_by, notes). So the
        # only available path is the installed one, and it is meaningful ONLY
        # while the hashes match — which is now CHECKED, immediately below.
        #
        # 🔴 THE COMPARISON COMES FIRST, before any question of what to record.
        # ⛔ It was originally placed after the path checks, so a donor whose hash
        # DIFFERED escaped the refusal whenever no path was supplied — a missing
        # path does not make two known hashes unknown. Caught by a T2 cross-check.
        if (
            installed_config_sha256 is not None
            and installed_config_sha256 != provenance.config_hash
        ):
            _refuse_changed_template(
                base_artifact_id=base_artifact_id,
                donor_kind="imported",
                recorded_hash=provenance.config_hash,
                installed_hash=installed_config_sha256,
                installed_path=installed_config_path,
            )
        if installed_config_path is None:
            # ⛔ Do NOT return a NULL path with no reason: `WarmStartRecord`
            # rejects that, and rightly — an unexplained NULL is indis-
            # tinguishable from a lost one. The donor's identity IS known here,
            # so say what is missing.
            return (
                None,
                provenance.config_hash,
                "donor's config hash is recorded in its provenance, but no "
                "installed config path was supplied to pair with it; "
                "provenance stores no path of its own",
            )
        if installed_config_sha256 is None:
            # ⛔ A path we cannot VERIFY is worse than no path: recording it
            # would name a configuration this donor may never have used, which
            # is 399 § 13's trap and T2's first "Out" bullet. The model declared
            # no hash, so the pairing cannot be checked — say so and keep NULL.
            return (
                None,
                provenance.config_hash,
                "donor's config hash is recorded in its provenance, but the "
                "installed model declares no config hash to pair with it, so "
                "the supplied path could not be VERIFIED to name the donor's "
                "configuration; recording it unverified would name a config "
                "this donor may never have used",
            )
        # Hashes agree (both known, compared above): the installed path is the
        # donor's config path, verified.
        return installed_config_path, provenance.config_hash, None

    inherited = fetch_warm_start(conn, base_artifact_id)
    if inherited is not None and inherited.base_config_sha256:
        if (
            inherited.base_config_path is not None
            and inherited.base_config_unknown_reason is not None
        ):
            # 🔴 A CONTRADICTORY record: a path AND a reason saying the config is
            # unknown.
            #
            # ⛔ **No PRODUCTION path produces it.** `record_warm_start`'s only
            # production caller is `PgWarmStartWriter.record`, reached solely from
            # the flow, which writes verbatim the triple this resolver returned —
            # and every exit here returns `(path, sha, None)` or `(None, sha,
            # reason)`, never both. ⚠️ *But the store API ACCEPTS the shape and a
            # test writes it deliberately, which is how the branch below is
            # pinned; `check_config_provenance` only rejects NULL-WITHOUT-reason,
            # and no DB CHECK covers the pair. Unreachable in production, not
            # impossible — the two T2 reviewers split on the earlier wording "not
            # producible by any writer", one reading it as production-only and one
            # literally. Both readings are satisfied by saying which.*
            #
            # Handled explicitly because the alternative is the exact failure this
            # task was fixing: skipping the comparison AND carrying the path
            # forward with `reason=None` would UPGRADE an explicitly unverified
            # record into a verified-looking one. The donor's own record says its
            # config is unknown, so its path is not a verified identity and is not
            # inherited.
            return (
                None,
                inherited.base_config_sha256,
                (
                    "donor's own warm-start record is self-contradictory: it "
                    "carries a config path AND a reason saying the config is "
                    f"unknown ({inherited.base_config_unknown_reason}). Its path "
                    "is therefore NOT treated as a verified identity and is not "
                    "inherited, and its hash could not be checked against the "
                    "installed template."
                ),
            )
        if inherited.base_config_path is not None:
            # 🔴 A donor whose OWN record was verified at its own time — a path,
            # and no "unknown" reason (the contradictory shape returned above).
            # ⭐ Its retrain was itself refused unless the template matched, so
            # this carried-forward hash DOES describe the config it was built
            # with, and comparing against it is valid. ⛔ Without this, a changed
            # template passed unrefused from generation 2 onward while the row
            # still read as verified.
            if (
                installed_config_sha256 is not None
                and installed_config_sha256 != inherited.base_config_sha256
            ):
                _refuse_changed_template(
                    base_artifact_id=base_artifact_id,
                    donor_kind="verified_retrain",
                    recorded_hash=inherited.base_config_sha256,
                    installed_hash=installed_config_sha256,
                    installed_path=installed_config_path,
                )
            # The donor has a real path: it carries forward with its hash, and no
            # reason is owed because nothing is missing.
            return (
                inherited.base_config_path,
                inherited.base_config_sha256,
                None,
            )
        # Plan 405 T1 — the donor's path is NULL, so this one is too, and a NULL
        # path MUST carry a reason (`WarmStartRecord` rejects an unexplained one,
        # rightly).
        #
        # ⛔ The reason is DERIVED for THIS donor, never inherited verbatim. The
        # donor here is SAP3-produced and has no `model_artifact_provenance` row —
        # that is precisely why this branch was reached — so propagating an
        # imported ancestor's sentence ("its config hash is recorded in its
        # provenance") would assert something FALSE about it.
        return (
            None,
            inherited.base_config_sha256,
            (
                "donor was produced by SAP3 (a retrain) and its own warm-start "
                "record carries no config path, so none can be inherited. The "
                "hash recorded alongside is the one CARRIED FORWARD through that "
                "record — it identifies the config of the donor's own ancestor, "
                "not a hash computed for this donor. Not inferred from the "
                "installed template, which would name a configuration this donor "
                "may never have used."
            ),
        )

    # ⛔ TWO different donors reach here and the reason must not describe the
    # wrong one: no warm-start row at all, OR a row whose `base_config_sha256` is
    # NULL. Saying "nor produced with a warm-start record" to the second is false —
    # it HAS one, it just records no config hash.
    #
    # ⛔ NEITHER reason INFERS AN ERA, and two successive attempts got that wrong.
    # "pre-dates Plan 399 T4" is false of the commonest donor here — an artifact
    # SAP3 trained from scratch has neither row TODAY. Re-attaching it to the
    # import case was equally false: `import_external_artifact` has required a
    # declared config hash since Plan 157 (`fff634fa`, the SAME commit that created
    # `model_artifact_provenance` via migration 0048) and is the only production
    # writer of that table.
    #
    # ⛔ But NOT "no import of any era lands here" — that absolute was stated in
    # the same commit that documented its own counterexample, and both reviewers
    # caught it. The guard tests `is None` while this resolver gates on TRUTHINESS,
    # so an import declaring `config_hash=""` writes `""` and DOES land here. What
    # is true: no import declaring a NON-EMPTY hash reaches this point.
    why = (
        "it has a warm-start record, but that record carries no config hash, so "
        "no config identity was ever captured for it"
        if inherited is not None
        else "it has no warm-start record, and no provenance row recording a "
        "usable config hash: either no provenance row exists, or one exists whose "
        "hash is absent or empty. An artifact SAP3 trained from scratch has "
        "neither row; a provenance row written directly, or an import that "
        "declared an EMPTY config hash, can also reach this point"
    )
    return (
        None,
        None,
        f"donor has no recorded config identity: {why}. Not inferred from the "
        "installed template — that would name a configuration the donor may "
        "never have used.",
    )


def resolve_donor_params(
    conn: sa.Connection, base_artifact_id: ArtifactId
) -> tuple[str | None, str | None]:
    """Plan 405 T6 / D1(b) — the donor's TRAINING-PARAMS identity.

    Returns `(path, reason)`. ⚖️ **The path is ALWAYS NULL, by decision.** The
    column asks WHERE a params file is; no donor class has one. What varies — and
    what 399 got wrong by using ONE constant for all of them — is the REASON, and
    T6's rule is that it must be true of the donor in hand.

    ⚖️ **D1's THREE donor classes, plus TWO exceptional observed states.** D1
    classifies imported, retrain-with-content and retrain-with-`{}`, and that
    three-way split stands — the empty-config class is the one D1 exists to settle.
    ⛔ *Not "five classes correcting three": an independent review of the plan ruled
    that rewriting it that way would recast a deliberate decision as my correction,
    and it was right.* The two states below are ones the code must SURVIVE, not a
    reclassification, and each needs its own sentence because T6's rule forbids a
    reason false of the donor at hand:

    * **NEITHER row** — nothing here records what it was trained with. ⛔ *No
      origin is inferred. An earlier version of this branch said "trained from
      scratch by SAP3 or predates provenance capture", which INFERS an origin from
      an absence: an artifact written directly through the artifact store has
      neither row too. Caught in review — and it is the same inference-from-absence
      error this plan has already corrected twice.*
    * **BOTH rows** — contradictory origins. ⛔ *The first version checked
      provenance FIRST and so called such a donor externally trained without ever
      looking at the conflicting warm-start evidence. Nothing in the schema
      excludes the state (no constraint in `0048` or `0060`), so it is named rather
      than assumed away — the same treatment `resolve_donor_config` gives its own
      contradictory record.*
    """
    from sapphire_flow.store.model_artifact_provenance import fetch_artifact_provenance

    provenance = fetch_artifact_provenance(conn, base_artifact_id)
    inherited = fetch_warm_start(conn, base_artifact_id)

    if provenance is not None and inherited is not None:
        return (
            None,
            "donor carries BOTH an import provenance row AND a warm-start record "
            "— contradictory origins, and nothing here can say which describes "
            "its training params. Recorded as UNKNOWN rather than asserting "
            "either one.",
        )
    if provenance is not None:
        return (
            None,
            "donor was IMPORTED into SAP3: it was trained outside this system, "
            "its training params were never recorded here, and no params file "
            "path exists to record. Genuinely unknown.",
        )

    if inherited is not None:
        if not isinstance(inherited.run_config, Mapping) or any(
            not isinstance(key, str) for key in inherited.run_config
        ):
            return (
                None,
                "donor warm-start record carries a malformed run_config, so "
                "nothing here can say whether configuration overrides were "
                "supplied. Recorded as UNKNOWN rather than asserting a known-"
                "empty or known-nonempty config.",
            )
        if inherited.run_config:
            return (
                None,
                "donor is a SAP3 retrain that WAS GIVEN a configuration. No "
                "params FILE exists — the settings are stored as values, not as "
                "a file — and they are reachable through this row's "
                "base_artifact_id.",
            )
        # ⚖️ D1: `{}` is KNOWN-EMPTY, not unknown. The flow normalises "nothing
        # supplied" to it deliberately, so the row accurately records what the
        # model was given. ⛔ Calling this unknown relabels a known fact, which is
        # the defect § 4 already has.
        return (
            None,
            "donor is a SAP3 retrain that was given NO configuration overrides: "
            "a KNOWN-EMPTY run config, not an unknown one. The empty mapping "
            "itself is recorded on the donor's row, reachable through this row's "
            "base_artifact_id. ⛔ Not 'unknown' — the caller supplied nothing and "
            "that is a fact, not a gap.",
        )

    return (
        None,
        "donor has neither a warm-start record nor a provenance row, so nothing "
        "recorded here says what it was trained with, and no params file path "
        "exists. No origin is inferred from that absence alone.",
    )


class PgWarmStartWriter:
    """Thin flow-facing adapter around `record_warm_start` — the
    ``warm_start_writer`` object `train_models_flow` calls right after storing a
    RETRAINED artifact.

    Mirrors `store/model_artifact_lineage.py::PgArtifactLineageWriter`
    deliberately: production wiring only, and tests inject a fake with the same
    `.record(...)` shape. Not a widening of the `ModelArtifactStore` Protocol.
    """

    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def record(self, record: WarmStartRecord) -> None:
        record_warm_start(self._conn, record)

    def resolve_donor_params(
        self, base_artifact_id: ArtifactId
    ) -> tuple[str | None, str | None]:
        return resolve_donor_params(self._conn, base_artifact_id)

    def resolve_donor_config(
        self,
        base_artifact_id: ArtifactId,
        *,
        installed_config_path: str | None = None,
        installed_config_sha256: str | None = None,
    ) -> tuple[str | None, str | None, str | None]:
        return resolve_donor_config(
            self._conn,
            base_artifact_id,
            installed_config_path=installed_config_path,
            installed_config_sha256=installed_config_sha256,
        )
