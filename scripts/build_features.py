"""Build feature matrix for all matches — the main pipeline that connects everything."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from tqdm import tqdm

from src.db.base import get_backend
from src.features.team_strength import team_strength, home_advantage_factor, get_league_standings, last_n_form
from src.features.form_calculator import form_momentum, head_to_head, goals_stats
from src.features.efficiency_engine import simulated_xg_efficiency
from src.features.player_impact import (
    team_attack_rating, team_defense_rating,
    squad_market_value, load_team_players,
)
from src.features.referee_impact import referee_strictness
from src.features.match_context_engine import check_is_derby, calculate_red_card_risk
from src.features.fixture_congestion import compute_match_congestion
from src.features.xg_features import compute_match_xg_features

def compute_clean_sheet_rate(team_matches: pd.DataFrame, team_id: int) -> float:
    if len(team_matches) == 0:
        return 0.0
    cs_count = 0
    for _, m in team_matches.iterrows():
        is_home = m["home_team_id"] == team_id
        conceded = m["ft_away_goals"] if is_home else m["ft_home_goals"]
        if conceded == 0:
            cs_count += 1
    return cs_count / len(team_matches)


def build_features_for_season(db, league_code: str, season: str) -> pd.DataFrame:
    """Build feature matrix for all matches in a league-season."""
    db_season = season
    if league_code in ["NORWAY_ELITESERIEN", "Eliteserien", "BRAZIL_SERIE_A", "Serie A", "USA_MLS", "SWEDEN_ALLSVENSKAN", "Allsvenskan", "FINLAND_VEIKKAUSLIIGA", "Veikkausliiga"]:
        if season == "2025-2026":
            db_season = "2026"
        elif season == "2024-2025":
            db_season = "2024"
        elif season == "2023-2024":
            db_season = "2023"
        elif season == "2022-2023":
            db_season = "2022"
        elif season == "2021-2022":
            db_season = "2021"
        elif season == "2020-2021":
            db_season = "2020"

    matches = db.fetchall(
        """SELECT m.*, ht.name as home_team, at.name as away_team
        FROM matches m
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
        WHERE m.league_code=? AND m.season=?
        ORDER BY m.date""",
        (league_code, db_season),
    )

    if not matches:
        return pd.DataFrame()

    rows = []
    for m in matches:
        home_id = m["home_team_id"]
        away_id = m["away_team_id"]
        match_date_str = str(m["date"])[:10]

        # Get team matches up to this point (exclude future)
        home_matches = pd.DataFrame(db.fetchall(
            """SELECT * FROM matches
            WHERE (home_team_id=? OR away_team_id=?) AND season=? AND date < ?
            ORDER BY date""",
            (home_id, home_id, db_season, m["date"]),
        ))
        away_matches = pd.DataFrame(db.fetchall(
            """SELECT * FROM matches
            WHERE (home_team_id=? OR away_team_id=?) AND season=? AND date < ?
            ORDER BY date""",
            (away_id, away_id, db_season, m["date"]),
        ))

        # Skip first few matches (not enough history)
        if len(home_matches) < 3 or len(away_matches) < 3:
            continue

        # Standings and Home advantage at this specific point in time (no leakage)
        standings = get_league_standings(db, league_code, db_season, before_date=match_date_str)
        ha_factor = home_advantage_factor(db, league_code, before_date=match_date_str)

        # Team strength
        home_str = team_strength(home_matches, home_id, league_standings=standings)
        away_str = team_strength(away_matches, away_id, league_standings=standings)

        # Form
        home_form = last_n_form(home_matches, home_id, league_standings=standings)
        away_form = last_n_form(away_matches, away_id, league_standings=standings)

        # Player ratings
        home_players = load_team_players(db, home_id)
        away_players = load_team_players(db, away_id)

        home_atk = team_attack_rating(home_players) if home_players else 50.0
        home_def = team_defense_rating(home_players) if home_players else 50.0
        away_atk = team_attack_rating(away_players) if away_players else 50.0
        away_def = team_defense_rating(away_players) if away_players else 50.0

        # Goals stats before this match
        home_goals = goals_stats(db, home_id, db_season, before_date=match_date_str)
        away_goals = goals_stats(db, away_id, db_season, before_date=match_date_str)

        # H2H stats before this match
        h2h = head_to_head(db, home_id, away_id, before_date=match_date_str)

        # Referee & Context
        ref = referee_strictness(db, m.get("referee", "Unknown"), league_code)
        is_derby = check_is_derby(m["home_team"], m["away_team"])
        red_card_risk = calculate_red_card_risk(home_def, away_def, ref["strictness_score"])

        # Squad value
        home_val = squad_market_value(home_players)
        away_val = squad_market_value(away_players)

        # Momentum
        home_mom = form_momentum(home_matches, home_id)
        away_mom = form_momentum(away_matches, away_id)

        # xG Proxy
        home_xg_eff = simulated_xg_efficiency(home_goals["avg_scored"], home_atk)
        away_xg_eff = simulated_xg_efficiency(away_goals["avg_scored"], away_atk)

        # Position
        home_pos = standings.get(home_id, 10)
        away_pos = standings.get(away_id, 10)

        # Get odds
        odds = db.fetchone(
            "SELECT * FROM odds WHERE match_id=? ORDER BY bookmaker LIMIT 1",
            (m["id"],),
        )

        # xG features before this match (no leakage)
        xg_features = compute_match_xg_features(db, home_id, away_id, league_code, before_date=m["date"])
        home_xg_avg = xg_features.get("home_xg_avg", 0.0)
        away_xg_avg = xg_features.get("away_xg_avg", 0.0)
        home_xg_overperformance = xg_features.get("home_xg_overperformance", 0.0)
        away_xg_overperformance = xg_features.get("away_xg_overperformance", 0.0)

        # Congestion features
        congestion_features = compute_match_congestion(db, home_id, away_id, str(m["date"])[:10])
        home_congestion_score = congestion_features.get("home_congestion_score", 0.0)
        away_congestion_score = congestion_features.get("away_congestion_score", 0.0)
        congestion_advantage = congestion_features.get("congestion_advantage", 0.0)

        # Clean sheet difference
        home_cs_rate = compute_clean_sheet_rate(home_matches, home_id)
        away_cs_rate = compute_clean_sheet_rate(away_matches, away_id)
        clean_sheet_rate_diff = home_cs_rate - away_cs_rate

        # Odds-derived features
        implied_home_prob = implied_away_prob = implied_draw_prob = 0.33
        if odds and odds["home_odds"] and odds["draw_odds"] and odds["away_odds"]:
            p_h = 1 / odds["home_odds"] if odds["home_odds"] else 0
            p_d = 1 / odds["draw_odds"] if odds["draw_odds"] else 0
            p_a = 1 / odds["away_odds"] if odds["away_odds"] else 0
            implied_sum = p_h + p_d + p_a
            if implied_sum > 0:
                implied_home_prob = p_h / implied_sum
                implied_away_prob = p_a / implied_sum
                implied_draw_prob = p_d / implied_sum

        row = {
            "match_id": m["id"],
            "date": m["date"],
            "league_code": league_code,
            "season": season,
            "home_team": m["home_team"],
            "away_team": m["away_team"],
            "home_team_id": home_id,
            "away_team_id": away_id,
            "ft_result": m["ft_result"],
            "ft_home_goals": m["ft_home_goals"],
            "ft_away_goals": m["ft_away_goals"],
            # Features
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
            "form_momentum_diff": home_mom - away_mom,
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
            # Summer features
            "travel_distance_km": float(m.get("travel_distance_km") or (450.0 if league_code in ["NORWAY_ELITESERIEN", "Eliteserien"] else 380.0 if league_code in ["SWEDEN_ALLSVENSKAN", "Allsvenskan"] else 300.0 if league_code in ["FINLAND_VEIKKAUSLIIGA", "Veikkausliiga"] else 950.0 if league_code in ["BRAZIL_SERIE_A", "Serie A"] else 800.0 if league_code == "USA_MLS" else 0.0)),
            "is_artificial_pitch": float(1.0 if m.get("pitch_type") == "ARTIFICIAL" else 0.0),
            "cup_rotation_fatigue": float(1.0 if m.get("cup_rotation_fatigue") else 0.0),
            "dp_presence": float(m.get("dp_presence") or 0.0),
            "extreme_humidity": float(1.0 if m.get("weather_condition") == "EXTREME_HUMIDITY" else 0.0),
            "implied_home_prob": implied_home_prob,
            "implied_away_prob": implied_away_prob,
            "implied_draw_prob": implied_draw_prob,
            # Odds (for value bet analysis)
            "home_odds": odds["home_odds"] if odds else None,
            "draw_odds": odds["draw_odds"] if odds else None,
            "away_odds": odds["away_odds"] if odds else None,
        }

        rows.append(row)

    return pd.DataFrame(rows)


def build_all_features(season_filter: str = None, league_filter: str = None) -> pd.DataFrame:
    """Build feature matrix for ALL leagues and seasons."""
    from config.leagues import MVP_LEAGUES
    from config.settings import SEASON_LABELS

    db = get_backend()
    db.connect()

    all_frames = []

    try:
        for league_code in MVP_LEAGUES:
            if league_filter and league_code != league_filter:
                continue
            for season_code, season_label in SEASON_LABELS.items():
                if season_filter and season_label != season_filter:
                    continue
                print(f"  Building features: {league_code} {season_label}...", end=" ")
                df = build_features_for_season(db, league_code, season_label)
                if len(df) > 0:
                    all_frames.append(df)
                    print(f"{len(df)} matches")
                else:
                    print("no data")
    finally:
        db.close()

    if not all_frames:
        raise ValueError("No feature data generated! Ensure database has match data.")

    combined = pd.concat(all_frames, ignore_index=True)
    print(f"\n[OK] Total features built: {len(combined)} matches")

    # Save to processed
    from config.settings import PROCESSED_DIR
    output_path = PROCESSED_DIR / "features.csv"
    combined.to_csv(output_path, index=False)
    print(f"[OK] Saved to {output_path}")

    return combined


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", help="Season label (e.g. 2025-2026)")
    parser.add_argument("--league", help="League code (e.g. T1)")
    args = parser.parse_args()
    
    build_all_features(season_filter=args.season, league_filter=args.league)
