"""Predict Weekend Matches.

Calculates the dates for the upcoming (or current) Friday, Saturday, and Sunday.
Fetches fixtures, injuries, and weather for all 3 days and generates predictions.
"""

import sys
from pathlib import Path
from datetime import date, timedelta
import argparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.ingestion.api_football_client import APIFootballClient
from src.ingestion.fuzzy_matcher import FuzzyMatcher
from src.model.predictor import predict_match


def get_weekend_dates(base_date: date) -> list[str]:
    """Return ISO date strings for Friday, Saturday, and Sunday of the respective week."""
    # weekday(): Monday is 0, Friday is 4, Sunday is 6
    days_to_friday = (4 - base_date.weekday()) % 7
    # If today is Sat (5) or Sun (6), days_to_friday would evaluate to 6 or 5 days ahead (next week).
    # If we are ALREADY in the weekend, we want THIS weekend.
    if base_date.weekday() in [4, 5, 6]:
        # Move back to Friday
        friday = base_date - timedelta(days=base_date.weekday() - 4)
    else:
        friday = base_date + timedelta(days=days_to_friday)

    saturday = friday + timedelta(days=1)
    sunday = friday + timedelta(days=2)

    return [friday.isoformat(), saturday.isoformat(), sunday.isoformat()]


def main():
    parser = argparse.ArgumentParser(description="Predict Weekend Matches")
    parser.add_argument("--base_date", type=str, default=date.today().isoformat(),
                        help="Base date to calculate weekend from (YYYY-MM-DD)")
    args = parser.parse_args()

    # Determine dates
    d = date.fromisoformat(args.base_date)
    weekend_dates = get_weekend_dates(d)

    print("=" * 60)
    print(f"  Güzel Tahmin — Weekend Live Predictions")
    print(f"  Target Dates: {', '.join(weekend_dates)}")
    print("=" * 60)

    db = get_backend()
    db.connect()
    
    client = APIFootballClient()
    fuzzy = FuzzyMatcher()

    # Pre-fetch all teams to optimize fuzzy matching
    db_teams = db.fetchall("SELECT id, name FROM teams")
    team_names = [t["name"] for t in db_teams]
    team_map = {t["name"]: t["id"] for t in db_teams}

    for target_date in weekend_dates:
        print(f"\n[> PROCESSING DATE: {target_date} <]")
        fixtures = client.get_fixtures_by_date(target_date)
        
        if not fixtures:
            print(f"  [INFO] No matches found for {target_date} or API limit reached.")
            continue

        print(f"  Found {len(fixtures)} matches in tracked leagues.")

        predictions = []
        for fix in fixtures:
            league_code = fix["internal_league_code"]
            season = str(fix["league"]["season"]) + "-" + str(fix["league"]["season"] + 1)
            
            home_name_api = fix["teams"]["home"]["name"]
            away_name_api = fix["teams"]["away"]["name"]
            fixture_id = fix["fixture"]["id"]
            
            home_match = fuzzy.match(home_name_api, team_names)
            away_match = fuzzy.match(away_name_api, team_names)
            
            if not home_match or not away_match:
                continue
                
            home_id = team_map[home_match]
            away_id = team_map[away_match]
            
            injuries = client.get_injuries(fixture_id)
            home_injuries_count = sum(1 for i in injuries if i["team"]["id"] == fix["teams"]["home"]["id"])
            away_injuries_count = sum(1 for i in injuries if i["team"]["id"] == fix["teams"]["away"]["id"])

            pred = predict_match(
                db=db,
                home_team_id=home_id,
                away_team_id=away_id,
                league_code=league_code,
                season=season,
                use_weather=True,
                home_team_name=home_match
            )
            
            # Simple Injury Penalty post-processing
            h_prob = pred["home_win_prob"]
            d_prob = pred["draw_prob"]
            a_prob = pred["away_win_prob"]
            
            if home_injuries_count > 0:
                penalty = min(0.015 * home_injuries_count, 0.10)
                h_prob -= penalty
                a_prob += penalty * 0.7
                d_prob += penalty * 0.3
                
            if away_injuries_count > 0:
                penalty = min(0.015 * away_injuries_count, 0.10)
                a_prob -= penalty
                h_prob += penalty * 0.7
                d_prob += penalty * 0.3
                
            total = h_prob + d_prob + a_prob
            pred["home_win_prob"] = round(h_prob / total, 3)
            pred["draw_prob"] = round(d_prob / total, 3)
            pred["away_win_prob"] = round(a_prob / total, 3)
            
            # Re-evaluate result class
            predicted_class_mapped = max(
                [("H", pred["home_win_prob"]), ("D", pred["draw_prob"]), ("A", pred["away_win_prob"])], 
                key=lambda item: item[1]
            )[0]
            pred["predicted_result"] = predicted_class_mapped

            predictions.append({
                "fixture": f"{home_match} vs {away_match}",
                "league": league_code,
                "pred": pred,
                "home_injuries": home_injuries_count,
                "away_injuries": away_injuries_count
            })

        print("-" * 60)
        for p in predictions:
            match_str = p["fixture"]
            probs = p["pred"]
            weather = probs.get("weather", {}).get("condition", "Unknown")
            print(f"[{p['league']}] {match_str}")
            print(f"   Win Prob : H: %{probs['home_win_prob']*100:.1f} | D: %{probs['draw_prob']*100:.1f} | A: %{probs['away_win_prob']*100:.1f}")
            print(f"   Result   : {probs['predicted_result']}")
            print(f"   Missing  : Home: {p['home_injuries']} | Away: {p['away_injuries']}")
            print(f"   Weather  : {weather}")
        print("-" * 60)


if __name__ == "__main__":
    main()
