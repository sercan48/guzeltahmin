# Feature Engineering Layer

This document details the feature compilation pipeline, team strength modeling, contextual summer league variables, and point-in-time calculation mechanics.

---

## 1. Feature Compilation Pipeline

The system constructs a 35-dimensional feature matrix for each match. These are split into base statistical features, team context features, and odds-derived variables.

---

## 2. Feature Definitions

### I. Team Strength & Elo Ratings (`team_strength.py`)
- **Elo Ratings:** Dynamic team rating with match outcome updates. Adjusts for league standing position differences.
- **Home Advantage Factor:** Dynamically calculates home-field advantage ratio per league using historical match ratios.

### II. Form & Momentum (`form_calculator.py`)
- **Rolling Form Last 5:** Computes weighted rolling form over the last 5 matches before match date.
- **Form Momentum Difference:** Difference between home and away team momentum (rate of form change).
- **Head-to-Head (H2H):** Winrate and goal averages of head-to-head match history between the two teams.

### III. Expected Goals (xG) Engine (`xg_features.py`)
- **xG Averages:** Rolling 10-match averages for expected goals created and conceded.
- **xG Overperformance:** Compares actual goals scored against expected goals to measure shooting efficiency.

### IV. Rest & Congestion (`fixture_congestion.py`)
- **Congestion Score:** Computes fixture density over the last 14 and 30 days.
- **Congestion Advantage:** Relative rest days difference between the home and away team.

### V. Defensive Metrics (`clean_sheet_rate_diff`)
- **Clean Sheet Difference:** Difference between home and away clean sheet rates over the current season.

### VI. Squad Valuations (`player_impact.py`)
- **Squad Market Value:** Aggregated player valuations sourced from TransferMarkt.
- **Attack/Defense Ratings:** Aggregated FIFA attributes of active squad players (pace, shooting, physical etc.).

---

## 3. Summer League & Venue Adaptation

Specific variables are added to address volatile summer leagues (MLS, Brazil Serie A, Norway Eliteserien):

- **Travel Fatigue (`travel_distance_km`):** Models travel wear. If not explicitly recorded, applies distance fallbacks (e.g. 950km for Brazil, 800km for MLS, 450km for Norway).
- **Pitch Type (`is_artificial_pitch`):** Binary indicator penalizing natural turf teams playing in stadiums with artificial turf.
- **Designated Player Presence (`dp_presence`):** Captures star player ratios on team lineups for MLS matches.
- **Extreme Weather Context (`weather_multiplier.py`):** Modifies final prediction probabilities when conditions indicate heavy rain, snow, wind ($>10 \text{ m/s}$), or extreme humidity.

---

## 4. Chronological Leakage Mitigation

To prevent target leakage during model training and validation, the system enforces strict point-in-time constraints.

For every match analyzed at `match_date_str`:
- Standing indexes, team strengths, rolling forms, and xG stats only query matches where `date < match_date_str`.
- SQLite queries use strict date boundaries:
  ```sql
  SELECT * FROM matches 
  WHERE (home_team_id = ? OR away_team_id = ?) 
    AND season = ? 
    AND date < ? 
  ORDER BY date
  ```
This ensures the model is trained only on information available at the time of match kickoff.
