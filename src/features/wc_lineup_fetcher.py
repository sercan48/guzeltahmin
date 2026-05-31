import logging
import requests
import sqlite3
import random
from pathlib import Path
from config.settings import API_FOOTBALL_KEY

logger = logging.getLogger(__name__)

def fetch_and_save_lineups(match_id: int, api_match_id: str):
    """
    Fetch lineups from API-Football for a specific match,
    simulate EA FC ratings calculation, and save to SQLite.
    """
    url = f"https://v3.football.api-sports.io/fixtures/lineups?fixture={api_match_id}"
    headers = {
        'x-rapidapi-host': "v3.football.api-sports.io",
        'x-rapidapi-key': API_FOOTBALL_KEY
    }
    
    db_path = Path("data/guzel_tahmin.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            logger.error(f"API Error fetching lineups for {api_match_id}")
            return False
            
        data = response.json()
        if not data or not data.get("response"):
            logger.warning(f"No lineup data yet for {api_match_id}")
            return False
            
        for team_lineup in data["response"]:
            api_team_id = team_lineup["team"]["id"]
            
            # Map api_team_id to our internal team_id
            cursor.execute("SELECT id FROM teams WHERE id IN (SELECT internal_team_id FROM team_mapping WHERE api_team_id = ?)", (str(api_team_id),))
            row = cursor.fetchone()
            if not row:
                continue
            
            team_id = row[0]
            start_xi = team_lineup.get("startXI", [])
            
            # Simulated Data Enrichment (In production, this queries an EA FC dataset)
            # We generate a random average between 75 and 88 for World Cup teams
            avg_rating = round(random.uniform(75.0, 88.0), 2)
            market_value = random.randint(150_000_000, 1_200_000_000)
            star_players = sum(1 for _ in range(11) if random.random() > 0.7) # Approx 30% chance for an 85+ player
            
            cursor.execute("""
                INSERT INTO match_lineups (match_id, team_id, avg_ea_fc_rating, total_market_value, star_player_count, is_confirmed)
                VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(match_id, team_id) DO UPDATE SET
                    avg_ea_fc_rating=excluded.avg_ea_fc_rating,
                    total_market_value=excluded.total_market_value,
                    star_player_count=excluded.star_player_count,
                    is_confirmed=1,
                    updated_at=CURRENT_TIMESTAMP
            """, (match_id, team_id, avg_rating, market_value, star_players))
            
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error processing lineups: {e}")
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    # Test execution
    print("Testing Lineup Fetcher...")
    result = fetch_and_save_lineups(1, "12345")
    print(f"Fetch completed: {result}")
