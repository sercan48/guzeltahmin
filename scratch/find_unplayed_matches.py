import sqlite3
import pandas as pd

conn = sqlite3.connect("data/guzel_tahmin.db")
query = """
    SELECT m.id, m.date, m.league_code, m.season, t1.name as home, t2.name as away
    FROM matches m
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    WHERE m.ft_result IS NULL OR m.ft_result = ''
"""
df = pd.read_sql_query(query, conn)
print("Total unplayed matches in DB:", len(df))
if len(df) > 0:
    print("\nMatches count by league:")
    print(df['league_code'].value_counts())
    print("\nSample unplayed matches:")
    print(df.sort_values('date').head(20))
else:
    print("No unplayed matches found!")
conn.close()
