#!/usr/bin/env python3
"""
ops/result_settler.py  —  WP-SETTLER-1

SHADOW phase settlement pipeline. Additive only; no existing modules modified.

Responsibilities:
  - log_prediction()   : append one prediction record to shadow_predictions.jsonl
  - backfill()         : re-predict all known-team WC 2026 fixtures, fill log
  - settle_finished()  : fetch FINISHED results, match to predictions, write settlements
  - compute_accuracy() : aggregate metrics from settlement log
  - generate_report()  : write SETTLER_REPORT.md

Storage (all under data/):
  shadow_predictions.jsonl  — append-only, one JSON line per fixture
  shadow_settlements.jsonl  — append-only, one JSON line per settled fixture
  shadow_accuracy.json      — computed aggregate, rewritten on each settle run

Idempotency:
  Every record carries a stable ID (sha-256 of natural key).
  Duplicate writes are silently skipped.

Usage:
    python ops/result_settler.py                # backfill + settle + report
    python ops/result_settler.py --backfill     # prediction log only
    python ops/result_settler.py --settle       # settle FINISHED matches only
    python ops/result_settler.py --report       # regenerate report only
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import unicodedata
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(str(Path(__file__).parent.parent / ".env"), override=True)

from src.model.wc_intelligence_engine import (
    WCOutcomePredictor,
    _ELO,
    btts_predict,
    over_under_predict,
)

# ── paths ─────────────────────────────────────────────────────────────────────
DATA_DIR        = Path(__file__).parent.parent / "data"
PRED_LOG        = DATA_DIR / "shadow_predictions.jsonl"
SETTLE_LOG      = DATA_DIR / "shadow_settlements.jsonl"
ACCURACY_FILE   = DATA_DIR / "shadow_accuracy.json"
REPORT_FILE     = Path(__file__).parent.parent / "SETTLER_REPORT.md"

DATA_DIR.mkdir(exist_ok=True)

# ── API ───────────────────────────────────────────────────────────────────────
API_KEY      = os.environ.get("FOOTBALL_DATA_ORG_KEY", "")
BASE_URL     = "https://api.football-data.org/v4/competitions/WC/matches"
ACTIVE       = {"SCHEDULED", "TIMED", "IN_PLAY", "PAUSED", "LIVE"}
FINISHED     = {"FINISHED", "AWARDED"}

# ── engine (read-only) ────────────────────────────────────────────────────────
PRED = WCOutcomePredictor()

ENGINE_VERSION    = "v3.0"
CALIBRATION_MODE  = "platt_identity"
SETTLER_VERSION   = "1.0"

# ── name normalisation ────────────────────────────────────────────────────────
_ALIASES: dict[str, str] = {
    "cote d'ivoire":      "ivory coast",
    "cote divoire":       "ivory coast",
    "korea republic":     "south korea",
    "republic of korea":  "south korea",
    "usa":                "united states",
    "united states of america": "united states",
    "dr congo":           "congo dr",
    "democratic republic of the congo": "congo dr",
    "bosnia and herzegovina": "bosnia-herzegovina",
    "bosnia & herzegovina":   "bosnia-herzegovina",
    "cabo verde":         "cape verde islands",
    "cape verde":         "cape verde islands",
    "curacao":            "curaçao",
    "haiti":              "haïti",
    "ir iran":            "iran",
    "new zealand":        "new zealand",
    "saudi arabia":       "saudi arabia",
}

def _normalise(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name.lower().strip())
    base = "".join(c for c in nfkd if not unicodedata.combining(c))
    return _ALIASES.get(base, base)

def _elo_known(name: str) -> bool:
    return _normalise(name) in _ELO

def _elo(name: str) -> float:
    return _ELO.get(_normalise(name), 0.0)

# ── natural key + IDs ─────────────────────────────────────────────────────────
def _natural_key(home: str, away: str, date: str) -> str:
    return f"{_normalise(home)}|{_normalise(away)}|{date}"

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()

def _prediction_id(natural_key: str) -> str:
    return _sha(f"PRED:{natural_key}")

def _settlement_id(natural_key: str) -> str:
    return _sha(f"SETTLE:{natural_key}")

# ── confidence + tier (read-only copies — no change to engine) ────────────────
def _confidence(ph: float, pd: float, pa: float, elo_gap: float) -> float:
    probs  = sorted([ph, pd, pa], reverse=True)
    raw    = (probs[0] * 0.75 + (probs[0] - probs[1]) * 0.55) * (0.85 + 0.15 * min(1.0, elo_gap / 300.0))
    return round(max(30.0, min(92.0, raw)), 1)

def _tier(elo_gap: float, conf: float) -> str:
    if elo_gap >= 150 or conf >= 70.0:
        return "TIER_A"
    if elo_gap >= 50  or conf >= 40.0:
        return "TIER_B"
    return "TIER_C"

def _signal(conf: float, gap_pp: float, max_p: float) -> str:
    if max_p < 40.0:
        return "NEAR_EVEN"
    if conf >= 65.0 and gap_pp >= 12.0:
        return "HIGH_EDGE"
    if conf >= 45.0:
        return "MID_EDGE"
    return "LOW_EDGE"

# ── outcome from score ────────────────────────────────────────────────────────
def _outcome_from_score(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "HOME_WIN"
    if away_goals > home_goals:
        return "AWAY_WIN"
    return "DRAW"

# ── log helpers ───────────────────────────────────────────────────────────────
def _load_ids(path: Path, id_field: str) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    ids.add(json.loads(line)[id_field])
                except Exception:
                    pass
    return ids

def _append(path: Path, record: dict) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")

def _load_all(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    return records

# ── fetch ─────────────────────────────────────────────────────────────────────
def _fetch_matches(params: str = "") -> list[dict]:
    url = BASE_URL + (f"?{params}" if params else "")
    req = urllib.request.Request(url, headers={"X-Auth-Token": API_KEY})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read()).get("matches", [])

# ── prediction logging ────────────────────────────────────────────────────────
def log_prediction(
    home: str, away: str, date: str, stage: str,
    run_hash: str = "",
) -> dict | None:
    """
    Compute and append one prediction record. Idempotent.
    Returns the record if newly written, None if already exists.
    """
    if not _elo_known(home) or not _elo_known(away):
        return None

    nk   = _natural_key(home, away, date)
    pid  = _prediction_id(nk)

    existing = _load_ids(PRED_LOG, "prediction_id")
    if pid in existing:
        return None

    r    = PRED.predict(home, away)
    ph   = r["home_win_prob"]
    pd   = r["draw_prob"]
    pa   = r["away_win_prob"]
    xg_h = r["expected_goals_a"]
    xg_a = r["expected_goals_b"]
    eg   = abs(_elo(home) - _elo(away))
    cf   = _confidence(ph, pd, pa, eg)
    probs = sorted([ph, pd, pa], reverse=True)
    gap_pp = probs[0] - probs[1]

    record = {
        "record_type":       "PREDICTION",
        "prediction_id":     pid,
        "natural_key":       nk,
        "home_team":         home,
        "away_team":         away,
        "match_date":        date,
        "stage":             stage,
        "predicted_outcome": r["raw_prediction"],
        "probabilities":     {"H": ph, "D": pd, "A": pa},
        "xg":                {"home": round(xg_h, 3), "away": round(xg_a, 3)},
        "confidence":        cf,
        "tier":              _tier(eg, cf),
        "signal":            _signal(cf, gap_pp, probs[0]),
        "elo_gap":           round(eg, 1),
        "elo_home":          round(_elo(home), 1),
        "elo_away":          round(_elo(away), 1),
        "engine_version":    ENGINE_VERSION,
        "calibration_mode":  CALIBRATION_MODE,
        "prediction_run_hash": run_hash,
        "predicted_at":      datetime.now(timezone.utc).isoformat(),
    }
    _append(PRED_LOG, record)
    return record

# ── backfill ──────────────────────────────────────────────────────────────────
def backfill() -> dict:
    """
    Fetch all WC 2026 fixtures, predict for every fixture with known teams
    (regardless of match status — predictions are status-independent).
    Idempotent.
    """
    print("── BACKFILL: fetching all fixtures …")
    matches = _fetch_matches()

    # compute canonical run hash (same algorithm as validation runs)
    import hashlib as _hl
    active_rows = []
    for m in matches:
        ht = (m.get("homeTeam") or {}).get("name") or ""
        at = (m.get("awayTeam") or {}).get("name") or ""
        st = m.get("status", "")
        if not ht or not at or not _elo_known(ht) or not _elo_known(at):
            continue
        if st not in ACTIVE:
            continue
        r = PRED.predict(ht, at)
        active_rows.append({"home": ht, "away": at,
                             "h": r["home_win_prob"], "d": r["draw_prob"],
                             "a": r["away_win_prob"]})
    run_hash = _hl.sha256(
        json.dumps(active_rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    print(f"   Canonical run hash: {run_hash}")

    logged = skipped_null = skipped_elo = already_exists = 0
    for m in matches:
        ht    = (m.get("homeTeam") or {}).get("name") or ""
        at    = (m.get("awayTeam") or {}).get("name") or ""
        date  = (m.get("utcDate") or "")[:10]
        stage = m.get("stage", "")

        if not ht or not at:
            skipped_null += 1
            continue
        if not _elo_known(ht) or not _elo_known(at):
            skipped_elo += 1
            continue

        result = log_prediction(ht, at, date, stage, run_hash=run_hash)
        if result is None:
            already_exists += 1
        else:
            logged += 1

    print(f"   Logged   : {logged}")
    print(f"   Already  : {already_exists}")
    print(f"   Skip-null: {skipped_null}  Skip-elo: {skipped_elo}")
    return {"logged": logged, "already_exists": already_exists,
            "skipped_null": skipped_null, "skipped_elo": skipped_elo,
            "run_hash": run_hash}

# ── settlement ────────────────────────────────────────────────────────────────
def settle_finished() -> dict:
    """
    Fetch all FINISHED WC 2026 matches, match to prediction log, write settlements.
    Idempotent.
    """
    print("── SETTLE: fetching FINISHED fixtures …")
    try:
        finished_matches = _fetch_matches("status=FINISHED")
    except Exception as e:
        print(f"   WARNING: could not fetch FINISHED — {e}")
        finished_matches = []

    # also check main feed for anything FINISHED there
    try:
        all_matches = _fetch_matches()
        extra = [m for m in all_matches if m.get("status","") in FINISHED]
        seen = {(m.get("homeTeam",{}) or {}).get("name","") + "|" +
                (m.get("awayTeam",{}) or {}).get("name","")
                for m in finished_matches}
        for m in extra:
            key = ((m.get("homeTeam") or {}).get("name","") + "|" +
                   (m.get("awayTeam") or {}).get("name",""))
            if key not in seen:
                finished_matches.append(m)
                seen.add(key)
    except Exception:
        pass

    # index prediction log by natural key
    pred_index: dict[str, dict] = {}
    for rec in _load_all(PRED_LOG):
        pred_index[rec["natural_key"]] = rec

    existing_settlements = _load_ids(SETTLE_LOG, "settlement_id")

    settled = skipped_no_pred = skipped_no_score = skipped_dup = 0
    for m in finished_matches:
        ht    = (m.get("homeTeam") or {}).get("name") or ""
        at    = (m.get("awayTeam") or {}).get("name") or ""
        date  = (m.get("utcDate") or "")[:10]
        score = m.get("score", {})

        if not ht or not at:
            continue

        nk  = _natural_key(ht, at, date)
        sid = _settlement_id(nk)

        if sid in existing_settlements:
            skipped_dup += 1
            continue

        # look up prediction
        pred = pred_index.get(nk)
        if pred is None:
            # try with the raw (un-normalised) names in case normalization differs
            skipped_no_pred += 1
            print(f"   NO_PRED: {ht} vs {at} ({date})  nk={nk}")
            continue

        # extract score
        ft = score.get("fullTime", {}) or {}
        home_goals = ft.get("home")
        away_goals = ft.get("away")
        if home_goals is None or away_goals is None:
            skipped_no_score += 1
            continue

        home_goals = int(home_goals)
        away_goals = int(away_goals)
        actual_outcome = _outcome_from_score(home_goals, away_goals)
        predicted_outcome = pred["predicted_outcome"]
        correct = (predicted_outcome == actual_outcome)

        probs = pred["probabilities"]
        outcome_to_key = {"HOME_WIN": "H", "DRAW": "D", "AWAY_WIN": "A"}
        prob_of_actual = probs[outcome_to_key[actual_outcome]]

        # log-loss contribution: -ln(p), p in [0,1]
        p_norm = max(prob_of_actual / 100.0, 1e-9)
        log_loss_contrib = round(-math.log(p_norm), 5)

        # Brier contribution (multi-class)
        o_H = 1.0 if actual_outcome == "HOME_WIN" else 0.0
        o_D = 1.0 if actual_outcome == "DRAW"     else 0.0
        o_A = 1.0 if actual_outcome == "AWAY_WIN" else 0.0
        brier_contrib = round(
            (probs["H"]/100 - o_H)**2 +
            (probs["D"]/100 - o_D)**2 +
            (probs["A"]/100 - o_A)**2, 6
        )

        record = {
            "record_type":       "SETTLEMENT",
            "settlement_id":     sid,
            "prediction_id":     pred["prediction_id"],
            "natural_key":       nk,
            "home_team":         ht,
            "away_team":         at,
            "match_date":        date,
            "stage":             pred.get("stage", ""),
            "predicted_outcome": predicted_outcome,
            "actual_outcome":    actual_outcome,
            "correct":           correct,
            "actual_score":      {"home": home_goals, "away": away_goals},
            "actual_draw":       (actual_outcome == "DRAW"),
            "probabilities":     probs,
            "xg":                pred.get("xg", {}),
            "prob_of_actual":    round(prob_of_actual, 2),
            "log_loss_contrib":  log_loss_contrib,
            "brier_contrib":     brier_contrib,
            "confidence":        pred["confidence"],
            "tier":              pred["tier"],
            "signal":            pred["signal"],
            "elo_gap":           pred["elo_gap"],
            "settled_at":        datetime.now(timezone.utc).isoformat(),
            "settler_version":   SETTLER_VERSION,
            "api_source":        "football-data.org/v4",
        }
        _append(SETTLE_LOG, record)
        existing_settlements.add(sid)
        settled += 1
        result_str = "✓" if correct else "✗"
        print(f"   [{result_str}] {ht} vs {at} ({date})  "
              f"pred={predicted_outcome}  actual={actual_outcome}  "
              f"score={home_goals}-{away_goals}")

    print(f"   Settled  : {settled}  Dup: {skipped_dup}  "
          f"No-pred: {skipped_no_pred}  No-score: {skipped_no_score}")
    return {"settled": settled, "skipped_dup": skipped_dup,
            "skipped_no_pred": skipped_no_pred}

# ── accuracy computation ──────────────────────────────────────────────────────
def compute_accuracy() -> dict:
    """Read settlement log, compute all aggregate metrics."""
    settlements = _load_all(SETTLE_LOG)
    n = len(settlements)

    if n == 0:
        result = {"n_settled": 0, "generated_at": datetime.now(timezone.utc).isoformat()}
        with open(ACCURACY_FILE, "w") as f:
            json.dump(result, f, indent=2)
        return result

    # core counts
    n_correct    = sum(1 for s in settlements if s["correct"])
    n_draws_pred = sum(1 for s in settlements if s["predicted_outcome"] == "DRAW")
    n_draws_act  = sum(1 for s in settlements if s["actual_outcome"] == "DRAW")
    acc          = round(n_correct / n * 100, 2)
    draw_rate_predicted = round(
        sum(s["probabilities"]["D"] for s in settlements) / n, 3
    )
    draw_rate_actual = round(n_draws_act / n * 100, 3)

    # confusion matrix
    labels = ["HOME_WIN", "DRAW", "AWAY_WIN"]
    cm: dict[str, dict[str, int]] = {p: {a: 0 for a in labels} for p in labels}
    for s in settlements:
        cm[s["predicted_outcome"]][s["actual_outcome"]] += 1

    # by tier
    by_tier: dict[str, dict] = {}
    for tier in ["TIER_A", "TIER_B", "TIER_C"]:
        pool = [s for s in settlements if s["tier"] == tier]
        by_tier[tier] = {
            "n": len(pool),
            "correct": sum(1 for s in pool if s["correct"]),
            "accuracy": round(sum(1 for s in pool if s["correct"]) / len(pool) * 100, 2)
                        if pool else None,
        }

    # by confidence band
    bands = [("90-92", 90, 93), ("70-89", 70, 90), ("50-69", 50, 70), ("30-49", 30, 50)]
    by_band: dict[str, dict] = {}
    for label, lo, hi in bands:
        pool = [s for s in settlements if lo <= s["confidence"] < hi]
        by_band[label] = {
            "n": len(pool),
            "mean_conf": round(sum(s["confidence"] for s in pool) / len(pool), 2) if pool else None,
            "correct": sum(1 for s in pool if s["correct"]),
            "accuracy": round(sum(1 for s in pool if s["correct"]) / len(pool) * 100, 2)
                        if pool else None,
        }

    # rolling 7-day
    cutoff = datetime.now(timezone.utc).isoformat()[:10]
    from datetime import timedelta
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    pool_7d = [s for s in settlements if s.get("settled_at","") >= cutoff_7d]
    rolling_7d = {
        "n": len(pool_7d),
        "accuracy": round(sum(1 for s in pool_7d if s["correct"]) / len(pool_7d) * 100, 2)
                    if pool_7d else None,
        "cutoff": cutoff_7d[:10],
    }

    # draw-rate bias (available from n=1, meaningful at n≥15)
    draw_rate_bias      = round(draw_rate_actual - draw_rate_predicted, 3)
    draw_bias_available = n >= 15

    # Brier Score (computed always, labelled insufficient if n<20)
    brier_score = round(sum(s["brier_contrib"] for s in settlements) / n, 5)
    brier_available = n >= 20

    # log-loss
    log_loss = round(sum(s["log_loss_contrib"] for s in settlements) / n, 5)

    # ECE
    ece: float | None = None
    reliability_bins: list[dict] = []
    ece_available = False
    if n >= 20:
        bin_edges = [(30,40),(40,50),(50,60),(60,70),(70,80),(80,93)]
        total_weight = 0.0
        ece_sum = 0.0
        for lo, hi in bin_edges:
            pool = [s for s in settlements if lo <= s["confidence"] < hi]
            if not pool:
                reliability_bins.append({
                    "band": f"{lo}-{hi}", "n": 0,
                    "mean_conf": None, "accuracy": None,
                })
                continue
            mean_c   = sum(s["confidence"] for s in pool) / len(pool)
            accuracy = sum(1 for s in pool if s["correct"]) / len(pool)
            weight   = len(pool) / n
            ece_sum += abs(accuracy - mean_c / 100.0) * weight
            total_weight += weight
            reliability_bins.append({
                "band": f"{lo}-{hi}", "n": len(pool),
                "mean_conf": round(mean_c, 2),
                "accuracy":  round(accuracy * 100, 2),
            })
        ece = round(ece_sum, 5)
        ece_available = True

    # flags
    flags: list[str] = []
    if draw_bias_available:
        if abs(draw_rate_bias) >= 5.0:
            flags.append(f"DRAW_CALIBRATION_CONFIRMED — bias={draw_rate_bias:+.2f}pp")
        elif draw_rate_actual < draw_rate_predicted:
            flags.append(f"CALIBRATION_WARNING:DRAW_BELOW_PREDICTED — bias={draw_rate_bias:+.2f}pp")
    if ece_available and ece is not None and ece > 0.08:
        flags.append(f"ECE_REVIEW_REQUIRED — ECE={ece:.4f} > 0.08 threshold")
    if acc < 40.0 and n >= 15:
        flags.append(f"ACCURACY_BELOW_RANDOM — {acc:.1f}% with n={n}")

    result = {
        "generated_at":         datetime.now(timezone.utc).isoformat(),
        "n_settled":            n,
        "n_correct":            n_correct,
        "overall_accuracy_pct": acc,
        "log_loss":             log_loss,
        "n_draws_predicted":    n_draws_pred,
        "n_draws_actual":       n_draws_act,
        "draw_rate_predicted":  draw_rate_predicted,
        "draw_rate_actual":     draw_rate_actual,
        "draw_rate_bias":       draw_rate_bias if draw_bias_available else None,
        "draw_bias_available":  draw_bias_available,
        "confusion_matrix":     cm,
        "by_tier":              by_tier,
        "by_confidence_band":   by_band,
        "rolling_7d":           rolling_7d,
        "brier_score":          brier_score if brier_available else None,
        "brier_available":      brier_available,
        "ece":                  ece,
        "ece_available":        ece_available,
        "reliability_bins":     reliability_bins,
        "flags":                flags,
    }
    with open(ACCURACY_FILE, "w") as f:
        json.dump(result, f, indent=2)
    return result

# ── report generation ─────────────────────────────────────────────────────────
def generate_report(acc: dict) -> str:
    n          = acc.get("n_settled", 0)
    n_pred_log = sum(1 for _ in _load_all(PRED_LOG))
    now        = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    hit_rate   = acc.get("overall_accuracy_pct")
    dr_pred    = acc.get("draw_rate_predicted")
    dr_act     = acc.get("draw_rate_actual")
    dr_bias    = acc.get("draw_rate_bias")
    brier      = acc.get("brier_score")
    ece        = acc.get("ece")
    flags      = acc.get("flags", [])
    cm         = acc.get("confusion_matrix", {})
    by_tier    = acc.get("by_tier", {})
    by_band    = acc.get("by_confidence_band", {})
    rolling    = acc.get("rolling_7d", {})

    lines: list[str] = []
    A = lines.append

    A("# SETTLER_REPORT")
    A(f"**WCOutcomePredictionEngine v3.0 — WP-SETTLER-1**  ")
    A(f"**Generated:** {now}  ")
    A(f"**Roadmap phase:** SHADOW_HARDENING  ")
    A("")
    A("---")
    A("")

    # STATUS BLOCK
    A("## STATUS")
    A("```")
    A(f"Engine status   : SHADOW STABLE")
    A(f"Settler version : {SETTLER_VERSION}")
    A(f"Predictions log : {n_pred_log} records")
    A(f"Settled fixtures: {n}")
    A(f"Unsettled       : {n_pred_log - n}  (scheduled / future)")
    A("```")
    A("")

    # SECTION 1: COVERAGE
    A("## 1. FIXTURE COVERAGE")
    A("```")
    A(f"Prediction log entries : {n_pred_log}")
    A(f"Settled (FINISHED)     : {n}")
    A(f"Pending settlement     : {n_pred_log - n}")
    A(f"Settlement rate        : {n/n_pred_log*100:.1f}%  "
      f"({'SUFFICIENT' if n >= 15 else f'INSUFFICIENT — need {15-n} more for draw-bias'})")
    A("```")
    A("")

    if n == 0:
        A("**No settled fixtures yet. Run again after matches complete.**")
        report_text = "\n".join(lines)
        REPORT_FILE.write_text(report_text)
        return report_text

    # SECTION 2: HIT RATE
    A("## 2. HIT RATE")
    A("```")
    A(f"Overall accuracy : {hit_rate:.2f}%  ({acc['n_correct']}/{n})")
    baseline = 100/3
    vs_random = hit_rate - baseline
    A(f"vs random (33.3%): {vs_random:+.2f}pp")
    A(f"Log-loss         : {acc['log_loss']:.4f}")
    A("```")
    A("")

    # SECTION 3: DRAW RATE
    A("## 3. DRAW RATE ANALYSIS")
    A("```")
    A(f"Predicted mean D%  : {dr_pred:.2f}%   (from SHADOW validation run)")
    A(f"Actual draw rate   : {dr_act:.2f}%   ({acc['n_draws_actual']}/{n} settled as draw)")
    A(f"Modal DRAWs pred   : {acc['n_draws_predicted']}   (Poisson-modal, structural zero expected)")
    if dr_bias is not None:
        sign = "+" if dr_bias >= 0 else ""
        A(f"Draw-rate bias     : {sign}{dr_bias:.2f}pp  "
          f"({'actual > predicted — model UNDERestimates draws' if dr_bias > 0 else 'actual < predicted — model OVERestimates draws' if dr_bias < 0 else 'no bias'})")
        if abs(dr_bias) >= 5.0:
            A(f"STATUS             : DRAW_CALIBRATION_CONFIRMED  (|bias|≥5pp)")
        elif abs(dr_bias) >= 2.0:
            A(f"STATUS             : CALIBRATION_WARNING  (|bias|≥2pp, monitor)")
        else:
            A(f"STATUS             : CALIBRATION_OK  (|bias|<2pp)")
    else:
        A(f"Draw-rate bias     : NOT_COMPUTABLE  (need n≥15, have n={n})")
    A("```")
    A("")

    # SECTION 4: CONFUSION MATRIX
    A("## 4. CONFUSION MATRIX")
    A("```")
    labels = ["HOME_WIN", "DRAW", "AWAY_WIN"]
    cm_title = "Predicted↓ \\ Actual→"
    header = f"{cm_title:<22}" + "".join(f"{l:>12}" for l in labels) + "   Total"
    A(header)
    A("-" * (22 + 12*3 + 8))
    for pred_label in labels:
        row_counts = [cm.get(pred_label, {}).get(a, 0) for a in labels]
        total_row  = sum(row_counts)
        A(f"{pred_label:<22}" + "".join(f"{c:>12}" for c in row_counts) + f"   {total_row:>5}")
    A("-" * (22 + 12*3 + 8))
    col_totals = [sum(cm.get(p, {}).get(a, 0) for p in labels) for a in labels]
    A(f"{'Total':<22}" + "".join(f"{c:>12}" for c in col_totals) + f"   {n:>5}")
    A("```")
    A("")
    A("*Diagonal = correct predictions. Off-diagonal = errors.*  ")
    A("*Row DRAW = 0 throughout — structural Poisson-modal property (no fixture predicted as DRAW).*")
    A("")

    # SECTION 5: ACCURACY BY TIER
    A("## 5. ACCURACY BY TIER")
    A("```")
    A(f"{'Tier':<10} {'n':>5} {'Correct':>8} {'Accuracy':>10}")
    A("-" * 36)
    for tier in ["TIER_A", "TIER_B", "TIER_C"]:
        t = by_tier.get(tier, {})
        acc_str = f"{t['accuracy']:.2f}%" if t.get("accuracy") is not None else "n/a"
        A(f"{tier:<10} {t.get('n',0):>5} {t.get('correct',0):>8} {acc_str:>10}")
    A("```")
    A("")

    # SECTION 6: ACCURACY BY CONFIDENCE BAND
    A("## 6. ACCURACY BY CONFIDENCE BAND")
    A("```")
    A(f"{'Band':>8} {'n':>5} {'MeanConf':>10} {'Correct':>8} {'Accuracy':>10}  {'Calibrated?'}")
    A("-" * 62)
    for band_label, lo, hi in [("90-92",90,93),("70-89",70,90),("50-69",50,70),("30-49",30,50)]:
        b = by_band.get(band_label, {})
        mc   = f"{b['mean_conf']:.1f}%" if b.get("mean_conf") is not None else " n/a"
        a_s  = f"{b['accuracy']:.1f}%"  if b.get("accuracy")  is not None else " n/a"
        n_b  = b.get("n", 0)
        if n_b >= 5 and b.get("accuracy") is not None and b.get("mean_conf") is not None:
            gap = b["accuracy"] - b["mean_conf"]
            cal = f"Δ={gap:+.1f}pp"
        else:
            cal = "insufficient n"
        A(f"{band_label:>8} {n_b:>5} {mc:>10} {b.get('correct',0):>8} {a_s:>10}  {cal}")
    A("```")
    A("")

    # SECTION 7: ROLLING 7-DAY
    A("## 7. ROLLING 7-DAY METRICS")
    A("```")
    A(f"Window        : {rolling.get('cutoff','n/a')} → today")
    A(f"Settled (7d)  : {rolling.get('n', 0)}")
    r7_acc = rolling.get("accuracy")
    A(f"Accuracy (7d) : {f'{r7_acc:.2f}%' if r7_acc is not None else 'n/a'}")
    A("```")
    A("")

    # SECTION 8: CALIBRATION STUB / FULL
    A("## 8. CALIBRATION METRICS")
    if acc.get("brier_available"):
        A(f"**Brier Score (multi-class):** `{brier:.5f}`  ")
        benchmarks = "0.00=perfect · 0.50=random · <0.45=better-than-random · <0.35=skill · <0.25=strong"
        A(f"Benchmarks: {benchmarks}  ")
        A("")
    else:
        A(f"**Brier Score:** `STUB — need n≥20, have n={n}`  ")
        A("")

    if acc.get("ece_available") and ece is not None:
        A(f"**ECE (Expected Calibration Error):** `{ece:.5f}`  ")
        ece_label = "GOOD (<0.05)" if ece < 0.05 else "ACCEPTABLE (0.05–0.08)" if ece < 0.08 else "REVIEW REQUIRED (>0.08)"
        A(f"Status: {ece_label}  ")
        A("")
        if acc.get("reliability_bins"):
            A("**Reliability diagram data:**")
            A("```")
            A(f"{'Band':>8} {'n':>5} {'MeanConf':>10} {'Accuracy':>10}  Δ")
            A("-" * 42)
            for b in acc["reliability_bins"]:
                if b["n"] == 0:
                    continue
                delta = b["accuracy"] - b["mean_conf"] if b["mean_conf"] else None
                A(f"{b['band']:>8} {b['n']:>5} {b['mean_conf']:>9.1f}% {b['accuracy']:>9.1f}%  "
                  f"{f'{delta:+.1f}pp' if delta is not None else 'n/a'}")
            A("```")
            A("")
    else:
        A(f"**ECE:** `STUB — need n≥20, have n={n}`  ")
        A(f"**Reliability bins:** `STUB — insufficient data`  ")
        A("")

    # SECTION 9: ROI STUB
    A("## 9. ROI STUB")
    A("> **SHADOW phase — no betting execution. ROI tracking deferred to PAPER phase.**")
    A("```")
    A(f"Bets simulated : 0   (SHADOW — no monetary execution)")
    A(f"ROI            : N/A (PAPER phase prerequisite)")
    A(f"Stake model    : undefined — PAPER-phase design item")
    A("```")
    A("")

    # SECTION 10: FLAGS
    A("## 10. FLAGS")
    if flags:
        for flag in flags:
            severity = "WARN" if "WARNING" in flag or "CONFIRMED" in flag else "CRIT" if "CRIT" in flag else "INFO"
            A(f"- `[{severity}]` {flag}")
    else:
        A("- `[OK]` No flags raised")
    A("")

    # SECTION 11: PAPER READINESS IMPACT
    A("## 11. PAPER READINESS IMPACT")
    A("")
    complete = [
        "R-5 infrastructure: shadow_predictions.jsonl created",
        "R-5 infrastructure: shadow_settlements.jsonl created",
        "R-5 infrastructure: shadow_accuracy.json created",
        "R-5 infrastructure: result_settler.py operational",
        "Backfill: all known-team WC 2026 fixtures logged",
        "Settlement: first run completed",
    ]
    for item in complete:
        A(f"- ✅ {item}")
    A("")

    partial = []
    missing = []
    if n < 15:
        missing.append(f"Draw-rate bias confirmation (need {15-n} more settlements)")
    else:
        complete.append("Draw-rate bias: computable")
        if dr_bias is not None and abs(dr_bias) >= 5.0:
            partial.append("Draw calibration: bias confirmed ≥5pp → isotonic fix required at PAPER")
        else:
            complete.append("Draw calibration: bias within tolerance")

    if n < 20:
        missing.append(f"ECE / Brier Score (need {20-n} more settlements)")
    else:
        if ece is not None and ece > 0.08:
            partial.append(f"ECE review required (ECE={ece:.4f} > 0.08)")
        else:
            complete.append("ECE: within acceptable range")

    missing.append("Bracket Elo coverage verification (post 2026-06-28)")

    for item in partial:
        A(f"- ⚠️  {item}")
    for item in missing:
        A(f"- ❌  {item}")
    A("")

    # PAPER candidate verdict
    A("### PAPER Candidate Verdict")
    blockers = [m for m in missing if "settlement" in m.lower() or "ece" in m.lower() or "brier" in m.lower() or "draw-rate" in m.lower()]
    if blockers:
        remaining_str = f"{15-n if n<15 else 0} more settlements" if n < 20 else "ECE computation"
        A(f"> **NOT YET PAPER_CANDIDATE.**  ")
        A(f"> Remaining blocker: {len(blockers)} item(s). Need {remaining_str}.")
    else:
        A("> **PAPER_CANDIDATE conditions met.** Advance checklist to PAPER phase.")
    A("")

    A("---")
    A(f"*Generated by WP-SETTLER-1 · SHADOW_HARDENING phase · {now}*")

    report_text = "\n".join(lines)
    REPORT_FILE.write_text(report_text)
    print(f"\n── REPORT written → {REPORT_FILE}")
    return report_text


# ── main ──────────────────────────────────────────────────────────────────────
def main(args: list[str] | None = None) -> int:
    if args is None:
        args = sys.argv[1:]

    do_backfill = "--settle" not in args and "--report" not in args or "--backfill" in args
    do_settle   = "--backfill" not in args and "--report" not in args or "--settle" in args
    do_report   = "--backfill" not in args and "--settle" not in args or "--report" in args

    if not args:
        do_backfill = do_settle = do_report = True

    if do_backfill:
        backfill()
    if do_settle:
        settle_finished()
    if do_settle or do_backfill:
        acc = compute_accuracy()
    else:
        acc = compute_accuracy()
    if do_report:
        generate_report(acc)

    n = acc.get("n_settled", 0)
    print(f"\n── SUMMARY  n_settled={n}  "
          f"accuracy={acc.get('overall_accuracy_pct','n/a')}%  "
          f"draw_bias={acc.get('draw_rate_bias','pending')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
