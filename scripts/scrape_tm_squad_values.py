"""One-time scraper for Transfermarkt squad values (Season 2025-2026).

Fetches total squad value and average player value for each team in the supported leagues
as of end of summer transfer window (2025-08-31).
"""

import os
import time
import random
import requests
import pandas as pd
from bs4 import BeautifulSoup
from pathlib import Path

# Mapping internal codes to Transfermarkt IDs and slugs
TM_MAP = {
    "T1": {"id": "TR1", "slug": "super-lig"},
    "E0": {"id": "GB1", "slug": "premier-league"},
    "E1": {"id": "GB2", "slug": "championship"},
    "SP1": {"id": "ES1", "slug": "la-liga"},
    "D1": {"id": "L1", "slug": "bundesliga"},
    "I1": {"id": "IT1", "slug": "serie-a"},
    "F1": {"id": "FR1", "slug": "ligue-1"},
    "N1": {"id": "NL1", "slug": "eredivisie"},
    "P1": {"id": "PO1", "slug": "primeira-liga"},
    "B1": {"id": "BE1", "slug": "jupiler-pro-league"},
    "SC0": {"id": "SC1", "slug": "scottish-premiership"},
}

# The target cut-off date to get "Season Start" values
CUT_OFF_DATE = "2025-08-31"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def parse_value(val_str: str) -> float:
    """Convert TM value strings (e.g. '€344.75m' or '€1.31bn') to float in Millions."""
    if not val_str or val_str == "-" or val_str == "?":
        return 0.0
    
    val_str = val_str.replace("€", "").lower().strip()
    
    multiplier = 1.0
    if "bn" in val_str:
        multiplier = 1000.0
        val_str = val_str.replace("bn", "")
    elif "m" in val_str:
        multiplier = 1.0
        val_str = val_str.replace("m", "")
    elif "k" in val_str:
        multiplier = 0.001
        val_str = val_str.replace("k", "")
        
    try:
        # Transfermarkt sometimes uses commas or dots depending on locale
        # We need to handle both. Given the browser saw '€344.75m', it's likely dot.
        # However, some systems might see '344,75'.
        clean_val = val_str.replace(",", ".")
        # Remove any remaining non-numeric chars except the dot
        clean_val = "".join(c for c in clean_val if c.isdigit() or c == ".")
        return float(clean_val) * multiplier
    except ValueError:
        return 0.0

def scrape_league(league_code: str):
    """Scrape squad values for a specific league."""
    info = TM_MAP[league_code]
    url = f"https://www.transfermarkt.com/{info['slug']}/startseite/wettbewerb/{info['id']}/plus/?stichtag={CUT_OFF_DATE}"
    
    print(f"Scraping {league_code} ({info['slug']})...")
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        table = soup.select_one("div#yw1 table.items")
        if not table:
            # Try alternate selector if first fails
            table = soup.select_one("table.items")
            if not table:
                print(f"[ERROR] Could not find items table for {league_code}")
                return []

        rows = table.select("tbody tr")
        data = []
        
        for row in rows:
            cells = row.find_all("td", recursive=False)
            if len(cells) < 7:
                continue
            
            # Team name
            team_cell = cells[1]
            team_link = team_cell.select_one("a")
            if not team_link:
                continue
            team_name = team_link.text.strip()
            
            # Squad size
            squad_cell = cells[2]
            squad_size_text = squad_cell.text.strip()
            squad_size = int(squad_size_text) if squad_size_text.isdigit() else 0
            
            # Avg Market Value (index 5)
            avg_val_str = cells[5].text.strip()
            
            # Total squad value (index 6)
            total_val_str = cells[6].text.strip()
            
            data.append({
                "league_code": league_code,
                "team_name": team_name,
                "squad_size": squad_size,
                "avg_player_value": parse_value(avg_val_str),
                "total_squad_value": parse_value(total_val_str)
            })
            
        return data
        
    except Exception as e:
        print(f"[ERROR] Failed to scrape {league_code}: {e}")
        return []

def main():
    all_data = []
    
    for code in TM_MAP.keys():
        league_results = scrape_league(code)
        all_data.extend(league_results)
        
        # Polite delay
        delay = random.uniform(2.5, 5.0)
        print(f"  -> Found {len(league_results)} teams. Waiting {delay:.1f}s...")
        time.sleep(delay)
        
    if not all_data:
        print("[CRITICAL] No data scraped! Check selectors or connection.")
        return
        
    df = pd.DataFrame(all_data)
    
    # Save to data directory
    output_dir = Path("data/transfermarkt")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "squad_values_2025_26.csv"
    
    df.to_csv(output_path, index=False)
    print(f"\n[OK] Scraped {len(df)} total teams across 11 leagues.")
    print(f"  -> Data saved to {output_path}")

if __name__ == "__main__":
    main()
