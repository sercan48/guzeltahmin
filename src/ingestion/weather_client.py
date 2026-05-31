"""OpenWeatherMap Client to fetch live and forecast weather data for matches.

Uses the OpenWeatherMap Current Weather Data API.
Note: Team names are mapped to roughly correct cities to get accurate weather.
"""

import os
import requests

from config.settings import OPENWEATHER_API_KEY


# A rough map for common teams that don't match their city directly,
# or where stripping "FC" isn't enough.
CITY_MAPPINGS = {
    "Arsenal": "London",
    "Chelsea": "London",
    "Tottenham Hotspur": "London",
    "West Ham United": "London",
    "Crystal Palace": "London",
    "Fulham": "London",
    "Brentford": "London",
    "Everton": "Liverpool",
    "Aston Villa": "Birmingham",
    "Galatasaray": "Istanbul",
    "Fenerbahçe": "Istanbul",
    "Beşiktaş": "Istanbul",
    "Kasımpaşa": "Istanbul",
    "İstanbul Başakşehir": "Istanbul",
    "Karagümrük": "Istanbul",
    "Juventus": "Turin",
    "Inter Milan": "Milan",
    "AC Milan": "Milan",
    "Lazio": "Rome",
    "AS Roma": "Rome",
    "Paris Saint-Germain": "Paris",
    "Real Madrid": "Madrid",
    "Atletico Madrid": "Madrid",
    "Real Betis": "Seville",
    "Sevilla": "Seville",
    "Athletic Bilbao": "Bilbao",
    "Bayern München": "Munich",
    "Borussia Dortmund": "Dortmund",
}

def resolve_city_name(team_name: str) -> str:
    """Attempt to resolve a team name to its city."""
    # Exact match in mapping
    if team_name in CITY_MAPPINGS:
        return CITY_MAPPINGS[team_name]
    
    # Strip common suffixes/prefixes
    clean = team_name.replace(" FC", "").replace(" SK", "").replace(" FK", "").replace(" AS", "")
    clean = clean.replace("United", "").replace("City", "").replace("Hotspur", "")
    return clean.strip()


class WeatherClient:
    BASE_URL = "https://api.openweathermap.org/data/2.5/weather"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or OPENWEATHER_API_KEY

    def get_current_weather(self, team_name: str) -> dict:
        """Fetch current weather for the team's city.
        
        Returns a dict with:
        - condition: Main weather condition (Rain, Snow, Clear, Clouds, Extreme)
        - temp: Temperature in Celsius
        - wind_speed: Wind speed in m/s
        - is_raining: bool
        - is_snowing: bool
        """
        if not self.api_key:
            return None

        city = resolve_city_name(team_name)
        params = {
            "q": city,
            "appid": self.api_key,
            "units": "metric"
        }

        try:
            resp = requests.get(self.BASE_URL, params=params, timeout=5)
            if resp.status_code != 200:
                return self._default_weather()

            data = resp.json()
            weather_main = data["weather"][0]["main"] if data.get("weather") else "Clear"
            temp = data.get("main", {}).get("temp", 20.0)
            wind = data.get("wind", {}).get("speed", 0.0)

            return {
                "condition": weather_main,
                "temp": temp,
                "wind_speed": wind,
                "is_raining": weather_main in ["Rain", "Drizzle", "Thunderstorm"],
                "is_snowing": weather_main == "Snow",
                "city_resolved": city
            }
            
        except Exception:
            return self._default_weather()

    def _default_weather(self):
        """Fallback natural weather conditions."""
        return {
            "condition": "Unknown",
            "temp": 15.0,  # mild
            "wind_speed": 2.0, # calm
            "is_raining": False,
            "is_snowing": False,
            "city_resolved": "Unknown"
        }
