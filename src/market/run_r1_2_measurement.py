"""R1.2 measurement-layer runner.

Builds the deterministic 5-match fixture, runs the full measurement pipeline and
emits a JSON report to stdout (and optionally a file). Pure computation — no
network, no ML.

Usage:
    python3 -m src.market.run_r1_2_measurement            # JSON to stdout
    python3 -m src.market.run_r1_2_measurement --pretty   # human summary too
"""

from __future__ import annotations

import argparse
import json
import sys

from .fixtures import build_fixture, _LABELS
from .measurement_pipeline import MeasurementPipeline
from .schema import Horizon


def _fmt(x, nd=4):
    return None if x is None else round(x, nd)


def human_summary(result, contexts) -> str:
    lines = []
    lines.append("=" * 72)
    lines.append("R1.2 LIVE MARKET MEASUREMENT — SUMMARY")
    lines.append("=" * 72)
    lines.append(
        f"records={result.n_records}  book_series={result.n_book_series}  "
        f"consensus_series={result.n_consensus_series}"
    )

    lines.append("\n-- DRIFT (consensus HOME selection per match) --")
    for mid, label in _LABELS.items():
        k = f"{mid}|1X2|HOME@consensus"
        d = result.drift.get(k)
        if not d:
            continue
        lines.append(
            f"  {label:<26} total_drift={_fmt(d.total_drift)!s:<9} "
            f"dir={d.direction:<10} vel={_fmt(d.drift_velocity)!s:<9} "
            f"acc={_fmt(d.drift_acceleration)}"
        )

    lines.append("\n-- CLV (consensus HOME, provisional close) --")
    for mid, label in _LABELS.items():
        k = f"{mid}|1X2|HOME@consensus"
        c = result.clv_consensus.get(k)
        if not c:
            continue
        lines.append(
            f"  {label:<26} status={c.status:<11} entry={_fmt(c.entry_odds)!s:<6} "
            f"close={_fmt(c.closing_odds)!s:<6} CLV_raw={_fmt(c.clv_raw)!s:<9} "
            f"backer={_fmt(c.clv_backer)!s:<8} wCLV={_fmt(c.weighted_clv)}"
        )

    lines.append("\n-- EFFICIENCY (1X2 per match) --")
    for mid, label in _LABELS.items():
        e = result.efficiency.get(f"{mid}|1X2")
        if not e:
            continue
        lines.append(
            f"  {label:<26} consensus_score={_fmt(e.market_consensus_score)!s:<8} "
            f"disagreement={_fmt(e.bookmaker_disagreement_index)!s:<9} "
            f"overround={_fmt(e.mean_overround)!s:<7} "
            f"sharp[{e.sharp_proxy_selection}]={_fmt(e.sharp_proxy_signal)}"
        )

    lines.append("\n-- DATA INTEGRITY --")
    lines.append(f"  series_checked={result.integrity.series_checked} "
                 f"total_flags={len(result.integrity.flags)}")
    for chk, n in sorted(result.integrity.counts.items()):
        lines.append(f"    {chk:<28} {n}")
    lines.append("  flag detail:")
    for f in result.integrity.flags:
        lines.append(f"    [{f.severity:<5}] {f.check:<26} {f.key_str}  — {f.detail}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="R1.2 market measurement runner")
    parser.add_argument("--pretty", action="store_true", help="print human summary")
    parser.add_argument("--out", help="write JSON report to this path")
    args = parser.parse_args(argv)

    records, contexts = build_fixture()
    pipeline = MeasurementPipeline()
    result = pipeline.run(records, contexts, consensus_horizon=Horizon.H1)

    report = result.to_dict()
    payload = json.dumps(report, indent=2, default=str)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)

    if args.pretty:
        print(human_summary(result, contexts))
        print("\n--- full JSON report below ---\n")
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
