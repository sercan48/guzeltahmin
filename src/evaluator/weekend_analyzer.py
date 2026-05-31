"""Weekend Analyzer & Batch Prediction Orchestrator.

Fetches upcoming fixtures for the weekend (Fri-Sun) across all supported leagues,
analyzes each match for the top 2 betting options, and categorizes them.
Includes a simple caching mechanism to reduce API-Football consumption.
"""

import os
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from src.db.base import get_backend
from src.model.predictor import predict_match
from src.evaluator.coupon_builder import (
    analyze_match_deep, BetPick, LEAGUE_NAMES
)
from config.settings import CACHE_DIR

logger = logging.getLogger("weekend_analyzer")
CACHE_FILE = CACHE_DIR / "weekend_analysis.json"

def get_weekend_dates() -> list[str]:
    """Get ISO dates for upcoming Friday, Saturday, and Sunday."""
    today = date.today()
    # Find next Friday
    days_to_friday = (4 - today.weekday()) % 7
    friday = today + timedelta(days=days_to_friday)
    return [
        friday.isoformat(),
        (friday + timedelta(days=1)).isoformat(),
        (friday + timedelta(days=2)).isoformat(),
    ]

def fetch_all_weekend_fixtures(st_progress=None) -> list[dict]:
    """Fetch live fixtures from API-Football, falling back to local DB for 2025-26."""
    try:
        from src.ingestion.api_football_client import APIFootballClient
        client = APIFootballClient()
        
        dates = get_weekend_dates()
        all_fixtures = []
        api_blocked = False
        
        db = get_backend()
        db.connect()
        
        for d in dates:
            if st_progress:
                st_progress.text(f"📅 {d} fikstürleri kontrol ediliyor...")
            
            logger.info(f"Fetching fixtures for {d}...")
            
            # If we already know the API is blocked for this season/plan, don't even try
            fixtures = []
            if not api_blocked:
                # This may sleep if it's the first call or not cached
                res_data = client.get_fixtures_by_date(d) if hasattr(client, 'get_fixtures_by_date') else []
                
                # Check for special error code I just added
                if isinstance(res_data, dict) and res_data.get("error") == "PLAN_RESTRICTION":
                    logger.warning(f"Plan restricted for {d}. Switching ALL remaining to DB fallback.")
                    api_blocked = True
                    fixtures = []
                else:
                    fixtures = res_data
            
            if not fixtures:
                logger.info(f"  -> Using Local DB fallback for {d}")
                # Fallback: Query local matches for this date in 2526 season
                # Using more robust date matching
                db_fixes = db.fetchall(
                    """SELECT m.date, m.league_code, h.name as home_name, a.name as away_name
                       FROM matches m
                       JOIN teams h ON m.home_team_id = h.id
                       JOIN teams a ON m.away_team_id = a.id
                       WHERE (m.date LIKE ? OR m.date = ?) AND m.season = '2025-2026'
                       AND (m.ft_home_goals IS NULL OR m.ft_home_goals = '')""",
                    (f"{d}%", d)
                )
                
                # Convert DB rows to API-like structure for the analyzer
                for df in db_fixes:
                    all_fixtures.append({
                        "fixture": {"date": df["date"]},
                        "teams": {
                            "home": {"name": df["home_name"]},
                            "away": {"name": df["away_name"]}
                        },
                        "internal_league_code": df["league_code"],
                        "source": "local_db"
                    })
            else:
                all_fixtures.extend(fixtures)
        
        db.close()
        return all_fixtures
    except Exception as e:
        logger.error(f"Batch fetch failed: {e}")
        if 'db' in locals(): db.close()
        return []

def get_predictions_batch(fixtures: list[dict], use_cache: bool = True) -> list[dict]:
    """Run batch analysis on fixtures to find Top 1 and Top 2 picks."""
    # Check cache first
    if use_cache and CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r") as f:
                cached_data = json.load(f)
            # Only use cache if it was generated today
            cache_date = cached_data.get("generated_at", "").split("T")[0]
            if cache_date == date.today().isoformat():
                logger.info("Using cached analysis.")
                return cached_data.get("matches", [])
        except Exception:
            pass

    db = get_backend()
    db.connect()
    
    from src.ingestion.fuzzy_matcher import FuzzyMatcher
    fuzzy = FuzzyMatcher()
    
    db_teams = db.fetchall("SELECT id, name FROM teams")
    team_names = [t["name"] for t in db_teams]
    team_map = {t["name"]: t["id"] for t in db_teams}
    
    # Initialize API client for injuries
    from src.ingestion.api_football_client import APIFootballClient
    api_client = APIFootballClient()
    
    analyzed_matches = []
    
    for fix in fixtures:
        home_name = fix.get("teams", {}).get("home", {}).get("name", "")
        away_name = fix.get("teams", {}).get("away", {}).get("name", "")
        api_home_id = fix.get("teams", {}).get("home", {}).get("id")
        api_away_id = fix.get("teams", {}).get("away", {}).get("id")
        api_fixture_id = fix.get("fixture", {}).get("id")
        lc = fix.get("internal_league_code", "")
        match_date = fix.get("fixture", {}).get("date", "")
        
        # Match teams to DB
        h_match = fuzzy.match(home_name, team_names)
        a_match = fuzzy.match(away_name, team_names)
        
        if not h_match or not a_match:
            continue
            
        h_id, a_id = team_map[h_match], team_map[a_match]
        
        h_missing = 0
        a_missing = 0
        if api_fixture_id and api_client.api_key:
            # Fetch Cached Injuries
            injuries = api_client.get_injuries(api_fixture_id)
            for inj in injuries:
                inj_team_id = inj.get("team", {}).get("id")
                if inj_team_id == api_home_id:
                    h_missing += 1
                elif inj_team_id == api_away_id:
                    a_missing += 1
        
        try:
            pred = predict_match(db, h_id, a_id, lc, season="2025-2026", 
                                 home_missing_count=h_missing, away_missing_count=a_missing)
            deep_picks = analyze_match_deep(pred, f"{h_match} vs {a_match}", lc)
            
            if not deep_picks:
                continue
                
            # Get Top 2 picks
            top_1 = deep_picks[0]
            top_2 = deep_picks[1] if len(deep_picks) > 1 else None

            # Value scanner: compare primary (raw model) vs secondary (best EV market)
            from src.evaluator.value_hunter import scan_all_markets, compare_primary_secondary
            scanned = scan_all_markets([pred])
            if scanned:
                decision = compare_primary_secondary(scanned[0])
                final_tag = decision["tag"]
            else:
                final_tag = pred["predicted_result"]

            # Persistent Archiving (v3 + Phase 1 tag upgrade)
            try:
                match_id_row = db.fetchone(
                    "SELECT id FROM matches WHERE (date LIKE ? OR date = ?) AND home_team_id = ? AND away_team_id = ?",
                    (f"{match_date.split('T')[0]}%", match_date.split('T')[0], h_id, a_id)
                )
                if match_id_row:
                    mid = match_id_row["id"]
                    from datetime import date as dt
                    db.execute(
                        """INSERT OR REPLACE INTO predictions 
                           (match_id, analysis_date, home_win_prob, draw_prob, away_win_prob, 
                            predicted_result, top_1_pick, top_1_type, top_2_pick, top_2_type)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (mid, dt.today().isoformat(), pred["home_win_prob"], pred["draw_prob"], pred["away_win_prob"],
                         final_tag, top_1.pick, top_1.bet_type.value, 
                         top_2.pick if top_2 else None, top_2.bet_type.value if top_2 else None)
                    )
            except Exception as e:
                logger.warning(f"Could not archive prediction for {h_match} vs {a_match}: {e}")

            analyzed_matches.append({
                "match": f"{h_match} vs {a_match}",
                "home_team": h_match,
                "away_team": a_match,
                "fixture_id": fix.get("fixture_id") or fix.get("fixture", {}).get("id"),
                "date": match_date,
                "league": lc,
                "league_name": LEAGUE_NAMES.get(lc, lc),
                "home_prob": round(pred["home_win_prob"], 2),
                "draw_prob": round(pred["draw_prob"], 2),
                "away_prob": round(pred["away_win_prob"], 2),
                "top_1": {
                    "pick": top_1.pick,
                    "type": top_1.bet_type.value,
                    "odds": top_1.estimated_odds,
                    "confidence": round(top_1.confidence, 2),
                    "reason": top_1.reasoning
                },
                "top_2": {
                    "pick": top_2.pick,
                    "type": top_2.bet_type.value,
                    "odds": top_2.estimated_odds,
                    "confidence": round(top_2.confidence, 2),
                    "reason": top_2.reasoning
                } if top_2 else None,
                "home_missing": h_missing,
                "away_missing": a_missing
            })
        except Exception as e:
            logger.warning(f"Failed analysis for {home_name} vs {away_name}: {e}")
            
    db.close()
    
    # Save to cache
    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump({
                "generated_at": datetime.now().isoformat(),
                "matches": analyzed_matches
            }, f, indent=2)
            
    return analyzed_matches

def run_weekend_analysis():
    """Trigger the full weekend analysis process."""
    fixtures = fetch_all_weekend_fixtures()
    if not fixtures:
        print("No fixtures found for the weekend.")
        return []
        
    print(f"Analyzing {len(fixtures)} weekend fixtures...")
    results = get_predictions_batch(fixtures)
    print(f"Analysis complete. {len(results)} matches processed.")
    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_weekend_analysis()
