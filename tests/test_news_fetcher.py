"""
Tests for News Fetcher features (similarity detection and dynamic footer).
Uses the AAA (Arrange, Act, Assert) pattern.
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from src.features.news_fetcher import generate_dynamic_footer

def test_generate_dynamic_footer():
    # Arrange
    news_with_injury = ["Kerem Aktürkoğlu sakatlandı", "Arsenal win 2-1"]
    news_no_injury = ["Transfer gündemi", "Porto kupayı kazandı"]
    
    # Act
    footer_injury = generate_dynamic_footer(news_with_injury)
    footer_no_injury = generate_dynamic_footer(news_no_injury)
    
    # Assert
    assert "sakatlık" in footer_injury.lower()
    assert len(footer_no_injury) > 0

