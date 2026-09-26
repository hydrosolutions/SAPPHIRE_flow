from __future__ import annotations

from datetime import timedelta

import pytest

from sapphire_flow.types.domain import QcRuleParams, QcRuleSet


def _rule(
    *,
    rule_id: str = "range_check",
    rule_version: str = "1.0.0",
    network: str | None = None,
) -> QcRuleParams:
    return QcRuleParams(
        rule_id=rule_id,  # type: ignore[arg-type]
        rule_version=rule_version,
        parameter="discharge",
        time_step=timedelta(days=1),
        thresholds={"value_min": 0.0, "value_max": 100.0},
        network=network,
    )


def test_network_specific_rule_replaces_generic_for_same_selector() -> None:
    generic = _rule(rule_version="generic-v1")
    dhm = _rule(rule_version="dhm-v1", network="dhm")
    rule_set = QcRuleSet(version="set-v1", rules=(generic, dhm))

    assert rule_set.rules_for("discharge", timedelta(days=1), network="dhm") == (dhm,)
    assert rule_set.rules_for("discharge", timedelta(days=1), network="bafu") == (
        generic,
    )
    assert rule_set.rules_for("discharge", timedelta(days=1)) == (generic,)


def test_generic_rule_still_applies_when_network_has_no_specific_rule() -> None:
    generic = _rule()
    other = _rule(rule_id="spike", network="dhm")
    rule_set = QcRuleSet(version="set-v1", rules=(generic, other))

    assert rule_set.rules_for("discharge", timedelta(days=1), network="dhm") == (
        generic,
        other,
    )


def test_same_selector_can_keep_distinct_rule_versions() -> None:
    old = _rule(rule_id="frozen_sensor", rule_version="1.0")
    new = _rule(rule_id="frozen_sensor", rule_version="1.1")
    rule_set = QcRuleSet(version="set-v1", rules=(old, new))

    assert rule_set.rules_for("discharge", timedelta(days=1)) == (old, new)


def test_duplicate_full_rule_identity_is_rejected() -> None:
    rule = _rule()

    with pytest.raises(ValueError, match="duplicate QC rule identity"):
        QcRuleSet(version="set-v1", rules=(rule, rule))
