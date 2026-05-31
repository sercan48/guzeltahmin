import sqlite3

db_path = "data/guzel_tahmin.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("PRAGMA table_info(venues)")
print("=== Venues Columns ===")
for col in cursor.fetchall():
    print(dict(col))

cursor.execute("SELECT * FROM venues LIMIT 5")
print("\n=== Venues Sample Rows ===")
for r in cursor.fetchall():
    print(dict(r))

conn.close()
