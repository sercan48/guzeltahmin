"""
Offline CSV Data Ingestion and Synthetic Feature Generation.
Reads standard historical CSVs and mathematically computes missing complex features 
like 'pitch_type' and 'travel_distance_km' on the fly.
"""
import sqlite3
import logging
from pathlib import Path
import math

logger = logging.getLogger(__name__)

# --- SYNTHETIC DATA GENERATION DICTIONARIES ---

# Static Pitch Dictionary (Example for Norway)
PITCH_DICTIONARY = {
    "NORWAY_ELITESERIEN": {
        "Bodo/Glimt": "ARTIFICIAL",
        "Tromso": "ARTIFICIAL",
        "Rosenborg": "NATURAL",
        "Molde": "ARTIFICIAL",
        "Brann": "NATURAL"
    }
}

# Static Coordinates for Travel Distance (Lat, Lon)
TEAM_COORDS = {
    # Brazil Example
    "Flamengo": (-22.912, -43.230), # Rio
    "Gremio": (-30.034, -51.217),   # Porto Alegre
    "Bahia": (-12.977, -38.501),    # Salvador
    
    # Norway Example
    "Bodo/Glimt": (67.280, 14.404),
    "Rosenborg": (63.430, 10.395)
}

def haversine(lat1, lon1, lat2, lon2):
    """Calculates the great-circle distance between two points on Earth in km."""
    R = 6371.0 # Earth radius in kilometers
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    
    a = (math.sin(dLat / 2) * math.sin(dLat / 2) +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * 
         math.sin(dLon / 2) * math.sin(dLon / 2))
    
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def get_pitch_type(league, home_team):
    """Look up pitch type statically."""
    league_dict = PITCH_DICTIONARY.get(league, {})
    return league_dict.get(home_team, "NATURAL") # Default to NATURAL

def calculate_travel(home_team, away_team):
    """Look up coords and calculate flight distance."""
    if home_team in TEAM_COORDS and away_team in TEAM_COORDS:
        h_coords = TEAM_COORDS[home_team]
        a_coords = TEAM_COORDS[away_team]
        return round(haversine(h_coords[0], h_coords[1], a_coords[0], a_coords[1]), 1)
    return 0.0

def ingest_offline_csv_data():
    db_path = Path("data/guzel_tahmin.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("Starting Offline CSV Data Ingestion and Synthetic Feature Engine...")
    
    # In a real run, we would read a CSV from data/csv/
    # For this script, we mock the CSV rows parsed from football-data.co.uk
    mock_csv_rows = [
        {"league": "NORWAY_ELITESERIEN", "date": "2023-05-10", "home": "Bodo/Glimt", "away": "Rosenborg", "result": "H"},
        {"league": "BRAZIL_SERIE_A", "date": "2023-06-15", "home": "Flamengo", "away": "Gremio", "result": "H"},
        {"league": "BRAZIL_SERIE_A", "date": "2023-07-20", "home": "Bahia", "away": "Flamengo", "result": "A"}
    ]
    
    for row in mock_csv_rows:
        # SYNTHETIC FEATURE GENERATION
        synthetic_pitch = get_pitch_type(row["league"], row["home"])
        synthetic_distance = calculate_travel(row["home"], row["away"])
        
        print(f"Parsed Match: {row['home']} vs {row['away']}")
        print(f" -> Generated Pitch: {synthetic_pitch}")
        print(f" -> Generated Distance: {synthetic_distance} km\n")
        
        try:
            query = """
                INSERT INTO matches (league_code, home_team_id, away_team_id, season, date, pitch_type, travel_distance_km, dp_presence, is_summer_league, ft_result)
                VALUES (?, ?, ?, '2023', ?, ?, ?, 0.0, 1, ?)
            """
            cursor.execute(query, (row['league'], hash(row['home'])%1000, hash(row['away'])%1000, row['date'], synthetic_pitch, synthetic_distance, row['result']))
        except Exception as e:
            print(f"Error inserting match: {e}")
            
    conn.commit()
    conn.close()
    print("Offline Data Ingestion complete.")

if __name__ == "__main__":
    ingest_offline_csv_data()
