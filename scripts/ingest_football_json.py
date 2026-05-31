"""Script to ingest football match results from OpenFootball (GitHub) into SQLite.

Usage:
    python scripts/ingest_football_json.py --season 2526 --league T1
    python scripts/ingest_football_json.py --all-leagues --season 2526
"""

import os
import sys
import argparse
import logging
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import SEASON_LABELS
from src.db.base import get_backend
from src.ingestion.football_json_client import FootballJSONClient
from src.ingestion.fuzzy_matcher import FuzzyMatcher

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("ingest_football_json")

def ingest(season_key: str, league_code: str):
    """Ingest matches for a specific season and league."""
    db = get_backend()
    db.connect()
    
    client = FootballJSONClient()
    fuzzy = FuzzyMatcher()
    
    season_label = SEASON_LABELS.get(season_key)
    if not season_label:
        logger.error(f"Invalid season key: {season_key}")
        return
        
    # Standardize season label for OpenFootball (e.g. 2025-26)
    open_season = f"{season_label[:4]}-{season_label[-2:]}"
    
    logger.info(f"Ingesting {league_code} for season {open_season}...")
    matches = client.get_matches(open_season, league_code)
    
    if not matches:
        logger.warning(f"No matches found for {league_code} in {open_season}.")
        return

    # Get team mapping from DB
    db_teams = db.fetchall("SELECT id, name FROM teams")
    team_names = [t["name"] for t in db_teams]
    team_map = {t["name"]: t["id"] for t in db_teams}
    
    ingested_count = 0
    for m in matches:
        t1_name = m.get("team1")
        t2_name = m.get("team2")
        match_date = m.get("date")
        score = m.get("score", {}).get("ft") # [home, away]
        
        if not t1_name or not t2_name or not match_date:
            continue
            
        # Match teams
        h_match = fuzzy.match(t1_name, team_names)
        a_match = fuzzy.match(t2_name, team_names)
        
        if not h_match or not a_match:
            # If not found, we could create them, but better to skip or log
            logger.debug(f"Teams not found in DB: {t1_name} or {t2_name}")
            continue
            
        h_id, a_id = team_map[h_match], team_map[a_match]
        
        # Determine result
        result = None
        h_goals = None
        a_goals = None
        if score and len(score) == 2:
            h_goals = score[0]
            a_goals = score[1]
            if h_goals > a_goals: result = "H"
            elif h_goals < a_goals: result = "A"
            else: result = "D"
            
        # Standardize date format
        try:
            # OpenFootball often uses YYYY-MM-DD
            dt = datetime.strptime(match_date, "%Y-%m-%d").strftime("%Y-%m-%d 00:00:00")
        except:
            dt = match_date

        # Upsert into matches
        # Unique constraint typically on (date, home_team_id, away_team_id)
        db.execute(
            """INSERT INTO matches 
            (date, home_team_id, away_team_id, ft_home_goals, ft_away_goals, ft_result, league_code, season)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, home_team_id, away_team_id) DO UPDATE SET
            ft_home_goals=excluded.ft_home_goals,
            ft_away_goals=excluded.ft_away_goals,
            ft_result=excluded.ft_result
            """,
            (dt, h_id, a_id, h_goals, a_goals, result, league_code, season_label)
        )
        ingested_count += 1
        
    db.close()
    logger.info(f"Successfully ingested {ingested_count} matches for {league_code}.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", default="2526", help="Season key (e.g. 2526)")
    parser.add_argument("--league", help="League code (e.g. T1)")
    parser.add_argument("--all-leagues", action="store_true", help="Ingest all mapped leagues")
    args = parser.parse_args()
    
    leagues = [args.league] if args.league else []
    if args.all_leagues:
        leagues = list(FootballJSONClient.LEAGUE_MAP.keys())
        
    if not leagues:
        parser.print_help()
        return
        
    for l in leagues:
        ingest(args.season, l)

if __name__ == "__main__":
    main()
