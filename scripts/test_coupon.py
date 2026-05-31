"""Test coupon builder v2 with realistic odds targets."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.model.predictor import predict_match
from src.evaluator.coupon_builder import build_match_bets, build_coupon, format_coupon


def main():
    db = get_backend()
    db.connect()

    matches = db.fetchall(
        """SELECT m.*, ht.name as home_team, at.name as away_team
        FROM matches m
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
        WHERE m.league_code IN ('T1', 'E0', 'SP1', 'D1', 'I1')
        AND m.season = '2024-2025'
        ORDER BY m.date DESC
        LIMIT 25"""
    )

    print(f"Test maclari: {len(matches)}\n")

    all_match_bets = []
    for m in matches:
        try:
            pred = predict_match(
                db=db, home_team_id=m["home_team_id"],
                away_team_id=m["away_team_id"],
                league_code=m["league_code"], season=m["season"],
            )
            fixture = f"{m['home_team']} vs {m['away_team']}"
            bets = build_match_bets(pred, fixture, m["league_code"])
            if bets:
                all_match_bets.append(bets)
        except Exception:
            pass

    print(f"Bahis uretilen mac: {len(all_match_bets)}\n")

    # Test all 3 strategies
    for strategy in ["banko", "value", "surpriz"]:
        coupon = build_coupon(all_match_bets, strategy=strategy)
        print(format_coupon(coupon))
        print()

    # Test league-specific coupon
    print("\n--- LIG FILTRELI KUPON ---\n")
    coupon_t1 = build_coupon(all_match_bets, strategy="banko", league_filter="T1")
    print(format_coupon(coupon_t1))

    db.close()


if __name__ == "__main__":
    main()
