import sqlite3

db_path = "data/guzel_tahmin.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Check table existence and row count
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='feature_integrity_log'")
table_exists = cursor.fetchone() is not None

if table_exists:
    cursor.execute("SELECT COUNT(*) as cnt FROM feature_integrity_log")
    cnt = cursor.fetchone()["cnt"]
    print(f"Total integrity warning logs: {cnt}")
    
    cursor.execute("SELECT * FROM feature_integrity_log ORDER BY id DESC LIMIT 10")
    print("\n=== Recent Integrity Warning Logs ===")
    for r in cursor.fetchall():
        print(dict(r))
else:
    print("Table feature_integrity_log does not exist!")

conn.close()
