import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

# Reconfigure stdout for Windows Terminal encoding issues
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from src.features.news_fetcher import (
    generate_news_bulletin
)

def run_tests():
    print("\n--- Testing Gemini News Bulletin Generation ---")
    bulletin = generate_news_bulletin()
    if bulletin:
        print("Generated Bulletin Output:\n")
        print(bulletin)
    else:
        print("Failed to generate bulletin (check console/logs for quota limits or errors).")

if __name__ == "__main__":
    run_tests()
