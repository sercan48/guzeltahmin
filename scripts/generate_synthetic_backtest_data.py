"""
Generates 1000 logically consistent synthetic matches to validate if XGBoost 
can autonomously learn our Summer League physics (Turf, Travel distance, Elo).
"""
import sqlite3
import random
from pathlib import Path

def generate_validation_data():
    db_path = Path("data/guzel_tahmin.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("Generating 1000 Synthetic Matches for Backtesting Validation...")
    
    # Generate 1000 matches
    for i in range(1000):
        league = random.choice(["NORWAY_ELITESERIEN", "BRAZIL_SERIE_A", "USA_MLS"])
        season = random.choice(["2023", "2024"])
        
        # Base probabilities
        home_elo = random.uniform(1300, 1800)
        away_elo = random.uniform(1300, 1800)
        elo_diff = home_elo - away_elo
        
        distance = 0
        pitch = "NATURAL"
        
        # 1. Norway Physics: Artificial Turf gives massive home advantage
        if league == "NORWAY_ELITESERIEN":
            pitch = random.choice(["ARTIFICIAL", "NATURAL"])
            if pitch == "ARTIFICIAL":
                elo_diff += 150 # Artificial turf behaves like a +150 Elo boost for home
                
        # 2. Brazil Physics: Travel distance destroys away teams
        if league == "BRAZIL_SERIE_A":
            distance = random.uniform(100, 3500)
            if distance > 2000:
                elo_diff += 200 # Extreme travel distance acts as a +200 Elo boost for home
                
        # 3. MLS Physics: Moderate travel
        if league == "USA_MLS":
            distance = random.uniform(50, 1500)
            if distance > 1000:
                elo_diff += 50
                
        # Calculate result based on adjusted Elo diff
        rand_val = random.uniform(-300, 300)
        final_score = elo_diff + rand_val
        
        if final_score > 50: res = "H"
        elif final_score < -50: res = "A"
        else: res = "D"
        
        date_val = f"{season}-01-{(i%28)+1:02d}"
        
        query = """
            INSERT INTO matches (
                league_code, home_team_id, away_team_id, season, date, 
                pitch_type, travel_distance_km, is_summer_league, ft_result,
                home_elo, away_elo, home_xg, away_xg
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 1.5, 1.0)
        """
        cursor.execute(query, (league, i+1, i+2, season, date_val, pitch, distance, res, home_elo, away_elo))
        
    conn.commit()
    conn.close()
    print("Generated 1000 synthetic validation matches successfully.")

if __name__ == "__main__":
    generate_validation_data()
