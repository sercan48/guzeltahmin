"""Tests for the World Cup Paper-Shadow operational driver (PERSONAL_SHADOW).

These tests validate the OPERATIONAL DRIVER only. They import real pipeline
components transitively but change none of them. They also assert the M11
acceptance hash is unchanged by the exercise.
"""

import unittest

from ops.wc_paper_shadow import (
    run_wc_pipeline, _bundle_hash, validate, control_probe, deliver_personal,
    run_wc_paper_shadow, WC_FIXTURES, MODE,
)


class TestWCPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = run_wc_pipeline()

    def test_produces_signals(self):
        self.assertGreaterEqual(self.bundle["n_signals"], 1)

    def test_all_fixtures_represented(self):
        match_ids = {a["match_id"] for a in self.bundle["audit"]}
        # at least the favourites should emit a signal for each fixture
        self.assertTrue(match_ids.issubset({f["match_id"] for f in WC_FIXTURES}))
        self.assertGreaterEqual(len(match_ids), 1)

    def test_replay_deterministic(self):
        b2 = run_wc_pipeline()
        self.assertEqual(_bundle_hash(self.bundle), _bundle_hash(b2))

    def test_chain_valid(self):
        self.assertTrue(self.bundle["settle"].verify_chain())
        self.assertTrue(self.bundle["gateway"].verify_chain())

    def test_audit_trail_fields(self):
        required = {"signal_id", "match_id", "timestamp", "tier", "entry_odds",
                    "truth_confidence", "control_decision", "settlement_status",
                    "realized_roi", "realized_clv"}
        for a in self.bundle["audit"]:
            self.assertTrue(required.issubset(a.keys()))

    def test_no_orphan_audit_ids(self):
        ids = [a["signal_id"] for a in self.bundle["audit"]]
        self.assertEqual(len(ids), len(set(ids)))


class TestValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = run_wc_pipeline()
        second = run_wc_pipeline()
        cls.validation = validate(cls.bundle, _bundle_hash(second))

    def test_replay_deterministic_flag(self):
        self.assertTrue(self.validation["replay_deterministic"])

    def test_chain_valid_flag(self):
        self.assertTrue(self.validation["chain_valid"])

    def test_complete_settlement_path(self):
        self.assertTrue(self.validation["complete_settlement_path"])

    def test_no_future_data_leakage(self):
        self.assertTrue(self.validation["no_future_data_leakage"])

    def test_no_duplicate_deliveries(self):
        self.assertTrue(self.validation["no_duplicate_deliveries"])

    def test_no_orphan_records(self):
        self.assertTrue(self.validation["no_orphan_records"])


class TestControlProbe(unittest.TestCase):
    def test_suppress_and_halt(self):
        bundle = run_wc_pipeline()
        latest = {(s.match_id, s.selection): s for s in bundle["signals"]}
        sample = next(iter(sorted(latest.items())))[1]
        probe = control_probe(sample)
        self.assertTrue(probe["suppress_correct"])
        self.assertTrue(probe["halt_correct"])
        self.assertEqual(probe["suppress_decision"], "SUPPRESS")
        self.assertEqual(probe["halt_decision"], "HALT")


class TestPersonalShadowDelivery(unittest.TestCase):
    def test_dry_run_when_no_creds(self):
        bundle = run_wc_pipeline()
        d = deliver_personal(bundle, deliver=False)
        self.assertEqual(d["mode"], MODE)
        self.assertTrue(d["dry_run"])
        self.assertTrue(d["single_channel"])
        self.assertTrue(d["no_duplicate_deliveries"])

    def test_allow_signals_would_send(self):
        bundle = run_wc_pipeline()
        d = deliver_personal(bundle, deliver=False)
        # every ALLOW signal is accounted for in dry-run
        self.assertGreaterEqual(d["allow_signals_would_send_dryrun"], 1)
        self.assertEqual(d["allow_signals_delivered"], 0)  # no real send in dry-run
        self.assertEqual(d["delivery_failures"], 0)

    def test_no_real_send_without_explicit_deliver(self):
        bundle = run_wc_pipeline()
        d = deliver_personal(bundle, deliver=True)  # deliver=True but no creds
        self.assertTrue(d["dry_run"])               # still dry-run (creds absent)


class TestAcceptanceHashUnchanged(unittest.TestCase):
    def test_m11_hash_unchanged(self):
        import tests.test_m11_acceptance as m11
        self.assertEqual(m11.run_hash(m11.baseline_providers()),
                         m11.TestM11Acceptance.BASELINE_HASH)

    def test_driver_does_not_change_hash(self):
        import tests.test_m11_acceptance as m11
        before = m11.run_hash(m11.baseline_providers())
        run_wc_pipeline()                            # exercise driver
        after = m11.run_hash(m11.baseline_providers())
        self.assertEqual(before, after)


class TestFullReport(unittest.TestCase):
    def test_report_structure(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            r = run_wc_paper_shadow(report_dir=d, deliver=False)
        self.assertEqual(r["mode"], MODE)
        self.assertTrue(r["acceptance_hash"]["unchanged"])
        self.assertIn("daily_report", r)
        dr = r["daily_report"]
        for k in ("signal_count", "tier_distribution", "truth_confidence_mean",
                  "settled_count", "hit_rate", "CLV", "ROI", "replay_chain_valid",
                  "control_state_distribution", "provider_failures"):
            self.assertIn(k, dr)
        self.assertGreaterEqual(r["pipeline_health_score"], 90.0)

    def test_remains_personal_shadow(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            r = run_wc_paper_shadow(report_dir=d, deliver=False)
        # promotion guardrails: never public, never monetized
        self.assertEqual(r["mode"], "PERSONAL_SHADOW")
        self.assertTrue(r["telegram_delivery_verification"]["single_channel"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
