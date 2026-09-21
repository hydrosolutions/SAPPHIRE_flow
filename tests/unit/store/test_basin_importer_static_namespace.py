"""Plan 306 T1 — a package-imported basin is in the wrong namespace.

Every aquacast model declares ``StaticNaming.CARAVAN``, which resolves a
declared name to a ``caravan:``-prefixed key with **no bare fallback**
(``types/enums.py``, ``services/caravan_statics.py``). The
``basin-static-artifact/v1`` importer writes the package's parquet columns
verbatim, unprefixed. The two do not meet.

⚠️ **The discriminating evidence is a TWO-PATH COMPARISON, not the text of an
error.** No code emits a ``caravan:``-prefixed key on a miss — the miss is
reported in *declared* names — so a test keyed on error text would be
asserting something the code never says. Instead: the *same* model and the
*same* declared names are resolved against a basin whose attributes arrived by
each of the two import paths, and the outcomes must differ.

⚠️ **The END-STATE proof lives in the integration suite**, not here — see
``tests/integration/services/test_basin_importer.py``. T2 namespaces at IMPORT
time, so a unit test that hands RAW columns to the resolver is measuring the
wrong seam: it could only go green by weakening the resolver, which T2's Out
forbids. This file keeps the two things that ARE unit-level — that the fixture
still carries every alias target, and that the Caravan control path resolves.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import pytest

from sapphire_flow.services.caravan_statics import (
    CARAVAN_ALIAS,
    CARAVAN_PREFIX,
    resolve_shared_static_frame,
)
from sapphire_flow.types.enums import StaticNaming

if TYPE_CHECKING:
    from collections.abc import Mapping

_FIXTURE = Path("tests/fixtures/basin_static/nepal-dhm-basins")

# Direct (unaliased) declared names — the package carries these under the same
# spelling the model uses, so they exercise the OTHER half of D15's rule.
_DIRECT_NAMES = ("area", "p_mean")


class _CaravanModel:
    """A synthetic model declaring the CARAVAN naming regime.

    ⚠️ **Deliberately synthetic rather than `cmal_small`.** The real model
    lives behind the optional ``aquacast`` extra and is not discoverable in a
    plain dev or CI environment (measured: ``discover_models()`` returns six
    models, none of them aquacast). A unit test must not require an optional
    dependency. The declared set below is taken from the REAL alias table, so
    all 23 aliased names are exercised — a stronger check than a hand-picked
    subset, and the same mapping `cmal_small` relies on.
    """

    static_naming: ClassVar[StaticNaming] = StaticNaming.CARAVAN

    def __init__(self, names: tuple[str, ...]) -> None:
        self._names = names

    @property
    def data_requirements(self) -> Any:
        names = self._names

        class _Req:
            static_features = frozenset(names)

        return _Req()


def _declared_names() -> tuple[str, ...]:
    return (*sorted(CARAVAN_ALIAS), *_DIRECT_NAMES)


def _package_columns() -> dict[str, float]:
    """The fixture's own static row, as the importer writes it today: the
    parquet column names, unprefixed."""
    import polars as pl

    frame = pl.read_parquet(_FIXTURE / "static_attributes.parquet")
    row = frame.head(1).to_dicts()[0]
    return {k: v for k, v in row.items() if k != "gauge_id" and v is not None}


def _caravan_columns(raw: Mapping[str, float]) -> dict[str, float]:
    """The same values as written by the Plan 155/188 Caravan path, which
    namespaces every column on the way in (`store/caravan_import.py`)."""
    return {f"{CARAVAN_PREFIX}{col}": val for col, val in raw.items()}


class TestPackageImportedBasinResolvesItsDeclaredStatics:
    def test_the_fixture_supplies_every_declared_name(self) -> None:
        """Guards the test itself: if the fixture stopped carrying one of the
        alias targets, the comparison below would 'pass' for the wrong reason
        on both paths."""
        raw = _package_columns()

        missing = [
            CARAVAN_ALIAS.get(name, name)
            for name in _declared_names()
            if CARAVAN_ALIAS.get(name, name) not in raw
        ]

        assert missing == [], f"fixture no longer carries {missing}"

    def test_a_caravan_imported_basin_resolves_every_declared_name(self) -> None:
        """The control. The same model and the same declared names resolve
        when the attributes arrived by the Caravan path."""
        names = _declared_names()
        model = _CaravanModel(names)
        attributes = _caravan_columns(_package_columns())

        frame = resolve_shared_static_frame(attributes, [model])

        unresolved = [name for name in names if frame.get(name) is None]
        assert unresolved == [], f"control path failed to resolve {unresolved}"


@pytest.mark.parametrize("name", sorted(CARAVAN_ALIAS))
def test_every_alias_is_exercised_by_the_fixture(name: str) -> None:
    """One case per alias, so a regression names the feature it broke rather
    than reporting '23 of 25 missing'."""
    raw = _package_columns()

    assert CARAVAN_ALIAS[name] in raw
