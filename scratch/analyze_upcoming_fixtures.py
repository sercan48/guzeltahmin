import sqlite3
import pandas as pd
from datetime import datetime, date, timedelta

conn = sqlite3.connect("data/guzel_tahmin.db")
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("=============================================================")
# 1. Check last match date for each active league in the matches table
print("1. LİGLERİN VERİ TABANINDAKİ EN SON MAÇ TARİHLERİ:")
print("-------------------------------------------------------------")
cursor.execute("""
    SELECT league_code, MAX(date) as last_date, COUNT(*) as total_matches
    FROM matches
    GROUP BY league_code
""")
rows = cursor.fetchall()
for r in rows:
    print(f"  Lig: {r['league_code']:<18} | En Son Maç Tarihi: {r['last_date']} | Toplam Maç: {r['total_matches']}")

# 2. Check unplayed matches in the next 3 days (Today, Tomorrow, Day After)
today_str = date.today().isoformat()
two_days_later_str = (date.today() + timedelta(days=2)).isoformat()
print(f"\n2. ÖNÜMÜZDEKİ 3 GÜN İÇİNDEKİ OYNANMAMIŞ MAÇLAR (Tarih Aralığı: {today_str} - {two_days_later_str}):")
print("-------------------------------------------------------------")
cursor.execute("""
    SELECT m.date, m.league_code, t1.name as home, t2.name as away, m.id
    FROM matches m
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    WHERE m.ft_result IS NULL
    AND DATE(m.date) >= DATE(?) AND DATE(m.date) <= DATE(?)
    ORDER BY m.date ASC
""", (today_str, two_days_later_str))
upcoming_matches = cursor.fetchall()
print(f"Veri tabanında önümüzdeki 3 gün için bulunan oynanmamış maç sayısı: {len(upcoming_matches)}")
for m in upcoming_matches:
    # Check if there is a prediction for this match
    cursor.execute("SELECT predicted_result, confidence_score FROM predictions WHERE match_id = ?", (m['id'],))
    pred = cursor.fetchone()
    pred_str = f"Tahmin: {pred['predicted_result']} (Güven: %{pred['confidence_score']:.1f})" if pred else "Tahmin Yok (SKIP/Odds eksik olabilir)"
    print(f"  [{m['league_code']}] {m['date']} | {m['home']} vs {m['away']} | {pred_str}")

# 3. Check general upcoming matches (all unplayed matches from 2026 onwards)
print("\n3. VERİ TABANINDAKİ TÜM GELECEK MAÇLAR (2026-05-29 ve Sonrası):")
print("-------------------------------------------------------------")
cursor.execute("""
    SELECT m.league_code, COUNT(*) as cnt, MIN(m.date) as min_date, MAX(m.date) as max_date
    FROM matches m
    WHERE m.ft_result IS NULL AND DATE(m.date) >= DATE('now')
    GROUP BY m.league_code
""")
future_by_league = cursor.fetchall()
if future_by_league:
    for f in future_by_league:
        print(f"  Lig: {f['league_code']:<18} | Gelecek Maç Sayısı: {f['cnt']} | İlk Tarih: {f['min_date']} | Son Tarih: {f['max_date']}")
else:
    print("  Hiçbir lig için gelecek maç bulunamadı.")

# 4. Check if there are any predictions that were generated but not posted
print("\n4. PAYLAŞILMAMIŞ TAHMİNLER (Gelecekteki Maçlar İçin):")
print("-------------------------------------------------------------")
cursor.execute("""
    SELECT p.predicted_result, p.confidence_score, m.date, m.league_code, t1.name as home, t2.name as away
    FROM predictions p
    JOIN matches m ON p.match_id = m.id
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    WHERE m.ft_result IS NULL AND DATE(m.date) >= DATE('now')
    ORDER BY m.date ASC
""")
preds = cursor.fetchall()
print(f"Gelecek maçlar için veri tabanında kayıtlı tahmin sayısı: {len(preds)}")
for p in preds[:15]:
    print(f"  [{p['league_code']}] {p['date']} | {p['home']} vs {p['away']} | Tahmin: {p['predicted_result']} (Güven: %{p['confidence_score']:.1f})")

conn.close()
