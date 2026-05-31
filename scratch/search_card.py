import os

def search_files(directory, query):
    results = []
    ignore_dirs = {'.venv', '.git', '.pytest_cache', 'node_modules', '__pycache__', 'catboost_info'}
    for root, dirs, files in os.walk(directory):
        # modify dirs in-place to prune directories we want to ignore
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        for file in files:
            if file.endswith('.py') or file.endswith('.md'):
                path = os.path.join(root, file)
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        for line_num, line in enumerate(f, 1):
                            if query in line:
                                results.append(f"{path}:{line_num} - {line.strip()}")
                except Exception:
                    pass
    return results

print("Searching for 'format_explainable_card'...")
for res in search_files('.', 'format_explainable_card'):
    print(res)

print("\nSearching for 'format_telegram_coupon'...")
for res in search_files('.', 'format_telegram_coupon'):
    print(res)
