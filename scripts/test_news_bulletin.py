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
    generate_news_bulletin,
    are_titles_similar
)

def run_tests():
    print("--- 1. Testing Similarity Analysis ---")
    t1 = "Cristiano Ronaldo signs with Al Nassr"
    t2 = "Al Nassr signs Cristiano Ronaldo officially"
    t3 = "Manchester United wins against Arsenal"
    
    sim_1_2 = are_titles_similar(t1, t2)
    sim_1_3 = are_titles_similar(t1, t3)
    
    print(f"Similarity ('{t1}' vs '{t2}'): {sim_1_2}")
    print(f"Similarity ('{t1}' vs '{t3}'): {sim_1_3}")
    
    print("\n--- 2. Testing Gemini News Bulletin Generation ---")
    bulletin = generate_news_bulletin()
    if bulletin:
        print("Generated Bulletin Output:\n")
        print(bulletin)
    else:
        print("Failed to generate bulletin (check console/logs for quota limits or errors).")

if __name__ == "__main__":
    run_tests()
