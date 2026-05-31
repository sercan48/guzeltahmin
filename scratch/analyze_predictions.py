import sqlite3
import sys

# Reconfigure stdout to use utf-8 to avoid console encoding errors on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

db_path = "data/guzel_tahmin.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("""
    SELECT m.id, m.date, m.league_code, t1.name as home, t2.name as away,
           m.ft_home_goals, m.ft_away_goals, m.ft_result,
           p.predicted_result, p.confidence_score, p.home_win_prob, p.draw_prob, p.away_win_prob
    FROM matches m
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    JOIN predictions p ON p.match_id = m.id
    WHERE m.date LIKE '2026-05-29%'
    ORDER BY m.id
""")
rows = cursor.fetchall()

def prob_to_outcome(prob_name):
    if prob_name == "H": return "MS 1"
    if prob_name == "D": return "MS X"
    if prob_name == "A": return "MS 2"
    return "UNKNOWN"

def res_to_outcome(res):
    if res == "H": return "MS 1"
    if res == "D": return "MS X"
    if res == "A": return "MS 2"
    return "UNKNOWN"

table_rows = []
primary_correct = 0
secondary_correct_for_failed_primary = 0
failed_primary_count = 0

for r in rows:
    home = r["home"]
    away = r["away"]
    ft_res = r["ft_result"]
    actual_score = f"{r['ft_home_goals']}-{r['ft_away_goals']}"
    
    probs = [
        ("H", r["home_win_prob"]),
        ("D", r["draw_prob"]),
        ("A", r["away_win_prob"])
    ]
    # Sort by probability descending
    probs_sorted = sorted(probs, key=lambda x: x[1], reverse=True)
    
    primary_pick = prob_to_outcome(probs_sorted[0][0])
    primary_prob = probs_sorted[0][1] * 100
    
    secondary_pick = prob_to_outcome(probs_sorted[1][0])
    secondary_prob = probs_sorted[1][1] * 100
    
    actual_outcome = res_to_outcome(ft_res)
    
    is_primary_correct = (primary_pick == actual_outcome)
    is_secondary_correct = (secondary_pick == actual_outcome)
    
    if is_primary_correct:
        primary_correct += 1
    else:
        failed_primary_count += 1
        if is_secondary_correct:
            secondary_correct_for_failed_primary += 1
            
    table_rows.append({
        "match": f"{home} - {away}",
        "score": actual_score,
        "actual": actual_outcome,
        "primary": f"{primary_pick} (%{primary_prob:.1f})",
        "primary_ok": "OK" if is_primary_correct else "FAIL",
        "secondary": f"{secondary_pick} (%{secondary_prob:.1f})",
        "secondary_ok": "OK" if is_secondary_correct else "FAIL"
    })

output_lines = []
output_lines.append("### 29 Mayıs 2026 Maçları Tahmin ve Sonuç Karşılaştırması\n")
output_lines.append("| Maç | Skor | Sonuç | Ana Tahmin (Olasılık) | Ana Başarı | İkincil Tahmin (Olasılık) | İkincil Başarı |")
output_lines.append("|---|---|---|---|---|---|---|")
for row in table_rows:
    output_lines.append(f"| {row['match']} | {row['score']} | {row['actual']} | {row['primary']} | {row['primary_ok']} | {row['secondary']} | {row['secondary_ok']} |")

output_lines.append("\n### İstatistikler")
total = len(rows)
primary_acc = (primary_correct / total) * 100 if total > 0 else 0
output_lines.append(f"- **Toplam Maç Sayısı:** {total}")
output_lines.append(f"- **Ana Tahmin Başarı Oranı:** %{primary_acc:.1f} ({primary_correct}/{total})")
output_lines.append(f"- **Başarısız Olunan Ana Tahmin Sayısı:** {failed_primary_count}")

if failed_primary_count > 0:
    secondary_acc_failed_primary = (secondary_correct_for_failed_primary / failed_primary_count) * 100
    output_lines.append(f"- **Ana Tahminin Başarısız Olduğu Maçlarda İkincil Seçeneğin Başarı Oranı:** %{secondary_acc_failed_primary:.1f} ({secondary_correct_for_failed_primary}/{failed_primary_count})")
else:
    output_lines.append("- **Başarısız olunan ana tahmin yok!**")

output_content = "\n".join(output_lines)
print(output_content)

# Write to file for safety
with open("scratch/analysis_output.md", "w", encoding="utf-8") as f:
    f.write(output_content)

conn.close()
