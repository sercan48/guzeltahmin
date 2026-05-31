"""API-Football Client for fetching live fixtures, lineups, and injuries.

Handles requests to the v3.football.api-sports.io endpoints.
Implements local JSON caching to avoid burning through the 100 requests/day free tier limit.
"""

import json
import os
from pathlib import Path
from datetime import datetime, date

import requests

from config.settings import RAW_DIR
from config.leagues import LEAGUES


LIVE_CACHE_DIR = RAW_DIR / "live_cache"
LIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


class APIFootballClient:
    """Client for API-Football (v3)."""

    BASE_URL = "https://v3.football.api-sports.io"

    def __init__(self):
        # We need to load from environment directly or config
        from config.settings import load_dotenv
        load_dotenv()
        self.api_key = os.getenv("API_FOOTBALL_KEY")
        
        self.headers = {
            "x-apisports-key": self.api_key,
            "x-rapidapi-host": "v3.football.api-sports.io",
        }

    def _get(self, endpoint: str, params: dict, cache_key: str, force_refresh: bool = False) -> dict:
        """Helper to make a GET request with daily caching."""
        if not self.api_key:
            print("[WARN] API_FOOTBALL_KEY is not set.")
            return {}

        today_str = date.today().isoformat()
        cache_file = LIVE_CACHE_DIR / f"{cache_key}_{today_str}.json"

        import time
        # Check cache if not forcing refresh
        if not force_refresh and cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                pass

        # To respect the 10 requests/minute limit, we sleep before making a live network call.
        time.sleep(6.1)
        
        url = f"{self.BASE_URL}/{endpoint}"
        try:
            print(f"  -> API-Football Rq: {endpoint} {params}")
            resp = requests.get(url, headers=self.headers, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                # Verify it's not an API error inside the 200 OK wrapper
                errors = data.get("errors")
                if errors:
                    err_msg = str(errors)
                    if "Free plans do not have access" in err_msg or "Plan restriction" in err_msg:
                        print(f"[WARN] API-Football Plan Restriction: {err_msg}")
                        return {"error": "PLAN_RESTRICTION", "message": err_msg}
                    
                    if isinstance(errors, dict) and len(errors) > 0:
                        print(f"[ERROR] API-Football returned error: {errors}")
                        return {}
                    if isinstance(errors, list) and len(errors) > 0:
                        print(f"[ERROR] API-Football returned error: {errors[0]}")
                        return {}

                # Save to cache
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                return data
            else:
                print(f"[ERROR] HTTP {resp.status_code} on {url}")
                return {}
        except requests.RequestException as e:
            print(f"[ERROR] Request failed: {e}")
            return {}

    def get_fixtures_by_date(self, target_date: str, league_code: str = None) -> list:
        """Get fixtures for a specific date (YYYY-MM-DD)."""
        params = {
            "date": target_date
        }
        
        season = int(target_date[:4])
        if int(target_date[5:7]) < 7:  # Jan-Jun belongs to previous year's season start
            season -= 1
        if season > 2026:
            season = 2026
        params["season"] = season

        # Determine target API IDs
        if league_code:
            target_ids = {LEAGUES[league_code].api_football_id: LEAGUES[league_code].code}
        else:
            target_ids = {lg.api_football_id: lg.code for lg in LEAGUES.values() if lg.api_football_id}

        cache_key = f"fixtures_all"
        data = self._get("fixtures", params, cache_key)
        
        matches = data.get("response", [])
        filtered_matches = []
        for m in matches:
            api_league_id = m.get("league", {}).get("id")
            if api_league_id in target_ids:
                m["internal_league_code"] = target_ids[api_league_id]
                filtered_matches.append(m)
                
        return filtered_matches

    def get_injuries(self, fixture_id: int) -> list:
        """Get injuries and suspensions for a specific fixture."""
        params = {"fixture": fixture_id}
        cache_key = f"injuries_fix{fixture_id}"
        
        data = self._get("injuries", params, cache_key)
        return data.get("response", [])

    def get_odds(self, fixture_id: int) -> dict:
        """Get betting odds for a specific fixture.
        
        Returns dict with bookmaker odds for 1X2, Over/Under, BTTS markets.
        """
        params = {"fixture": fixture_id}
        cache_key = f"odds_fix{fixture_id}"
        
        data = self._get("odds", params, cache_key)
        response = data.get("response", [])
        
        result = {"1x2": {}, "ou25": {}, "btts": {}}
        
        if not response:
            return result
            
        for bookmaker_data in response:
            for bm in bookmaker_data.get("bookmakers", []):
                bm_name = bm.get("name", "Unknown")
                for bet in bm.get("bets", []):
                    bet_name = bet.get("name", "")
                    values = {v["value"]: float(v["odd"]) for v in bet.get("values", []) if v.get("odd")}
                    
                    if bet_name == "Match Winner" and not result["1x2"]:
                        result["1x2"] = {
                            "bookmaker": bm_name,
                            "home": values.get("Home", 0),
                            "draw": values.get("Draw", 0),
                            "away": values.get("Away", 0),
                        }
                    elif "Over/Under" in bet_name and "2.5" in str(values) and not result["ou25"]:
                        result["ou25"] = {
                            "bookmaker": bm_name,
                            "over": values.get("Over 2.5", 0),
                            "under": values.get("Under 2.5", 0),
                        }
                    elif bet_name == "Both Teams Score" and not result["btts"]:
                        result["btts"] = {
                            "bookmaker": bm_name,
                            "yes": values.get("Yes", 0),
                            "no": values.get("No", 0),
                        }
        
        return result

