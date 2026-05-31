"""Run predictions on all this week's matches and generate accuracy report."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend

db = get_backend()
db.connect()

# 1. This week's matches with results (already played)
print("=" * 60)
print("  BU HAFTA SONUCLANAN MACLAR - RETROACTIVE TAHMIN TESTI")
print("=" * 60)

played = db.fetchall("""
    SELECT m.id, m.date, m.league_code, m.season,
           m.home_team_id, m.away_team_id,
           m.ft_home_goals as hg, m.ft_away_goals as ag, m.ft_result,
           t1.name as home, t2.name as away
    FROM matches m
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    WHERE m.date >= '2026-05-16' AND m.date <= '2026-05-25'
    AND m.ft_result IS NOT NULL
    ORDER BY m.date, m.league_code
""")

print(f"\nSonuclanan mac sayisi: {len(played)}")

# Check retro predictions for these
correct = 0
total = 0
league_stats = {}
results_detail = []

for m in played:
    pred = db.fetchone("""
        SELECT predicted_result, home_win_prob, draw_prob, away_win_prob, confidence_score
        FROM predictions WHERE match_id = ?
    """, (m["id"],))

    if pred:
        is_correct = pred["predicted_result"] == m["ft_result"]
        if is_correct:
            correct += 1
        total += 1

        league = m["league_code"]
        if league not in league_stats:
            league_stats[league] = {"total": 0, "correct": 0}
        league_stats[league]["total"] += 1
        if is_correct:
            league_stats[league]["correct"] += 1

        icon = "[OK]" if is_correct else "[XX]"
        score = f"{m['hg']}-{m['ag']}"
        results_detail.append({
            "icon": icon, "date": str(m["date"])[:10],
            "league": league, "home": m["home"], "away": m["away"],
            "score": score, "actual": m["ft_result"],
            "predicted": pred["predicted_result"],
            "confidence": pred["confidence_score"] or 0,
        })

# Print results
if results_detail:
    print(f"\nTahmin edilen: {total} | Dogru: {correct} | Dogruluk: %{correct/max(total,1)*100:.1f}")
    print()
    for r in results_detail[:50]:
        print(f"  {r['icon']} {r['date']} {r['league']:3s} | {r['home'][:18]:18s} vs {r['away'][:18]:18s} | {r['score']} ({r['actual']}) Tahmin:{r['predicted']} G:{r['confidence']}")

    print(f"\nLig bazinda:")
    for league, stats in sorted(league_stats.items(), key=lambda x: x[1]["total"], reverse=True):
        acc = stats["correct"] / stats["total"] * 100
        print(f"  {league}: %{acc:.0f} ({stats['correct']}/{stats['total']})")
else:
    print("  Bu hafta icin tahmin bulunamadi.")

# 2. Upcoming matches (not yet played)
print(f"\n{'='*60}")
print("  HENUZ OYNANMAMIS MACLAR (TAHMIN YAPILACAK)")
print("=" * 60)

upcoming = db.fetchall("""
    SELECT m.id, m.date, m.league_code, m.season,
           m.home_team_id, m.away_team_id,
           t1.name as home, t2.name as away
    FROM matches m
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    WHERE m.ft_result IS NULL
    AND m.date >= '2026-05-20' AND m.date <= '2026-06-01'
    ORDER BY m.date, m.league_code
""")

print(f"\nOynanmamis mac sayisi: {len(upcoming)}")

# Generate predictions for upcoming
from scripts.retroactive_learner import _build_team_cache, _predict_match_retro

for season in ["2025-2026"]:
    cache = _build_team_cache(db, season)

    predictions_made = 0
    for m in upcoming:
        # Check if already predicted
        existing = db.fetchone("SELECT id FROM predictions WHERE match_id = ?", (m["id"],))
        if existing:
            continue

        pred = _predict_match_retro(db, m | {"ft_result": None}, cache, season)

        # Store
        try:
            db.execute("""
                INSERT INTO predictions (match_id, home_win_prob, draw_prob, away_win_prob,
                                         confidence_score, predicted_result, model_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'retro_v2_live', CURRENT_TIMESTAMP)
            """, (
                m["id"], pred["h_prob"], pred["d_prob"], pred["a_prob"],
                int(pred["confidence"]), pred["predicted"],
            ))
            predictions_made += 1
        except Exception:
            pass

    print(f"  Yeni tahmin olusturuldu: {predictions_made}")

# Show upcoming predictions
print(f"\nGELECEK MAC TAHMINLERI:")
for m in upcoming:
    pred = db.fetchone("""
        SELECT predicted_result, home_win_prob, draw_prob, away_win_prob, confidence_score
        FROM predictions WHERE match_id = ?
    """, (m["id"],))
    if pred:
        result_label = {"H": "1", "D": "X", "A": "2"}.get(pred["predicted_result"], "?")
        conf = pred["confidence_score"] or 0
        h = (pred["home_win_prob"] or 0) * 100
        d = (pred["draw_prob"] or 0) * 100
        a = (pred["away_win_prob"] or 0) * 100
        print(f"  {str(m['date'])[:10]} {m['league_code']:3s} | {m['home'][:18]:18s} vs {m['away'][:18]:18s} | TAHMiN: {result_label} (G:{conf}) H:{h:.0f}% D:{d:.0f}% A:{a:.0f}%")

# 3. Overall system stats
print(f"\n{'='*60}")
print("  GENEL SISTEM ISTATISTIKLERI")
print("=" * 60)

total_preds = db.fetchone("SELECT COUNT(*) as c FROM predictions")
with_result = db.fetchone("SELECT COUNT(*) as c FROM predictions WHERE actual_result IS NOT NULL")
correct_all = db.fetchone("""
    SELECT COUNT(*) as c FROM predictions
    WHERE actual_result IS NOT NULL AND predicted_result = actual_result
""")

t = total_preds["c"]
w = with_result["c"]
c = correct_all["c"]
print(f"  Toplam tahmin: {t}")
print(f"  Sonucu olan: {w}")
print(f"  Dogru tahmin: {c}")
if w > 0:
    print(f"  Genel dogruluk: %{c/w*100:.1f}")

# Per model type
models = db.fetchall("""
    SELECT model_type, COUNT(*) as total,
           SUM(CASE WHEN predicted_result = actual_result THEN 1 ELSE 0 END) as correct
    FROM predictions WHERE actual_result IS NOT NULL
    GROUP BY model_type
""")
if models:
    print(f"\n  Model bazinda:")
    for m in models:
        acc = m["correct"] / m["total"] * 100 if m["total"] else 0
        print(f"    {m['model_type'] or 'unknown'}: %{acc:.1f} ({m['correct']}/{m['total']})")

db.close()
