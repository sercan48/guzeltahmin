"""Match prediction v3 — Ensemble-powered with Pre-Match Intelligence.

Pipeline:
  32 Features + Odds Features
    -> StackingEnsemble (XGB + LGB + Poisson)
    -> Platt Scaling (calibration)
    -> Value Detection Layer
    -> Explainable Prediction Card
"""

import logging
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from config.settings import MODELS_DIR, RANDOM_SEED
from config.constants import FEATURE_COLUMNS, LABEL_MAP, LABEL_MAP_INV

logger = logging.getLogger(__name__)
def build_match_features(db, home_team_id: int, away_team_id: int,
                          league_code: str, season: str,
                          referee_name: str = "Unknown",
                          match_id: int = None,
                          match_date: str = None) -> dict:
    """Build all features for a single match prediction.

    Assembles 32 base features + 3 odds features = 35 total.
    """
    from src.features.team_strength import team_strength, home_advantage_factor, get_league_standings, last_n_form
    from src.features.form_calculator import form_momentum, head_to_head, goals_stats
    from src.features.player_impact import (
        team_attack_rating, team_defense_rating,
        squad_market_value, load_team_players,
    )
    from src.features.referee_impact import referee_strictness
    from datetime import datetime, date

    # Get match details (with context columns for summer leagues)
    if match_id:
        match_row = db.fetchone("SELECT * FROM matches WHERE id=?", (match_id,))
    elif match_date:
        match_row = db.fetchone("SELECT * FROM matches WHERE home_team_id=? AND away_team_id=? AND date LIKE ? LIMIT 1", (home_team_id, away_team_id, f"{match_date}%"))
    else:
        match_row = db.fetchone(
            "SELECT * FROM matches WHERE home_team_id=? AND away_team_id=? AND ft_result IS NULL ORDER BY date LIMIT 1",
            (home_team_id, away_team_id)
        )
        if not match_row:
            match_row = db.fetchone(
                "SELECT * FROM matches WHERE home_team_id=? AND away_team_id=? ORDER BY date DESC LIMIT 1",
                (home_team_id, away_team_id)
            )

    match_date_str = str(match_row["date"])[:10] if match_row and match_row.get("date") else (match_date or date.today().isoformat())
    ref_dt = datetime.strptime(match_date_str, "%Y-%m-%d")

    standings = get_league_standings(db, league_code, season, before_date=match_date_str)

    home_matches = pd.DataFrame(db.fetchall(
        "SELECT * FROM matches WHERE (home_team_id=? OR away_team_id=?) AND season=? AND date < ? ORDER BY date",
        (home_team_id, home_team_id, season, match_date_str),
    ))
    away_matches = pd.DataFrame(db.fetchall(
        "SELECT * FROM matches WHERE (home_team_id=? OR away_team_id=?) AND season=? AND date < ? ORDER BY date",
        (away_team_id, away_team_id, season, match_date_str),
    ))

    home_str = team_strength(home_matches, home_team_id, reference_date=ref_dt, league_standings=standings)
    away_str = team_strength(away_matches, away_team_id, reference_date=ref_dt, league_standings=standings)
    home_form = last_n_form(home_matches, home_team_id, reference_date=ref_dt, league_standings=standings)
    away_form = last_n_form(away_matches, away_team_id, reference_date=ref_dt, league_standings=standings)

    home_players = load_team_players(db, home_team_id)
    away_players = load_team_players(db, away_team_id)

    home_atk = team_attack_rating(home_players) if home_players else 50.0
    home_def = team_defense_rating(home_players) if home_players else 50.0
    away_atk = team_attack_rating(away_players) if away_players else 50.0
    away_def = team_defense_rating(away_players) if away_players else 50.0

    home_goals = goals_stats(db, home_team_id, season, before_date=match_date_str)
    away_goals = goals_stats(db, away_team_id, season, before_date=match_date_str)
    h2h = head_to_head(db, home_team_id, away_team_id, before_date=match_date_str)
    ha_factor = home_advantage_factor(db, league_code, before_date=match_date_str)
    ref = referee_strictness(db, referee_name, league_code)
    home_val = squad_market_value(home_players)
    away_val = squad_market_value(away_players)
    home_momentum = form_momentum(home_matches, home_team_id)
    away_momentum = form_momentum(away_matches, away_team_id)
    home_pos = standings.get(home_team_id, 10)
    away_pos = standings.get(away_team_id, 10)

    from src.features.match_context_engine import check_is_derby, calculate_red_card_risk
    from src.features.efficiency_engine import simulated_xg_efficiency

    ht_row = db.fetchone("SELECT name FROM teams WHERE id=?", (home_team_id,))
    ht_name = ht_row["name"] if ht_row else f"Team_{home_team_id}"
    at_row = db.fetchone("SELECT name FROM teams WHERE id=?", (away_team_id,))
    at_name = at_row["name"] if at_row else f"Team_{away_team_id}"

    is_derby = check_is_derby(ht_name, at_name)
    red_card_risk = calculate_red_card_risk(home_def, away_def, ref["strictness_score"])
    home_xg_eff = simulated_xg_efficiency(home_goals["avg_scored"], home_atk)
    away_xg_eff = simulated_xg_efficiency(away_goals["avg_scored"], away_atk)

    from src.features.fixture_congestion import compute_match_congestion
    from src.features.xg_features import compute_match_xg_features

    # xG features
    xg_features = compute_match_xg_features(db, home_team_id, away_team_id, league_code)
    home_xg_avg = xg_features.get("home_xg_avg", 0.0)
    away_xg_avg = xg_features.get("away_xg_avg", 0.0)
    home_xg_overperformance = xg_features.get("home_xg_overperformance", 0.0)
    away_xg_overperformance = xg_features.get("away_xg_overperformance", 0.0)

    # Congestion features
    congestion_features = compute_match_congestion(db, home_team_id, away_team_id, match_date_str)
    home_congestion_score = congestion_features.get("home_congestion_score", 0.0)
    away_congestion_score = congestion_features.get("away_congestion_score", 0.0)
    congestion_advantage = congestion_features.get("congestion_advantage", 0.0)

    # Clean sheet difference helper
    def _cs_rate(matches_df, t_id):
        if len(matches_df) == 0:
            return 0.0
        cs = 0
        for _, m in matches_df.iterrows():
            is_home = m["home_team_id"] == t_id
            conceded = m["ft_away_goals"] if is_home else m["ft_home_goals"]
            if conceded == 0:
                cs += 1
        return cs / len(matches_df)

    home_cs_rate = _cs_rate(home_matches, home_team_id)
    away_cs_rate = _cs_rate(away_matches, away_team_id)
    clean_sheet_rate_diff = home_cs_rate - away_cs_rate

    features = {
        "home_team_strength": home_str,
        "away_team_strength": away_str,
        "home_form_last5": home_form,
        "away_form_last5": away_form,
        "home_attack_rating": home_atk,
        "home_defense_rating": home_def,
        "away_attack_rating": away_atk,
        "away_defense_rating": away_def,
        "home_goals_scored_avg": home_goals["avg_scored"],
        "home_goals_conceded_avg": home_goals["avg_conceded"],
        "away_goals_scored_avg": away_goals["avg_scored"],
        "away_goals_conceded_avg": away_goals["avg_conceded"],
        "h2h_home_winrate": h2h["team1_winrate"],
        "h2h_goals_avg": h2h["avg_goals"],
        "home_advantage_factor": ha_factor,
        "referee_strictness": ref["strictness_score"],
        "home_squad_value": home_val,
        "away_squad_value": away_val,
        "form_momentum_diff": home_momentum - away_momentum,
        "league_position_diff": away_pos - home_pos,
        "is_derby": is_derby,
        "red_card_risk": red_card_risk,
        "home_xg_efficiency": home_xg_eff,
        "away_xg_efficiency": away_xg_eff,
        # New features
        "home_xg_avg": home_xg_avg,
        "away_xg_avg": away_xg_avg,
        "home_xg_overperformance": home_xg_overperformance,
        "away_xg_overperformance": away_xg_overperformance,
        "home_congestion_score": home_congestion_score,
        "away_congestion_score": away_congestion_score,
        "congestion_advantage": congestion_advantage,
        "clean_sheet_rate_diff": clean_sheet_rate_diff,
        "implied_home_prob": 0.33,
        "implied_away_prob": 0.33,
        "implied_draw_prob": 0.33,
    }

    # Odds features (if available)
    odds = _get_latest_odds(db, home_team_id, away_team_id)
    if odds:
        p_h = 1 / odds["h"] if odds["h"] else 0
        p_d = 1 / odds["d"] if odds["d"] else 0
        p_a = 1 / odds["a"] if odds["a"] else 0
        implied_sum = p_h + p_d + p_a
        if implied_sum > 0:
            features["implied_home_prob"] = p_h / implied_sum
            features["implied_away_prob"] = p_a / implied_sum
            features["implied_draw_prob"] = p_d / implied_sum

    # Context metadata (not used in model, but in output)
    features["_home_name"] = ht_name
    features["_away_name"] = at_name
    features["_home_pos"] = home_pos
    features["_away_pos"] = away_pos
    features["_odds"] = odds

    # Summer league context columns
    if match_row:
        features["_pitch_type"] = match_row.get("pitch_type", "NATURAL")
        features["_travel_distance"] = match_row.get("travel_distance_km")
        features["_cup_rotation_fatigue"] = bool(match_row.get("cup_rotation_fatigue", False))
        features["_weather_condition"] = match_row.get("weather_condition", "NORMAL")
        features["_home_dp_ratio"] = match_row.get("dp_presence", 0.0)
        features["_away_dp_ratio"] = 0.0
    else:
        features["_pitch_type"] = "NATURAL"
        features["_travel_distance"] = None
        features["_cup_rotation_fatigue"] = False
        features["_weather_condition"] = "NORMAL"
        features["_home_dp_ratio"] = 0.0
        features["_away_dp_ratio"] = 0.0

    # Add summer features to FEATURE_COLUMNS
    dist = features["_travel_distance"]
    if dist is None or dist == 0:
        is_summer = league_code in ["NORWAY_ELITESERIEN", "Eliteserien", "BRAZIL_SERIE_A", "Serie A", "SWEDEN_ALLSVENSKAN", "Allsvenskan", "FINLAND_VEIKKAUSLIIGA", "Veikkausliiga", "USA_MLS"]
        if is_summer:
            fallbacks = {
                "NORWAY_ELITESERIEN": 450.0,
                "Eliteserien": 450.0,
                "SWEDEN_ALLSVENSKAN": 380.0,
                "Allsvenskan": 380.0,
                "FINLAND_VEIKKAUSLIIGA": 300.0,
                "Veikkausliiga": 300.0,
                "BRAZIL_SERIE_A": 950.0,
                "Serie A": 950.0,
                "USA_MLS": 800.0
            }
            dist = fallbacks.get(league_code, 300.0)
        else:
            dist = 0.0

    features["travel_distance_km"] = float(dist)
    features["is_artificial_pitch"] = float(1.0 if features["_pitch_type"] == "ARTIFICIAL" else 0.0)
    features["cup_rotation_fatigue"] = float(1.0 if features["_cup_rotation_fatigue"] else 0.0)
    features["dp_presence"] = float(features["_home_dp_ratio"])
    features["extreme_humidity"] = float(1.0 if features["_weather_condition"] == "EXTREME_HUMIDITY" else 0.0)

    return features


def _get_latest_odds(db, home_team_id: int, away_team_id: int) -> dict:
    """Get latest odds for a matchup."""
    row = db.fetchone("""
        SELECT o.home_odds, o.draw_odds, o.away_odds, o.over25_odds, o.under25_odds
        FROM odds o
        JOIN matches m ON o.match_id = m.id
        WHERE m.home_team_id = ? AND m.away_team_id = ?
        ORDER BY m.date DESC LIMIT 1
    """, (home_team_id, away_team_id))

    if row:
        return {
            "h": row["home_odds"] or 2.5,
            "d": row["draw_odds"] or 3.3,
            "a": row["away_odds"] or 3.0,
            "o25": row.get("over25_odds"),
            "u25": row.get("under25_odds"),
        }
    return None


def _load_ensemble():
    """Load trained StackingEnsemble, fall back to XGBoost if unavailable."""
    ensemble_path = MODELS_DIR / "ensemble"
    if (ensemble_path / "meta.pkl").exists():
        from src.model.ensemble import StackingEnsemble
        ens = StackingEnsemble()
        ens.load(ensemble_path)
        return ens, "ensemble"

    # Fallback: legacy single XGBoost
    from src.model.trainer import load_model
    return load_model(), "xgboost"


def _load_calibrator(league_code: str = None):
    """Load pre-fitted probability calibrator (Platt Scaling / Isotonic / Beta / Temp).

    Returns the calibrator or None if not available.
    """
    if league_code:
        cal_path = MODELS_DIR / "ensemble" / f"calibrator_{league_code}.pkl"
        if cal_path.exists():
            try:
                with open(cal_path, "rb") as f:
                    calibrator = pickle.load(f)
                logger.info(f"[Calibration] Loaded league-specific calibrator for {league_code} from disk.")
                return calibrator
            except Exception as e:
                logger.warning(f"[Calibration] Failed to load calibrator for {league_code}: {e}")

    cal_path = MODELS_DIR / "ensemble" / "calibrator.pkl"
    if cal_path.exists():
        try:
            with open(cal_path, "rb") as f:
                calibrator = pickle.load(f)
            logger.info("[Calibration] Loaded global calibrator from disk.")
            return calibrator
        except Exception as e:
            logger.warning(f"[Calibration] Failed to load global calibrator: {e}")
    return None


def _calibrate_probs(raw_probs: np.ndarray, calibrator) -> np.ndarray:
    """Apply calibrator to raw ensemble probabilities.

    Args:
        raw_probs: shape (1, 3) — [H, D, A] probabilities
        calibrator: fitted calibrator object

    Returns:
        Calibrated probabilities, normalized to sum=1.
    """
    if calibrator is None:
        return raw_probs

    try:
        if hasattr(calibrator, "predict"):
            cal = calibrator.predict(raw_probs)
        elif hasattr(calibrator, "predict_proba"):
            cal = calibrator.predict_proba(raw_probs)
        elif isinstance(calibrator, dict):
            # Per-class IsotonicRegression dict: {0: ir_h, 1: ir_d, 2: ir_a}
            cal = np.zeros_like(raw_probs)
            for cls_idx, ir in calibrator.items():
                cal[:, cls_idx] = ir.transform(raw_probs[:, cls_idx])
        else:
            return raw_probs

        # Normalize to sum=1
        row_sums = cal.sum(axis=1, keepdims=True)
        row_sums = np.where(row_sums == 0, 1, row_sums)
        cal = cal / row_sums
        return cal
    except Exception as e:
        logger.warning(f"[Calibration] Error during calibration, using raw: {e}")
        return raw_probs


def predict_match(db, home_team_id: int, away_team_id: int,
                  league_code: str, season: str,
                  referee_name: str = "Unknown",
                  use_weather: bool = False,
                  home_team_name: str = "",
                  home_missing_count: int = 0,
                  away_missing_count: int = 0,
                  temperature: float = 1.15,
                  match_id: int = None,
                  match_date: str = None) -> dict:
    """Predict a single match with Ensemble + Calibration + Value Detection.

    Returns explainable prediction dict with:
      - Probabilities (calibrated)
      - Model breakdown (per-model probs)
      - Value bet detection
      - Confidence score (0-10)
      - Explanation factors
      - Live betting guide
      - Pre-match checklist
    """
    # Load temperature dynamically from data/admin_config.json if exists
    import json
    from pathlib import Path
    config_path = Path(__file__).parent.parent.parent / "data" / "admin_config.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
                temperature = config_data.get("temperature", temperature)
        except Exception:
            pass

    features = build_match_features(
        db, home_team_id, away_team_id, league_code, season, referee_name,
        match_id=match_id, match_date=match_date
    )

    model, model_type = _load_ensemble()
    calibrator = _load_calibrator(league_code=league_code)

    # Determine how many features the model expects
    if model_type == "ensemble" and hasattr(model, 'xgb_model'):
        n_expected = getattr(model.xgb_model, 'n_features_in_', len(FEATURE_COLUMNS))
    else:
        n_expected = getattr(model, 'n_features_in_', len(FEATURE_COLUMNS))
    
    # Handle legacy models expecting 24 features vs new ones expecting 40
    use_columns = FEATURE_COLUMNS[:n_expected]
    
    # Build feature vector dynamically based on model's expected input shape
    # Fill None values with 0 to prevent "unsupported operand type(s) for -: 'NoneType'"
    X = np.array([[float(features.get(col) or 0.0) for col in use_columns]])

    if model_type == "ensemble":
        result = model.predict_full(X, league_code=league_code)
        h_prob = result["h_prob"]
        d_prob = result["d_prob"]
        a_prob = result["a_prob"]
        model_agreement = result.get("model_agreement", 0)
        xgb_probs = result.get("xgb_probs", [h_prob, d_prob, a_prob])
        lgb_probs = result.get("lgb_probs", [h_prob, d_prob, a_prob])
        cat_probs = result.get("cat_probs", [h_prob, d_prob, a_prob])
        poi_probs = result.get("poi_probs", [h_prob, d_prob, a_prob])
        over25 = result.get("over25_prob", 0.5)
        btts = result.get("btts_prob", 0.5)
        top_scores = result.get("top_scores", [])
        home_lambda = result.get("home_lambda", 1.3)
        away_lambda = result.get("away_lambda", 1.1)
    else:
        # Legacy single model
        probs = model.predict_proba(X)[0]
        h_prob, d_prob, a_prob = float(probs[0]), float(probs[1]), float(probs[2])
        model_agreement = 1.0
        xgb_probs = [h_prob, d_prob, a_prob]
        lgb_probs = xgb_probs
        cat_probs = xgb_probs
        poi_probs = xgb_probs
        over25 = 0.5
        btts = 0.5
        top_scores = []
        home_lambda = features.get("home_goals_scored_avg", 1.3)
        away_lambda = features.get("away_goals_scored_avg", 1.1)

    # ── Platt Scaling / Isotonic Calibration ─────────────────────────
    raw_probs = np.array([[h_prob, d_prob, a_prob]])
    cal_probs = _calibrate_probs(raw_probs, calibrator)
    h_prob, d_prob, a_prob = float(cal_probs[0][0]), float(cal_probs[0][1]), float(cal_probs[0][2])

    # ── Weather Multiplier Integration ──────────────────────────────
    weather_info = None
    if use_weather:
        try:
            from src.ingestion.weather_client import WeatherClient
            from src.features.weather_multiplier import apply_weather_multiplier
            weather_client = WeatherClient()
            h_name = home_team_name or features.get("_home_name")
            weather_info = weather_client.get_current_weather(h_name)
            if weather_info and weather_info.get("condition") != "Unknown":
                adj = apply_weather_multiplier({"H": h_prob, "D": d_prob, "A": a_prob}, weather_info)
                h_prob, d_prob, a_prob = adj["H"], adj["D"], adj["A"]
                # Update features so it has the resolved weather condition
                features["_weather_condition"] = weather_info.get("condition", "NORMAL")
                logger.info(f"[Weather] Applied weather multiplier. Condition: {weather_info['condition']}, Temp: {weather_info['temp']}C")
        except Exception as we:
            logger.warning(f"[Weather] Failed to apply weather multiplier: {we}")

    # ── Agent Penalty Layer (injury/availability from DB cache) ───────
    home_absences = []
    away_absences = []
    home_power_loss = 0.0
    away_power_loss = 0.0

    try:
        from src.agents.data_agent import get_team_status_from_db, apply_agent_penalty
        home_status = get_team_status_from_db(db, home_team_id)
        away_status = get_team_status_from_db(db, away_team_id)

        import json
        if home_status:
            try:
                home_absences = json.loads(home_status.get("key_absences", "[]"))
            except Exception:
                home_absences = []
            home_power_loss = float(home_status.get("power_loss_pct", 0.0))

        if away_status:
            try:
                away_absences = json.loads(away_status.get("key_absences", "[]"))
            except Exception:
                away_absences = []
            away_power_loss = float(away_status.get("power_loss_pct", 0.0))

        # Override missing counts from agent data if caller didn't provide them
        if home_missing_count == 0 and home_status["injury_count"] > 0:
            home_missing_count = home_status["injury_count"]
        if away_missing_count == 0 and away_status["injury_count"] > 0:
            away_missing_count = away_status["injury_count"]

        h_prob, d_prob, a_prob = apply_agent_penalty(
            h_prob, d_prob, a_prob, home_status, away_status,
            temperature=temperature
        )
    except Exception as e:
        logger.debug(f"[Predictor] Agent penalty skipped: {e}")

    # Log Probability Pipeline Stages for diagnostics
    logger.info(
        f"[Probability Pipeline Log] Match: {features.get('_home_name', 'Home')} vs {features.get('_away_name', 'Away')}\n"
        f"  - BASE MODELS:\n"
        f"    * XGBoost:  H: {xgb_probs[0]:.4f} | D: {xgb_probs[1]:.4f} | A: {xgb_probs[2]:.4f}\n"
        f"    * LightGBM: H: {lgb_probs[0]:.4f} | D: {lgb_probs[1]:.4f} | A: {lgb_probs[2]:.4f}\n"
        f"    * Poisson:  H: {poi_probs[0]:.4f} | D: {poi_probs[1]:.4f} | A: {poi_probs[2]:.4f}\n"
        f"  - ENSEMBLE RAW: H: {raw_probs[0][0]:.4f} | D: {raw_probs[0][1]:.4f} | A: {raw_probs[0][2]:.4f}\n"
        f"  - CALIBRATED:   H: {cal_probs[0][0]:.4f} | D: {cal_probs[0][1]:.4f} | A: {cal_probs[0][2]:.4f}\n"
        f"  - FINAL:        H: {h_prob:.4f} | D: {d_prob:.4f} | A: {a_prob:.4f}"
    )

    # Apply Summer League Modifiers if applicable
    from src.model.summer_league_modifier import apply_summer_modifiers, SUMMER_LEAGUES
    if league_code in SUMMER_LEAGUES:
        prob_dict = {"home_win": h_prob * 100, "draw": d_prob * 100, "away_win": a_prob * 100}
        
        # Runtime fallbacks if distance is NULL
        distance = features.get("_travel_distance")
        if distance is None or distance == 0:
            fallbacks = {
                "NORWAY_ELITESERIEN": 450.0,
                "Eliteserien": 450.0,
                "SWEDEN_ALLSVENSKAN": 380.0,
                "Allsvenskan": 380.0,
                "FINLAND_VEIKKAUSLIIGA": 300.0,
                "Veikkausliiga": 300.0,
                "BRAZIL_SERIE_A": 950.0,
                "Serie A": 950.0
            }
            distance = fallbacks.get(league_code, 300.0)
            logger.info(f"[Fallback] Travel distance is NULL. Using league fallback: {distance}km for {league_code}")

        match_data = {
            "pitch_type": features.get("_pitch_type", "NATURAL"),
            "travel_distance_km": distance,
            "cup_rotation_fatigue": features.get("_cup_rotation_fatigue", False),
            "home_dp_ratio": features.get("_home_dp_ratio", 0),
            "away_dp_ratio": features.get("_away_dp_ratio", 0),
            "weather_condition": features.get("_weather_condition", "NORMAL")
        }
        modified = apply_summer_modifiers(prob_dict, match_data, league_code)
        h_prob = modified["home_win"] / 100
        d_prob = modified["draw"] / 100
        a_prob = modified["away_win"] / 100

    # Predicted result
    probs_map = {"H": h_prob, "D": d_prob, "A": a_prob}
    predicted_result = max(probs_map, key=probs_map.get)
    max_prob = max(h_prob, d_prob, a_prob)

    # Confidence score (0-10) — multi-factor
    confidence = _calculate_confidence(
        max_prob, model_agreement, features, h_prob, d_prob, a_prob,
        home_missing_count, away_missing_count,
    )

    # ── League-Aware Metadata & Decision Score ───────────────────────
    from src.model.league_classifier import LeagueClassifier
    classifier = LeagueClassifier(db)
    resolved_league_type = classifier.get_league_type(league_code)

    # Get sample size from matches count in DB (fallback to 100)
    sample_size = 100
    if db:
        try:
            row = db.fetchone(
                "SELECT COUNT(*) as count FROM matches WHERE league_code = ? AND ft_result IS NOT NULL",
                (league_code,)
            )
            if row and row["count"] > 0:
                sample_size = row["count"]
        except Exception:
            pass

    # Fetch dynamic calibration meta for coverage (fallback to 0.20)
    coverage = 0.20
    meta_path = MODELS_DIR / "ensemble" / f"calibrator_{league_code}_meta.json"
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_data = json.load(f)
                coverage = meta_data.get("coverage", 0.20)
        except Exception:
            pass

    # Calculate Decision Scores for H, D, A
    # Formula: calibrated_prob * confidence_factor * sample_size_factor * coverage_factor
    conf_factor = 0.85 + 0.30 * (confidence / 10.0)
    sample_size_factor = 1.0 - 0.15 * np.exp(-sample_size / 50.0)
    coverage_factor = 0.9 + 0.2 * min(max(coverage, 0.0), 1.0)
    
    ds_multiplier = conf_factor * sample_size_factor * coverage_factor
    decision_scores = {
        "H": round(h_prob * ds_multiplier, 4),
        "D": round(d_prob * ds_multiplier, 4),
        "A": round(a_prob * ds_multiplier, 4),
    }

    # Value detection
    odds = features.get("_odds")
    value_info = {}
    value_edge = 0.0
    value_class = "NO_VALUE"
    if odds:
        from src.features.pre_match_intel import detect_value_bet
        from src.model.value_clv_engine import clean_implied_probabilities, calculate_edge, classify_value
        
        odds_map = {"H": odds["h"], "D": odds["d"], "A": odds["a"]}
        prob_map = {"H": h_prob, "D": d_prob, "A": a_prob}
        for outcome in ["H", "D", "A"]:
            value_info[outcome] = detect_value_bet(prob_map[outcome], odds_map[outcome])
            
        o_h = odds.get("home_odds") or odds.get("h")
        o_d = odds.get("draw_odds") or odds.get("d")
        o_a = odds.get("away_odds") or odds.get("a")
        if o_h and o_d and o_a:
            clean_h, clean_d, clean_a = clean_implied_probabilities(o_h, o_d, o_a)
            
            # Resolve predicted outcome to calculate edge
            pred_res_norm = predicted_result
            if predicted_result in ("MS 1", "1", "Ev Sahibi", "H"):
                model_prob = h_prob
                clean_mkt_prob = clean_h
            elif predicted_result in ("MS X", "X", "Berabere", "D"):
                model_prob = d_prob
                clean_mkt_prob = clean_d
            elif predicted_result in ("MS 2", "2", "Deplasman", "A"):
                model_prob = a_prob
                clean_mkt_prob = clean_a
            else:
                model_prob = 0.0
                clean_mkt_prob = 0.0
                
            if model_prob > 0:
                value_edge = calculate_edge(model_prob, clean_mkt_prob)
                value_class = classify_value(value_edge)

    # Explanation factors
    factors = _build_explanation_factors(features, h_prob, d_prob, a_prob)
    
    # SHAP Explainability Integration
    try:
        from src.model.shap_explainer import SHAPExplainer
        from config.constants import LABEL_MAP
        pred_class_idx = LABEL_MAP[predicted_result]
        shap_exp = SHAPExplainer()
        shap_factors = shap_exp.explain_match(X, pred_class_idx)
        for sf in shap_factors:
            factors.append({
                "direction": sf["direction"],
                "text": f"SHAP: {sf['text']} ({sf['impact'].upper()} etki)",
                "impact": sf["impact"]
            })
    except Exception as e:
        logger.warning(f"[SHAP] Explainability failed to run: {e}")

    # Live betting guide (premium feature — flag-gated)
    from config.settings import LIVE_BETTING_ENABLED
    from src.features.pre_match_intel import (
        generate_live_betting_guide, OddsMovement, build_pre_match_checklist,
        PreMatchIntel,
    )

    if LIVE_BETTING_ENABLED:
        odds_mvmt = OddsMovement()
        live_guide = generate_live_betting_guide(
            {"h_prob": h_prob, "d_prob": d_prob, "a_prob": a_prob,
             "predicted_result": predicted_result, "over25_prob": over25},
            odds_mvmt,
        )
    else:
        live_guide = [{"timing": "PREMIUM", "bet": "Canli Bahis Rehberi yakinda!",
                       "condition": "Premium uyelik gerekli", "stop_loss": None,
                       "confidence": "TEASER"}]

    # Pre-match checklist
    intel = PreMatchIntel(
        match_id=0, home_team=features.get("_home_name", ""),
        away_team=features.get("_away_name", ""),
        match_date=None,
    )
    if home_missing_count > 0 or away_missing_count > 0:
        intel.key_absences = ["Kilit oyuncu eksik"]
    checklist = build_pre_match_checklist(intel)

    # Draw value flag: when model sees draw differently than market
    draw_value_flag = False
    if odds:
        implied_draw = features.get("implied_draw_prob", 0.33)
        if abs(d_prob - implied_draw) > 0.05:
            draw_value_flag = True

    return {
        # Core probabilities (calibrated)
        "home_win_prob": round(h_prob, 4),
        "draw_prob": round(d_prob, 4),
        "away_win_prob": round(a_prob, 4),
        "predicted_result": predicted_result,
        "confidence_score": round(confidence, 1),
        "value_edge": round(value_edge, 4),
        "value_class": value_class,

        # League-aware fields
        "league_type": resolved_league_type,
        "decision_scores": decision_scores,
        "sample_size": sample_size,
        "coverage": coverage,

        # Card formatting support fields
        "home_team": features.get("_home_name", "Home"),
        "away_team": features.get("_away_name", "Away"),
        "league_code": league_code,
        "home_absences": home_absences,
        "away_absences": away_absences,
        "home_power_loss": home_power_loss,
        "away_power_loss": away_power_loss,

        # Model breakdown
        "model_type": model_type,
        "model_agreement": round(model_agreement, 3),
        "xgb_probs": [round(p, 4) for p in xgb_probs],
        "lgb_probs": [round(p, 4) for p in lgb_probs],
        "cat_probs": [round(p, 4) for p in cat_probs],
        "poi_probs": [round(p, 4) for p in poi_probs],

        # Poisson outputs
        "over25_prob": round(over25, 4),
        "btts_prob": round(btts, 4),
        "top_scores": top_scores[:5],
        "home_lambda": round(home_lambda, 2),
        "away_lambda": round(away_lambda, 2),

        # Odds passthrough for EV calculation
        "_odds": odds,
        "over25_odds": odds.get("o25") if odds else None,
        "under25_odds": odds.get("u25") if odds else None,
        "draw_value_flag": draw_value_flag,

        # Value detection
        "value_bets": value_info,

        # Explanation
        "factors": factors,

        # Live guide
        "live_guide": live_guide,
        "checklist": checklist,

        # Raw features
        "features": {k: v for k, v in features.items() if not k.startswith("_")},
    }


def _calculate_confidence(max_prob: float, model_agreement: float,
                          features: dict, h_prob: float, d_prob: float,
                          a_prob: float, home_missing: int = 0,
                          away_missing: int = 0) -> float:
    """Calculate explainable confidence score (0-10).

    Factors:
      - Probability strength (40%)
      - Model agreement (25%)
      - Data quality (15%)
      - Risk factors (20%)
    """
    # Probability strength (0-10)
    prob_score = min(10, max_prob * 14)  # 0.50 = 7, 0.70 = 9.8

    # Model agreement (0-10)
    agree_score = model_agreement * 10

    # Data quality (0-10)
    h2h_data = features.get("h2h_home_winrate", 0.5)
    form_data = features.get("home_form_last5", 0) + features.get("away_form_last5", 0)
    data_score = 7.0
    if h2h_data > 0 and h2h_data < 1:
        data_score += 1.0
    if abs(form_data) > 0:
        data_score += 1.0
    if features.get("implied_home_prob", 0) > 0:
        data_score += 1.0
    data_score = min(10, data_score)

    # Risk penalty (0-10, higher = less risk)
    risk_score = 10.0
    if features.get("is_derby", 0):
        risk_score -= 1.5  # Derbies are unpredictable
    if features.get("red_card_risk", 0) > 0.5:
        risk_score -= 1.0
    if home_missing > 0:
        risk_score -= min(home_missing * 0.5, 2.0)
    if away_missing > 0:
        risk_score -= min(away_missing * 0.5, 2.0)
    # Close match = less confident
    margin = max_prob - sorted([h_prob, d_prob, a_prob])[-2]
    if margin < 0.10:
        risk_score -= 2.0
    risk_score = max(0, risk_score)

    # Weighted combination
    confidence = (
        prob_score * 0.40 +
        agree_score * 0.25 +
        data_score * 0.15 +
        risk_score * 0.20
    )

    return max(1.0, min(10.0, confidence))


def _build_explanation_factors(features: dict, h_prob: float,
                               d_prob: float, a_prob: float) -> list:
    """Build human-readable explanation factors."""
    factors = []
    predicted = max({"H": h_prob, "D": d_prob, "A": a_prob},
                    key={"H": h_prob, "D": d_prob, "A": a_prob}.get)

    # Home advantage
    ha = features.get("home_advantage_factor", 0)
    if ha > 0.55:
        factors.append({
            "direction": "+",
            "text": f"Guclu ev sahibi avantaji ({ha:.0%})",
            "impact": "high" if ha > 0.65 else "medium",
        })

    # Form difference
    form_diff = features.get("form_momentum_diff", 0)
    if abs(form_diff) > 0.2:
        leader = "Ev sahibi" if form_diff > 0 else "Deplasman"
        factors.append({
            "direction": "+" if (form_diff > 0 and predicted == "H") or (form_diff < 0 and predicted == "A") else "-",
            "text": f"{leader} form ustunlugu ({abs(form_diff):.2f})",
            "impact": "medium",
        })

    # H2H
    h2h_wr = features.get("h2h_home_winrate", 0.5)
    if h2h_wr > 0.6:
        factors.append({
            "direction": "+" if predicted == "H" else "-",
            "text": f"H2H ev sahibi baskin (%{h2h_wr*100:.0f})",
            "impact": "medium",
        })
    elif h2h_wr < 0.3:
        factors.append({
            "direction": "+" if predicted == "A" else "-",
            "text": f"H2H deplasman baskin (%{(1-h2h_wr)*100:.0f})",
            "impact": "medium",
        })

    # Quality gap
    pos_diff = features.get("league_position_diff", 0)
    if abs(pos_diff) > 5:
        better = "Ev sahibi" if pos_diff > 0 else "Deplasman"
        factors.append({
            "direction": "+" if (pos_diff > 0 and predicted == "H") or (pos_diff < 0 and predicted == "A") else "-",
            "text": f"{better} lig siralama ustunlugu ({abs(pos_diff)} sira fark)",
            "impact": "high" if abs(pos_diff) > 10 else "medium",
        })

    # Derby risk
    if features.get("is_derby", 0):
        factors.append({
            "direction": "=",
            "text": "Derbi maci — surpriz riski yuksek",
            "impact": "high",
        })

    # xG efficiency
    h_xg = features.get("home_xg_efficiency", 0)
    a_xg = features.get("away_xg_efficiency", 0)
    if abs(h_xg - a_xg) > 0.15:
        better = "Ev sahibi" if h_xg > a_xg else "Deplasman"
        factors.append({
            "direction": "+" if (h_xg > a_xg and predicted == "H") or (a_xg > h_xg and predicted == "A") else "-",
            "text": f"{better} xG verimlilik ustunlugu",
            "impact": "medium",
        })

    # Value from odds
    if features.get("implied_home_prob", 0) > 0:
        impl_h = features["implied_home_prob"]
        model_h = h_prob
        if model_h - impl_h > 0.05:
            factors.append({
                "direction": "+",
                "text": f"Ev sahibi value bet (model %{model_h*100:.0f} vs piyasa %{impl_h*100:.0f})",
                "impact": "high",
            })
        elif impl_h - model_h > 0.05:
            factors.append({
                "direction": "-",
                "text": f"Piyasa ev sahibini daha favori goruyor (piyasa %{impl_h*100:.0f})",
                "impact": "medium",
            })

    return factors


def format_explainable_card(prediction: dict) -> str:
    """Format prediction card for Telegram using the user's specific template."""
    from config.leagues import LEAGUE_EMOJI
    
    # 1. Fetch basic details
    home = prediction.get("home_team", "Ev Sahibi")
    away = prediction.get("away_team", "Deplasman")
    league = prediction.get("league_code", "LIG")
    
    h_prob = prediction["home_win_prob"]
    d_prob = prediction["draw_prob"]
    a_prob = prediction["away_win_prob"]
    
    predicted_result = prediction["predicted_result"]
    conf = prediction["confidence_score"]
    
    # Normalize predicted_result to H/D/A for dictionary lookups
    pred_res_norm = predicted_result
    if predicted_result in ("MS 1", "1", "Ev Sahibi"):
        pred_res_norm = "H"
    elif predicted_result in ("MS X", "X", "Berabere"):
        pred_res_norm = "D"
    elif predicted_result in ("MS 2", "2", "Deplasman"):
        pred_res_norm = "A"

    # Resolve Main Pick Label
    main_pick = {"H": "MS 1", "D": "MS X", "A": "MS 2"}.get(pred_res_norm, predicted_result)
    
    # Get odds for main pick
    odds_data = prediction.get("_odds") or {}
    h_odds = odds_data.get("h") or odds_data.get("home_odds") or 1.0
    d_odds = odds_data.get("d") or odds_data.get("draw_odds") or 1.0
    a_odds = odds_data.get("a") or odds_data.get("away_odds") or 1.0
    
    main_odds = 1.0
    if pred_res_norm == "H":
        main_odds = h_odds
    elif pred_res_norm == "D":
        main_odds = d_odds
    elif pred_res_norm == "A":
        main_odds = a_odds
        
    # Calculate EV
    prob_map = {"H": h_prob, "D": d_prob, "A": a_prob}
    main_prob = prob_map.get(pred_res_norm, 0.33)
    ev_val = (main_prob * main_odds - 1.0) * 100.0 if main_odds > 1.0 else 0.0
    ev_str = f"+%{ev_val:.1f}" if ev_val >= 0 else f"-%{abs(ev_val):.1f}"
    
    # AI Absences and Power Drop
    home_absences = prediction.get("home_absences", [])
    away_absences = prediction.get("away_absences", [])
    home_pl = prediction.get("home_power_loss", 0.0)
    away_pl = prediction.get("away_power_loss", 0.0)
    
    missing_players = []
    if home_absences:
        missing_players.append(f"{home} ({len(home_absences)})")
    if away_absences:
        missing_players.append(f"{away} ({len(away_absences)})")
    missing_str = ", ".join(missing_players) if missing_players else "Yok"
    
    power_drop_str = f"Ev: -%{home_pl*100:.1f} | Dep: -%{away_pl*100:.1f}"
    
    # xG Expectations
    home_xg = prediction.get("home_lambda", 1.3)
    away_xg = prediction.get("away_lambda", 1.1)
    
    # Compute Second Best Pick (Runner-up class, Double Chance, or Over/Under, BTTS)
    p_over = prediction.get("over25_prob", 0.5)
    p_under = 1.0 - p_over
    p_btts = prediction.get("btts_prob", 0.5)
    p_btts_no = 1.0 - p_btts
    
    candidates = [
        {"name": "MS 1", "prob": h_prob, "odds": h_odds, "type": "1X2"},
        {"name": "MS X", "prob": d_prob, "odds": d_odds, "type": "1X2"},
        {"name": "MS 2", "prob": a_prob, "odds": a_odds, "type": "1X2"},
        {"name": "1X Çifte Şans", "prob": h_prob + d_prob, "odds": round(1.0 / ((1.0/h_odds + 1.0/d_odds)) * 1.08, 2) if h_odds > 1 and d_odds > 1 else 1.25, "type": "DC"},
        {"name": "X2 Çifte Şans", "prob": a_prob + d_prob, "odds": round(1.0 / ((1.0/a_odds + 1.0/d_odds)) * 1.08, 2) if a_odds > 1 and d_odds > 1 else 1.25, "type": "DC"},
        {"name": "12 Çifte Şans", "prob": h_prob + a_prob, "odds": round(1.0 / ((1.0/h_odds + 1.0/a_odds)) * 1.08, 2) if h_odds > 1 and a_odds > 1 else 1.25, "type": "DC"},
        {"name": "2.5 ÜST", "prob": p_over, "odds": prediction.get("over25_odds") or odds_data.get("o25") or 1.85, "type": "GOALS"},
        {"name": "2.5 ALT", "prob": p_under, "odds": prediction.get("under25_odds") or odds_data.get("u25") or 1.85, "type": "GOALS"},
        {"name": "KG VAR", "prob": p_btts, "odds": odds_data.get("btts") or odds_data.get("btts_y") or 1.75, "type": "BTTS"},
        {"name": "KG YOK", "prob": p_btts_no, "odds": odds_data.get("btts_no") or odds_data.get("btts_n") or 1.95, "type": "BTTS"}
    ]
    
    filtered_candidates = [c for c in candidates if c["name"] != main_pick and c["odds"] is not None and c["odds"] > 1.0]
    filtered_candidates.sort(key=lambda x: x["prob"], reverse=True)
    best_second = filtered_candidates[0]
    
    second_pick = best_second["name"]
    second_odds = best_second["odds"]
    second_conf = int(best_second["prob"] * 100)
    
    # Scale confidence to 0-100 if on a 0-10 scale
    display_conf = conf
    if conf < 10.0:
        display_conf = conf * 10.0

    flag = LEAGUE_EMOJI.get(league, "⚽")

    card_text = (
        f" {flag} {league} | {home} vs {away}\n"
        f"🎯 ANA TAHMİN: {main_pick}\n"
        f"🔥 Güven: %{int(display_conf):.0f} | Oran: {main_odds:.2f} | Değer (EV): {ev_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 AI Analizi: Eksikler: {missing_str} | Güç Kaybı: {power_drop_str}\n"
        f"📊 Piyasa Oranları: 1={h_odds:.2f} | X={d_odds:.2f} | 2={a_odds:.2f}\n"
        f"🧠 Model İhtimali: 1=%{h_prob*100:.1f} | X=%{d_prob*100:.1f} | 2=%{a_prob*100:.1f}\n"
        f"🎯 xG Beklentisi: {home_xg:.2f} – {away_xg:.2f}\n"
        f"────────────────────────────\n"
        f"💡 2. Güçlü Seçenek: {second_pick} (Güven: %{second_conf} | Oran: {second_odds:.2f})\n\n"
        f"⚠️ Yasal Uyarı: Bu analiz ve tahminler sadece istatistiksel verilere dayanmaktadır, kesin kazanç taahhüt etmez. "
        f"Bahis oynamak risk içerir; kayıplarınızdan sistemimiz sorumlu tutulamaz. Bahis tavsiyesi değildir. 18 yaşından büyükler içindir."
    )
    
    return card_text
