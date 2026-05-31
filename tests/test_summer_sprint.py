"""Automated tests for Model Reliability & Summer League Recovery Sprint."""

import pytest
import sqlite3
import pandas as pd
from src.ingestion.venue_registry import get_team_venue
from src.preprocessing.schema_mapper import haversine_distance, ingest_matches_to_db
from src.evaluator.market_builder import MarketBuilder, BetSelector
from src.db.base import get_backend
from src.model.predictor import predict_match

# Mock Database Backend for lightweight testing
class MockDB:
    def __init__(self):
        self.queries = []
        self.rows = []
        self.lastrowid = 1

    def connect(self): pass
    def close(self): pass
    def execute(self, query, params=()):
        self.queries.append((query, params))
        class DummyCursor:
            def __init__(self, rid):
                self.lastrowid = rid
        return DummyCursor(self.lastrowid)
    def fetchone(self, query, params=()):
        self.queries.append((query, params))
        return self.rows[0] if self.rows else None
    def fetchall(self, query, params=()):
        self.queries.append((query, params))
        return self.rows

def test_venue_registry():
    # Test normalization and case insensitivity
    res_bodo = get_team_venue("Bodo/Glimt")
    assert res_bodo is not None
    assert res_bodo["pitch"] == "ARTIFICIAL"
    assert res_bodo["lat"] == 67.280

    res_flamengo = get_team_venue("Flamengo RJ")
    assert res_flamengo is not None
    assert res_flamengo["pitch"] == "NATURAL"

    res_missing = get_team_venue("Unknown Team FC")
    assert res_missing is None

def test_haversine_distance():
    # Test distance between Bodo/Glimt and Rosenborg
    bodo = get_team_venue("Bodo/Glimt")
    rbk = get_team_venue("Rosenborg")
    dist = haversine_distance(bodo["lat"], bodo["lon"], rbk["lat"], rbk["lon"])
    # Rosenborg (63.43, 10.395) to Bodo/Glimt (67.28, 14.404) ~468.8 km
    assert abs(dist - 468.8) < 10.0

def test_market_builder_math():
    builder = MarketBuilder()
    # Test balanced probabilities
    m = builder.build_markets(0.40, 0.30, 0.30, 8.0)
    
    # Verify Double Chance
    assert abs(m["1X"]["probability"] - 0.70) < 0.01
    assert abs(m["X2"]["probability"] - 0.60) < 0.01
    assert abs(m["12"]["probability"] - 0.70) < 0.01

    # Verify DNB
    assert abs(m["DNB1"]["probability"] - 0.571) < 0.01 # 0.40 / 0.70
    assert abs(m["DNB2"]["probability"] - 0.429) < 0.01 # 0.30 / 0.70

def test_bet_selector_rules():
    selector = BetSelector()
    builder = MarketBuilder()

    # Rule 1: High 1X2 Prob in stable Europe league
    m1 = builder.build_markets(0.95, 0.02, 0.03, 9.0)
    bet1 = selector.select_best_bet(m1, league_code="E0")
    assert bet1["decision"] == "PLAY"
    assert bet1["market"] == "1"

    # Rule 2: Low 1X2 but high DC in volatile Summer league
    m2 = builder.build_markets(0.40, 0.40, 0.20, 7.0)
    bet2 = selector.select_best_bet(m2, league_code="NORWAY_ELITESERIEN")
    assert bet2["decision"] == "PLAY"
    assert bet2["market"] == "1X"

    # Rule 3: Low 1X2, low DC but high DNB
    m3 = builder.build_markets(0.52, 0.20, 0.28, 7.5)
    bet3 = selector.select_best_bet(m3, league_code="NORWAY_ELITESERIEN")
    assert bet3["decision"] == "PLAY"
    assert bet3["market"] in ["DNB1", "12"]

    # Rule 4: All low (SKIP)
    m4 = builder.build_markets(0.35, 0.33, 0.32, 5.0)
    bet4 = selector.select_best_bet(m4, league_code="UNKNOWN")
    assert bet4["decision"] == "SKIP"

def test_schema_mapper_ingestion_with_distance():
    # Setup test df
    test_match = pd.DataFrame([{
        "date": "2026-05-29",
        "league_code": "NORWAY_ELITESERIEN",
        "season": "2026",
        "home_team": "Bodo/Glimt",
        "away_team": "Rosenborg",
        "ft_home_goals": 2,
        "ft_away_goals": 2,
        "ft_result": "D",
        "ht_home_goals": 1,
        "ht_away_goals": 1,
        "referee": "Unknown"
    }])

    db = MockDB()
    # Team ID mocks
    db.rows = [{"id": 1, "name": "Bodo/Glimt"}, {"id": 2, "name": "Rosenborg"}]
    
    # We mock fetchone to return None first (no existing match), then the ID on insert
    db.fetchone = lambda query, params=(): None

    inserted = ingest_matches_to_db(test_match, db)
    assert inserted == 1
    
    # Let's inspect queries to verify distance was computed and inserted
    insert_query = [q for q in db.queries if "INSERT INTO matches" in q[0]]
    assert len(insert_query) > 0
    params = insert_query[0][1]
    
    # Parameter indexes check: referee is index 22, pitch_type 23, travel_distance 24, is_summer_league 25
    assert params[23] == "ARTIFICIAL" # Bodo pitch
    assert params[24] > 400.0 # Bodo to Rosenborg distance
    assert params[25] == 1 # summer league flag
