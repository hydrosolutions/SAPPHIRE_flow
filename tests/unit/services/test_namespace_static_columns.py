"""Plan 306 T2 — the namespacing operation both import paths now share.

Extracted from `store/caravan_import.py`, which has always namespaced the
Swiss path's columns, so the basin-package path can reuse it rather than grow
a second copy that drifts.

Three cases, and the third is the one that matters. Review established that
idempotent prefixing ALONE is insufficient: it maps ``for_pc_sse`` and
``caravan:for_pc_sse`` onto the same output key, so one value silently
overwrites the other and *column order* decides which survives. Separate
bare-only and prefixed-only fixtures both pass against that implementation —
only a mixed source with conflicting values catches it.
"""

from __future__ import annotations

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.caravan_statics import (
    CARAVAN_PREFIX,
    namespace_static_columns,
)


class TestNamespaceStaticColumns:
    def test_bare_columns_are_namespaced(self) -> None:
        assert namespace_static_columns({"for_pc_sse": 45.5, "area": 12.0}) == {
            f"{CARAVAN_PREFIX}for_pc_sse": 45.5,
            f"{CARAVAN_PREFIX}area": 12.0,
        }

    def test_already_prefixed_columns_are_left_alone(self) -> None:
        """A contract-compliant package may arrive already namespaced.
        Prefixing it again yields ``caravan:caravan:…``, which the resolver
        cannot resolve — so the basin would import cleanly and then resolve
        nothing, exactly the failure this plan exists to remove."""
        source = {f"{CARAVAN_PREFIX}for_pc_sse": 45.5}

        result = namespace_static_columns(source)

        assert result == source
        assert not any(
            key.startswith(f"{CARAVAN_PREFIX}{CARAVAN_PREFIX}") for key in result
        )

    def test_a_mixed_source_is_refused_not_silently_collapsed(self) -> None:
        """🔴 The case the other two cannot catch. Both spellings of one
        concept, with DIFFERENT values: an idempotent prefixer maps them onto
        one key and keeps whichever came last."""
        with pytest.raises(ConfigurationError, match="for_pc_sse"):
            namespace_static_columns(
                {"for_pc_sse": 45.5, f"{CARAVAN_PREFIX}for_pc_sse": 11.0}
            )

    def test_the_refusal_does_not_depend_on_column_order(self) -> None:
        """If the guard ran while building the output instead of on the raw
        column set, the answer would depend on which spelling came first."""
        forward = {"for_pc_sse": 45.5, f"{CARAVAN_PREFIX}for_pc_sse": 11.0}
        reverse = dict(reversed(list(forward.items())))

        for source in (forward, reverse):
            with pytest.raises(ConfigurationError, match="for_pc_sse"):
                namespace_static_columns(source)

    def test_a_mixed_source_is_refused_even_when_the_values_agree(self) -> None:
        """Equal values do not make the source understood — it still carries
        two shapes for one concept, and the next delivery may disagree."""
        with pytest.raises(ConfigurationError, match="for_pc_sse"):
            namespace_static_columns(
                {"for_pc_sse": 45.5, f"{CARAVAN_PREFIX}for_pc_sse": 45.5}
            )

    def test_the_refusal_names_every_colliding_column(self) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            namespace_static_columns(
                {
                    "for_pc_sse": 1.0,
                    f"{CARAVAN_PREFIX}for_pc_sse": 2.0,
                    "cly_pc_sav": 3.0,
                    f"{CARAVAN_PREFIX}cly_pc_sav": 4.0,
                }
            )

        message = str(excinfo.value)
        assert "for_pc_sse" in message
        assert "cly_pc_sav" in message

    def test_an_empty_source_is_not_an_error(self) -> None:
        assert namespace_static_columns({}) == {}


class TestTheSwissPathsBehaviourIsPreserved:
    """Plan 306 T2 extracted this operation FROM the Swiss import path, which
    the plan forbids disturbing. These pin what changed and what did not.

    ⚠️ Independent review 2026-09-21: the extraction is **not** byte-for-byte
    behaviour-preserving across every input the Swiss loader accepts. It was
    an unconditional comprehension; it is now idempotent-plus-refuse. Ordinary
    bare columns — the only shape a Caravan attributes parquet has ever
    produced — are unaffected. The two shapes that differ are recorded here
    rather than left implicit, because "we extracted it unchanged" was not
    quite true.
    """

    def test_ordinary_bare_columns_are_unchanged_by_the_extraction(self) -> None:
        """The real Swiss shape: HydroATLAS codes, none prefixed. This is what
        the extraction had to preserve, and does."""
        raw = {"for_pc_sse": 45.5, "cly_pc_sav": 12.0, "area": 3716.26}

        assert namespace_static_columns(raw) == {
            f"{CARAVAN_PREFIX}for_pc_sse": 45.5,
            f"{CARAVAN_PREFIX}cly_pc_sav": 12.0,
            f"{CARAVAN_PREFIX}area": 3716.26,
        }

    def test_an_already_prefixed_column_no_longer_becomes_double_prefixed(
        self,
    ) -> None:
        """CHANGED, deliberately. The old unconditional comprehension turned
        `caravan:area` into `caravan:caravan:area`, which no resolver can
        read — a silently unusable attribute. Nothing can depend on that."""
        assert namespace_static_columns({f"{CARAVAN_PREFIX}area": 12.0}) == {
            f"{CARAVAN_PREFIX}area": 12.0
        }

    def test_a_mixed_column_set_is_now_refused_instead_of_kept_apart(self) -> None:
        """CHANGED, deliberately. The old comprehension kept `area` and
        `caravan:area` as two distinct output keys — `caravan:caravan:area`
        and `caravan:area` — so both survived and neither was obviously
        wrong. Refusing is the safer answer: one source, two spellings of one
        concept, and no way to tell which the model should read."""
        with pytest.raises(ConfigurationError, match="area"):
            namespace_static_columns({"area": 12.0, f"{CARAVAN_PREFIX}area": 34.0})
