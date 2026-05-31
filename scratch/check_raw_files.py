import os

raw_dir = "data/raw"
found = False
for root, dirs, files in os.walk(raw_dir):
    for file in files:
        if file.endswith('.csv'):
            found = True
            filepath = os.path.join(root, file)
            size = os.path.getsize(filepath)
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    preview = f.readline().strip()
                print(f"File: {filepath} ({size} bytes) | First line: {preview[:80]}")
            except Exception as e:
                print(f"File: {filepath} | Error reading: {e}")

if not found:
    print("No CSV files found in data/raw!")
