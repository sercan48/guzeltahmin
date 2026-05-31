import sqlite3
from pathlib import Path

def remove_check_constraint():
    db_path = Path("data/guzel_tahmin.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("Recreating predictions table to remove CHECK constraint...")
    
    # 1. Rename existing
    cursor.execute("ALTER TABLE predictions RENAME TO predictions_old")
    
    # 2. Create new without the CHECK constraint
    cursor.execute("""
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL UNIQUE,
            home_win_prob REAL,
            draw_prob REAL,
            away_win_prob REAL,
            confidence_score INTEGER,
            is_value_bet INTEGER DEFAULT 0,
            value_margin REAL DEFAULT 0,
            predicted_result TEXT,
            actual_result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            analysis_date TEXT,
            top_1_pick TEXT,
            top_1_type TEXT,
            top_1_success INTEGER,
            top_2_pick TEXT,
            top_2_type TEXT,
            top_2_success INTEGER,
            model_type TEXT,
            FOREIGN KEY(match_id) REFERENCES matches(id)
        )
    """)
    
    # 3. Copy data
    cursor.execute("""
        INSERT INTO predictions (
            id, match_id, home_win_prob, draw_prob, away_win_prob, 
            confidence_score, is_value_bet, value_margin, predicted_result, 
            actual_result, created_at, analysis_date, top_1_pick, top_1_type, 
            top_1_success, top_2_pick, top_2_type, top_2_success, model_type
        )
        SELECT 
            id, match_id, home_win_prob, draw_prob, away_win_prob, 
            confidence_score, is_value_bet, value_margin, predicted_result, 
            actual_result, created_at, analysis_date, top_1_pick, top_1_type, 
            top_1_success, top_2_pick, top_2_type, top_2_success, model_type
        FROM predictions_old
    """)
    
    # 4. Drop old
    cursor.execute("DROP TABLE predictions_old")
    
    conn.commit()
    conn.close()
    print("Done. CHECK constraint removed.")

if __name__ == "__main__":
    remove_check_constraint()
