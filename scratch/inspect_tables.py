import sqlite3

db_path = "data/guzel_tahmin.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print("=== Tables in guzel_tahmin.db ===")
for t in tables:
    print(t[0])

conn.close()
