import sqlite3
import math
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.ingestion.venue_registry import get_team_venue
from src.preprocessing.schema_mapper import haversine_distance, SUMMER_LEAGUE_CODES

def patch_matches():
    db = get_backend()
    db.connect()

    print("=== Patching Historical Summer League Matches in DB ===")
    
    # Get all matches in summer leagues
    query = """
        SELECT m.id, m.date, m.league_code,
               t1.name as home, t2.name as away
        FROM matches m
        JOIN teams t1 ON m.home_team_id = t1.id
        JOIN teams t2 ON m.away_team_id = t2.id
        WHERE m.league_code IN ('NORWAY_ELITESERIEN', 'Eliteserien', 'BRAZIL_SERIE_A', 'Serie A', 'SWEDEN_ALLSVENSKAN', 'Allsvenskan', 'FINLAND_VEIKKAUSLIIGA', 'Veikkausliiga')
    """
    matches = db.fetchall(query)
    print(f"Found {len(matches)} matches to update.")

    updated_count = 0
    for m in matches:
        home_team = m["home"]
        away_team = m["away"]
        league_code = m["league_code"]

        pitch_type = "NATURAL"
        travel_distance = None

        home_venue = get_team_venue(home_team)
        away_venue = get_team_venue(away_team)

        if home_venue:
            pitch_type = home_venue.get("pitch", "NATURAL")

        if home_venue and away_venue:
            travel_distance = haversine_distance(
                home_venue["lat"], home_venue["lon"],
                away_venue["lat"], away_venue["lon"]
            )

        db.execute(
            "UPDATE matches SET pitch_type = ?, travel_distance_km = ?, is_summer_league = 1 WHERE id = ?",
            (pitch_type, travel_distance, m["id"])
        )
        updated_count += 1

    print(f"Successfully patched {updated_count} matches in the database.")
    db.close()

if __name__ == "__main__":
    patch_matches()
