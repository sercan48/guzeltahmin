"""ML Self-Learning Engine v2 — Full match database analysis.

3 Layers:
  Layer 1: Sync results → predictions.actual_result from matches.ft_result
  Layer 2: Analyze ALL matches (not just TG-shared) → model vs reality
  Layer 3: Generate tuning weights + retrain signal

Runs nightly at 23:00 via scheduled job.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from src.db.base import get_backend

logger = logging.getLogger(__name__)

TUNING_FILE = Path("data/processed/tuning_weights.json")
LEARNING_LOG = Path("data/processed/learning_log.jsonl")


def run_learning_cycle() -> dict:
    """Full self-learning cycle on ALL match data."""
    db = get_backend()
    db.connect()

    try:
        report = {"timestamp": datetime.now().isoformat(), "cycle": "daily"}

        # Layer 1: Sync actual results into predictions table
        synced = _sync_actual_results(db)
        report["synced_results"] = synced

        # Layer 2a: Predictions table accuracy (TG-shared matches)
        pred_stats = _predictions_table_accuracy(db)
        report["predictions_table"] = pred_stats

        # Layer 2b: FULL database accuracy (all 59K+ matches)
        full_stats = _full_database_analysis(db)
        report["full_database"] = full_stats

        # Layer 2c: Per-league breakdown (all matches)
        league_stats = _per_league_full(db)
        report["per_league"] = league_stats

        # Layer 2d: Confidence band analysis
        bands = _confidence_band_analysis(db)
        report["confidence_bands"] = bands

        # Layer 2e: Calibration check
        calibration = _calibration_analysis(db)
        report["calibration"] = calibration

        # Layer 2f: Trend (recent vs historical)
        trend = _trend_analysis(db)
        report["trend"] = trend

        # Layer 2g: Season-by-season performance
        seasons = _season_analysis(db)
        report["per_season"] = seasons

        # Layer 3: Generate weights
        weights = _generate_weights(pred_stats, full_stats, league_stats, bands, calibration, trend)
        report["weights"] = weights

        # Check retrain signal
        retrain = _check_retrain_signal(full_stats, trend, calibration)
        report["retrain_needed"] = retrain

        # Save
        TUNING_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TUNING_FILE, "w") as f:
            json.dump(weights, f, indent=2)

        LEARNING_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LEARNING_LOG, "a") as f:
            f.write(json.dumps(report, default=str) + "\n")

        if retrain:
            logger.warning("[SELF-LEARN] Retrain signal triggered!")
            _log_retrain_event(db)

        acc = full_stats.get("accuracy", 0)
        logger.info(f"[SELF-LEARN] Cycle complete. Full DB accuracy: {acc:.1%}")
        return report

    except Exception as e:
        logger.error(f"[SELF-LEARN] Error: {e}", exc_info=True)
        return {"error": str(e), "timestamp": datetime.now().isoformat()}
    finally:
        db.close()


# ─────────────────────────────────────
# LAYER 1: RESULT SYNC
# ─────────────────────────────────────

def _sync_actual_results(db) -> int:
    """Sync ft_result from matches into predictions.actual_result."""
    try:
        db.execute("""
            UPDATE predictions
            SET actual_result = (
                SELECT m.ft_result FROM matches m WHERE m.id = predictions.match_id
            )
            WHERE actual_result IS NULL
            AND match_id IN (SELECT id FROM matches WHERE ft_result IS NOT NULL)
        """)
        row = db.fetchone("""
            SELECT COUNT(*) as c FROM predictions WHERE actual_result IS NOT NULL
        """)
        synced = row["c"] if row else 0
        if synced > 0:
            logger.info(f"[SYNC] {synced} predictions now have actual results")
        return synced
    except Exception as e:
        logger.error(f"[SYNC] Error: {e}")
        return 0


# ─────────────────────────────────────
# LAYER 2: FULL DATABASE ANALYSIS
# ─────────────────────────────────────

def _predictions_table_accuracy(db) -> dict:
    """Accuracy from predictions table (TG-shared matches only)."""
    row = db.fetchone("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN predicted_result = actual_result THEN 1 ELSE 0 END) as correct
        FROM predictions WHERE actual_result IS NOT NULL
    """)
    if not row or not row["total"]:
        return {"total": 0, "correct": 0, "accuracy": 0, "source": "predictions_table"}
    return {
        "total": row["total"],
        "correct": row["correct"],
        "accuracy": round(row["correct"] / row["total"], 4),
        "source": "predictions_table",
    }


def _full_database_analysis(db) -> dict:
    """Analyze model performance across ALL matches with results.

    This uses the matches table directly, calculating what the model
    WOULD have predicted based on basic heuristics (home advantage,
    team strength differential, historical form).

    For matches that have predictions stored, uses actual model output.
    For all others, uses a statistical baseline to measure trends.
    """
    # First: use actual predictions where available
    pred_row = db.fetchone("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN predicted_result = actual_result THEN 1 ELSE 0 END) as correct
        FROM predictions WHERE actual_result IS NOT NULL
    """)
    pred_total = pred_row["total"] if pred_row and pred_row["total"] else 0
    pred_correct = pred_row["correct"] if pred_row and pred_row["correct"] else 0

    # Second: baseline analysis on full match database
    # This tells us how predictable each league/season is
    baseline = _baseline_accuracy(db)

    # Merge
    total = pred_total + baseline.get("total", 0)
    correct = pred_correct + baseline.get("correct", 0)

    return {
        "model_predictions": {"total": pred_total, "correct": pred_correct},
        "baseline_analysis": baseline,
        "combined_total": total,
        "combined_correct": correct,
        "accuracy": round(correct / max(total, 1), 4),
        "source": "full_database",
    }


def _baseline_accuracy(db) -> dict:
    """Calculate baseline accuracy: how often does the home-advantage-based
    favorite actually win? This establishes the floor to beat."""
    row = db.fetchone("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN ft_result = 'H' THEN 1 ELSE 0 END) as home_wins,
            SUM(CASE WHEN ft_result = 'D' THEN 1 ELSE 0 END) as draws,
            SUM(CASE WHEN ft_result = 'A' THEN 1 ELSE 0 END) as away_wins
        FROM matches
        WHERE ft_result IS NOT NULL
    """)
    if not row or not row["total"]:
        return {"total": 0, "correct": 0}

    total = row["total"]
    home_rate = row["home_wins"] / total
    draw_rate = row["draws"] / total
    away_rate = row["away_wins"] / total

    # Baseline: "always predict home" accuracy
    always_home_acc = home_rate

    # Smart baseline: predict based on historical rates per match context
    # Using match-level features from the DB
    smart = _smart_baseline_accuracy(db)

    return {
        "total": total,
        "home_win_rate": round(home_rate, 4),
        "draw_rate": round(draw_rate, 4),
        "away_win_rate": round(away_rate, 4),
        "always_home_accuracy": round(always_home_acc, 4),
        "smart_baseline_accuracy": smart.get("accuracy", 0),
        "correct": smart.get("correct", 0),
    }


def _smart_baseline_accuracy(db) -> dict:
    """Smart baseline: for each match, predict based on
    home team's season win rate vs away team's season win rate."""
    # Get team seasonal win rates
    team_stats = db.fetchall("""
        SELECT
            team_id,
            season,
            SUM(wins) as wins,
            SUM(played) as played
        FROM (
            SELECT home_team_id as team_id, season,
                   SUM(CASE WHEN ft_result='H' THEN 1 ELSE 0 END) as wins,
                   COUNT(*) as played
            FROM matches WHERE ft_result IS NOT NULL
            GROUP BY home_team_id, season
            UNION ALL
            SELECT away_team_id as team_id, season,
                   SUM(CASE WHEN ft_result='A' THEN 1 ELSE 0 END) as wins,
                   COUNT(*) as played
            FROM matches WHERE ft_result IS NOT NULL
            GROUP BY away_team_id, season
        )
        GROUP BY team_id, season
    """)

    # Build lookup: (team_id, season) -> win_rate
    rates = {}
    for r in team_stats:
        if r["played"] and r["played"] >= 5:
            rates[(r["team_id"], r["season"])] = r["wins"] / r["played"]

    # Now evaluate each match
    matches = db.fetchall("""
        SELECT id, home_team_id, away_team_id, season, ft_result
        FROM matches
        WHERE ft_result IS NOT NULL AND season IN (
            SELECT DISTINCT season FROM matches ORDER BY season DESC LIMIT 5
        )
    """)

    correct = 0
    total = 0
    for m in matches:
        h_rate = rates.get((m["home_team_id"], m["season"]), 0.45)
        a_rate = rates.get((m["away_team_id"], m["season"]), 0.30)

        # Simple prediction: compare win rates + home boost
        h_score = h_rate + 0.08  # home advantage boost
        a_score = a_rate

        if h_score > a_score + 0.10:
            pred = "H"
        elif a_score > h_score + 0.05:
            pred = "A"
        else:
            pred = "D"

        if pred == m["ft_result"]:
            correct += 1
        total += 1

    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / max(total, 1), 4),
    }


def _per_league_full(db) -> dict:
    """Per-league analysis from full matches table."""
    rows = db.fetchall("""
        SELECT league_code,
               COUNT(*) as total,
               SUM(CASE WHEN ft_result = 'H' THEN 1 ELSE 0 END) as home_wins,
               SUM(CASE WHEN ft_result = 'D' THEN 1 ELSE 0 END) as draws,
               SUM(CASE WHEN ft_result = 'A' THEN 1 ELSE 0 END) as away_wins,
               AVG(COALESCE(ft_home_goals, 0) + COALESCE(ft_away_goals, 0)) as avg_goals
        FROM matches
        WHERE ft_result IS NOT NULL
        GROUP BY league_code
        ORDER BY total DESC
    """)
    result = {}
    for r in rows:
        if r["total"] >= 20:
            result[r["league_code"]] = {
                "total": r["total"],
                "home_win_pct": round(r["home_wins"] / r["total"] * 100, 1),
                "draw_pct": round(r["draws"] / r["total"] * 100, 1),
                "away_win_pct": round(r["away_wins"] / r["total"] * 100, 1),
                "avg_goals": round(r["avg_goals"], 2) if r["avg_goals"] else 0,
                "predictability": "high" if r["home_wins"] / r["total"] > 0.48 else "low",
            }
    return result


def _season_analysis(db) -> dict:
    """Per-season breakdown."""
    rows = db.fetchall("""
        SELECT season, COUNT(*) as total,
               SUM(CASE WHEN ft_result = 'H' THEN 1 ELSE 0 END) as home_wins,
               AVG(COALESCE(ft_home_goals, 0) + COALESCE(ft_away_goals, 0)) as avg_goals
        FROM matches WHERE ft_result IS NOT NULL
        GROUP BY season ORDER BY season DESC
    """)
    result = {}
    for r in rows:
        result[r["season"]] = {
            "total": r["total"],
            "home_win_pct": round(r["home_wins"] / r["total"] * 100, 1),
            "avg_goals": round(r["avg_goals"], 2) if r["avg_goals"] else 0,
        }
    return result


def _confidence_band_analysis(db) -> list:
    """Confidence band accuracy from predictions table."""
    bands = [("low", 0, 55), ("medium", 55, 65), ("high", 65, 80), ("very_high", 80, 100)]
    result = []
    for label, lo, hi in bands:
        row = db.fetchone(f"""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN predicted_result = actual_result THEN 1 ELSE 0 END) as correct
            FROM predictions
            WHERE actual_result IS NOT NULL
            AND confidence_score >= {lo} AND confidence_score < {hi}
        """)
        if row and row["total"] and row["total"] > 0:
            acc = round(row["correct"] / row["total"], 4)
            result.append({
                "band": label, "range": f"{lo}-{hi}",
                "total": row["total"], "accuracy": acc,
                "calibrated": abs(acc - (lo + hi) / 200) < 0.10,
            })
    return result


def _calibration_analysis(db) -> dict:
    """Calibration check from predictions with results."""
    rows = db.fetchall("""
        SELECT predicted_result, actual_result,
               home_win_prob, draw_prob, away_win_prob
        FROM predictions WHERE actual_result IS NOT NULL
        ORDER BY created_at DESC LIMIT 500
    """)
    if len(rows) < 10:
        return {"sufficient_data": False, "sample_size": len(rows)}

    outcome_data = {"H": [], "D": [], "A": []}
    for r in rows:
        actual = r["actual_result"]
        probs = {
            "H": r["home_win_prob"] or 0.33,
            "D": r["draw_prob"] or 0.33,
            "A": r["away_win_prob"] or 0.33,
        }
        for outcome in ["H", "D", "A"]:
            outcome_data[outcome].append({
                "predicted_prob": probs[outcome],
                "occurred": 1 if actual == outcome else 0,
            })

    calibration = {}
    total_drift = 0
    for outcome, data in outcome_data.items():
        avg_pred = sum(d["predicted_prob"] for d in data) / len(data)
        avg_actual = sum(d["occurred"] for d in data) / len(data)
        drift = avg_pred - avg_actual
        calibration[outcome] = {
            "avg_predicted": round(avg_pred, 4),
            "avg_actual": round(avg_actual, 4),
            "drift": round(drift, 4),
            "overconfident": drift > 0.05,
        }
        total_drift += abs(drift)

    calibration["sufficient_data"] = True
    calibration["sample_size"] = len(rows)
    calibration["total_drift"] = round(total_drift, 4)
    calibration["well_calibrated"] = total_drift < 0.15
    return calibration


def _trend_analysis(db) -> dict:
    """Trend from predictions + full match database."""
    now_str = datetime.now().strftime("%Y-%m-%d")
    d7 = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    d30 = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    def _acc(where, params=()):
        row = db.fetchone(f"""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN p.predicted_result = p.actual_result THEN 1 ELSE 0 END) as correct
            FROM predictions p JOIN matches m ON p.match_id = m.id
            WHERE p.actual_result IS NOT NULL {where}
        """, params)
        if not row or not row["total"]:
            return 0, 0
        return round(row["correct"] / row["total"], 4), row["total"]

    acc_7, n_7 = _acc("AND m.date >= ?", (d7,))
    acc_30, n_30 = _acc("AND m.date >= ?", (d30,))
    acc_all, n_all = _acc("")

    # Full DB trend (matches table — how predictable are recent results?)
    recent_predictability = db.fetchone("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN ft_result = 'H' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as home_rate
        FROM matches
        WHERE ft_result IS NOT NULL AND date >= ?
    """, (d30,))

    return {
        "last_7_days": {"accuracy": acc_7, "count": n_7},
        "last_30_days": {"accuracy": acc_30, "count": n_30},
        "all_time": {"accuracy": acc_all, "count": n_all},
        "recent_home_rate": round(recent_predictability["home_rate"], 4) if recent_predictability and recent_predictability["home_rate"] else 0,
        "trend": "improving" if (n_7 >= 5 and n_30 >= 10 and acc_7 >= acc_30) else
                 "declining" if (n_7 >= 5 and n_30 >= 10 and acc_7 < acc_30) else "insufficient_data",
    }


# ─────────────────────────────────────
# LAYER 3: WEIGHTS + RETRAIN
# ─────────────────────────────────────

def _generate_weights(pred_stats, full_stats, league_stats, bands, calibration, trend) -> dict:
    weights = {
        "global_overconfidence_penalty": 0.0,
        "current_hit_rate": pred_stats.get("accuracy", 0),
        "full_db_baseline": full_stats.get("accuracy", 0),
        "league_adjustments": {},
        "league_home_rates": {},
        "confidence_floor": 50,
        "last_updated": datetime.now().isoformat(),
    }

    # Overconfidence from calibration
    if calibration.get("sufficient_data") and not calibration.get("well_calibrated"):
        weights["global_overconfidence_penalty"] = min(0.05, calibration.get("total_drift", 0) * 0.3)

    # League-specific: use full DB home_win rates for smarter baseline
    for code, stats in league_stats.items():
        weights["league_home_rates"][code] = stats.get("home_win_pct", 45) / 100
        # Predictability-based adjustment
        if stats.get("predictability") == "high":
            weights["league_adjustments"][code] = 0.02
        elif stats["home_win_pct"] < 40:
            weights["league_adjustments"][code] = -0.03

    # Confidence floor
    for band in bands:
        if band["band"] == "low" and band["accuracy"] < 0.35:
            weights["confidence_floor"] = 55

    return weights


def _check_retrain_signal(full_stats, trend, calibration) -> bool:
    if trend.get("trend") == "declining":
        t7 = trend["last_7_days"]["accuracy"]
        t30 = trend["last_30_days"]["accuracy"]
        if t7 > 0 and t30 > 0 and (t30 - t7) > 0.08:
            return True

    if calibration.get("total_drift", 0) > 0.25:
        return True

    baseline = full_stats.get("baseline_analysis", {}).get("smart_baseline_accuracy", 0)
    model_acc = full_stats.get("model_predictions", {})
    if model_acc.get("total", 0) > 50:
        if model_acc.get("correct", 0) / max(model_acc["total"], 1) < baseline:
            logger.warning("[RETRAIN] Model underperforming baseline!")
            return True

    return False


def _log_retrain_event(db):
    try:
        db.execute("""
            INSERT INTO model_experiments (model_type, parameters, train_seasons, test_season, metrics)
            VALUES ('ensemble', '{"trigger": "self_learning_v2"}', 'auto', 'auto',
                    '{"reason": "performance_degradation"}')
        """)
    except Exception as e:
        logger.error(f"[RETRAIN-LOG] {e}")


# ─────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────

def get_tuning_weights() -> dict:
    if not TUNING_FILE.exists():
        return {
            "global_overconfidence_penalty": 0.0,
            "current_hit_rate": 0.0,
            "full_db_baseline": 0.0,
            "league_adjustments": {},
            "league_home_rates": {},
            "confidence_floor": 50,
            "last_updated": None,
        }
    with open(TUNING_FILE, "r") as f:
        return json.load(f)


def get_learning_history(last_n: int = 10) -> list:
    if not LEARNING_LOG.exists():
        return []
    lines = LEARNING_LOG.read_text().strip().split("\n")
    results = []
    for line in lines[-last_n:]:
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return results


def format_learning_report(report: dict) -> str:
    """Human-readable report for admin TG notification."""
    lines = [
        "ML SELF-LEARNING RAPORU v2",
        "=" * 32,
        f"Tarih: {report.get('timestamp', '?')[:19]}",
    ]

    # Synced results
    synced = report.get("synced_results", 0)
    if synced:
        lines.append(f"\nSonuc eslemesi: {synced} tahmin guncellendi")

    # Predictions table
    pt = report.get("predictions_table", {})
    if pt.get("total"):
        lines.append(f"\nTG Tahminleri: %{pt['accuracy']*100:.1f} ({pt['correct']}/{pt['total']})")

    # Full database
    fd = report.get("full_database", {})
    bl = fd.get("baseline_analysis", {})
    if bl.get("total"):
        lines.append(f"\nTUM MACLAR ({bl['total']} mac):")
        lines.append(f"  Ev sahibi galibiyet orani: %{bl.get('home_win_rate',0)*100:.1f}")
        lines.append(f"  'Hep ev sahibi' baseline: %{bl.get('always_home_accuracy',0)*100:.1f}")
        lines.append(f"  Akilli baseline: %{bl.get('smart_baseline_accuracy',0)*100:.1f}")

    # League breakdown
    leagues = report.get("per_league", {})
    if leagues:
        lines.append(f"\nLIG BAZINDA (en onemli):")
        sorted_leagues = sorted(leagues.items(), key=lambda x: x[1].get("total", 0), reverse=True)
        for code, stats in sorted_leagues[:6]:
            pred_icon = "[+]" if stats.get("predictability") == "high" else "[-]"
            lines.append(
                f"  {pred_icon} {code}: H:%{stats['home_win_pct']:.0f} "
                f"D:%{stats['draw_pct']:.0f} A:%{stats['away_win_pct']:.0f} "
                f"({stats['total']} mac, ort:{stats['avg_goals']} gol)"
            )

    # Trend
    trend = report.get("trend", {})
    t7 = trend.get("last_7_days", {})
    t30 = trend.get("last_30_days", {})
    lines.append(f"\nTREND:")
    lines.append(f"  7 gun: %{t7.get('accuracy',0)*100:.1f} ({t7.get('count',0)} mac)")
    lines.append(f"  30 gun: %{t30.get('accuracy',0)*100:.1f} ({t30.get('count',0)} mac)")
    lines.append(f"  Yon: {trend.get('trend', '?')}")

    # Season breakdown
    seasons = report.get("per_season", {})
    if seasons:
        lines.append(f"\nSEZON BAZINDA:")
        for season, stats in list(seasons.items())[:5]:
            lines.append(
                f"  {season}: {stats['total']} mac, "
                f"Ev:%{stats['home_win_pct']:.0f}, Ort:{stats['avg_goals']} gol"
            )

    # Retrain
    if report.get("retrain_needed"):
        lines.append("\n[!!!] RETRAIN SINYALI AKTIF!")
        lines.append("Komut: python scripts/train_ensemble.py")

    # Calibration
    cal = report.get("calibration", {})
    if cal.get("sufficient_data"):
        lines.append(f"\nKalibrasyon: drift={cal['total_drift']:.4f} "
                     f"({'OK' if cal.get('well_calibrated') else 'DUZELTME GEREKLI'})")

    return "\n".join(lines)
