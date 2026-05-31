import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
from src.db.base import get_backend
from src.ingestion.csv_loader import load_season

# Connect to DB
conn = sqlite3.connect("data/guzel_tahmin.db")
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# 1. Total matches count for NORWAY_ELITESERIEN
cursor.execute("SELECT count(*) as cnt FROM matches WHERE league_code = 'NORWAY_ELITESERIEN'")
row = cursor.fetchone()
print(f"Total Norway Eliteserien matches in DB: {row['cnt']}")

# 2. Seasons present for NORWAY_ELITESERIEN
cursor.execute("SELECT season, count(*) as cnt FROM matches WHERE league_code = 'NORWAY_ELITESERIEN' GROUP BY season")
rows = cursor.fetchall()
print("Seasons in DB:")
for r in rows:
    print(f"  Season: {r['season']} | Count: {r['cnt']}")

# 3. Sample matches
cursor.execute("SELECT date, season, ft_result FROM matches WHERE league_code = 'NORWAY_ELITESERIEN' LIMIT 5")
rows = cursor.fetchall()
print("Sample Norway Eliteserien matches in DB:")
for r in rows:
    print(f"  Date: {r['date']} | Season: {r['season']} | Result: {r['ft_result']}")

# 4. Load from CSV and check
print("\nLoading season 2526 from CSV...")
df = load_season("2526", "NORWAY_ELITESERIEN")
if df is not None:
    print(f"Loaded DataFrame size: {len(df)}")
    print("Columns:", df.columns.tolist())
    print("Sample rows:")
    print(df[['date', 'home_team', 'away_team', 'ft_result']].head(5))
else:
    print("DataFrame is None!")

conn.close()
