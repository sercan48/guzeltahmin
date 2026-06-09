"""Central configuration for the R1.3 Edge Detection Kernel.

Every threshold/weight the kernel uses lives here so the "exact rules"
required by the spec are auditable in one place. Nothing here trains, optimizes
thresholds, sizes stakes, or learns — these are fixed measurement constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass(frozen=True)
class EdgeConfig:
    # --- Task 2: edge metric reference scales --------------------------
    overround_ref: float = 0.12          # margin at which f_mkt would hit 0 (unused w/ 1/R form)
    drift_contradiction_ref: float = 0.08   # D_REF: prob-drift against model that zeroes f_drift
    drift_velocity_ref: float = 0.05        # V_REF: prob-velocity that floors stability
    drift_stability_floor: float = 0.5
    sharp_contradiction_ref: float = 0.03   # SH_REF: sharp move against model that zeroes f_sharp

    # --- Task 3: edge confidence weights (sum = 1) ---------------------
    conf_weights: Dict[str, float] = field(default_factory=lambda: {
        "calibration": 0.25,
        "efficiency": 0.15,
        "disagreement": 0.25,
        "drift_stability": 0.15,
        "clv_alignment": 0.20,
    })
    disagreement_center_z: float = 1.5   # z at which disagreement confidence peaks
    disagreement_sigma_z: float = 1.0    # width of the disagreement confidence bump

    # --- Task 4: agreement framework thresholds ------------------------
    agree_gap_tol: float = 0.02          # |p_model - p_market| <= tol  => Agree band
    agree_z_lo: float = 1.0              # |z| < z_lo                    => Agree band
    conflict_z: float = 2.5              # |z| >= conflict_z             => Conflict (implausible)
    conflict_drift_align: float = -0.02  # sign(gap)*prob_drift < this   => Conflict (clash)

    # --- Task 6: EQS blend (sum = 1) -----------------------------------
    eqs_weights: Dict[str, float] = field(default_factory=lambda: {
        "edge": 0.45,
        "confidence": 0.40,
        "clv_alignment": 0.15,
    })
    eqs_edge_ref: float = 0.10           # |sharp_adjusted_edge| = 10% => full edge score

    # --- Task 7: tier cut-offs (exact) ---------------------------------
    eqs_reject_below: float = 40.0
    eqs_tier_c: float = 40.0             # [40,55)
    eqs_tier_b: float = 55.0             # [55,70)
    eqs_tier_a: float = 70.0             # [70,85)
    eqs_tier_s: float = 85.0             # >=85 (+ extra gates)
    conf_reject_below: float = 0.35
    tier_s_min_conf: float = 0.70
    tier_s_min_confirm: float = 0.90     # f_drift & f_sharp must both be >= this

    # --- defaults for injected segment metadata ------------------------
    default_calibration_quality: float = 0.65
    default_clv_alignment: float = 0.50


DEFAULT_CONFIG = EdgeConfig()
