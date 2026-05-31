"""Calibration Audit and Backtest Runner.

Runs the HistoricalReplayEngine on summer leagues and prints/saves the quantitative results.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.evaluator.historical_replay_engine import HistoricalReplayEngine

def run_calibration_audit():
    db = get_backend()
    db.connect()

    print("=" * 60)
    print("  SUMMER LEAGUE CALIBRATION AUDIT & BACKTEST")
    print("=" * 60)

    replay = HistoricalReplayEngine(db)

    # Leagues to audit
    leagues = ["NORWAY_ELITESERIEN", "BRAZIL_SERIE_A"]
    results = {}

    for league in leagues:
        print(f"\nRunning time-aware backtest on {league}...")
        res = replay.run_replay(league, limit=300) # Replaying last 300 finished matches
        if res:
            results[league] = res
            print(f"Completed {res['samples']} matches.")
            print(f"  - Brier Score: {res['brier_score']:.4f}")
            print(f"  - Log Loss   : {res['log_loss']:.4f}")
            print(f"  - ECE (Error): {res['ece']:.4f}")
            bet = res["betting"]
            print(f"  - Bets Played: {bet['play_count']} | Wins: {bet['win_count']} | Voids: {bet['refund_count']}")
            print(f"  - Hit Rate   : {bet['hit_rate_pct']:.1f}%")
            print(f"  - ROI        : {bet['roi_pct']:.1f}%")
        else:
            print(f"No results for {league}")

    db.close()

    # Generate Markdown Report
    report_lines = [
        "# Summer League Calibration Audit & Backtest Report\n",
        "This report summarizes the time-aware historical replay backtest and calibration metrics for the summer leagues.\n",
        "## Performance Metrics\n",
        "| League | Matches Replayed | Brier Score | Log Loss | ECE (Calibration Error) | ROI (%) | Hit Rate (%) | Played Bets |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for league, res in results.items():
        bet = res["betting"]
        report_lines.append(
            f"| {league} | {res['samples']} | {res['brier_score']:.4f} | {res['log_loss']:.4f} | {res['ece']:.4f} | {bet['roi_pct']:.1f}% | {bet['hit_rate_pct']:.1f}% | {bet['play_count']} |"
        )

    report_lines.append("\n## Detailed Betting Stats\n")
    for league, res in results.items():
        bet = res["betting"]
        report_lines.extend([
            f"### {league}",
            f"- **Total Bets Placed:** {bet['play_count']}",
            f"- **Win Count:** {bet['win_count']}",
            f"- **Refund Count (Void DNB):** {bet['refund_count']}",
            f"- **Total Stake:** {bet['total_stake']} units",
            f"- **Total Profit:** {bet['total_profit']} units",
            f"- **ROI:** **{bet['roi_pct']:.2f}%**",
            f"- **Hit Rate (excluding voids):** **{bet['hit_rate_pct']:.2f}%**\n"
        ])

    report_content = "\n".join(report_lines)

    # Save to scratch folder
    output_path = Path("scratch/calibration_report.md")
    output_path.write_text(report_content, encoding="utf-8")
    print(f"\n[OK] Audit report saved to {output_path}")

if __name__ == "__main__":
    run_calibration_audit()
