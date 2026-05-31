import pandas as pd
import datetime

print("--- Reading transfermarkt competitions ---")
comps_df = pd.read_csv("data/transfermarkt/competitions.csv")
print(comps_df[['competition_id', 'name', 'country_name']])

# Filter Norway
norway_comps = comps_df[comps_df['country_name'].str.lower() == 'norway']
print("\nNorway Competitions:")
print(norway_comps)

print("\n--- Reading transfermarkt games ---")
# Since games.csv is 25MB, we read in chunks or just look at header first
games_df = pd.read_csv("data/transfermarkt/games.csv", nrows=5)
print("Games columns:", games_df.columns.tolist())

# Now let's scan games.csv for matches on 2026-05-29 or around this date
print("\nScanning for games on 2026-05-29...")
chunks = pd.read_csv("data/transfermarkt/games.csv", chunksize=10000)
found_today = []
for chunk in chunks:
    # Ensure date column is string or datetime
    chunk['date'] = chunk['date'].astype(str)
    matches_today = chunk[chunk['date'].str.startswith('2026-05-29')]
    if not matches_today.empty:
        found_today.append(matches_today)

if found_today:
    today_df = pd.concat(found_today)
    print(f"Found {len(today_df)} games on 2026-05-29 in Transfermarkt dataset:")
    print(today_df[['game_id', 'competition_id', 'season', 'date', 'home_club_id', 'away_club_id']])
else:
    print("No games found on 2026-05-29 in Transfermarkt dataset.")

print("\nScanning for max date in games.csv...")
max_date = None
chunks = pd.read_csv("data/transfermarkt/games.csv", chunksize=10000)
for chunk in chunks:
    chunk_max = chunk['date'].max()
    if max_date is None or chunk_max > max_date:
        max_date = chunk_max
print("Max date in games.csv:", max_date)
