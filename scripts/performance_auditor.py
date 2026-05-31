"""Performance Auditor — Analyze prediction accuracy vs actual results.

Compares model predictions stored in the database or cache with actual 
match results to identify system weaknesses and improvement areas.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.model.predictor import predict_match
from config.leagues import LEAGUES

def run_audit(limit: int = 100):
    """Audit the last X matches to compare prediction vs result."""
    db = get_backend()
    db.connect()
    
    # Get recent matches that have a result
    matches = db.fetchall(
        f"""SELECT m.*, ht.name as home_team, at.name as away_team
        FROM matches m
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
        WHERE m.ft_result IS NOT NULL AND m.season = '2024-2025'
        ORDER BY m.date DESC
        LIMIT {limit}"""
    )
    
    if not matches:
        print("No completed matches found in DB for auditing.")
        return
        
    correct = 0
    total = 0
    league_stats = {}
    
    print(f"\n{'='*60}")
    print(f"  PERFORMANCE AUDIT (Last {len(matches)} matches)")
    print(f"{'='*60}")
    
    for m in matches:
        try:
            # Predict using the model (as if the match hasn't happened yet)
            pred = predict_match(
                db=db,
                home_team_id=m["home_team_id"],
                away_team_id=m["away_team_id"],
                league_code=m["league_code"],
                season=m["season"],
            )
            
            predicted = pred["predicted_result"]
            actual = m["ft_result"]
            lc = m["league_code"]
            
            if lc not in league_stats:
                league_stats[lc] = {"correct": 0, "total": 0}
            
            league_stats[lc]["total"] += 1
            total += 1
            
            is_correct = (predicted == actual)
            if is_correct:
                correct += 1
                league_stats[lc]["correct"] += 1
                
        except Exception:
            continue
            
    # Print findings
    accuracy = (correct / total * 100) if total > 0 else 0
    print(f"OVERALL ACCURACY: {accuracy:.1f}% ({correct}/{total})")
    print("-" * 30)
    
    for lc, stats in sorted(league_stats.items(), key=lambda x: x[1]["correct"]/x[1]["total"], reverse=True):
        l_acc = (stats["correct"] / stats["total"] * 100)
        l_name = LEAGUES[lc].name if lc in LEAGUES else lc
        print(f"{l_name:<20}: {l_acc:>5.1f}% ({stats['correct']}/{stats['total']})")
        
    print(f"{'='*60}\n")
    
    db.close()

if __name__ == "__main__":
    run_audit(200)
