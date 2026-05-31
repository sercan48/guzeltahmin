import os

found = False
for root, dirs, files in os.walk('.'):
    for file in files:
        if ('nor' in file.lower() or 'eliteserien' in file.lower()) and (file.endswith('.csv') or file.endswith('.xlsx')):
            found = True
            filepath = os.path.join(root, file)
            print(f"Found match file: {filepath} ({os.path.getsize(filepath)} bytes)")

if not found:
    print("No Norway CSV or Excel files found in the project directory.")
