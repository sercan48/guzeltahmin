"""
Unit tests for ShadowPredictor.

Covers:
  - prediction direction for clear mismatches
  - no-bet flag for close matchups
  - probability sum invariant
  - full determinism (no randomness)
  - acceptance hash stability (regression guard)
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Pinned acceptance hash for Germany vs Curaçao.
# If this test fails after a code change, the prediction logic changed
# intentionally — update the hash and document the reason in the commit.
PINNED_GERMANY_CURACAO_HASH = (
    "8c4a399d01f91811ce9f558e49855f1d09e4f96d56feabc6c71e4e8badc3311c"
)


class TestShadowPredictorPredictions:
    def test_strong_favourite_wins(self):
        from ops.shadow_predictor import ShadowPredictor

        result = ShadowPredictor().predict("Germany", "Curacao")
        assert result["raw_prediction"] == "HOME_WIN"
        assert result["home_win_prob"] > result["away_win_prob"]
        assert result["home_win_prob"] > result["draw_prob"]

    def test_away_strong_team_wins(self):
        from ops.shadow_predictor import ShadowPredictor

        result = ShadowPredictor().predict("Curacao", "Germany")
        assert result["raw_prediction"] == "AWAY_WIN"
        assert result["away_win_prob"] > result["home_win_prob"]

    def test_close_matchup_is_no_bet(self):
        from ops.shadow_predictor import ShadowPredictor

        # Argentina (1955) vs France (1935): elo_diff = 20 < 50
        result = ShadowPredictor().predict("Argentina", "France")
        assert result["is_no_bet"] is True
        assert "Too Close" in result["market_note"]

    def test_clear_mismatch_is_not_no_bet(self):
        from ops.shadow_predictor import ShadowPredictor

        result = ShadowPredictor().predict("Germany", "Curacao")
        assert result["is_no_bet"] is False
        assert result["market_note"] == "Elo-shadow"

    def test_probabilities_sum_to_100(self):
        from ops.shadow_predictor import ShadowPredictor

        sp = ShadowPredictor()
        for home, away in [
            ("Brazil", "Curacao"),
            ("Netherlands", "Japan"),
            ("Argentina", "France"),
        ]:
            r = sp.predict(home, away)
            total = r["home_win_prob"] + r["draw_prob"] + r["away_win_prob"]
            assert abs(total - 100.0) < 0.5, f"{home} vs {away}: total={total}"

    def test_unknown_team_uses_default_elo(self):
        from ops.shadow_predictor import ShadowPredictor

        result = ShadowPredictor().predict("UnknownFC", "AnotherFC")
        total = result["home_win_prob"] + result["draw_prob"] + result["away_win_prob"]
        assert abs(total - 100.0) < 0.5

    def test_xg_reflects_strength(self):
        from ops.shadow_predictor import ShadowPredictor

        result = ShadowPredictor().predict("Germany", "Curacao")
        assert result["expected_goals_a"] > result["expected_goals_b"]

    def test_xg_at_least_minimum(self):
        from ops.shadow_predictor import ShadowPredictor

        result = ShadowPredictor().predict("Curacao", "Germany")
        assert result["expected_goals_a"] >= 0.20
        assert result["expected_goals_b"] >= 0.20

    def test_confidence_bounded(self):
        from ops.shadow_predictor import ShadowPredictor

        sp = ShadowPredictor()
        for home, away in [("Germany", "Curacao"), ("Argentina", "France")]:
            r = sp.predict(home, away)
            assert 0.0 <= r["final_confidence"] <= 90.0 * sp._CONFIDENCE_DISCOUNT + 1


class TestDeterminism:
    def test_same_result_on_repeated_calls(self):
        from ops.shadow_predictor import ShadowPredictor

        sp = ShadowPredictor()
        r1 = sp.predict("Netherlands", "Japan")
        r2 = sp.predict("Netherlands", "Japan")
        assert r1 == r2

    def test_independent_instances_agree(self):
        from ops.shadow_predictor import ShadowPredictor

        r1 = ShadowPredictor().predict("Brazil", "Ecuador")
        r2 = ShadowPredictor().predict("Brazil", "Ecuador")
        assert r1 == r2


class TestAcceptanceHash:
    def test_hash_stable_across_calls(self):
        from ops.shadow_predictor import ShadowPredictor

        sp = ShadowPredictor()
        r = sp.predict("Germany", "Curacao")
        h1 = ShadowPredictor.acceptance_hash("Germany", "Curacao", r)
        h2 = ShadowPredictor.acceptance_hash("Germany", "Curacao", r)
        assert h1 == h2

    def test_pinned_acceptance_hash(self):
        """Regression guard: fails if prediction logic changes without updating the pin."""
        from ops.shadow_predictor import ShadowPredictor

        sp = ShadowPredictor()
        result = sp.predict("Germany", "Curacao")
        h = ShadowPredictor.acceptance_hash("Germany", "Curacao", result)
        assert h == PINNED_GERMANY_CURACAO_HASH, (
            f"Acceptance hash changed: {h}\n"
            "If this is intentional, update PINNED_GERMANY_CURACAO_HASH "
            "and document the reason in your commit message."
        )

    def test_hash_differs_for_different_inputs(self):
        from ops.shadow_predictor import ShadowPredictor

        sp = ShadowPredictor()
        r1 = sp.predict("Germany", "Curacao")
        r2 = sp.predict("Curacao", "Germany")
        h1 = ShadowPredictor.acceptance_hash("Germany", "Curacao", r1)
        h2 = ShadowPredictor.acceptance_hash("Curacao", "Germany", r2)
        assert h1 != h2
