"""R1.3 Edge Detection Kernel runner.

Runs the R1.2 measurement layer on the 5-match fixture, injects illustrative
model probabilities + per-segment metadata (calibration quality, historical CLV
alignment), then runs the edge kernel and prints a per-selection report. Also
demonstrates the historical validation layer on synthesized settled records.

No network, no ML training, no stake sizing.

    python3 -m src.market.edge.run_r1_3_edge            # JSON
    python3 -m src.market.edge.run_r1_3_edge --pretty   # human summary too
"""

from __future__ import annotations

import argparse
import json
import sys

from ..fixtures import build_fixture, _LABELS
from ..measurement_pipeline import MeasurementPipeline
from ..schema import Horizon
from .pipeline import EdgeDetectionKernel, SegmentMeta
from .validation import HistoricalValidator, SettledRecord

# Injected model probabilities (the existing model is NOT called here).
# Designed to produce a spread of agreement classes / tiers.
MODEL_PROBS = {
    ("evt_ars_che", "1X2", "HOME"): 0.52, ("evt_ars_che", "1X2", "DRAW"): 0.27, ("evt_ars_che", "1X2", "AWAY"): 0.21,
    ("evt_liv_mci", "1X2", "HOME"): 0.46, ("evt_liv_mci", "1X2", "DRAW"): 0.28, ("evt_liv_mci", "1X2", "AWAY"): 0.26,
    ("evt_bar_rma", "1X2", "HOME"): 0.378, ("evt_bar_rma", "1X2", "DRAW"): 0.27, ("evt_bar_rma", "1X2", "AWAY"): 0.352,
    ("evt_juv_mil", "1X2", "HOME"): 0.57, ("evt_juv_mil", "1X2", "DRAW"): 0.24, ("evt_juv_mil", "1X2", "AWAY"): 0.19,
    ("evt_bay_dor", "1X2", "HOME"): 0.665, ("evt_bay_dor", "1X2", "DRAW"): 0.21, ("evt_bay_dor", "1X2", "AWAY"): 0.125,
}

# Injected per-segment metadata (calibration quality, historical CLV alignment).
SEGMENT_META = {
    "evt_ars_che": SegmentMeta(calibration_quality=0.88, clv_alignment=0.72),
    "evt_liv_mci": SegmentMeta(calibration_quality=0.80, clv_alignment=0.55),
    "evt_bar_rma": SegmentMeta(calibration_quality=0.75, clv_alignment=0.50),
    "evt_juv_mil": SegmentMeta(calibration_quality=0.82, clv_alignment=0.68),
    "evt_bay_dor": SegmentMeta(calibration_quality=0.70, clv_alignment=0.58),
}


def _f(x, nd=4):
    return None if x is None else round(x, nd)


def human_summary(edges) -> str:
    lines = ["=" * 96, "R1.3 EDGE DETECTION KERNEL — HOME selection per match", "=" * 96]
    hdr = f"{'Match':<26}{'p_mdl':>7}{'p_mkt':>7}{'gap':>7}{'z':>6}{'raw_e':>8}{'sharp_e':>9}{'ECS':>6}{'EQS':>7}  {'class':<22}{'tier'}"
    lines.append(hdr)
    for mid, label in _LABELS.items():
        e = edges.get((mid, "1X2", "HOME"))
        if not e:
            continue
        c, m, cf, ag, q, cl = e.comparator, e.metrics, e.confidence, e.agreement, e.eqs, e.classification
        lines.append(
            f"{label:<26}{c.model_probability:>7.3f}{c.market_probability:>7.3f}"
            f"{c.probability_gap:>7.3f}{(c.probability_gap_zscore or 0):>6.2f}"
            f"{m.raw_edge:>8.3f}{m.sharp_adjusted_edge:>9.3f}"
            f"{cf.edge_confidence_score:>6.2f}{q.eqs:>7.1f}  "
            f"{ag.agreement_class.split('_',1)[0]+' '+ag.agreement_class.split('_',1)[1]:<22}{cl.tier}"
        )
    return "\n".join(lines)


def demo_validation():
    """Synthesize settled records (outcome correlated with model edge) to show
    the evaluation-only metrics compute. Deterministic, illustrative."""
    recs = []
    # tier -> (n, win_rate, entry, close)
    spec = {
        "TIER_S": (40, 0.62, 2.10, 1.95),
        "TIER_A": (40, 0.55, 2.30, 2.20),
        "TIER_B": (40, 0.49, 2.50, 2.48),
        "TIER_C": (40, 0.44, 2.70, 2.75),
        "REJECT": (40, 0.40, 2.90, 3.10),
    }
    for tier, (n, wr, entry, close) in spec.items():
        wins = round(n * wr)
        for i in range(n):
            outcome = 1 if i < wins else 0
            recs.append(SettledRecord(
                match_id=f"{tier}_{i}", selection="HOME",
                model_probability=wr, market_probability=1.0 / entry,
                entry_odds=entry, closing_odds=close, outcome=outcome, tier=tier,
            ))
    return HistoricalValidator().evaluate(recs, by="tier")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="R1.3 edge kernel runner")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    measurement = MeasurementPipeline().run(*build_fixture(), consensus_horizon=Horizon.H1)
    edges = EdgeDetectionKernel().run(measurement, MODEL_PROBS, SEGMENT_META)
    validation = demo_validation()

    report = {
        "edges": {f"{k[0]}|{k[1]}|{k[2]}": v.to_dict() for k, v in edges.items()},
        "historical_validation_by_tier": {k: v.to_dict() for k, v in validation.items()},
    }
    payload = json.dumps(report, indent=2, default=str)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
    if args.pretty:
        print(human_summary(edges))
        print("\n-- historical validation (by tier) --")
        for tier, mt in validation.items():
            if mt.n:
                print(f"  {tier:<8} n={mt.n:<3} ROI={_f(mt.roi)!s:<9} CLV={_f(mt.clv)!s:<8} "
                      f"beat={_f(mt.pct_beat_close)!s:<6} Brier(m/k)={_f(mt.brier_model)}/{_f(mt.brier_market)} "
                      f"ECE={_f(mt.calibration_error)}")
        print("\n--- full JSON report below ---\n")
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
