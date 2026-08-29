"""Shared circular diurnal-phase estimator (D5, M-A9 / Plan 216 M-A11).

TRACKED so every consumer imports the SAME functions from the SAME file on
a fresh checkout. Before this module existed, the M-A11 TIGGE-IFS screen
(`tigge_gauge_timing.py`) dynamically loaded these functions BY PATH from
`data/dhm_precip/figures/era5-timing/era5_gauge_timing_figure.py` — an
M-A6 output artefact under the gitignored `data/` tree, present only via a
local symlink into the sibling checkout. That made unit-test collection
(and CI, which never provisions `data/`) fail with `FileNotFoundError` on
any clean clone. The implementation here is unchanged byte-for-byte from
that file's original `harmonic_phase_h`/`same_day_branch`/`principal_branch`
/`band_of`/`npt_label` — `era5_gauge_timing_figure.py` now imports from
here too, so both analyses run the SAME code path (D5), not a fork.
"""

from __future__ import annotations

import numpy as np

HOUR_OF_DAY_PERIOD = 24
NPT_OFFSET_H = 5.75  # NPT = UTC + 5:45, exactly; never rounded.
BAND_EDGES: tuple[float, float] = (1000.0, 2000.0)
BAND_NAMES: tuple[str, str, str] = (
    "low (< 1,000 m)",
    "mid (1,000–2,000 m)",
    "high (≥ 2,000 m)",
)


def harmonic_phase_h(weights: np.ndarray) -> float:
    """Phase (hour of day) of the first diurnal harmonic of a 24-value cycle."""
    z = (weights * np.exp(1j * 2 * np.pi * np.arange(24) / 24)).sum()
    return float((np.angle(z) * 24 / (2 * np.pi)) % 24)


def harmonic_amplitude(weights: np.ndarray) -> float:
    z = (weights * np.exp(1j * 2 * np.pi * np.arange(24) / 24)).sum()
    return float(abs(z) / weights.sum())


def same_day_branch(lag_raw_h: float) -> float:
    """Signed offset on the branch (-18, +6] h — the "same convective day"
    reading. Near |offset| = 12 h the two cycles are antiphase and the
    SIGN is not identified."""
    return ((lag_raw_h + 18.0) % 24.0) - 18.0


def principal_branch(lag_raw_h: float) -> float:
    """Shortest-arc signed offset on (-12, +12]."""
    return ((lag_raw_h + 12.0) % 24.0) - 12.0


def central_arc_h(values: list[float], frac: float = 0.90) -> float:
    """Shortest circular arc containing `frac` of `values` (hours, period 24)."""
    v = np.sort(np.asarray(values) % 24.0)
    n = len(v)
    if n == 0:
        return float("nan")
    k = min(int(np.ceil(frac * n)), n)
    ext = np.concatenate([v, v + 24.0])
    idx = np.arange(n)
    return float((ext[idx + k - 1] - ext[idx]).min())


def cross_correlation_lag_h(gauge: np.ndarray, era5: np.ndarray) -> tuple[float, float]:
    """Integer-hour circular shift k maximising corr(gauge[h], era5[h+k])."""
    r = np.array(
        [float(np.corrcoef(gauge, np.roll(era5, -k))[0, 1]) for k in range(24)]
    )
    k = int(np.argmax(r))
    return same_day_branch(float(k)), float(r[k])


def band_of(elev_m: float, *, edges: tuple[float, float] = BAND_EDGES) -> int:
    """`edges` is an explicit parameter (not a module-global mutated in
    place) precisely so a sensitivity sweep over alternate band edges can
    call this with a different `edges` value without a `global` monkey-
    patch trick — which would silently stop working once callers in other
    modules held their own reference to this function."""
    return 0 if elev_m < edges[0] else (1 if elev_m < edges[1] else 2)


def npt_label(hour_utc: float) -> str:
    t = (hour_utc + NPT_OFFSET_H) % 24.0
    return f"{int(t):02d}:{int(round((t % 1) * 60)):02d}"
