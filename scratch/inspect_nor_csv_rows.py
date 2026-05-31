import pandas as pd
from pathlib import Path

csv_path = Path("scratch/NOR.csv")
df = pd.read_csv(csv_path)

# Convert Date to datetime
df['Date'] = pd.to_datetime(df['Date'], errors='coerce', dayfirst=True)

# Filter 2026 matches
df_2026 = df[df['Date'].dt.year == 2026].copy()
print(f"Total 2026 matches in CSV: {len(df_2026)}")

played_2026 = df_2026[df_2026['Res'].notna() & df_2026['Res'].isin(['H', 'D', 'A'])]
unplayed_2026 = df_2026[df_2026['Res'].isna() | ~df_2026['Res'].isin(['H', 'D', 'A'])]

print(f"Played 2026 matches in CSV (with result): {len(played_2026)}")
print(f"Unplayed 2026 matches in CSV (no result): {len(unplayed_2026)}")

if len(unplayed_2026) > 0:
    print("\nSample unplayed 2026 matches:")
    print(unplayed_2026[['Date', 'Home', 'Away', 'Res']].head(10))
else:
    print("\nNo unplayed 2026 matches found in CSV!")
