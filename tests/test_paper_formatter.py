"""M6 Telegram presentation & explainability invariants. Pure, no network/sending."""

import unittest

from src.market.truth import TruthAdjustedEdge
from src.market.orchestration import PaperSignal
from app.bot.paper_formatter import (
    PaperSignalFormatter, SignalView, Channel, route,
)


def adjusted(tier="TIER_A", provenance="OBSERVED", raw=0.091, final=0.074,
             discount=0.82, sharp_disc=0.9, truth_conf=0.91):
    return TruthAdjustedEdge(
        match_id="m", market="1X2", selection="HOME",
        edge_before_truth=raw, edge_after_truth=final, truth_discount=discount,
        confidence_discount=0.9, provenance_discount=1.0, sharp_consensus_discount=sharp_disc,
        eqs_before=80.0, eqs_after=74.0, tier_before=tier, tier_after=tier,
        provenance=provenance, truth_confidence=truth_conf,
    )


def view(**kw):
    return SignalView.from_adjusted(adjusted(**kw), confidence=0.82,
                                    timestamp="2026-05-01T17:00:00+00:00")


class TestFormatting(unittest.TestCase):
    def test_message_contains_explainability_fields(self):
        msg = PaperSignalFormatter().format(view())
        for token in ("Tier: A", "Edge: 7.4%", "Confidence: 0.82",
                      "Truth Confidence: 0.91", "Source: OBSERVED"):
            self.assertIn(token, msg)

    def test_edge_decomposition(self):
        msg = PaperSignalFormatter().format(view())
        self.assertIn("Raw Edge: 9.1%", msg)
        self.assertIn("Truth Discount: 0.82", msg)
        self.assertIn("Final Edge: 7.4%", msg)

    def test_why_this_pick_section(self):
        msg = PaperSignalFormatter().format(view())
        self.assertIn("WHY THIS PICK", msg)
        self.assertIn("Model probability exceeds market probability", msg)
        self.assertIn("Strong truth confidence", msg)
        self.assertIn("Sharp consensus present", msg)
        self.assertIn("No lifecycle restrictions active", msg)

    def test_weak_signals_omit_reasons(self):
        # low truth confidence + weak sharp -> those reasons absent
        v = view(truth_conf=0.4, sharp_disc=0.45)
        msg = PaperSignalFormatter().format(v)
        self.assertNotIn("Strong truth confidence", msg)
        self.assertNotIn("Sharp consensus present", msg)
        self.assertIn("No lifecycle restrictions active", msg)


class TestProvenanceWarnings(unittest.TestCase):
    def test_reconstructed_warning(self):
        msg = PaperSignalFormatter().format(view(provenance="RECONSTRUCTED"))
        self.assertIn("⚠️ Reconstructed market truth. Use with caution.", msg)

    def test_partial_warning(self):
        msg = PaperSignalFormatter().format(view(provenance="PARTIAL"))
        self.assertIn("⚠️ Partial market truth. Use with caution.", msg)

    def test_observed_no_warning(self):
        msg = PaperSignalFormatter().format(view(provenance="OBSERVED"))
        self.assertNotIn("⚠️", msg)


class TestRouting(unittest.TestCase):
    def test_tier_routing_map(self):
        self.assertEqual(route("TIER_S"), Channel.VIP)
        self.assertEqual(route("TIER_A"), Channel.VIP)
        self.assertEqual(route("TIER_B"), Channel.STANDARD)
        self.assertEqual(route("TIER_C"), Channel.MONITORING)
        self.assertEqual(route("REJECT"), Channel.NONE)

    def test_reject_suppression(self):
        fmt = PaperSignalFormatter()
        msg, ch = fmt.format_and_route(view(tier="REJECT"))
        self.assertIsNone(msg)
        self.assertEqual(ch, Channel.NONE)
        self.assertEqual(fmt.metrics.rejected_messages, 1)
        self.assertEqual(fmt.metrics.formatted_messages, 0)

    def test_publication_routes_and_counts(self):
        fmt = PaperSignalFormatter()
        _, ch = fmt.format_and_route(view(tier="TIER_A"))
        self.assertEqual(ch, Channel.VIP)
        self.assertEqual(fmt.metrics.formatted_messages, 1)
        self.assertEqual(fmt.metrics.routing_distribution["VIP"], 1)


class TestMonitoringAndDeterminism(unittest.TestCase):
    def test_routing_distribution(self):
        fmt = PaperSignalFormatter()
        fmt.format_and_route(view(tier="TIER_S"))
        fmt.format_and_route(view(tier="TIER_A"))
        fmt.format_and_route(view(tier="TIER_B"))
        fmt.format_and_route(view(tier="TIER_C"))
        fmt.format_and_route(view(tier="REJECT"))
        dist = fmt.metrics.routing_distribution
        self.assertEqual(dist.get("VIP"), 2)
        self.assertEqual(dist.get("STANDARD"), 1)
        self.assertEqual(dist.get("MONITORING"), 1)
        self.assertEqual(fmt.metrics.rejected_messages, 1)
        self.assertEqual(fmt.metrics.formatted_messages, 4)

    def test_determinism(self):
        a = PaperSignalFormatter().format(view())
        b = PaperSignalFormatter().format(view())
        self.assertEqual(a, b)


class TestPaperSignalView(unittest.TestCase):
    def test_from_paper_degraded_view(self):
        ps = PaperSignal("m", "1X2", "HOME", edge_score=0.074, tier="TIER_A",
                         confidence=0.82, truth_confidence=0.91,
                         timestamp="2026-05-01T17:00:00+00:00")
        msg = PaperSignalFormatter().format(SignalView.from_paper(ps))
        self.assertIn("Final Edge: 7.4%", msg)
        self.assertIn("Raw Edge: n/a", msg)        # decomposition not on PaperSignal
        self.assertIn("Source: UNKNOWN", msg)

    def test_no_stake_or_bankroll_in_output(self):
        msg = PaperSignalFormatter().format(view()).lower()
        for forbidden in ("stake", "bankroll", "kelly", "bet amount"):
            self.assertNotIn(forbidden, msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
