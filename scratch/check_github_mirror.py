import requests
import pandas as pd
import io

urls = [
    "https://raw.githubusercontent.com/footballcsv/cache.footballdata/master/NOR.csv",
    "https://raw.githubusercontent.com/footballcsv/cache.footballdata/main/NOR.csv"
]

success = False
for url in urls:
    print(f"Trying to fetch from: {url}")
    try:
        resp = requests.get(url, timeout=30)
        print("Status code:", resp.status_code)
        if resp.status_code == 200:
            content = resp.text
            print("Successfully downloaded! Length:", len(content))
            with open("scratch/NOR.csv", "w", encoding="utf-8") as f:
                f.write(content)
            
            df = pd.read_csv(io.StringIO(content))
            print("\nCSV Head:")
            print(df.head(3))
            print("\nCSV Tail:")
            print(df.tail(5))
            
            print("\nChecking columns:")
            print(df.columns.tolist())
            
            df['Date'] = pd.to_datetime(df['Date'], errors='coerce', dayfirst=True)
            df_2026 = df[df['Date'] >= '2026-01-01']
            print(f"\nTotal matches in 2026 in this file: {len(df_2026)}")
            success = True
            break
    except Exception as e:
        print("Error:", e)

if not success:
    print("Failed to fetch from all GitHub raw URLs")
