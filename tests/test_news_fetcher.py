"""
Tests for News Fetcher features (similarity detection and dynamic footer).
Uses the AAA (Arrange, Act, Assert) pattern.
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from src.features.news_fetcher import are_titles_similar, is_duplicate, generate_dynamic_footer

def test_are_titles_similar():
    # Arrange
    title_a = "Kylian Mbappe signs for Real Madrid"
    title_b = "Real Madrid signs Mbappe"
    title_c = "Arsenal wins the league"
    
    # Act
    similar_ab = are_titles_similar(title_a, title_b)
    similar_ac = are_titles_similar(title_a, title_c)
    
    # Assert
    assert similar_ab is True
    assert similar_ac is False

def test_is_duplicate():
    # Arrange
    title = "Jurgen Klopp leaves Liverpool"
    existing = {"klopp leaves liverpool", "manchester united win"}
    
    # Act
    duplicate = is_duplicate(title, existing)
    
    # Assert
    assert duplicate is True

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
