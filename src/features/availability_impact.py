"""Module to calculate the impact of missing players (injuries/suspensions).

Links names from API-Football injuries endpoint to the players database to estimate
the financial 'Power Loss' of a team for a specific fixture.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.ingestion.api_football_client import APIFootballClient
from src.ingestion.fuzzy_matcher import FuzzyMatcher
from src.db.base import get_backend

def calculate_power_loss(fixture_id: int, team_id_db: int) -> dict:
    """Calculate the % power loss for a team based on missing players' market values.
    
    Returns:
        dict: {
            "power_loss_pct": float,
            "missing_value_m": float,
            "total_squad_value_m": float,
            "injured_players": list[str]
        }
    """
    db = get_backend()
    db.connect()
    
    # 1. Get team info
    team = db.fetchone("SELECT squad_value FROM teams WHERE id = ?", (team_id_db,))
    if not team or not team["squad_value"]:
        db.close()
        return {"power_loss_pct": 0.0, "missing_value_m": 0.0, "total_squad_value_m": 1.0, "injured_players": []}
    
    total_val = team["squad_value"]
    
    # 2. Fetch injuries from API
    client = APIFootballClient()
    injuries = client.get_injuries(fixture_id)
    
    if not injuries:
        db.close()
        return {"power_loss_pct": 0.0, "missing_value_m": 0.0, "total_squad_value_m": total_val, "injured_players": []}
    
    # 3. Filter for our specific team
    # Note: API-Football injuries Response objects have a 'team' section
    # But fixture_id might be different? fixture_id is unique per match.
    # The injuries endpoint returns all injuries for that fixture.
    
    # We need to map team_id_db to API-Football ID to filter
    # But wait, players table has team_id. We can match player names globally within that team_id.
    
    team_players = db.fetchall(
        "SELECT name, market_value FROM players WHERE team_id = ?",
        (team_id_db,)
    )
    db_player_names = [p["name"] for p in team_players]
    db_player_map = {p["name"]: p["market_value"] for p in team_players}
    
    fuzzy = FuzzyMatcher()
    missing_value = 0.0
    matched_players = []
    
    for inj in injuries:
        # Check if the injury belongs to our team
        # (Assuming the caller passed the correct fixture_id and wants stats for team_id_db)
        # Note: API-Football injury response structure: {"player": {"name": "..."}, "team": {"id": ...}}
        
        # We'll just match the name against the team's roster
        api_player_name = inj.get("player", {}).get("name", "")
        if not api_player_name:
            continue
            
        match = fuzzy.match(api_player_name, db_player_names)
        if match:
            val = db_player_map[match]
            missing_value += val
            matched_players.append(f"{match} ({val:.1f}M€)")
            
    db.close()
    
    pct_loss = (missing_value / total_val) * 100 if total_val > 0 else 0.0
    
    return {
        "power_loss_pct": round(pct_loss, 2),
        "missing_value_m": round(missing_value, 2),
        "total_squad_value_m": round(total_val, 2),
        "injured_players": matched_players
    }

if __name__ == "__main__":
    # Test with a mock run or placeholder
    print("Availability Impact Module Loaded.")
    # Example: print(calculate_power_loss(1234, 1))
