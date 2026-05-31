import sqlite3

db_path = "data/guzel_tahmin.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT COUNT(*) as cnt FROM backtest_results")
print(f"Backtest results count: {cursor.fetchone()[0]}")

cursor.execute("SELECT COUNT(*) as cnt FROM model_experiments")
print(f"Model experiments count: {cursor.fetchone()[0]}")

# Let's inspect columns of backtest_results
cursor.execute("PRAGMA table_info(backtest_results)")
print("\n=== Backtest Results Columns ===")
for col in cursor.fetchall():
    print(dict(col))

# Let's see some sample rows of backtest_results
cursor.execute("SELECT * FROM backtest_results LIMIT 5")
print("\n=== Backtest Results Samples ===")
for r in cursor.fetchall():
    print(dict(r))

conn.close()
