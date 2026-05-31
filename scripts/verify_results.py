import sys
from pathlib import Path
sys.path.insert(0, ".")

from src.db.base import get_backend

def verify_latest_predictions():
    db = get_backend()
    db.connect()
    
    print("=" * 50)
    print("  HAFTALIK TAHMIN DOGRULAMA RAPORU (24-31 Mayis)")
    print("=" * 50)

    # Fetch predictions we made for the target week where actual results are now available
    results = db.fetchall("""
        SELECT m.date, m.league_code,
               t1.name as home, t2.name as away,
               m.ft_result, p.predicted_result, p.confidence_score
        FROM matches m
        JOIN teams t1 ON m.home_team_id = t1.id
        JOIN teams t2 ON m.away_team_id = t2.id
        JOIN predictions p ON p.match_id = m.id
        WHERE m.date >= '2026-05-20' AND m.date <= '2026-06-01'
        AND m.ft_result IS NOT NULL
        ORDER BY m.date
    """)

    if not results:
        print("\nHenuz sonuclanmis mac (veya veritabanina girilmis skor) bulunamadi.")
        print("Lutfen maclarin oynanmasini veya sonuclarin guncellenmesini bekleyin.")
        return

    correct = 0
    total = len(results)
    
    for r in results:
        is_correct = r["predicted_result"] == r["ft_result"]
        if is_correct:
            correct += 1
            
        icon = "✅" if is_correct else "❌"
        date_str = str(r["date"])[:10]
        print(f"{icon} {date_str} | {r['home']} vs {r['away']} | Tahmin: {r['predicted_result']} -> Gercek: {r['ft_result']} (Güven: %{r['confidence_score']})")

    acc = (correct / total) * 100
    print("\n" + "=" * 50)
    print(f"Toplam Oynanan Mac: {total}")
    print(f"Dogru Bilinen: {correct}")
    print(f"HAFTALIK BASARI YUZDESI: %{acc:.1f}")
    print("=" * 50)
    
    db.close()

if __name__ == "__main__":
    verify_latest_predictions()
