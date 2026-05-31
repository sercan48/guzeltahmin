import os
from pathlib import Path

search_terms = ["INSERT INTO matches", "matches (", "insert_matches"]
found = []

for root, dirs, files in os.walk("."):
    if ".venv" in root or ".git" in root or ".pytest_cache" in root:
        continue
    for file in files:
        if file.endswith(".py"):
            path = Path(root) / file
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                for term in search_terms:
                    if term in content:
                        found.append(f"{path} (matched term: '{term}')")
                        break
            except Exception as e:
                pass

print("=== Insert Search Results ===")
for f in found:
    print(f)
