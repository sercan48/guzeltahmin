import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.db.base import get_backend

db = get_backend()
db.connect()

# Bologna vs Inter match
matches = db.fetchall("""
    SELECT m.id, m.date, m.league_code, m.season,
           t1.name as home, t1.id as home_id,
           t2.name as away, t2.id as away_id
    FROM matches m
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    WHERE t1.name LIKE '%Bologna%' AND t2.name LIKE '%Inter%'
    AND m.ft_result IS NULL
    ORDER BY m.date DESC LIMIT 1
""")

if not matches:
    print("Mac bulunamadi, en guncel I1 macini ariyorum...")
    matches = db.fetchall("""
        SELECT m.id, m.date, m.league_code,
               t1.name as home, t1.id as home_id,
               t2.name as away, t2.id as away_id
        FROM matches m
        JOIN teams t1 ON m.home_team_id = t1.id
        JOIN teams t2 ON m.away_team_id = t2.id
        WHERE m.ft_result IS NULL AND m.league_code IN ('I1','E0','SP1','D1')
        ORDER BY m.date LIMIT 5
    """)
    for r in matches:
        print(f"  {r['date']} | {r['league_code']} | {r['home']}({r['home_id']}) vs {r['away']}({r['away_id']})")

m = matches[0]
HOME_ID = m["home_id"]
AWAY_ID = m["away_id"]
print(f"\n=== MAC: {m['home']} vs {m['away']} ({m['date']}) ===")

# Home team - last 7
print(f"\n--- {m['home']} Son 7 Mac ---")
h7 = db.fetchall("""
    SELECT m.date, t1.name as h, t2.name as a,
           m.ft_home_goals as hg, m.ft_away_goals as ag, m.ft_result,
           m.home_shots as hs, m.away_shots as as2,
           m.home_shots_target as hst, m.away_shots_target as ast2,
           m.home_corners as hc, m.away_corners as ac
    FROM matches m
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    WHERE (m.home_team_id = ? OR m.away_team_id = ?)
    AND m.ft_result IS NOT NULL
    ORDER BY m.date DESC LIMIT 7
""", (HOME_ID, HOME_ID))
for r in h7:
    is_home = r["h"] == m["home"]
    if is_home:
        print(f"  {r['date']} (H) vs {r['a']}: {r['hg']}-{r['ag']} ({r['ft_result']}) | S:{r['hs']}-{r['as2']} ST:{r['hst']}-{r['ast2']} C:{r['hc']}-{r['ac']}")
    else:
        print(f"  {r['date']} (A) @ {r['h']}: {r['hg']}-{r['ag']} ({r['ft_result']}) | S:{r['hs']}-{r['as2']} ST:{r['hst']}-{r['ast2']} C:{r['hc']}-{r['ac']}")

# Away team - last 7
print(f"\n--- {m['away']} Son 7 Mac ---")
a7 = db.fetchall("""
    SELECT m.date, t1.name as h, t2.name as a,
           m.ft_home_goals as hg, m.ft_away_goals as ag, m.ft_result,
           m.home_shots as hs, m.away_shots as as2,
           m.home_shots_target as hst, m.away_shots_target as ast2,
           m.home_corners as hc, m.away_corners as ac
    FROM matches m
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    WHERE (m.home_team_id = ? OR m.away_team_id = ?)
    AND m.ft_result IS NOT NULL
    ORDER BY m.date DESC LIMIT 7
""", (AWAY_ID, AWAY_ID))
for r in a7:
    is_home = r["h"] == m["away"]
    if is_home:
        print(f"  {r['date']} (H) vs {r['a']}: {r['hg']}-{r['ag']} ({r['ft_result']}) | S:{r['hs']}-{r['as2']} ST:{r['hst']}-{r['ast2']} C:{r['hc']}-{r['ac']}")
    else:
        print(f"  {r['date']} (A) @ {r['h']}: {r['hg']}-{r['ag']} ({r['ft_result']}) | S:{r['hs']}-{r['as2']} ST:{r['hst']}-{r['ast2']} C:{r['hc']}-{r['ac']}")

# H2H last 10
print(f"\n--- H2H Son 10 ---")
h2h = db.fetchall("""
    SELECT m.date, m.season, t1.name as h, t2.name as a,
           m.ft_home_goals as hg, m.ft_away_goals as ag, m.ft_result
    FROM matches m
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    WHERE (m.home_team_id = ? AND m.away_team_id = ?)
       OR (m.home_team_id = ? AND m.away_team_id = ?)
    AND m.ft_result IS NOT NULL
    ORDER BY m.date DESC LIMIT 10
""", (HOME_ID, AWAY_ID, AWAY_ID, HOME_ID))
for r in h2h:
    print(f"  {r['date']} {r['h']} {r['hg']}-{r['ag']} {r['a']} ({r['ft_result']})")

# Season aggregates
print(f"\n--- 2025-26 Sezon Ozeti ---")
for tid, tname in [(HOME_ID, m["home"]), (AWAY_ID, m["away"])]:
    stats = db.fetchone("""
        SELECT COUNT(*) as played,
               SUM(CASE WHEN (home_team_id = ? AND ft_result = 'H') OR (away_team_id = ? AND ft_result = 'A') THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN ft_result = 'D' THEN 1 ELSE 0 END) as draws,
               SUM(CASE WHEN (home_team_id = ? AND ft_result = 'A') OR (away_team_id = ? AND ft_result = 'H') THEN 1 ELSE 0 END) as losses,
               SUM(CASE WHEN home_team_id = ? THEN ft_home_goals ELSE ft_away_goals END) as total_scored,
               SUM(CASE WHEN home_team_id = ? THEN ft_away_goals ELSE ft_home_goals END) as total_conceded,
               AVG(CASE WHEN home_team_id = ? THEN ft_home_goals ELSE ft_away_goals END) as avg_scored,
               AVG(CASE WHEN home_team_id = ? THEN ft_away_goals ELSE ft_home_goals END) as avg_conceded
        FROM matches
        WHERE (home_team_id = ? OR away_team_id = ?) AND season = '2025-2026' AND ft_result IS NOT NULL
    """, (tid, tid, tid, tid, tid, tid, tid, tid, tid, tid))
    if stats and stats["played"]:
        print(f"  {tname}: {stats['played']}M {stats['wins']}G {stats['draws']}B {stats['losses']}M | {stats['total_scored']} gol att, {stats['total_conceded']} gol yed | Ort: {stats['avg_scored']:.2f}-{stats['avg_conceded']:.2f}")

# Odds from last H2H or recent
print(f"\n--- Son Oranlar ---")
odds = db.fetchall("""
    SELECT o.bookmaker, o.home_odds, o.draw_odds, o.away_odds, o.over25_odds, o.under25_odds
    FROM odds o
    JOIN matches mx ON o.match_id = mx.id
    WHERE (mx.home_team_id = ? AND mx.away_team_id = ?)
    ORDER BY mx.date DESC LIMIT 5
""", (HOME_ID, AWAY_ID))
if odds:
    for r in odds:
        print(f"  {r['bookmaker']}: H={r['home_odds']} D={r['draw_odds']} A={r['away_odds']} O25={r['over25_odds']} U25={r['under25_odds']}")
else:
    print("  Oran bulunamadi - son maclardaki oranlara bakilyor")
    odds2 = db.fetchall("""
        SELECT o.bookmaker, o.home_odds, o.draw_odds, o.away_odds
        FROM odds o
        JOIN matches mx ON o.match_id = mx.id
        WHERE mx.home_team_id = ? OR mx.away_team_id = ?
        ORDER BY mx.date DESC LIMIT 3
    """, (HOME_ID, HOME_ID))
    for r in odds2:
        print(f"  {r['bookmaker']}: H={r['home_odds']} D={r['draw_odds']} A={r['away_odds']}")

db.close()
