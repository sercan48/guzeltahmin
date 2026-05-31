"""Retroactive Learner — Learn from ALL historical matches.

Processes past matches in batches:
  1. Build features for each match
  2. Generate prediction (heuristic + statistical)
  3. Compare with actual result
  4. Calculate per-feature accuracy impact
  5. Store retroactive predictions
  6. Generate calibration data for future model improvement

Usage:
  python scripts/retroactive_learner.py              # Last 2 seasons
  python scripts/retroactive_learner.py --all         # All seasons
  python scripts/retroactive_learner.py --season 2425 # Specific season
"""

import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

from src.db.base import get_backend
from config.settings import SEASON_LABELS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

INSIGHTS_FILE = Path("data/processed/retro_insights.json")
BATCH_SIZE = 200


def run(seasons=None, force=False):
    db = get_backend()
    db.connect()

    try:
        if not seasons:
            seasons = ["2025-2026", "2024-2025"]

        logger.info(f"Retroactive learning: {seasons}")

        all_results = []
        total_correct = 0
        total_processed = 0

        for season in seasons:
            logger.info(f"\n{'='*40}")
            logger.info(f"SEZON: {season}")
            logger.info(f"{'='*40}")

            matches = _get_season_matches(db, season)
            if not matches:
                logger.warning(f"  {season}: mac bulunamadi")
                continue

            logger.info(f"  {len(matches)} mac bulundu")

            # Build team stats cache for this season
            team_cache = _build_team_cache(db, season)

            season_correct = 0
            season_total = 0
            season_details = []

            for i in range(0, len(matches), BATCH_SIZE):
                batch = matches[i:i + BATCH_SIZE]

                for m in batch:
                    prediction = _predict_match_retro(
                        db, m, team_cache, season
                    )

                    actual = m["ft_result"]
                    is_correct = prediction["predicted"] == actual

                    if is_correct:
                        season_correct += 1
                        total_correct += 1

                    season_total += 1
                    total_processed += 1

                    season_details.append({
                        "match_id": m["id"],
                        "predicted": prediction["predicted"],
                        "actual": actual,
                        "correct": is_correct,
                        "confidence": prediction["confidence"],
                        "method": prediction["method"],
                        "features": prediction["features"],
                    })

                    # Store retroactive prediction
                    _store_retro_prediction(db, m, prediction)

                processed = min(i + BATCH_SIZE, len(matches))
                acc = season_correct / max(season_total, 1) * 100
                logger.info(f"  [{processed}/{len(matches)}] Dogruluk: %{acc:.1f}")

            season_acc = season_correct / max(season_total, 1)
            all_results.append({
                "season": season,
                "total": season_total,
                "correct": season_correct,
                "accuracy": round(season_acc, 4),
                "details": season_details,
            })

            logger.info(f"  SEZON SONUC: %{season_acc*100:.1f} ({season_correct}/{season_total})")

        # Generate insights
        overall_acc = total_correct / max(total_processed, 1)
        logger.info(f"\n{'='*50}")
        logger.info(f"GENEL DOGRULUK: %{overall_acc*100:.1f} ({total_correct}/{total_processed})")
        logger.info(f"{'='*50}")

        insights = _generate_insights(all_results)
        insights["overall"] = {
            "total": total_processed,
            "correct": total_correct,
            "accuracy": round(overall_acc, 4),
            "timestamp": datetime.now().isoformat(),
        }

        INSIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(INSIGHTS_FILE, "w") as f:
            json.dump(insights, f, indent=2, default=str)

        logger.info(f"\nInsights kaydedildi: {INSIGHTS_FILE}")

        # Apply insights to tuning weights
        _apply_insights_to_weights(insights)

        return insights

    finally:
        db.close()


def _get_season_matches(db, season):
    """Get all matches with results for a season."""
    return db.fetchall("""
        SELECT m.id, m.date, m.league_code, m.season,
               m.home_team_id, m.away_team_id,
               m.ft_home_goals, m.ft_away_goals, m.ft_result,
               m.home_shots, m.away_shots,
               m.home_shots_target, m.away_shots_target,
               m.home_corners, m.away_corners,
               t1.name as home_name, t2.name as away_name
        FROM matches m
        JOIN teams t1 ON m.home_team_id = t1.id
        JOIN teams t2 ON m.away_team_id = t2.id
        WHERE m.ft_result IS NOT NULL AND m.season = ?
        ORDER BY m.date
    """, (season,))


def _build_team_cache(db, season):
    """Pre-compute team stats for the season."""
    cache = {}

    rows = db.fetchall("""
        SELECT
            team_id, season,
            SUM(wins) as wins, SUM(draws) as draws, SUM(losses) as losses,
            SUM(played) as played,
            SUM(goals_for) as gf, SUM(goals_against) as ga
        FROM (
            SELECT home_team_id as team_id, season,
                   SUM(CASE WHEN ft_result='H' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN ft_result='D' THEN 1 ELSE 0 END) as draws,
                   SUM(CASE WHEN ft_result='A' THEN 1 ELSE 0 END) as losses,
                   COUNT(*) as played,
                   SUM(COALESCE(ft_home_goals,0)) as goals_for,
                   SUM(COALESCE(ft_away_goals,0)) as goals_against
            FROM matches WHERE ft_result IS NOT NULL AND season = ?
            GROUP BY home_team_id, season
            UNION ALL
            SELECT away_team_id as team_id, season,
                   SUM(CASE WHEN ft_result='A' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN ft_result='D' THEN 1 ELSE 0 END) as draws,
                   SUM(CASE WHEN ft_result='H' THEN 1 ELSE 0 END) as losses,
                   COUNT(*) as played,
                   SUM(COALESCE(ft_away_goals,0)) as goals_for,
                   SUM(COALESCE(ft_home_goals,0)) as goals_against
            FROM matches WHERE ft_result IS NOT NULL AND season = ?
            GROUP BY away_team_id, season
        )
        GROUP BY team_id, season
    """, (season, season))

    for r in rows:
        played = r["played"] or 1
        cache[r["team_id"]] = {
            "wins": r["wins"] or 0,
            "draws": r["draws"] or 0,
            "losses": r["losses"] or 0,
            "played": played,
            "win_rate": (r["wins"] or 0) / played,
            "goals_per_game": (r["gf"] or 0) / played,
            "conceded_per_game": (r["ga"] or 0) / played,
            "points_per_game": ((r["wins"] or 0) * 3 + (r["draws"] or 0)) / played,
        }

    # League home advantage rates
    league_rates = db.fetchall("""
        SELECT league_code,
               SUM(CASE WHEN ft_result='H' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as home_rate
        FROM matches WHERE ft_result IS NOT NULL AND season = ?
        GROUP BY league_code
    """, (season,))

    cache["_league_home_rate"] = {}
    for r in league_rates:
        cache["_league_home_rate"][r["league_code"]] = r["home_rate"] or 0.44

    # H2H cache
    h2h_rows = db.fetchall("""
        SELECT home_team_id, away_team_id,
               COUNT(*) as total,
               SUM(CASE WHEN ft_result='H' THEN 1 ELSE 0 END) as h_wins,
               SUM(CASE WHEN ft_result='A' THEN 1 ELSE 0 END) as a_wins,
               AVG(COALESCE(ft_home_goals,0) + COALESCE(ft_away_goals,0)) as avg_goals
        FROM matches WHERE ft_result IS NOT NULL
        GROUP BY home_team_id, away_team_id
        HAVING total >= 2
    """)
    cache["_h2h"] = {}
    for r in h2h_rows:
        key = (r["home_team_id"], r["away_team_id"])
        cache["_h2h"][key] = {
            "total": r["total"],
            "home_win_rate": r["h_wins"] / r["total"],
            "away_win_rate": r["a_wins"] / r["total"],
            "avg_goals": r["avg_goals"] or 2.5,
        }

    return cache


def _predict_match_retro(db, match, cache, season):
    """Generate prediction using draw-aware probability model.

    Key fix: Explicitly model draw probability instead of
    treating it as residual (which caused 0% draw prediction).
    """
    h_id = match["home_team_id"]
    a_id = match["away_team_id"]
    league = match["league_code"]

    h_stats = cache.get(h_id, {})
    a_stats = cache.get(a_id, {})
    league_home_rate = cache.get("_league_home_rate", {}).get(league, 0.44)
    h2h = cache.get("_h2h", {}).get((h_id, a_id), {})

    features = {}

    # 1. Team strength
    h_strength = h_stats.get("win_rate", 0.40)
    a_strength = a_stats.get("win_rate", 0.35)
    features["strength_diff"] = h_strength - a_strength

    # 2. Points per game
    h_ppg = h_stats.get("points_per_game", 1.3)
    a_ppg = a_stats.get("points_per_game", 1.2)
    features["ppg_diff"] = h_ppg - a_ppg

    # 3. Goals
    h_gpg = h_stats.get("goals_per_game", 1.3)
    a_gpg = a_stats.get("goals_per_game", 1.1)
    h_cpg = h_stats.get("conceded_per_game", 1.1)
    a_cpg = a_stats.get("conceded_per_game", 1.2)
    features["goal_diff"] = (h_gpg - h_cpg) - (a_gpg - a_cpg)
    features["expected_goals"] = (h_gpg + a_cpg) / 2 + (a_gpg + h_cpg) / 2

    # 4. Home advantage
    features["home_advantage"] = league_home_rate

    # 5. H2H
    h2h_home_wr = h2h.get("home_win_rate", 0.44)
    features["h2h_bias"] = h2h_home_wr - 0.44

    # 6. Draw indicators (NEW — fixes draw blindness)
    h_draw_rate = h_stats.get("draws", 0) / max(h_stats.get("played", 1), 1)
    a_draw_rate = a_stats.get("draws", 0) / max(a_stats.get("played", 1), 1)
    features["draw_tendency"] = (h_draw_rate + a_draw_rate) / 2

    # Quality gap: close teams draw more
    quality_gap = abs(h_ppg - a_ppg)
    features["quality_gap"] = quality_gap

    # Low-scoring matches draw more
    low_scoring = 1.0 if features["expected_goals"] < 2.3 else 0.0
    features["low_scoring"] = low_scoring

    # ── Draw-aware probability model ──
    # Base draw probability from league average + team draw rates
    league_draw_rate = 1.0 - league_home_rate - (1.0 - league_home_rate) * 0.58
    league_draw_rate = max(0.18, min(0.35, league_draw_rate))

    # Draw gets boosted when teams are similar
    draw_boost = 0.0
    if quality_gap < 0.3:
        draw_boost += 0.06
    if quality_gap < 0.15:
        draw_boost += 0.04
    if low_scoring:
        draw_boost += 0.03
    if features["draw_tendency"] > 0.28:
        draw_boost += 0.04

    d_prob = league_draw_rate + draw_boost
    d_prob = max(0.12, min(0.38, d_prob))

    # Remaining probability split between H and A
    remaining = 1.0 - d_prob

    # Home vs away split using composite score
    h_score = (
        features["strength_diff"] * 0.25 +
        features["ppg_diff"] * 0.20 +
        features["goal_diff"] * 0.15 +
        (league_home_rate - 0.33) * 0.25 +
        features["h2h_bias"] * 0.15
    )

    # Convert score to home share of remaining probability
    home_share = 0.58 + h_score * 1.2  # 0.58 is average home share
    home_share = max(0.25, min(0.80, home_share))

    h_prob = remaining * home_share
    a_prob = remaining * (1.0 - home_share)

    # Load tuning weights corrections
    try:
        from src.model.ml_feedback_loop import get_tuning_weights
        weights = get_tuning_weights()
        h_prob += weights.get("home_bias_correction", 0)
        d_prob += weights.get("draw_boost", 0)
        a_prob -= weights.get("home_bias_correction", 0) + weights.get("draw_boost", 0)
    except Exception:
        pass

    # Normalize
    total = max(h_prob, 0.05) + max(d_prob, 0.05) + max(a_prob, 0.05)
    h_prob = max(0.05, h_prob) / total
    d_prob = max(0.05, d_prob) / total
    a_prob = max(0.05, a_prob) / total

    probs = {"H": h_prob, "D": d_prob, "A": a_prob}
    predicted = max(probs, key=probs.get)
    confidence = max(probs.values()) * 100

    return {
        "predicted": predicted,
        "h_prob": round(h_prob, 4),
        "d_prob": round(d_prob, 4),
        "a_prob": round(a_prob, 4),
        "confidence": round(confidence, 1),
        "method": "retro_v2_draw_aware",
        "features": features,
    }


def _store_retro_prediction(db, match, prediction):
    """Store retroactive prediction in predictions table."""
    try:
        existing = db.fetchone(
            "SELECT id FROM predictions WHERE match_id = ?", (match["id"],)
        )
        if existing:
            return

        db.execute("""
            INSERT INTO predictions (match_id, home_win_prob, draw_prob, away_win_prob,
                                     confidence_score, predicted_result, actual_result,
                                     model_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'retro_statistical', CURRENT_TIMESTAMP)
        """, (
            match["id"],
            prediction["h_prob"],
            prediction["d_prob"],
            prediction["a_prob"],
            int(prediction["confidence"]),
            prediction["predicted"],
            match["ft_result"],
        ))
    except Exception:
        pass  # Skip duplicates silently


def _generate_insights(all_results):
    """Analyze prediction patterns to find what works and what doesn't."""
    insights = {
        "per_season": {},
        "feature_importance": {},
        "error_patterns": {},
        "league_difficulty": {},
    }

    all_details = []
    for season_data in all_results:
        season = season_data["season"]
        insights["per_season"][season] = {
            "accuracy": season_data["accuracy"],
            "total": season_data["total"],
        }
        all_details.extend(season_data["details"])

    if not all_details:
        return insights

    # Feature importance: which features correlate with correct predictions?
    feature_keys = list(all_details[0].get("features", {}).keys())
    for fk in feature_keys:
        correct_vals = [d["features"].get(fk, 0) for d in all_details if d["correct"]]
        wrong_vals = [d["features"].get(fk, 0) for d in all_details if not d["correct"]]

        if correct_vals and wrong_vals:
            correct_mean = sum(correct_vals) / len(correct_vals)
            wrong_mean = sum(wrong_vals) / len(wrong_vals)
            insights["feature_importance"][fk] = {
                "correct_mean": round(correct_mean, 4),
                "wrong_mean": round(wrong_mean, 4),
                "impact": round(abs(correct_mean - wrong_mean), 4),
                "direction": "higher_better" if correct_mean > wrong_mean else "lower_better",
            }

    # Sort by impact
    sorted_features = sorted(
        insights["feature_importance"].items(),
        key=lambda x: x[1]["impact"], reverse=True
    )
    insights["feature_ranking"] = [f[0] for f in sorted_features]

    # Error patterns: when do we fail most?
    wrong = [d for d in all_details if not d["correct"]]
    correct = [d for d in all_details if d["correct"]]

    # High confidence failures
    high_conf_wrong = [d for d in wrong if d["confidence"] > 60]
    insights["error_patterns"]["high_confidence_failures"] = {
        "count": len(high_conf_wrong),
        "pct_of_errors": round(len(high_conf_wrong) / max(len(wrong), 1) * 100, 1),
    }

    # Prediction bias
    pred_counts = {"H": 0, "D": 0, "A": 0}
    actual_counts = {"H": 0, "D": 0, "A": 0}
    for d in all_details:
        pred_counts[d["predicted"]] = pred_counts.get(d["predicted"], 0) + 1
        actual_counts[d["actual"]] = actual_counts.get(d["actual"], 0) + 1

    total = len(all_details)
    insights["error_patterns"]["prediction_bias"] = {
        "predicted_H_pct": round(pred_counts["H"] / total * 100, 1),
        "predicted_D_pct": round(pred_counts["D"] / total * 100, 1),
        "predicted_A_pct": round(pred_counts["A"] / total * 100, 1),
        "actual_H_pct": round(actual_counts["H"] / total * 100, 1),
        "actual_D_pct": round(actual_counts["D"] / total * 100, 1),
        "actual_A_pct": round(actual_counts["A"] / total * 100, 1),
    }

    # Draw blindness check
    draw_predicted = pred_counts.get("D", 0)
    draw_actual = actual_counts.get("D", 0)
    if draw_actual > 0:
        insights["error_patterns"]["draw_detection_rate"] = round(
            draw_predicted / draw_actual * 100, 1
        )

    # Confidence calibration
    conf_buckets = {}
    for d in all_details:
        bucket = int(d["confidence"] // 10) * 10
        if bucket not in conf_buckets:
            conf_buckets[bucket] = {"total": 0, "correct": 0}
        conf_buckets[bucket]["total"] += 1
        if d["correct"]:
            conf_buckets[bucket]["correct"] += 1

    insights["confidence_calibration"] = {}
    for bucket, stats in sorted(conf_buckets.items()):
        acc = stats["correct"] / stats["total"] if stats["total"] > 0 else 0
        insights["confidence_calibration"][f"{bucket}-{bucket+10}"] = {
            "total": stats["total"],
            "accuracy": round(acc, 4),
            "expected": round(bucket / 100 + 0.05, 2),
            "calibrated": abs(acc - (bucket / 100 + 0.05)) < 0.10,
        }

    return insights


def _apply_insights_to_weights(insights):
    """Apply retroactive learning insights to tuning weights."""
    from src.model.ml_feedback_loop import TUNING_FILE

    try:
        if TUNING_FILE.exists():
            with open(TUNING_FILE, "r") as f:
                weights = json.load(f)
        else:
            weights = {}

        # Feature ranking
        weights["feature_ranking"] = insights.get("feature_ranking", [])

        # Prediction bias correction
        bias = insights.get("error_patterns", {}).get("prediction_bias", {})
        if bias:
            pred_h = bias.get("predicted_H_pct", 45)
            actual_h = bias.get("actual_H_pct", 44)
            if pred_h - actual_h > 3:
                weights["home_bias_correction"] = -0.02
            elif actual_h - pred_h > 3:
                weights["home_bias_correction"] = 0.02
            else:
                weights["home_bias_correction"] = 0

        # Draw detection correction
        draw_rate = insights.get("error_patterns", {}).get("draw_detection_rate", 100)
        if draw_rate < 50:
            weights["draw_boost"] = 0.03
        elif draw_rate > 150:
            weights["draw_boost"] = -0.02
        else:
            weights["draw_boost"] = 0

        # Confidence calibration adjustment
        cal = insights.get("confidence_calibration", {})
        overconfident_bands = sum(
            1 for bucket, data in cal.items()
            if not data.get("calibrated") and data.get("accuracy", 0) < data.get("expected", 0)
        )
        if overconfident_bands > 2:
            weights["global_overconfidence_penalty"] = min(
                weights.get("global_overconfidence_penalty", 0) + 0.01, 0.06
            )

        # Overall accuracy from retroactive
        weights["retro_accuracy"] = insights.get("overall", {}).get("accuracy", 0)
        weights["retro_total"] = insights.get("overall", {}).get("total", 0)
        weights["last_retro_run"] = datetime.now().isoformat()

        TUNING_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TUNING_FILE, "w") as f:
            json.dump(weights, f, indent=2)

        logger.info(f"Tuning weights guncellendi: {TUNING_FILE}")

    except Exception as e:
        logger.error(f"Weights update hatasi: {e}")


def print_insights_summary(insights):
    """Print human-readable summary."""
    print("\n" + "=" * 50)
    print("  RETROACTIVE LEARNING SONUCLARI")
    print("=" * 50)

    overall = insights.get("overall", {})
    print(f"\nGenel: %{overall.get('accuracy', 0) * 100:.1f} ({overall.get('correct', 0)}/{overall.get('total', 0)})")

    # Per season
    for season, data in insights.get("per_season", {}).items():
        print(f"  {season}: %{data['accuracy']*100:.1f} ({data['total']} mac)")

    # Feature ranking
    ranking = insights.get("feature_ranking", [])
    if ranking:
        print(f"\nEn etkili feature'lar:")
        for i, f in enumerate(ranking[:5], 1):
            imp = insights["feature_importance"][f]
            print(f"  {i}. {f}: etki={imp['impact']:.4f} ({imp['direction']})")

    # Error patterns
    errors = insights.get("error_patterns", {})
    bias = errors.get("prediction_bias", {})
    if bias:
        print(f"\nTahmin bias:")
        print(f"  Tahmin: H:%{bias.get('predicted_H_pct',0):.0f} D:%{bias.get('predicted_D_pct',0):.0f} A:%{bias.get('predicted_A_pct',0):.0f}")
        print(f"  Gercek: H:%{bias.get('actual_H_pct',0):.0f} D:%{bias.get('actual_D_pct',0):.0f} A:%{bias.get('actual_A_pct',0):.0f}")

    draw_det = errors.get("draw_detection_rate", 0)
    if draw_det:
        print(f"  Beraberlik tespit orani: %{draw_det:.0f}")

    # Confidence calibration
    cal = insights.get("confidence_calibration", {})
    if cal:
        print(f"\nGuven kalibrasyonu:")
        for bucket, data in sorted(cal.items()):
            icon = "[OK]" if data.get("calibrated") else "[!!]"
            print(f"  {icon} %{bucket}: dogruluk=%{data['accuracy']*100:.0f} (beklenen=%{data['expected']*100:.0f}) [{data['total']} mac]")

    hcf = errors.get("high_confidence_failures", {})
    if hcf:
        print(f"\nYuksek guvenli hatalar: {hcf.get('count', 0)} (%{hcf.get('pct_of_errors', 0):.0f} hatalarin)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retroactive Learner")
    parser.add_argument("--all", action="store_true", help="Process all seasons")
    parser.add_argument("--season", type=str, help="Specific season code (e.g. 2425)")
    args = parser.parse_args()

    if args.all:
        target_seasons = list(SEASON_LABELS.values())
    elif args.season:
        target_seasons = [SEASON_LABELS.get(args.season, args.season)]
    else:
        target_seasons = ["2025-2026", "2024-2025"]

    insights = run(seasons=target_seasons)
    print_insights_summary(insights)
