"""League-relative team tiering based on squad market value."""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.base import get_backend

def calculate_league_tiers(league_code: str):
    """Categorize teams into 5 Tiers based on league-relative market value.
    
    Tier 1 (Top) -> Tier 5 (Relegation Candidate)
    
    Thresholds (relative to league mean):
    - Tier 1: > 200% of Mean
    - Tier 2: 125% - 200% of Mean
    - Tier 3: 75% - 125% of Mean
    - Tier 4: 40% - 75% of Mean
    - Tier 5: < 40% of Mean
    """
    db = get_backend()
    db.connect()
    
    teams = db.fetchall(
        "SELECT id, name, squad_value FROM teams WHERE league_code = ?",
        (league_code,)
    )
    
    if not teams:
        db.close()
        return
        
    values = [t["squad_value"] for t in teams if t["squad_value"] > 0]
    if not values:
        # Fallback to default tier 3 if no financial data
        for t in teams:
            db.execute("UPDATE teams SET tier = 3 WHERE id = ?", (t["id"],))
        db.close()
        return
        
    mean_val = sum(values) / len(values)
    print(f"  League {league_code} Mean Value: {mean_val:.2f}M€")
    
    for t in teams:
        val = t["squad_value"]
        if val == 0:
            tier = 3 # Neutral
        elif val > mean_val * 2.0:
            tier = 1
        elif val > mean_val * 1.25:
            tier = 2
        elif val > mean_val * 0.75:
            tier = 3
        elif val > mean_val * 0.40:
            tier = 4
        else:
            tier = 5
            
        db.execute("UPDATE teams SET tier = ? WHERE id = ?", (tier, t["id"]))
        
    db.close()

def run_all_tiering():
    """Update tiers for all teams in the database."""
    db = get_backend()
    db.connect()
    leagues = db.fetchall("SELECT DISTINCT league_code FROM teams")
    db.close()
    
    print("=" * 60)
    print("  Calculating Team Tiers (League-Relative)")
    print("=" * 60)
    
    for l in leagues:
        calculate_league_tiers(l["league_code"])
    
    print("\n[OK] Tier mapping completed.")

if __name__ == "__main__":
    run_all_tiering()
