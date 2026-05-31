import requests
import json
import pandas as pd
import io

proxy_url = "https://api.allorigins.win/get?url=http://www.football-data.co.uk/new/NOR.csv"
print(f"Fetching Norway CSV via allorigins proxy: {proxy_url}")

try:
    resp = requests.get(proxy_url, timeout=30)
    print("Response status:", resp.status_code)
    if resp.status_code == 200:
        data = resp.json()
        csv_content = data.get("contents")
        if csv_content:
            print("Successfully extracted CSV content! Length:", len(csv_content))
            
            # Save it
            with open("scratch/NOR.csv", "w", encoding="utf-8") as f:
                f.write(csv_content)
                
            # Parse it
            df = pd.read_csv(io.StringIO(csv_content))
            print("\nCSV Head:")
            print(df.head(3))
            print("\nCSV Tail:")
            print(df.tail(10))
            
            print("\nChecking columns:")
            print(df.columns.tolist())
            
            df['Date'] = pd.to_datetime(df['Date'], errors='coerce', dayfirst=True)
            df_2026 = df[df['Date'] >= '2026-01-01']
            print(f"\nTotal matches in 2026: {len(df_2026)}")
            if not df_2026.empty:
                print("First few matches in 2026:")
                print(df_2026.head(3)[['Date', 'Home', 'Away', 'FTR']])
                print("Last few matches in 2026:")
                print(df_2026.tail(5)[['Date', 'Home', 'Away', 'FTR']])
                
                # Check for matches today (2026-05-29) or upcoming
                print("\nMatches today (2026-05-29) or in next 3 days:")
                upcoming = df_2026[(df_2026['Date'] >= '2026-05-29') & (df_2026['Date'] <= '2026-06-01')]
                print(upcoming[['Date', 'Home', 'Away', 'FTR']])
        else:
            print("No contents field in JSON response")
    else:
        print("Failed to fetch from proxy")
except Exception as e:
    print("Error:", e)
