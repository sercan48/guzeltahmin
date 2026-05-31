import traceback
from src.db.base import get_backend
from app.bot.formatters import format_match_analysis_card
from src.agents.data_agent import get_team_status_from_db

def main():
    db = get_backend()
    db.connect()
    try:
        predictions = db.fetchall("""
            SELECT p.*, m.date, m.league_code,
                   m.home_team_id, m.away_team_id,
                   t1.name as home_team, t2.name as away_team
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            JOIN teams t1 ON m.home_team_id = t1.id
            JOIN teams t2 ON m.away_team_id = t2.id
            WHERE DATE(m.date) = DATE('now') AND m.ft_result IS NULL
            AND p.confidence_score >= 60
            ORDER BY p.confidence_score DESC
            LIMIT 2
        """)
        
        print(f"Found {len(predictions)} predictions to test.")
        
        for p in predictions:
            print(f"\n--- Testing Match: {p['home_team']} vs {p['away_team']} ---")
            odds_row = db.fetchone("""
                SELECT o.home_odds, o.draw_odds, o.away_odds,
                       o.over25_odds, o.under25_odds
                FROM odds o WHERE o.match_id = ?
                ORDER BY o.id DESC LIMIT 1
            """, (p["match_id"],))

            market_odds = {}
            if odds_row:
                market_odds = {
                    "h": odds_row.get("home_odds"),
                    "d": odds_row.get("draw_odds"),
                    "a": odds_row.get("away_odds"),
                    "o25": odds_row.get("over25_odds"),
                    "u25": odds_row.get("under25_odds"),
                }

            home_status = get_team_status_from_db(db, p["home_team_id"])
            away_status = get_team_status_from_db(db, p["away_team_id"])

            pred_dict = {
                "home_team": p["home_team"],
                "away_team": p["away_team"],
                "league_code": p.get("league_code", ""),
                "predicted_result": p.get("predicted_result", "?"),
                "confidence": p.get("confidence_score", 0) or 0,
                "home_win_prob": p.get("home_win_prob", 0) or 0,
                "draw_prob": p.get("draw_prob", 0) or 0,
                "away_win_prob": p.get("away_win_prob", 0) or 0,
                "over25_prob": p.get("over25_prob"),
                "btts_prob": p.get("btts_prob"),
                "model_agreement": p.get("model_agreement"),
                "value_margin": p.get("value_margin", 0),
                "home_lambda": p.get("home_lambda"),
                "away_lambda": p.get("away_lambda"),
                "model_type": p.get("model_type", "Ensemble"),
                "_odds": market_odds if market_odds else None,
                "home_status": home_status,
                "away_status": away_status,
            }

            try:
                card_text = format_match_analysis_card(pred_dict, is_free=True, promo_footer="Mock Promo Footer")
                with open("scratch/output_cards.txt", "a", encoding="utf-8") as f_out:
                    f_out.write(f"\n--- {p['home_team']} vs {p['away_team']} ---\n")
                    f_out.write(card_text)
                    f_out.write("\n" + "="*40 + "\n")
                print(f"Formatted card for {p['home_team']} successfully written to scratch/output_cards.txt.")
            except Exception as e:
                print(f"Format Error for {p['home_team']} vs {p['away_team']}: {e}")
                traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    main()
