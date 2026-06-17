"""
Isolation tests for ShadowPredictor.

Guarantees enforced here:
  1. No production predictor source file references 'shadow_predictor'.
  2. ops/shadow_predictor.py does not import any production predictor module.
  3. Running ShadowPredictor does not mutate production module outputs.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_ROOT = Path(__file__).parent.parent

# All production predictor source files that must stay shadow-free.
_PRODUCTION_FILES = [
    _ROOT / "src" / "model" / "wc_three_tier_inference.py",
    _ROOT / "src" / "model" / "wc_ensemble_inference.py",
    _ROOT / "src" / "model" / "wc_monte_carlo.py",
    _ROOT / "src" / "model" / "wc_xgb_pipeline.py",
    _ROOT / "src" / "features" / "wc_confidence_calibrator.py",
    _ROOT / "src" / "features" / "wc_market_delta.py",
    _ROOT / "src" / "features" / "wc_lineup_fetcher.py",
    _ROOT / "src" / "features" / "world_cup_engine.py",
]

# Production modules that shadow_predictor must never import.
_FORBIDDEN_IN_SHADOW = [
    "wc_three_tier_inference",
    "wc_ensemble_inference",
    "wc_monte_carlo",
    "wc_xgb_pipeline",
    "wc_confidence_calibrator",
    "wc_market_delta",
    "wc_lineup_fetcher",
    "world_cup_engine",
]


class TestProductionDoesNotImportShadow:
    @pytest.mark.parametrize("prod_file", _PRODUCTION_FILES, ids=lambda p: p.name)
    def test_no_shadow_predictor_reference(self, prod_file):
        if not prod_file.exists():
            pytest.skip(f"{prod_file} not found")
        text = prod_file.read_text()
        assert "shadow_predictor" not in text, (
            f"{prod_file.relative_to(_ROOT)} references 'shadow_predictor' — "
            "production predictors must not depend on shadow infrastructure."
        )


class TestShadowDoesNotImportProduction:
    def test_shadow_predictor_forbidden_imports(self):
        shadow_file = _ROOT / "ops" / "shadow_predictor.py"
        assert shadow_file.exists(), "ops/shadow_predictor.py not found"
        # Check only actual import lines, not comments or docstrings.
        import_lines = [
            line
            for line in shadow_file.read_text().splitlines()
            if line.strip().startswith(("import ", "from ")) and not line.strip().startswith("#")
        ]
        import_text = "\n".join(import_lines)
        for mod in _FORBIDDEN_IN_SHADOW:
            assert mod not in import_text, (
                f"ops/shadow_predictor.py imports '{mod}' — "
                "shadow predictor must not depend on production inference code."
            )


class TestShadowDoesNotMutateProductionOutputs:
    """Running ShadowPredictor must not change production Monte Carlo results."""

    def test_monte_carlo_unchanged_after_shadow(self):
        import numpy as np
        from src.model.wc_monte_carlo import TeamStats, run_monte_carlo_simulation

        team_a = TeamStats(elo=1700, att_vs_def_delta=5.0, synergy=10.0, fatigue=0.0)
        team_b = TeamStats(elo=1500, att_vs_def_delta=-5.0, synergy=0.0, fatigue=5.0)

        np.random.seed(42)
        before = run_monte_carlo_simulation(team_a, team_b, simulations=500)

        # Run shadow prediction in between
        from ops.shadow_predictor import ShadowPredictor
        ShadowPredictor().predict("Germany", "Curacao")

        np.random.seed(42)
        after = run_monte_carlo_simulation(team_a, team_b, simulations=500)

        assert before == after, (
            "Monte Carlo output changed after running ShadowPredictor — "
            "shadow code must not have side effects on production modules."
        )

    def test_ensemble_unchanged_after_shadow(self):
        import numpy as np
        from src.model.wc_ensemble_inference import run_ensemble_inference
        from src.model.wc_monte_carlo import TeamStats

        team_a = TeamStats(elo=1700, att_vs_def_delta=5.0, synergy=10.0, fatigue=0.0)
        team_b = TeamStats(elo=1500, att_vs_def_delta=-5.0, synergy=0.0, fatigue=5.0)

        # Seed before each call: proves shadow code does not alter the seeded path.
        np.random.seed(42)
        before = run_ensemble_inference(team_a, team_b)

        from ops.shadow_predictor import ShadowPredictor
        ShadowPredictor().predict("Netherlands", "Japan")  # must not touch np.random

        np.random.seed(42)
        after = run_ensemble_inference(team_a, team_b)

        assert before == after, (
            "Ensemble output changed after running ShadowPredictor — "
            "shadow code must not affect the numpy random state or production modules."
        )
