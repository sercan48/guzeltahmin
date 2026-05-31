import requests
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("API_FOOTBALL_KEY")

headers = {
    "x-apisports-key": api_key,
    "x-rapidapi-host": "v3.football.api-sports.io",
}

print("--- Testing API-Football for Norway Eliteserien (ID 103) ---")
# Let's check if we can query Eliteserien fixtures for 2026 season on 2026-05-29
url = "https://v3.football.api-sports.io/fixtures"
params = {
    "date": "2026-05-29",
    "league": 103,
    "season": 2026
}

resp = requests.get(url, headers=headers, params=params)
print("Status code:", resp.status_code)
data = resp.json()
print("Response keys:", data.keys())
if "errors" in data:
    print("Errors:", data["errors"])
if "response" in data:
    print(f"Fixtures returned: {len(data['response'])}")
    for f in data["response"]:
        print(f"{f['fixture']['id']} | {f['teams']['home']['name']} vs {f['teams']['away']['name']} | {f['fixture']['date']}")

print("\n--- Testing API-Football for Norway 1. Division (ID 104) ---")
params = {
    "date": "2026-05-29",
    "league": 104,
    "season": 2026
}

resp = requests.get(url, headers=headers, params=params)
print("Status code:", resp.status_code)
data = resp.json()
if "errors" in data:
    print("Errors:", data["errors"])
if "response" in data:
    print(f"Fixtures returned: {len(data['response'])}")
    for f in data["response"]:
        print(f"{f['fixture']['id']} | {f['teams']['home']['name']} vs {f['teams']['away']['name']} | {f['fixture']['date']}")
