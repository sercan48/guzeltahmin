import os

search_dir = r"c:\Users\WIN\Desktop\Güzel Tahmin"
query = "BetSelector"

for root, dirs, files in os.walk(search_dir):
    if ".venv" in root or ".git" in root or "__pycache__" in root or "catboost_info" in root:
        continue
    for file in files:
        if file.endswith(".py"):
            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line_no, line in enumerate(f, 1):
                        if query in line:
                            print(f"{os.path.relpath(path, search_dir)}:L{line_no} -> {line.strip()}")
            except Exception as e:
                pass
