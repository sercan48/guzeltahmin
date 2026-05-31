import pandas as pd
from pathlib import Path

df = pd.read_csv("data/processed/features.csv")
print("Total rows:", len(df))
for league in ["NORWAY_ELITESERIEN", "BRAZIL_SERIE_A"]:
    ldf = df[df["league_code"] == league]
    print(f"\nLeague: {league}")
    print("  Match count:", len(ldf))
    print("  Seasons distribution:")
    print(ldf["season"].value_counts())
