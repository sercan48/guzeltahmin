import sqlite3
conn = sqlite3.connect('data/guzel_tahmin.db')
conn.execute("DELETE FROM matches WHERE ft_result NOT IN ('H', 'A', 'D')")
conn.commit()
conn.close()
