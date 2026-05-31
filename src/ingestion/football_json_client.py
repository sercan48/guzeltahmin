"""Client for fetching football match data from openfootball/football.json (GitHub)."""

import requests
import logging
from typing import List, Dict, Optional

logger = logging.getLogger("football_json_client")

class FootballJSONClient:
    BASE_URL = "https://raw.githubusercontent.com/openfootball/football.json/master"
    
    from config.leagues import LEAGUES
    # Mapping internal league codes to openfootball filenames/paths
    LEAGUE_MAP = {k: v.openfootball_code for k, v in LEAGUES.items() if v.openfootball_code}

    def get_matches(self, season_label: str, league_code: str) -> List[Dict]:
        """Fetch matches for a season and league.
        
        Args:
            season_label: e.g. "2025-26" or "2024-25"
            league_code: e.g. "T1", "E0"
            
        Returns:
            List of matches in JSON format.
        """
        filename = self.LEAGUE_MAP.get(league_code)
        if not filename:
            logger.error(f"League code {league_code} not mapped to openfootball.")
            return []
            
        url = f"{self.BASE_URL}/{season_label}/{filename}.json"
        
        try:
            logger.info(f"Fetching: {url}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # OpenFootball format: { "name": "...", "matches": [...] }
            return data.get("matches", [])
        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            return []
        except ValueError as e:
            logger.error(f"Failed to parse JSON for {url}: {e}")
            return []

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = FootballJSONClient()
    matches = client.get_matches("2024-25", "T1")
    print(f"Fetched {len(matches)} matches for T1 2024-25")
    if matches:
        print(f"First match example: {matches[0]}")
