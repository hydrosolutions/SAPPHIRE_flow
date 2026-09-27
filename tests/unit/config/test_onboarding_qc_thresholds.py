from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sapphire_flow.config.onboarding import OnboardingConfig, load_onboarding_config

if TYPE_CHECKING:
    from pathlib import Path

_BLOCK = (
    "[[onboarding.station_qc_thresholds]]\n"
    'tenant_code = "sapphire"\n'
    'code = "2135"\n'
    'network = "bafu"\n'
    'rule_id = "range_check"\n'
    'parameter = "discharge"\n'
    "time_step_seconds = 600\n"
    "thresholds = { value_max = 90000.0 }\n"
)


def _load(tmp_path: Path, text: str) -> OnboardingConfig | None:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return load_onboarding_config(path)


def test_parses_explicit_tenant_and_pending_networks(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        '[onboarding]\nqc_pending_networks = ["dhm"]\n' + _BLOCK,
    )
    assert cfg is not None
    assert cfg.qc_pending_networks == ("dhm",)
    assert cfg.station_qc_thresholds[0].tenant_code == "sapphire"
    assert cfg.station_qc_thresholds[0].thresholds == {"value_max": 90000.0}


@pytest.mark.parametrize(
    "replacement",
    [
        "",
        'tenant_code = ""\n',
        "tenant_code = 3\n",
    ],
)
def test_tenant_code_is_required(tmp_path: Path, replacement: str) -> None:
    block = _BLOCK.replace('tenant_code = "sapphire"\n', replacement)
    with pytest.raises(ValueError, match="tenant_code"):
        _load(tmp_path, '[onboarding]\ntenant = "sapphire"\n' + block)


@pytest.mark.parametrize(
    "original,replacement,match",
    [
        ("90000.0", '"90000"', "thresholds"),
        ("90000.0", "nan", "finite numeric"),
        ("value_max", "max_delta", "threshold"),
        ("time_step_seconds = 600", "time_step_seconds = 0", "time_step_seconds"),
    ],
)
def test_invalid_block_fails_at_boundary(
    tmp_path: Path, original: str, replacement: str, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        _load(tmp_path, "[onboarding]\n" + _BLOCK.replace(original, replacement))


def test_duplicate_declaration_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _load(tmp_path, "[onboarding]\n" + _BLOCK + _BLOCK)


def test_overlay_rejects_threshold_keys_but_allows_tenant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "base.toml"
    base.write_text("[onboarding]\n" + _BLOCK)
    overlay = tmp_path / "overlay.toml"
    overlay.write_text('[onboarding]\ntenant = "other"\n')
    monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", str(overlay))
    cfg = load_onboarding_config(base)
    assert cfg is not None
    assert cfg.tenant_code == "other"
    assert cfg.station_qc_thresholds[0].tenant_code == "sapphire"

    for key, value in (
        ("qc_pending_networks", '["dhm"]'),
        ("station_qc_thresholds", "[]"),
    ):
        overlay.write_text(f"[onboarding]\n{key} = {value}\n")
        with pytest.raises(ValueError, match=key):
            load_onboarding_config(base)
