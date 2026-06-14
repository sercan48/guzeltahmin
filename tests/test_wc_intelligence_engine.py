"""
Tests for WCIntelligenceEngine and downstream adapter integrations.

Coverage:
  - No symmetry collapse (different teams → different outcomes)
  - No all-DRAW bias
  - Determinism (same inputs, same outputs every time)
  - Poisson 1X2 probabilities sum to ≈ 1
  - Market stub structure (BTTS, O/U, Double Chance)
  - WCIntelligenceXGBAdapter interface compatibility
  - Confidence bounded in [8, 92]
  - xG at minimum floor (≥ 0.20)
  - Strong-favourite direction checks
  - synthetic_form_history per-team variability + determinism
  - features_to_team_stats TeamStats field compatibility
  - compute_team_features_by_id produces asymmetric stats
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestNoSymmetryCollapse:
    def test_strong_favourite_wins(self):
        from src.model.wc_intelligence_engine import WCOutcomePredictor

        r = WCOutcomePredictor().predict("Germany", "Curacao")
        assert r["raw_prediction"] == "HOME_WIN"
        assert r["home_win_prob"] > r["away_win_prob"]

    def test_away_strong_team_wins(self):
        from src.model.wc_intelligence_engine import WCOutcomePredictor

        r = WCOutcomePredictor().predict("Curacao", "Germany")
        assert r["raw_prediction"] == "AWAY_WIN"
        assert r["away_win_prob"] > r["home_win_prob"]

    def test_predictions_not_all_draw(self):
        from src.model.wc_intelligence_engine import WCOutcomePredictor

        pred = WCOutcomePredictor()
        matchups = [
            ("Germany",     "Curacao"),
            ("Argentina",   "New Zealand"),
            ("Brazil",      "Haiti"),
            ("France",      "Jordan"),
            ("Netherlands", "Panama"),
        ]
        results = [pred.predict(h, a)["raw_prediction"] for h, a in matchups]
        assert results.count("DRAW") < len(results), "All predictions are DRAW — symmetry collapse detected"

    def test_different_teams_different_features(self):
        from src.model.wc_intelligence_engine import compute_team_features_by_name

        germany  = compute_team_features_by_name("Germany")
        curacao  = compute_team_features_by_name("Curacao")
        assert germany.elo != curacao.elo
        assert germany.attack_strength != curacao.attack_strength


class TestDeterminism:
    def test_same_output_on_repeated_calls(self):
        from src.model.wc_intelligence_engine import WCOutcomePredictor

        pred = WCOutcomePredictor()
        r1 = pred.predict("Netherlands", "Japan")
        r2 = pred.predict("Netherlands", "Japan")
        assert r1 == r2

    def test_independent_instances_agree(self):
        from src.model.wc_intelligence_engine import WCOutcomePredictor

        r1 = WCOutcomePredictor().predict("Brazil", "Ecuador")
        r2 = WCOutcomePredictor().predict("Brazil", "Ecuador")
        assert r1 == r2

    def test_features_deterministic(self):
        from src.model.wc_intelligence_engine import compute_team_features_by_name

        f1 = compute_team_features_by_name("Argentina")
        f2 = compute_team_features_by_name("Argentina")
        assert f1 == f2

    def test_features_by_id_deterministic(self):
        from src.model.wc_intelligence_engine import compute_team_features_by_id

        f1 = compute_team_features_by_id(1001)
        f2 = compute_team_features_by_id(1001)
        assert f1 == f2


class TestPoissonModel:
    def test_probabilities_sum_to_one(self):
        from src.model.wc_intelligence_engine import compute_1x2_poisson

        for xg_h, xg_a in [(1.5, 0.8), (0.5, 2.0), (1.2, 1.2), (0.2, 0.2)]:
            ph, pd, pa = compute_1x2_poisson(xg_h, xg_a)
            total = ph + pd + pa
            assert abs(total - 1.0) < 1e-9, f"Poisson sum={total} for xg ({xg_h},{xg_a})"

    def test_favourite_has_higher_win_prob(self):
        from src.model.wc_intelligence_engine import compute_1x2_poisson

        ph, _pd, pa = compute_1x2_poisson(2.0, 0.5)
        assert ph > pa

    def test_symmetric_xg_gives_symmetric_outcome(self):
        from src.model.wc_intelligence_engine import compute_1x2_poisson

        ph, pd, pa = compute_1x2_poisson(1.25, 1.25)
        assert abs(ph - pa) < 1e-9


class TestOutputFormat:
    def test_all_keys_present(self):
        from src.model.wc_intelligence_engine import WCOutcomePredictor

        r = WCOutcomePredictor().predict("Germany", "Curacao")
        required = {
            "raw_prediction", "final_confidence",
            "home_win_prob", "draw_prob", "away_win_prob",
            "expected_goals_a", "expected_goals_b",
            "is_no_bet", "market_note", "elo_home", "elo_away",
        }
        assert required.issubset(r.keys())

    def test_probabilities_sum_to_100(self):
        from src.model.wc_intelligence_engine import WCOutcomePredictor

        pred = WCOutcomePredictor()
        for home, away in [("Brazil", "Curacao"), ("Netherlands", "Japan"), ("Argentina", "France")]:
            r = pred.predict(home, away)
            total = r["home_win_prob"] + r["draw_prob"] + r["away_win_prob"]
            assert abs(total - 100.0) < 0.5, f"{home} vs {away}: total={total}"

    def test_confidence_bounded(self):
        from src.model.wc_intelligence_engine import WCOutcomePredictor

        pred = WCOutcomePredictor()
        for home, away in [("Germany", "Curacao"), ("Argentina", "France"), ("Brazil", "Japan")]:
            r = pred.predict(home, away)
            assert 8.0 <= r["final_confidence"] <= 92.0, (
                f"{home} vs {away}: confidence={r['final_confidence']}"
            )

    def test_xg_at_minimum_floor(self):
        from src.model.wc_intelligence_engine import WCOutcomePredictor

        r = WCOutcomePredictor().predict("Curacao", "Germany")
        assert r["expected_goals_a"] >= 0.20
        assert r["expected_goals_b"] >= 0.20

    def test_xg_reflects_strength(self):
        from src.model.wc_intelligence_engine import WCOutcomePredictor

        r = WCOutcomePredictor().predict("Germany", "Curacao")
        assert r["expected_goals_a"] > r["expected_goals_b"]

    def test_unknown_team_does_not_crash(self):
        from src.model.wc_intelligence_engine import WCOutcomePredictor

        r = WCOutcomePredictor().predict("UnknownFC", "AnotherFC")
        total = r["home_win_prob"] + r["draw_prob"] + r["away_win_prob"]
        assert abs(total - 100.0) < 0.5


class TestMarketStubs:
    def test_btts_structure(self):
        from src.model.wc_intelligence_engine import btts_predict

        r = btts_predict(1.5, 1.0)
        assert r["status"] == "STUB_NOT_ACTIVE"
        assert "btts_yes" in r and "btts_no" in r
        assert abs(r["btts_yes"] + r["btts_no"] - 100.0) < 0.5

    def test_over_under_structure(self):
        from src.model.wc_intelligence_engine import over_under_predict

        r = over_under_predict(1.5, 1.0, line=2.5)
        assert r["status"] == "STUB_NOT_ACTIVE"
        assert "over_2.5" in r and "under_2.5" in r
        assert "total_xg" in r

    def test_double_chance_structure(self):
        from src.model.wc_intelligence_engine import double_chance_predict

        r = double_chance_predict(0.50, 0.25, 0.25)
        assert r["status"] == "STUB_NOT_ACTIVE"
        assert "home_or_draw" in r
        assert "away_or_draw" in r
        assert "home_or_away" in r

    def test_btts_probability_range(self):
        from src.model.wc_intelligence_engine import btts_predict

        r = btts_predict(1.5, 1.0)
        assert 0.0 <= r["btts_yes"] <= 100.0
        assert 0.0 <= r["btts_no"]  <= 100.0


class TestXGBAdapter:
    def _make_feature_vector(self, elo_diff=200, fatigue_diff=0, att_a=5, att_b=-5, synergy=2, base=1800):
        import numpy as np
        return np.array([[elo_diff, fatigue_diff, att_a, att_b, synergy, base]])

    def test_predict_proba_shape(self):
        from src.model.wc_xgb_pipeline import WCIntelligenceXGBAdapter

        X = self._make_feature_vector()
        out = WCIntelligenceXGBAdapter().predict_proba(X)
        assert out.shape == (1, 3)

    def test_predict_proba_sums_to_one(self):
        from src.model.wc_xgb_pipeline import WCIntelligenceXGBAdapter

        X = self._make_feature_vector()
        out = WCIntelligenceXGBAdapter().predict_proba(X)
        assert abs(out[0].sum() - 1.0) < 1e-9

    def test_predict_proba_values_in_range(self):
        from src.model.wc_xgb_pipeline import WCIntelligenceXGBAdapter

        X = self._make_feature_vector()
        out = WCIntelligenceXGBAdapter().predict_proba(X)
        assert all(0.0 <= v <= 1.0 for v in out[0])

    def test_adapter_favours_home_on_large_positive_elo_diff(self):
        import numpy as np
        from src.model.wc_xgb_pipeline import WCIntelligenceXGBAdapter

        X = np.array([[300, 0, 10, -10, 2, 1800]])
        out = WCIntelligenceXGBAdapter().predict_proba(X)
        # out = [P(away), P(draw), P(home)]
        assert out[0][2] > out[0][0], "Expected home probability higher"

    def test_get_xgb_model_returns_adapter(self):
        from src.model.wc_xgb_pipeline import WCIntelligenceXGBAdapter, get_xgb_model

        assert isinstance(get_xgb_model(), WCIntelligenceXGBAdapter)

    def test_prepare_and_predict_integration(self):
        """prepare_xgb_features → WCIntelligenceXGBAdapter full roundtrip."""
        from src.model.wc_intelligence_engine import compute_team_features_by_name, features_to_team_stats
        from src.model.wc_xgb_pipeline import WCIntelligenceXGBAdapter, prepare_xgb_features

        home = features_to_team_stats(compute_team_features_by_name("Germany", is_home=True))
        away = features_to_team_stats(compute_team_features_by_name("Curacao", is_home=False))

        X   = prepare_xgb_features(home, away)
        out = WCIntelligenceXGBAdapter().predict_proba(X)
        assert out.shape == (1, 3)
        assert abs(out[0].sum() - 1.0) < 1e-9
        assert out[0][2] > out[0][0], "Home win probability should exceed away for Germany vs Curacao"


class TestSyntheticFormHistory:
    def test_deterministic(self):
        from src.model.wc_intelligence_engine import synthetic_form_history

        assert synthetic_form_history(1001) == synthetic_form_history(1001)

    def test_different_teams_give_different_histories(self):
        from src.model.wc_intelligence_engine import synthetic_form_history

        h1 = synthetic_form_history(1001)
        h2 = synthetic_form_history(1002)
        assert h1 != h2

    def test_returns_n_records(self):
        from src.model.wc_intelligence_engine import synthetic_form_history

        result = synthetic_form_history(42, n=10)
        assert len(result) == 10

    def test_result_points_valid(self):
        from src.model.wc_intelligence_engine import synthetic_form_history

        for rec in synthetic_form_history(7):
            assert rec["result_points"] in (0, 1, 3)
            assert "type" in rec


class TestFeaturesToTeamStats:
    def test_teamstats_fields(self):
        from src.model.wc_intelligence_engine import compute_team_features_by_name, features_to_team_stats

        stats = features_to_team_stats(compute_team_features_by_name("Germany"))
        assert hasattr(stats, "elo")
        assert hasattr(stats, "att_vs_def_delta")
        assert hasattr(stats, "synergy")
        assert hasattr(stats, "fatigue")

    def test_teamstats_elo_within_range(self):
        from src.model.wc_intelligence_engine import compute_team_features_by_name, features_to_team_stats

        for name in ["Germany", "Argentina", "Curacao", "UnknownTeam"]:
            stats = features_to_team_stats(compute_team_features_by_name(name))
            assert 1400 <= stats.elo <= 2100, f"{name}: elo={stats.elo}"

    def test_asymmetric_team_ids(self):
        from src.model.wc_intelligence_engine import compute_team_features_by_id, features_to_team_stats

        s1 = features_to_team_stats(compute_team_features_by_id(1001))
        s2 = features_to_team_stats(compute_team_features_by_id(1002))
        assert s1.elo != s2.elo, "Different team_ids must produce different Elo ratings"
