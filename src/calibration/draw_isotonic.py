"""
src/calibration/draw_isotonic.py  —  R-1 Draw Calibration Work Package

Standalone, additive calibration layer for the draw-probability dimension.

This module is fully decoupled from the live prediction/settlement
pipeline. It consumes already-computed (H, D, A) probability triplets and
a model fitted from settled fixtures, and returns a recalibrated triplet.
It does NOT import, call, or modify:
  - the Elo model
  - the GBM model
  - the bivariate Poisson engine
  - WCOutcomePredictor / wc_intelligence_engine.py
  - ops/result_settler.py (settlement pipeline)
  - shadow_predictions.jsonl / shadow_settlements.jsonl (replay chain)

Feature flag (rollback mechanism):
    calibration_mode = "identity"       <- DEFAULT. Pass-through, exact
                                            no-op. Output == input.
    calibration_mode = "isotonic_draw"  <- apply fitted isotonic
                                            correction to the draw
                                            dimension only.

Rollback:
    Set calibration_mode back to "identity" (the default), or simply
    omit the argument. No data files are mutated by this module — it is
    a pure function library operating on in-memory probability triplets.
    There is nothing to "undo" on disk.

Environment override:
    DRAW_CALIBRATION_MODE=identity|isotonic_draw
    Unset or invalid -> falls back to "identity".
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Sequence

from sklearn.isotonic import IsotonicRegression

# ── feature flag ────────────────────────────────────────────────────────────
CALIBRATION_MODES = ("identity", "isotonic_draw")
DEFAULT_CALIBRATION_MODE = "identity"
CALIBRATION_MODE_ENV_VAR = "DRAW_CALIBRATION_MODE"

# Safety bounds applied to the calibrated draw probability so the model
# cannot collapse draw probability to 0% or saturate it to 100% on small
# samples. These are deliberately wide and do not encode WC-specific priors.
MIN_DRAW_FLOOR = 0.01   # 1%
MAX_DRAW_CEIL  = 0.80   # 80%


def get_calibration_mode() -> str:
    """Read the active calibration mode from the environment, defaulting to identity."""
    mode = os.environ.get(CALIBRATION_MODE_ENV_VAR, DEFAULT_CALIBRATION_MODE)
    return mode if mode in CALIBRATION_MODES else DEFAULT_CALIBRATION_MODE


@dataclass(frozen=True)
class IsotonicDrawModel:
    """A fitted, monotonic mapping predicted_D% (0-100) -> calibrated_D% (0-100)."""
    regressor: IsotonicRegression
    n_fit: int
    fit_hash: str

    def predict(self, draw_pct: float) -> float:
        """Map a single predicted draw probability (0-100 scale) to calibrated (0-100)."""
        d_raw = max(0.0, min(100.0, draw_pct)) / 100.0
        d_cal = float(self.regressor.predict([d_raw])[0])
        d_cal = max(MIN_DRAW_FLOOR, min(MAX_DRAW_CEIL, d_cal))
        return d_cal * 100.0


def fit_isotonic_draw(samples: Sequence[tuple[float, bool]]) -> IsotonicDrawModel:
    """
    Fit an isotonic regression mapping predicted draw probability -> observed
    draw frequency, using settled fixtures only.

    samples: sequence of (predicted_draw_probability_pct, actual_was_draw)
             e.g. [(19.9, False), (17.5, True), ...]  — pulled from
             shadow_settlements.jsonl (read-only).

    Deterministic: identical input multiset always yields an identical model
    (sklearn's isotonic regression has no internal randomness).
    """
    if len(samples) < 2:
        raise ValueError(
            f"isotonic_draw requires at least 2 settled fixtures to fit, got {len(samples)}"
        )

    xs = [max(0.0, min(100.0, s[0])) / 100.0 for s in samples]
    ys = [1.0 if s[1] else 0.0 for s in samples]

    regressor = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
    regressor.fit(xs, ys)

    fit_hash = hashlib.sha256(
        json.dumps(sorted(zip(xs, ys)), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    return IsotonicDrawModel(regressor=regressor, n_fit=len(samples), fit_hash=fit_hash)


def apply_calibration(
    ph: float, pd: float, pa: float,
    *,
    mode: str = DEFAULT_CALIBRATION_MODE,
    model: IsotonicDrawModel | None = None,
) -> tuple[float, float, float]:
    """
    Apply the calibration layer to one (H, D, A) triplet (each 0-100 scale,
    summing to ~100). Returns a recalibrated triplet that also sums to 100.

    mode="identity"      -> returns (ph, pd, pa) unchanged, byte-for-byte.
    mode="isotonic_draw" -> recalibrates D only, renormalises H and A
                            proportionally so H+D+A stays 100.
    """
    if mode not in CALIBRATION_MODES:
        raise ValueError(f"unknown calibration_mode: {mode!r} (expected one of {CALIBRATION_MODES})")

    if mode == "identity":
        return ph, pd, pa

    # mode == "isotonic_draw"
    if model is None:
        raise ValueError("isotonic_draw mode requires a fitted IsotonicDrawModel")

    d_cal = model.predict(pd)               # 0-100 scale
    remaining = 100.0 - d_cal

    h_plus_a = ph + pa
    if h_plus_a <= 0:
        h_cal = a_cal = remaining / 2.0
    else:
        h_cal = (ph / h_plus_a) * remaining
        a_cal = (pa / h_plus_a) * remaining

    # Renormalise to absorb floating-point drift — guarantees exact conservation.
    total = h_cal + d_cal + a_cal
    if total <= 0:
        return ph, pd, pa
    scale = 100.0 / total
    return h_cal * scale, d_cal * scale, a_cal * scale


def recompute_confidence(ph: float, pd: float, pa: float, elo_gap: float) -> float:
    """
    Read-only copy of the v3.0 confidence formula (see
    ops/result_settler.py::_confidence and
    src/model/wc_intelligence_engine.py). Duplicated intentionally so this
    module has zero import dependency on the protected prediction pipeline.
    Recomputing confidence from a calibrated triplet does not change the
    formula itself — only its inputs.
    """
    probs = sorted([ph, pd, pa], reverse=True)
    raw = (probs[0] * 0.75 + (probs[0] - probs[1]) * 0.55) * (0.85 + 0.15 * min(1.0, elo_gap / 300.0))
    return round(max(30.0, min(92.0, raw)), 1)


def outcome_from_probs(ph: float, pd: float, pa: float) -> str:
    """Argmax outcome label from a probability triplet."""
    best = max(("HOME_WIN", ph), ("DRAW", pd), ("AWAY_WIN", pa), key=lambda t: t[1])
    return best[0]


def is_monotonic_nondecreasing(model: IsotonicDrawModel, sample_points: Sequence[float]) -> bool:
    """Utility for tests/validation: confirms the fitted mapping never decreases."""
    ordered = sorted(sample_points)
    outputs = [model.predict(x) for x in ordered]
    return all(b >= a - 1e-9 for a, b in zip(outputs, outputs[1:]))
