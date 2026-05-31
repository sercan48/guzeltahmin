import os
import re

patterns = [
    r"api_match_id"
]

def scan_file(filepath):
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    matches = []
    for pattern in patterns:
        for match in re.finditer(pattern, content, re.IGNORECASE):
            start = match.start()
            line_no = content.count('\n', 0, start) + 1
            line_start = content.rfind('\n', 0, start) + 1
            line_end = content.find('\n', start)
            line = content[line_start:line_end].strip()
            matches.append((line_no, line))
    return matches

for root, dirs, files in os.walk('.'):
    if any(p in root for p in ['.venv', '.git', '__pycache__', 'scratch', 'data']):
        continue
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file)
            found = scan_file(filepath)
            if found:
                print(f"File: {filepath}")
                for line_no, line in found:
                    print(f"  Line {line_no}: {line}")
