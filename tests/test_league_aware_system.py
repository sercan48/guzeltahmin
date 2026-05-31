"""Unit tests for League-Aware Prediction & Calibration System."""

import pytest
import numpy as np
import shutil
from pathlib import Path
import logging

from src.model.league_classifier import LeagueClassifier
from src.evaluator.market_builder import BetSelector, MarketBuilder
from src.model.calibration_benchmarker import CalibrationBenchmarker, calculate_multiclass_ece, calculate_multiclass_brier


class MockDatabase:
    """Mock database client for testing."""
    def __init__(self, table_exists_val=True, return_row=None):
        self.table_exists_val = table_exists_val
        self.return_row = return_row
        self.queries = []

    def table_exists(self, table_name):
        return self.table_exists_val

    def fetchone(self, query, params=()):
        self.queries.append((query, params))
        return self.return_row

    def fetchall(self, query, params=()):
        self.queries.append((query, params))
        return []


def test_league_classifier_hierarchy(caplog):
    # 1. Test Config Overrides & Default Rules
    classifier = LeagueClassifier(db=None)
    assert classifier.get_league_type("E0") == "EUROPE_STABLE"
    assert classifier.get_league_type("NORWAY_ELITESERIEN") == "SUMMER_VOLATILE"
    assert classifier.get_league_type("E1") == "EUROPE_STABLE"

    # 2. Test DB Metadata Lookup
    db = MockDatabase(table_exists_val=True, return_row={"league_type": "SUMMER_VOLATILE"})
    classifier_db = LeagueClassifier(db=db)
    assert classifier_db.get_league_type("UNKNOWN_DB_LEAGUE") == "SUMMER_VOLATILE"
    assert len(db.queries) == 1

    # 3. Test Unknown League Fallback & Warning Log
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        classifier_unknown = LeagueClassifier(db=None)
        res = classifier_unknown.get_league_type("GIBBERISH_LEAGUE_123")
        assert res == "HIGH_ROTATION"
        assert any("GIBBERISH_LEAGUE_123" in record.message for record in caplog.records)


def test_decision_score_scaling():
    selector = BetSelector()
    
    # 1. Base test: high confidence, large sample size, and high coverage should yield a higher score
    ds_high = selector.calculate_decision_score(
        prob=0.70, confidence=9.0, sample_size=300, coverage=0.25
    )
    
    # 2. Low confidence should penalize score
    ds_low_conf = selector.calculate_decision_score(
        prob=0.70, confidence=3.0, sample_size=300, coverage=0.25
    )
    assert ds_low_conf < ds_high

    # 3. Very small sample size should penalize score
    ds_small_sample = selector.calculate_decision_score(
        prob=0.70, confidence=9.0, sample_size=5, coverage=0.25
    )
    assert ds_small_sample < ds_high


def test_market_selector_competition_and_bonuses():
    # Setup BetSelector with standard thresholds
    selector = BetSelector()
    
    # Create fake markets output
    builder = MarketBuilder()
    markets = builder.build_markets(h_prob=0.65, d_prob=0.20, a_prob=0.15, base_confidence=8.0)
    
    # Run selector on stable European league: should favor 1X2, DC, or DNB based on 4-factor score ranking
    bet_europe = selector.select_best_bet(markets, league_code="E0", db=None)
    assert bet_europe["decision"] == "PLAY"
    assert bet_europe["market"] in ["1", "1X", "DNB1"]
    assert bet_europe["league_type"] == "EUROPE_STABLE"

    # Run selector on volatile Summer league: should favor DNB or DC due to bonuses
    bet_summer = selector.select_best_bet(markets, league_code="NORWAY_ELITESERIEN", db=None)
    assert bet_summer["decision"] == "PLAY"
    assert bet_summer["market"] in ["1X", "DNB1"]
    assert bet_summer["league_type"] == "SUMMER_VOLATILE"


def test_calibration_benchmarking(tmp_path):
    # Setup temporary directory for saving models
    models_dir = tmp_path / "models"
    benchmarker = CalibrationBenchmarker(models_dir=models_dir)

    # Generate synthetic validation probability matrices and labels
    np.random.seed(42)
    n_samples = 60
    # Simulate slightly miscalibrated probs: actual class has lower prob on average
    raw_probs = np.random.dirichlet([2, 1, 1], size=n_samples)
    y = np.argmax(raw_probs, axis=1)
    
    # Perform benchmark
    best_method = benchmarker.benchmark_and_save("TEST_LEAGUE", raw_probs, y)
    assert best_method in ["Platt", "Isotonic", "Beta"]

    # Verify files saved correctly
    model_file = models_dir / "calibrator_TEST_LEAGUE.pkl"
    meta_file = models_dir / "calibrator_TEST_LEAGUE_meta.json"
    assert model_file.exists()
    assert meta_file.exists()

    # Load and verify metadata
    import json
    with open(meta_file, "r") as f:
        meta = json.load(f)
    assert meta["league_code"] == "TEST_LEAGUE"
    assert meta["best_method"] == best_method
    assert "cv_ece" in meta
    assert "cv_brier" in meta


def test_ece_calculation():
    # Fully calibrated: accuracy matches probability
    probs = np.array([[0.8, 0.1, 0.1], [0.8, 0.1, 0.1], [0.2, 0.7, 0.1], [0.2, 0.7, 0.1]])
    y_true = np.array([0, 0, 1, 1])
    ece = calculate_multiclass_ece(probs, y_true, n_bins=2)
    # Since confidence (0.8 and 0.7) matches accuracy (100% in both bins), ECE should be small
    assert ece >= 0.0 and ece <= 0.35

    # High error
    y_wrong = np.array([2, 2, 2, 2])
    ece_wrong = calculate_multiclass_ece(probs, y_wrong, n_bins=2)
    assert ece_wrong > ece


def test_telegram_card_formatting():
    from src.model.predictor import format_explainable_card
    
    # Setup mock prediction output dictionary
    prediction = {
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "league_code": "E0",
        "home_win_prob": 0.65,
        "draw_prob": 0.20,
        "away_win_prob": 0.15,
        "predicted_result": "H",
        "confidence_score": 8.5,
        "home_absences": ["Saka (Sakat)"],
        "away_absences": [],
        "home_power_loss": 0.05,
        "away_power_loss": 0.0,
        "home_lambda": 2.1,
        "away_lambda": 0.9,
        "over25_prob": 0.58,
        "btts_prob": 0.52,
        "_odds": {
            "h": 1.75,
            "d": 3.40,
            "a": 4.50
        },
        "xgb_probs": [0.65, 0.20, 0.15],
        "lgb_probs": [0.65, 0.20, 0.15],
        "poi_probs": [0.65, 0.20, 0.15]
    }
    
    card_text = format_explainable_card(prediction)
    
    # Verify template structures exist in the returned text
    assert "🏴󠁧󠁢󠁥󠁮󠁧󠁿 E0 | Arsenal vs Chelsea" in card_text
    assert "🎯 ANA TAHMİN: MS 1" in card_text
    assert "🔥 Güven: %85" in card_text
    assert "Oran: 1.75" in card_text
    # EV = 0.65 * 1.75 - 1 = 1.1375 - 1 = 0.1375 (+13.8%)
    assert "Değer (EV): +%13.7" in card_text
    assert "🤖 AI Analizi: Eksikler: Arsenal (1) | Güç Kaybı: Ev: -%5.0 | Dep: -%0.0" in card_text
    assert "🧠 Model İhtimali: 1=%65.0 | X=%20.0 | 2=%15.0" in card_text
    assert "🎯 xG Beklentisi: 2.10 – 0.90" in card_text
    # Since predicted is MS 1 (prob 0.65), other candidate options like 1X Çifte Şans (0.85) should be runner-up
    assert "💡 2. Güçlü Seçenek: 1X Çifte Şans" in card_text
    assert "Yasal Uyarı" in card_text
    assert "Bahis tavsiyesi değildir" in card_text

