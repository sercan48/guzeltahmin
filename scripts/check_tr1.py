import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend

db = get_backend()
db.connect()

# Check what seasons TR1 has
rows = db.fetchall("SELECT DISTINCT league_code, season FROM matches WHERE league_code='TR1' ORDER BY season")
print("TR1 seasons in DB:")
for r in rows:
    print(f"  {r['league_code']} - {r['season']}")

# Check what the SEASON_LABELS are looking for
from config.settings import SEASON_LABELS
print("\nSEASON_LABELS:")
for k, v in SEASON_LABELS.items():
    print(f"  {k} -> {v}")

# Check total TR1 match count 
count = db.fetchone("SELECT COUNT(*) as cnt FROM matches WHERE league_code='TR1'")
print(f"\nTR1 total matches: {count['cnt']}")

# Check a few TR1 league_code values
raw = db.fetchall("SELECT DISTINCT league_code FROM matches LIMIT 20")
print("\nAll league_codes in DB:")
for r in raw:
    print(f"  '{r['league_code']}'")

db.close()
