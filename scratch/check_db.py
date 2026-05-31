import sys
sys.path.insert(0, '.')
from config.settings import DB_PATH
import sqlite3

print("DB PATH:", DB_PATH)
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table';")
print("Tables:", c.fetchall())

try:
    c.execute("SELECT * FROM matches LIMIT 1;")
    cols = [description[0] for description in c.description]
    print("Matches cols:", cols)
except Exception as e:
    print("Error:", e)
