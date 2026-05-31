import pandas as pd

try:
    df = pd.read_csv("scratch/NOR.csv")
    print("Columns in scratch/NOR.csv:")
    print(df.columns.tolist())
    print("\nFirst row:")
    print(df.iloc[0].to_dict())
except Exception as e:
    print("Error:", e)
