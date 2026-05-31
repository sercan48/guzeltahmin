import shutil
from datetime import datetime
from pathlib import Path

def backup_database():
    source = Path("data/guzel_tahmin.db")
    if not source.exists():
        print(f"Error: Database not found at {source}")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = Path("data/backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    destination = backup_dir / f"guzel_tahmin_backup_{timestamp}.db"
    
    try:
        shutil.copy2(source, destination)
        print(f"✅ Secure Backup Successful: {destination}")
    except Exception as e:
        print(f"❌ Backup Failed: {e}")

if __name__ == "__main__":
    backup_database()
