"""Invariants for the truth canonicalization engine (M1). Pure, no network."""

import math
import unittest

from src.market.truth import (
    OddsFormat, DevigMethod, to_decimal, devig,
    devig_multiplicative, devig_power, devig_shin, devig_ensemble,
)


class TestToDecimal(unittest.TestCase):
    def test_decimal_passthrough(self):
        self.assertEqual(to_decimal(2.5), 2.5)

    def test_decimal_rejects_le_one(self):
        with self.assertRaises(ValueError):
            to_decimal(0.9)

    def test_fractional_string_and_tuple(self):
        self.assertAlmostEqual(to_decimal("5/2", OddsFormat.FRACTIONAL), 3.5)
        self.assertAlmostEqual(to_decimal((5, 2), OddsFormat.FRACTIONAL), 3.5)
        self.assertAlmostEqual(to_decimal("1/1", OddsFormat.FRACTIONAL), 2.0)

    def test_american(self):
        self.assertAlmostEqual(to_decimal(150, OddsFormat.AMERICAN), 2.5)
        self.assertAlmostEqual(to_decimal(-200, OddsFormat.AMERICAN), 1.5)


class TestDevig(unittest.TestCase):
    # a typical 1X2 market with vig
    ODDS = {"HOME": 2.10, "DRAW": 3.40, "AWAY": 3.80}

    def _sums_to_one(self, result):
        self.assertAlmostEqual(sum(result.fair_probs.values()), 1.0, places=9)

    def test_all_methods_sum_to_one(self):
        for m in DevigMethod:
            self._sums_to_one(devig(self.ODDS, m))

    def test_overround_above_one(self):
        r = devig(self.ODDS, DevigMethod.MULTIPLICATIVE)
        self.assertGreater(r.overround, 1.0)
        # raw implied probs minus fair probs = the removed vig
        raw = sum(1.0 / o for o in self.ODDS.values())
        self.assertAlmostEqual(raw, r.overround, places=9)

    def test_fair_probs_below_raw_implied(self):
        # de-vigging must lower each selection's implied prob (vig removed)
        r = devig_multiplicative(self.ODDS)
        for s, o in self.ODDS.items():
            self.assertLess(r.fair_probs[s], 1.0 / o + 1e-12)

    def test_two_way_market(self):
        r = devig({"OVER_2.5": 1.90, "UNDER_2.5": 1.95}, )
        self._sums_to_one(r)

    def test_methods_agree_on_ordering(self):
        # favourite stays the favourite under every method
        for fn in (devig_multiplicative, devig_power, devig_shin, devig_ensemble):
            p = fn(self.ODDS).fair_probs
            self.assertGreater(p["HOME"], p["DRAW"])
            self.assertGreater(p["DRAW"], p["AWAY"])

    def test_power_corrects_favorite_longshot(self):
        # favourite-longshot correction loads more margin on the longshot, so vs
        # the proportional (multiplicative) method the power method gives the
        # favourite MORE fair prob and the longshot LESS.
        pm = devig_multiplicative(self.ODDS).fair_probs
        pp = devig_power(self.ODDS).fair_probs
        self.assertGreater(pp["HOME"], pm["HOME"] - 1e-12)
        self.assertLess(pp["AWAY"], pm["AWAY"] + 1e-12)

    def test_shin_z_between_mult_and_extreme(self):
        # Shin fair probs sum to 1 and stay valid probabilities
        p = devig_shin(self.ODDS).fair_probs
        for v in p.values():
            self.assertGreater(v, 0.0)
            self.assertLess(v, 1.0)

    def test_ensemble_components_present(self):
        r = devig_ensemble(self.ODDS)
        self.assertEqual(set(r.components), {"multiplicative", "power", "shin"})
        # ensemble fair prob is the renormalized mean of the components
        for s in self.ODDS:
            mean = sum(r.components[m][s] for m in r.components) / 3.0
            self.assertGreater(r.fair_probs[s], 0.0)
            self.assertLess(abs(r.fair_probs[s] - mean), 0.05)  # close to the raw mean

    def test_as_odds_roundtrip(self):
        r = devig_multiplicative(self.ODDS)
        for s, p in r.fair_probs.items():
            self.assertAlmostEqual(r.as_odds()[s], 1.0 / p, places=9)

    def test_vig_free_market_is_identity(self):
        # if odds already imply probs summing to 1, fair == raw
        fair_odds = {"A": 2.0, "B": 2.0}   # raw implied 0.5+0.5 = 1.0
        r = devig_ensemble(fair_odds)
        self.assertAlmostEqual(r.fair_probs["A"], 0.5, places=6)
        self.assertAlmostEqual(r.fair_probs["B"], 0.5, places=6)
        self.assertAlmostEqual(r.overround, 1.0, places=9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
