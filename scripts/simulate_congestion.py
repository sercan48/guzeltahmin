"""
Simulate extreme fatigue (Congestion) + Travel Distance for Summer Leagues.
Generates 1000 synthetic matches to train XGBoost and measure the combined effect.
"""
import sqlite3
import random
from pathlib import Path

def generate_congestion_simulation():
    db_path = Path("data/guzel_tahmin.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("Generating 1000 Simulated Matches with Extreme Congestion...")
    
    # Clean previous synthetic data to ensure pure test
    cursor.execute("DELETE FROM matches WHERE season = '2024'")
    
    for i in range(1000):
        league = random.choice(["NORWAY_ELITESERIEN", "BRAZIL_SERIE_A", "USA_MLS"])
        season = "2024"
        
        # Base Elo
        home_elo = random.uniform(1400, 1600)
        away_elo = random.uniform(1400, 1600)
        elo_diff = home_elo - away_elo
        
        pitch = "NATURAL"
        
        # Base modifiers
        if league == "NORWAY_ELITESERIEN":
            pitch = random.choice(["ARTIFICIAL", "NATURAL"])
            if pitch == "ARTIFICIAL": elo_diff += 100
            
        # 1. Distance (0 to 3000 km)
        distance = random.uniform(0, 3000)
        
        # 2. Congestion Advantage (Positive means Away is tired, Home is rested)
        # Range: -1.0 to 1.0
        congestion_adv = random.uniform(-1.0, 1.0)
        
        # --- LETHAL SYNERGY LOGIC (The Hypothesis) ---
        # If Away team is exhausted (congestion_adv > 0.5) AND flying far (>1500km)
        if congestion_adv > 0.5 and distance > 1500:
            elo_diff += 300  # Massive penalty to Away
            
        # If Home team is exhausted (congestion_adv < -0.5) AND Home
        elif congestion_adv < -0.5:
            elo_diff -= 150  # Significant penalty to Home (but no travel penalty)
            
        # Calculate result
        rand_val = random.uniform(-300, 300)
        final_score = elo_diff + rand_val
        
        if final_score > 50: res = "H"
        elif final_score < -50: res = "A"
        else: res = "D"
        
        date_val = f"2024-06-{(i%28)+1:02d}"
        
        query = """
            INSERT INTO matches (
                league_code, home_team_id, away_team_id, season, date, 
                pitch_type, travel_distance_km, is_summer_league, ft_result,
                home_elo, away_elo, home_xg, away_xg, congestion_advantage
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 1.5, 1.0, ?)
        """
        cursor.execute(query, (
            league, i+1000, i+2000, season, date_val, pitch, distance, res, 
            home_elo, away_elo, congestion_adv
        ))
        
    conn.commit()
    conn.close()
    print("Simulated Data Inserted. Ready for XGBoost Feature Analysis.")

if __name__ == "__main__":
    generate_congestion_simulation()
