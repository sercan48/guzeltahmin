"""
ShadowPredictor — temporary paper-trading inference engine.

TEMPORARY INFRASTRUCTURE: Used ONLY for PERSONAL_SHADOW validation
(Phase 11 shadow mode). Auto-disables when real API-Football +
Betfair/Pinnacle live feeds are available (see _is_real_feed_available).

ISOLATION CONTRACT
------------------
* This module MUST NOT be imported by any production predictor.
* Production predictors (src/model/*, src/features/*) MUST NOT depend on
  this module.
* Enforcement: tests/test_shadow_isolation.py

REMOVAL PLAN (see bottom of file)
"""
from __future__ import annotations

import hashlib
import json
import os

# ---------------------------------------------------------------------------
# Elo lookup — WC 2026 participant teams, club-Elo approximations (June 2026)
# Keys are lowercase, normalised. Aliases cover common API name variants.
# ---------------------------------------------------------------------------
_ELO: dict[str, float] = {
    "argentina": 1955.0,
    "france": 1935.0,
    "spain": 1925.0,
    "england": 1910.0,
    "brazil": 1900.0,
    "portugal": 1890.0,
    "netherlands": 1870.0,
    "germany": 1860.0,
    "colombia": 1845.0,
    "morocco": 1840.0,
    "croatia": 1830.0,
    "italy": 1825.0,
    "uruguay": 1820.0,
    "switzerland": 1820.0,
    "senegal": 1800.0,
    "ivory coast": 1790.0,
    "côte d'ivoire": 1790.0,
    "cote d'ivoire": 1790.0,
    "united states": 1790.0,
    "usa": 1790.0,
    "japan": 1785.0,
    "mexico": 1780.0,
    "nigeria": 1770.0,
    "south korea": 1760.0,
    "korea republic": 1760.0,
    "ecuador": 1755.0,
    "canada": 1750.0,
    "australia": 1745.0,
    "algeria": 1740.0,
    "denmark": 1740.0,
    "austria": 1735.0,
    "ukraine": 1730.0,
    "poland": 1720.0,
    "serbia": 1715.0,
    "sweden": 1715.0,
    "cameroon": 1710.0,
    "wales": 1705.0,
    "ghana": 1700.0,
    "tunisia": 1695.0,
    "saudi arabia": 1680.0,
    "egypt": 1675.0,
    "iran": 1665.0,
    "costa rica": 1655.0,
    "panama": 1640.0,
    "jamaica": 1620.0,
    "curaçao": 1590.0,
    "curacao": 1590.0,
    "new zealand": 1570.0,
}

_DEFAULT_ELO = 1650.0  # fallback for unknown teams


def _elo(name: str) -> float:
    return _ELO.get(name.lower().strip(), _DEFAULT_ELO)


def _elo_win_prob(elo_a: float, elo_b: float) -> float:
    """Standard Elo expected score for team A."""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))


def _is_real_feed_available() -> bool:
    """True when both API-Football and a live-odds feed (Betfair/Pinnacle) are wired up."""
    return bool(
        os.getenv("API_FOOTBALL_KEY", "").strip()
        and os.getenv("ODDS_API_KEY", "").strip()
    )


class ShadowPredictor:
    """
    Lightweight, deterministic predictor for paper-trading observation.

    Engine  : Elo-based win probability + xG estimation
    Inputs  : team name strings (matched against _ELO table)
    Outputs : dict compatible with ops/wc_paper_shadow bulletin format

    Never imported by src/model/* or src/features/* (enforced by
    tests/test_shadow_isolation.py).
    """

    # Applied to all confidence values to remind observers this is not a
    # calibrated production model.
    _CONFIDENCE_DISCOUNT = 0.85

    # Elo gap below which a match is "too close to call" → no-bet flag.
    _NO_BET_ELO_GAP = 50

    def predict(self, home_name: str, away_name: str) -> dict:
        """
        Return a prediction dict for the shadow bulletin.

        Raises RuntimeError when a real live feed is detected so that
        callers notice the auto-disable condition.
        """
        if _is_real_feed_available():
            raise RuntimeError(
                "ShadowPredictor is disabled: real API-Football + live-odds "
                "feeds are active. Switch to the production inference pipeline."
            )

        elo_h = _elo(home_name)
        elo_a = _elo(away_name)

        p_home_raw = _elo_win_prob(elo_h, elo_a)
        p_away_raw = 1.0 - p_home_raw

        # Draw probability scales with matchup evenness:
        # max draw (~30 %) at even Elo, minimum 15 % floor for internationals.
        draw_share = max(0.15, 0.30 - 0.50 * abs(p_home_raw - 0.50))
        remainder = 1.0 - draw_share

        home_pct = round(p_home_raw * remainder * 100, 1)
        draw_pct = round(draw_share * 100, 1)
        away_pct = round(p_away_raw * remainder * 100, 1)

        if home_pct > away_pct and home_pct > draw_pct:
            raw_prediction = "HOME_WIN"
        elif away_pct > home_pct and away_pct > draw_pct:
            raw_prediction = "AWAY_WIN"
        else:
            raw_prediction = "DRAW"

        elo_diff = abs(elo_h - elo_a)
        raw_conf = min(90.0, 40.0 + elo_diff * 0.10)
        final_conf = round(raw_conf * self._CONFIDENCE_DISCOUNT, 1)

        xg_h = round(max(0.20, 1.15 + (elo_h - elo_a) * 0.003), 2)
        xg_a = round(max(0.20, 1.15 + (elo_a - elo_h) * 0.003), 2)

        is_no_bet = elo_diff < self._NO_BET_ELO_GAP

        return {
            "raw_prediction": raw_prediction,
            "final_confidence": final_conf,
            "home_win_prob": home_pct,
            "draw_prob": draw_pct,
            "away_win_prob": away_pct,
            "expected_goals_a": xg_h,
            "expected_goals_b": xg_a,
            "is_no_bet": is_no_bet,
            "market_note": "Elo-shadow" if not is_no_bet else "Too Close (shadow)",
            "elo_home": elo_h,
            "elo_away": elo_a,
        }

    @staticmethod
    def acceptance_hash(home: str, away: str, result: dict) -> str:
        """
        Deterministic SHA-256 fingerprint of a prediction.

        Covers the fields that change when prediction logic changes.
        Pin this value in tests/test_shadow_predictor.py as
        PINNED_GERMANY_CURACAO_HASH to detect unintended regressions.
        """
        payload = {
            "home": home.lower().strip(),
            "away": away.lower().strip(),
            "raw_prediction": result["raw_prediction"],
            "home_win_prob": result["home_win_prob"],
            "draw_prob": result["draw_prob"],
            "away_win_prob": result["away_win_prob"],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()


# ---------------------------------------------------------------------------
# SHADOW_REMOVAL_PLAN
# ---------------------------------------------------------------------------
# Trigger condition
#   API_FOOTBALL_KEY is set  AND  ODDS_API_KEY is set
#   (i.e. _is_real_feed_available() returns True)
#
# Step 1 — Delete these files:
#   ops/shadow_predictor.py              (this file)
#   tests/test_shadow_predictor.py
#   tests/test_shadow_isolation.py
#
# Step 2 — Update ops/wc_paper_shadow.py:
#   Replace shadow_predict(match) with the original production path:
#     from src.model.wc_three_tier_inference import (
#         extract_team_stats, get_expected_lineups)
#     from src.model.wc_ensemble_inference import run_ensemble_inference
#     from src.features.wc_confidence_calibrator import calibrate_confidence
#   (once the production mocks in wc_three_tier_inference are replaced with
#    real DB/API lookups)
#
# Step 3 — No changes needed to:
#   app/bot/predictions.py        (imports wc_paper_shadow functions only)
#   src/model/*                   (never touched shadow code)
#   src/features/*                (never touched shadow code)
#
# Step 4 — Verification:
#   pytest tests/test_wc_ensemble.py tests/test_wc_monte_carlo.py
#   grep -r "shadow_predictor" src/ ops/ app/   # must return empty
#   python scripts/trigger_shadow.py            # must use production path
# ---------------------------------------------------------------------------
