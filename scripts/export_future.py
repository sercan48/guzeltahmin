import sys
import json
from pathlib import Path
sys.path.insert(0, ".")

from src.db.base import get_backend

db = get_backend()
db.connect()

upcoming = db.fetchall("""
    SELECT m.date, m.league_code,
           t1.name as home, t2.name as away,
           p.predicted_result, p.home_win_prob, p.draw_prob, p.away_win_prob, p.confidence_score
    FROM matches m
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    JOIN predictions p ON p.match_id = m.id
    WHERE m.ft_result IS NULL
    AND m.date >= '2026-05-20' AND m.date <= '2026-06-01'
    ORDER BY m.date, m.league_code
""")

md_lines = [
    "# ⚽ Gelecek Maç Tahminleri (24-31 Mayıs 2026)",
    "",
    "Sistemimizin geriye dönük öğrenme ile güncellenen son versiyonunun bu haftaki maç tahminleri:",
    "",
    "| Tarih | Lig | Ev Sahibi | Deplasman | Tahmin | Güven | H (%) | D (%) | A (%) |",
    "|---|---|---|---|:---:|:---:|---|---|---|",
]

json_data = []

for m in upcoming:
    date_str = str(m["date"])[:10]
    h_prob = int((m["home_win_prob"] or 0) * 100)
    d_prob = int((m["draw_prob"] or 0) * 100)
    a_prob = int((m["away_win_prob"] or 0) * 100)
    conf = int(m["confidence_score"] or 0)
    pred = m["predicted_result"]
    pred_label = {"H": "1", "D": "X", "A": "2"}.get(pred, "?")
    
    md_lines.append(
        f"| {date_str} | {m['league_code']} | {m['home']} | {m['away']} | **{pred_label}** | %{conf} | {h_prob} | {d_prob} | {a_prob} |"
    )
    
    json_data.append({
        "date": date_str,
        "league": m["league_code"],
        "home": m["home"],
        "away": m["away"],
        "prediction": pred_label,
        "confidence": conf,
        "probs": {"H": h_prob, "D": d_prob, "A": a_prob}
    })

# Save MD
with open("gelecek_tahminler_mayis.md", "w", encoding="utf-8") as f:
    f.write("\n".join(md_lines))

# Save JSON
with open("gelecek_tahminler_mayis.json", "w", encoding="utf-8") as f:
    json.dump(json_data, f, ensure_ascii=False, indent=2)

print("Dosyalar olusturuldu.")
db.close()
