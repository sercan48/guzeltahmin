"""
Tests for src/calibration/draw_isotonic.py — R-1 Draw Calibration Work Package.

Coverage (per work package spec):
  - determinism            : same input -> identical output, every time
  - replay compatibility   : identity mode is an exact, byte-for-byte no-op
  - probability conservation: H_cal + D_cal + A_cal == 100 after calibration
  - calibration monotonicity: fitted mapping never decreases
  - rollback path           : switching back to identity restores original behaviour
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.calibration.draw_isotonic import (
    CALIBRATION_MODES,
    DEFAULT_CALIBRATION_MODE,
    apply_calibration,
    fit_isotonic_draw,
    get_calibration_mode,
    is_monotonic_nondecreasing,
    outcome_from_probs,
    recompute_confidence,
)

SAMPLE_SETTLED = [
    (59.0, False), (17.5, True), (55.9, False), (20.6, True),
    (15.4, False), (18.1, True), (18.9, True), (50.6, False),
    (21.8, False), (40.5, False), (24.1, False), (12.0, False),
    (30.2, True), (45.0, False), (22.0, True),
]


class TestFeatureFlagDefault:
    def test_default_mode_is_identity(self):
        assert DEFAULT_CALIBRATION_MODE == "identity"

    def test_unset_env_falls_back_to_identity(self, monkeypatch):
        monkeypatch.delenv("DRAW_CALIBRATION_MODE", raising=False)
        assert get_calibration_mode() == "identity"

    def test_invalid_env_falls_back_to_identity(self, monkeypatch):
        monkeypatch.setenv("DRAW_CALIBRATION_MODE", "not_a_real_mode")
        assert get_calibration_mode() == "identity"

    def test_valid_env_isotonic_draw(self, monkeypatch):
        monkeypatch.setenv("DRAW_CALIBRATION_MODE", "isotonic_draw")
        assert get_calibration_mode() == "isotonic_draw"


class TestDeterminism:
    def test_fit_is_deterministic(self):
        m1 = fit_isotonic_draw(SAMPLE_SETTLED)
        m2 = fit_isotonic_draw(SAMPLE_SETTLED)
        assert m1.fit_hash == m2.fit_hash
        assert m1.n_fit == m2.n_fit == len(SAMPLE_SETTLED)

    def test_fit_hash_changes_with_different_samples(self):
        m1 = fit_isotonic_draw(SAMPLE_SETTLED)
        altered = SAMPLE_SETTLED[:-1] + [(99.0, True)]
        m2 = fit_isotonic_draw(altered)
        assert m1.fit_hash != m2.fit_hash

    def test_predict_same_input_same_output(self):
        model = fit_isotonic_draw(SAMPLE_SETTLED)
        for x in (10.0, 18.5, 25.0, 50.0, 75.0):
            assert model.predict(x) == model.predict(x)

    def test_apply_calibration_repeated_calls_identical(self):
        model = fit_isotonic_draw(SAMPLE_SETTLED)
        r1 = apply_calibration(60.0, 20.0, 20.0, mode="isotonic_draw", model=model)
        r2 = apply_calibration(60.0, 20.0, 20.0, mode="isotonic_draw", model=model)
        assert r1 == r2

    def test_independent_models_from_same_data_agree(self):
        m1 = fit_isotonic_draw(SAMPLE_SETTLED)
        m2 = fit_isotonic_draw(SAMPLE_SETTLED)
        for x in (10.0, 30.0, 60.0):
            assert m1.predict(x) == m2.predict(x)


class TestReplayCompatibility:
    """identity mode must be an exact no-op so plugging this layer into any
    pipeline at its default setting cannot change a previously-computed
    canonical replay/run hash."""

    @pytest.mark.parametrize("ph,pd,pa", [
        (70.0, 15.0, 15.0),
        (33.3, 33.4, 33.3),
        (0.0, 100.0, 0.0),
        (59.0, 19.9, 21.1),
        (17.3, 17.5, 65.1),  # a real stored triplet that sums to 99.9
    ])
    def test_identity_returns_exact_input(self, ph, pd, pa):
        out = apply_calibration(ph, pd, pa, mode="identity")
        assert out == (ph, pd, pa)

    def test_identity_ignores_model_argument(self):
        model = fit_isotonic_draw(SAMPLE_SETTLED)
        out_with_model = apply_calibration(60.0, 20.0, 20.0, mode="identity", model=model)
        out_without_model = apply_calibration(60.0, 20.0, 20.0, mode="identity")
        assert out_with_model == out_without_model == (60.0, 20.0, 20.0)

    def test_identity_requires_no_model(self):
        # Must not raise even though no model is fitted/supplied.
        out = apply_calibration(50.0, 30.0, 20.0, mode="identity")
        assert out == (50.0, 30.0, 20.0)

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError):
            apply_calibration(50.0, 30.0, 20.0, mode="not_a_mode")

    def test_isotonic_draw_without_model_rejected(self):
        with pytest.raises(ValueError):
            apply_calibration(50.0, 30.0, 20.0, mode="isotonic_draw")


class TestProbabilityConservation:
    def test_identity_mode_preserves_input_sum(self):
        # Identity must reproduce whatever was given, including pre-existing
        # upstream rounding drift -- it does not "fix" anything.
        out = apply_calibration(17.3, 17.5, 65.1, mode="identity")
        assert sum(out) == pytest.approx(17.3 + 17.5 + 65.1)

    @pytest.mark.parametrize("ph,pd,pa", [
        (70.0, 15.0, 15.0),
        (33.3, 33.4, 33.3),
        (59.0, 19.9, 21.1),
        (17.3, 17.5, 65.1),
        (90.0, 5.0, 5.0),
        (5.0, 5.0, 90.0),
    ])
    def test_isotonic_draw_sums_to_100(self, ph, pd, pa):
        model = fit_isotonic_draw(SAMPLE_SETTLED)
        h, d, a = apply_calibration(ph, pd, pa, mode="isotonic_draw", model=model)
        assert (h + d + a) == pytest.approx(100.0, abs=1e-6)

    def test_isotonic_draw_zero_home_and_away(self):
        # h_plus_a == 0 edge case must not divide by zero and must still conserve.
        model = fit_isotonic_draw(SAMPLE_SETTLED)
        h, d, a = apply_calibration(0.0, 100.0, 0.0, mode="isotonic_draw", model=model)
        assert (h + d + a) == pytest.approx(100.0, abs=1e-6)

    def test_isotonic_draw_outputs_nonnegative(self):
        model = fit_isotonic_draw(SAMPLE_SETTLED)
        for ph, pd, pa in [(70, 15, 15), (10, 80, 10), (45, 10, 45)]:
            h, d, a = apply_calibration(ph, pd, pa, mode="isotonic_draw", model=model)
            assert h >= 0 and d >= 0 and a >= 0


class TestCalibrationMonotonicity:
    def test_fitted_mapping_is_nondecreasing(self):
        model = fit_isotonic_draw(SAMPLE_SETTLED)
        sample_points = [x / 2.0 for x in range(0, 201)]  # 0..100 step 0.5
        assert is_monotonic_nondecreasing(model, sample_points)

    def test_predict_nondecreasing_pairwise(self):
        model = fit_isotonic_draw(SAMPLE_SETTLED)
        xs = sorted([12.0, 18.0, 18.9, 21.8, 30.2, 59.0, 80.0])
        ys = [model.predict(x) for x in xs]
        for a, b in zip(ys, ys[1:]):
            assert b >= a - 1e-9

    def test_requires_minimum_two_samples(self):
        with pytest.raises(ValueError):
            fit_isotonic_draw([(20.0, True)])

    def test_clips_to_safety_bounds(self):
        # All-draw training data should not push calibrated D% to 100%.
        model = fit_isotonic_draw([(10.0, True), (90.0, True)])
        assert model.predict(50.0) <= 80.0 + 1e-9
        # All-non-draw training data should not push calibrated D% to 0%.
        model2 = fit_isotonic_draw([(10.0, False), (90.0, False)])
        assert model2.predict(50.0) >= 1.0 - 1e-9


class TestRollbackPath:
    def test_switch_back_to_identity_restores_original(self):
        model = fit_isotonic_draw(SAMPLE_SETTLED)
        original = (60.0, 20.0, 20.0)
        # exercise isotonic_draw first (simulating it having been "turned on")
        apply_calibration(*original, mode="isotonic_draw", model=model)
        # rollback: identity mode on the *original* inputs reproduces them exactly
        restored = apply_calibration(*original, mode="identity")
        assert restored == original

    def test_rollback_does_not_require_discarding_model(self):
        # The model object can remain in memory after rollback; identity mode
        # simply ignores it. No state needs to be torn down.
        model = fit_isotonic_draw(SAMPLE_SETTLED)
        out = apply_calibration(60.0, 20.0, 20.0, mode="identity", model=model)
        assert out == (60.0, 20.0, 20.0)

    def test_mode_is_a_pure_per_call_argument(self):
        # No global mutable state -- alternating modes on the same model
        # never leaves residue that affects the next call.
        model = fit_isotonic_draw(SAMPLE_SETTLED)
        for _ in range(5):
            iso = apply_calibration(60.0, 20.0, 20.0, mode="isotonic_draw", model=model)
            ident = apply_calibration(60.0, 20.0, 20.0, mode="identity")
            assert ident == (60.0, 20.0, 20.0)
            assert iso != ident or iso == (60.0, 20.0, 20.0)


class TestSupportingHelpers:
    def test_outcome_from_probs_argmax(self):
        assert outcome_from_probs(70.0, 15.0, 15.0) == "HOME_WIN"
        assert outcome_from_probs(15.0, 70.0, 15.0) == "DRAW"
        assert outcome_from_probs(15.0, 15.0, 70.0) == "AWAY_WIN"

    def test_recompute_confidence_bounded(self):
        for ph, pd, pa, gap in [(70, 15, 15, 300), (40, 35, 25, 0), (33, 34, 33, 150)]:
            c = recompute_confidence(ph, pd, pa, gap)
            assert 30.0 <= c <= 92.0

    def test_calibration_modes_tuple_contains_identity_and_isotonic(self):
        assert set(CALIBRATION_MODES) == {"identity", "isotonic_draw"}
