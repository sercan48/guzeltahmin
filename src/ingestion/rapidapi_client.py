"""RapidAPI Client for Live Stats and Betting Odds."""

import json
import logging
from pathlib import Path
from datetime import datetime, date
import requests

from config.settings import load_dotenv, RAW_DIR

logger = logging.getLogger("rapidapi_client")

RAPIDAPI_CACHE_DIR = RAW_DIR / "rapidapi_cache"
RAPIDAPI_CACHE_DIR.mkdir(parents=True, exist_ok=True)

class RapidAPIClient:
    def __init__(self):
        load_dotenv()
        # Ensure we fallback to the user's provided key if env var isn't set
        import os
        self.api_key = os.getenv("RAPIDAPI_KEY", "7830e009bfmsh3b278df884fa170p1b1bb5jsnffb7d6dfed73")
        
    def _make_get_request(self, host: str, endpoint: str, params: dict, cache_key: str, cache_hours: int = 1) -> dict:
        """Helper to fetch from rapid API with caching."""
        today_str = date.today().isoformat()
        # Very simple hourly cache hash to avoid burning limits during development/usage
        current_hour = datetime.now().hour
        cache_file = RAPIDAPI_CACHE_DIR / f"{cache_key}_{today_str}_{current_hour}.json"
        
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
                
        url = f"https://{host}/{endpoint}"
        headers = {
            "x-rapidapi-key": self.api_key,
            "x-rapidapi-host": host,
            "Content-Type": "application/json"
        }
        
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                
                # Check for "You are not subscribed" error
                if isinstance(data, dict) and data.get("message") == "You are not subscribed to this API.":
                    logger.warning(f"Not subscribed to Host: {host}")
                    return data
                    
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                return data
            else:
                logger.error(f"RapidAPI HTTP {resp.status_code} from {url}")
                return {}
        except Exception as e:
            logger.error(f"RapidAPI request failed: {e}")
            return {}

    def get_live_player_stats(self, search_term: str) -> dict:
        """Fetch player stats to compute in-game form."""
        host = "free-api-live-football-data.p.rapidapi.com"
        endpoint = "football-players-search"
        params = {"search": search_term}
        cache_key = f"player_{search_term.replace(' ', '_')}"
        
        return self._make_get_request(host, endpoint, params, cache_key)
        
    def get_betting_odds(self) -> dict:
        """Fetch odds names/mappings from betting API."""
        host = "football-betting-odds1.p.rapidapi.com"
        endpoint = "oddsnames"
        params = {}
        cache_key = "oddsnames_list"
        
        return self._make_get_request(host, endpoint, params, cache_key)
