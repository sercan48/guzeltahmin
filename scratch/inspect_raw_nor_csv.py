import pandas as pd

df = pd.read_csv("scratch/NOR.csv")
print("Total rows in CSV:", len(df))
print("Columns in CSV:", df.columns.tolist())

# Let's count unique values of Season or League if they exist
if 'Season' in df.columns:
    print("\nSeason value counts:")
    print(df['Season'].value_counts().head(10))

# Let's look at the tail of the CSV
print("\nLast 15 rows in CSV:")
print(df.tail(15)[['Date', 'Home', 'Away', 'Res'] if 'Res' in df.columns else df.columns[:5]])

# Check dates
if 'Date' in df.columns:
    # Try parsing dates and see what years exist
    df['ParsedDate'] = pd.to_datetime(df['Date'], errors='coerce', dayfirst=True)
    print("\nYears present in CSV:")
    print(df['ParsedDate'].dt.year.value_counts())
