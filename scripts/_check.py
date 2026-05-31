import sys
sys.path.insert(0, ".")
from src.db.base import get_backend
db = get_backend()
db.connect()
db.execute("DELETE FROM predictions WHERE model_type LIKE 'retro%'")
c = db.fetchone("SELECT COUNT(*) as c FROM predictions")
print(f"Kalan tahmin: {c['c']}")
db.close()
