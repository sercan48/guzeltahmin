import sys
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ingestion.api_football_client import APIFootballClient
from src.db.base import get_backend
from src.ingestion.fuzzy_matcher import FuzzyMatcher

client = APIFootballClient()
db = get_backend()
db.connect()

db_teams = db.fetchall("SELECT id, name FROM teams WHERE league_code = 'NORWAY_ELITESERIEN'")
print(f"Norway Eliteserien teams in DB: {len(db_teams)}")
for t in db_teams:
    print(f"  - {t['name']} (ID: {t['id']})")

today = date.today()
for i in range(5):
    target_date = (today + timedelta(days=i)).isoformat()
    print(f"\nChecking date: {target_date}")
    fixtures = client.get_fixtures_by_date(target_date, league_code="NORWAY_ELITESERIEN")
    print(f"Fixtures found: {len(fixtures)}")
    for f in fixtures:
        print(f"  Fixture ID: {f['fixture']['id']}")
        print(f"  Teams: {f['teams']['home']['name']} vs {f['teams']['away']['name']}")
        print(f"  League: {f['league']['name']} (ID: {f['league']['id']})")
        print(f"  Date: {f['fixture']['date']}")

db.close()
