import pandas as pd

df = pd.read_csv("scratch/BRA.csv")
print("Total rows in BRA.csv:", len(df))

# Convert Date to datetime
df['ParsedDate'] = pd.to_datetime(df['Date'], errors='coerce', dayfirst=True)

# Filter 2026 matches
df_2026 = df[df['ParsedDate'].dt.year == 2026].copy()
print(f"Total 2026 matches in BRA.csv: {len(df_2026)}")

played_2026 = df_2026[df_2026['Res'].notna() & df_2026['Res'].isin(['H', 'D', 'A'])]
unplayed_2026 = df_2026[df_2026['Res'].isna() | ~df_2026['Res'].isin(['H', 'D', 'A'])]

print(f"Played 2026 matches in CSV: {len(played_2026)}")
print(f"Unplayed 2026 matches in CSV: {len(unplayed_2026)}")

if len(unplayed_2026) > 0:
    print("\nSample unplayed matches:")
    print(unplayed_2026[['Date', 'Home', 'Away', 'Res']].head(10))
else:
    print("\nNo unplayed matches found in BRA.csv!")

print("\nLast 10 rows in BRA.csv:")
print(df_2026.tail(10)[['Date', 'Home', 'Away', 'Res']])
