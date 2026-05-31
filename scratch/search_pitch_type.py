import os
from pathlib import Path

search_terms = ["_pitch_type", "_travel_distance", "_cup_rotation_fatigue"]
found = {}

for root, dirs, files in os.walk("."):
    if ".venv" in root or ".git" in root or ".pytest_cache" in root or "scratch" in root:
        continue
    for file in files:
        if file.endswith(".py"):
            path = Path(root) / file
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                for term in search_terms:
                    if term in content:
                        if term not in found:
                            found[term] = []
                        found[term].append(str(path))
            except Exception as e:
                pass

print("=== Search Results ===")
for term, paths in found.items():
    print(f"\nTerm: {term}")
    for p in set(paths):
        print(f"  - {p}")
