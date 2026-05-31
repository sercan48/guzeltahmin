"""Predict Today's Matches.

1. Fetches fixtures from API-Football for today.
2. Gets weather for match locations.
3. Retrieves injuries/suspensions.
4. Predicts match outcomes using XGBoost and finds Value Bets.
"""

import sys
from pathlib import Path
from datetime import date
import argparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.ingestion.api_football_client import APIFootballClient
from src.ingestion.fuzzy_matcher import FuzzyMatcher
from src.model.predictor import predict_match


def get_injury_penalty(client: APIFootballClient, fixture_id: int) -> float:
    """Calculate a simplistic penalty based on number of injuries."""
    injuries = client.get_injuries(fixture_id)
    if not injuries:
        return 0.0
    
    # Rough logic: every injured player drops win prob by a tiny margin depending on count.
    # In a full system, we match player names against DB to check their 'importance_score'.
    # For now, we apply a flat 0.01 penalty per reported injury/suspension.
    # We will compute Home Team penalty and Away Team penalty.
    return injuries


def main():
    parser = argparse.ArgumentParser(description="Predict Today's Matches")
    parser.add_argument("--date", type=str, default=date.today().isoformat(),
                        help="Date to predict in YYYY-MM-DD format. Default is today.")
    args = parser.parse_args()

    print("=" * 60)
    print(f"  Güzel Tahmin — Live Predictions for {args.date}")
    print("=" * 60)

    db = get_backend()
    db.connect()
    
    client = APIFootballClient()
    fuzzy = FuzzyMatcher()

    print("[1/3] Fetching fixtures from API-Football...")
    fixtures = client.get_fixtures_by_date(args.date)
    
    if not fixtures:
        print("[INFO] No matches found for today or API limit reached.")
        return

    print(f"  Found {len(fixtures)} matches in tracked leagues.\n")

    # DB teams for fuzzy matching
    db_teams = db.fetchall("SELECT id, name FROM teams")
    team_names = [t["name"] for t in db_teams]
    team_map = {t["name"]: t["id"] for t in db_teams}

    predictions = []

    print("[2/3] Predicting outcomes...")
    for fix in fixtures:
        league_code = fix["internal_league_code"]
        season = str(fix["league"]["season"]) + "-" + str(fix["league"]["season"] + 1)
        
        home_name_api = fix["teams"]["home"]["name"]
        away_name_api = fix["teams"]["away"]["name"]
        fixture_id = fix["fixture"]["id"]
        
        # Match names to local DB
        home_match = fuzzy.match(home_name_api, team_names)
        away_match = fuzzy.match(away_name_api, team_names)
        
        if not home_match or not away_match:
            print(f"  [SKIP] Could not match team names for: {home_name_api} vs {away_name_api}")
            continue
            
        home_id = team_map[home_match]
        away_id = team_map[away_match]
        
        # Get Injuries
        injuries = client.get_injuries(fixture_id)
        home_injuries_count = sum(1 for i in injuries if i["team"]["id"] == fix["teams"]["home"]["id"])
        away_injuries_count = sum(1 for i in injuries if i["team"]["id"] == fix["teams"]["away"]["id"])

        # Predict (using weather via predictor's internal flag)
        # Note: weather uses `home_match` (the local standard name like "Galatasaray") to resolve city.
        pred = predict_match(
            db=db,
            home_team_id=home_id,
            away_team_id=away_id,
            league_code=league_code,
            season=season,
            use_weather=True,
            home_team_name=home_match
        )
        
        # Apply strict dynamic injury penalties (Post-processing)
        # Assuming missing a player decreases win probability slightly.
        # This is a heuristic. In v2 it should alter `home_attack_rating` directly.
        h_prob = pred["home_win_prob"]
        d_prob = pred["draw_prob"]
        a_prob = pred["away_win_prob"]
        
        if home_injuries_count > 0:
            penalty = min(0.015 * home_injuries_count, 0.10) # max 10% penalty
            h_prob -= penalty
            a_prob += penalty * 0.7
            d_prob += penalty * 0.3
            
        if away_injuries_count > 0:
            penalty = min(0.015 * away_injuries_count, 0.10)
            a_prob -= penalty
            h_prob += penalty * 0.7
            d_prob += penalty * 0.3
            
        # Normalize
        total = h_prob + d_prob + a_prob
        pred["home_win_prob"] = round(h_prob / total, 3)
        pred["draw_prob"] = round(d_prob / total, 3)
        pred["away_win_prob"] = round(a_prob / total, 3)
        
        predictions.append({
            "fixture": f"{home_match} vs {away_match}",
            "league": league_code,
            "pred": pred,
            "home_injuries": home_injuries_count,
            "away_injuries": away_injuries_count
        })

    print("[3/3] Results")
    print("-" * 60)
    for p in predictions:
        match_str = p["fixture"]
        probs = p["pred"]
        weather = probs.get("weather", {}).get("condition", "Unknown")
        print(f"[{p['league']}] {match_str}")
        print(f"   Win Prob : H: %{probs['home_win_prob']*100:.1f} | D: %{probs['draw_prob']*100:.1f} | A: %{probs['away_win_prob']*100:.1f}")
        print(f"   Result   : {probs['predicted_result']}")
        print(f"   Missing  : Home: {p['home_injuries']} players | Away: {p['away_injuries']} players")
        print(f"   Weather  : {weather}")
        print("-" * 60)


if __name__ == "__main__":
    main()
