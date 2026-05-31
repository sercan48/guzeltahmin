"""Initialize database and load all historical data."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.db.migrations import run_migrations
from src.ingestion.csv_loader import load_all_data
from src.preprocessing.schema_mapper import ingest_matches_to_db


def init_database():
    """Create schema, load CSV data into database."""
    print("=" * 60)
    print("  Güzel Tahmin — Database Initialization")
    print("=" * 60)

    db = get_backend()
    db.connect()

    try:
        # Step 1: Create tables
        print("\n[1/3] Creating schema...")
        run_migrations(db)

        # Step 2: Load CSV data
        print("\n[2/3] Loading CSV data...")
        df = load_all_data()

        # Step 3: Ingest into database
        print("\n[3/3] Ingesting into database...")
        count = ingest_matches_to_db(df, db)
        print(f"\n[OK] {count} matches inserted into database.")

        # Summary
        print("\n" + "=" * 60)
        print("  Database Summary")
        print("=" * 60)
        for table in ["teams", "matches", "odds", "referees"]:
            if db.table_exists(table):
                row_count = db.fetchone(f"SELECT COUNT(*) as cnt FROM {table}")
                print(f"  {table}: {row_count['cnt']} rows")

    finally:
        db.close()

    print("\n[DONE] Database initialized successfully!")


if __name__ == "__main__":
    init_database()
