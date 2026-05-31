"""Quick DB verification script."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend

db = get_backend()
db.connect()

tables = db.fetchall("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
print(f"Toplam {len(tables)} tablo:")
for t in tables:
    count = db.fetchone(f"SELECT COUNT(*) as c FROM [{t['name']}]")
    c = count["c"] if count else 0
    print(f"  [OK] {t['name']} ({c} kayit)")

ver = db.fetchone("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
print(f"\nSchema version: v{ver['version']}" if ver else "\nSchema version: N/A")

db.close()
print("\nDB verification complete!")
