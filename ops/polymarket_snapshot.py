"""
ops/polymarket_snapshot.py — Polymarket Market Intelligence Snapshot Tool (WP-POLY-1)

Data flow:
  shadow_predictions.jsonl → fixture list
       ↓  PolymarketProvider.find_market()
  Polymarket Gamma API → MarketInfo
       ↓  PolymarketProvider.get_snapshot()
  Polymarket Gamma + CLOB APIs → MarketSnapshot
       ↓  write
  data/polymarket/snapshots/{date}.jsonl   (pre-match snapshots, appendable)
  data/polymarket/closing_snapshots.jsonl  (IMMUTABLE once written)

Observer mode only. Zero impact on prediction engine.

Usage:
  python -m ops.polymarket_snapshot --date 2026-06-26 --mode pre_match
  python -m ops.polymarket_snapshot --date 2026-06-26 --mode closing
  python -m ops.polymarket_snapshot --mode settle [--date 2026-06-26]
  python -m ops.polymarket_snapshot --mode report [--date 2026-06-26]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
_DATA_DIR = _ROOT / "data" / "polymarket"
_SNAPSHOTS_DIR = _DATA_DIR / "snapshots"
_CLOSING_FILE = _DATA_DIR / "closing_snapshots.jsonl"
_SHADOW_PREDS = _ROOT / "data" / "shadow_predictions.jsonl"
_SHADOW_SETTLE = _ROOT / "data" / "shadow_settlements.jsonl"


def _ensure_dirs() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_predictions_for_date(date_str: str) -> list[dict]:
    """Load today's shadow predictions from JSONL."""
    if not _SHADOW_PREDS.exists():
        return []
    records = []
    with _SHADOW_PREDS.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("match_date", "").startswith(date_str):
                    records.append(rec)
            except json.JSONDecodeError:
                continue
    return records


def load_all_settlements() -> list[dict]:
    """Load all settled matches from shadow_settlements.jsonl."""
    if not _SHADOW_SETTLE.exists():
        return []
    records = []
    with _SHADOW_SETTLE.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def load_closing_snapshots() -> dict[str, dict]:
    """Load all immutable closing snapshots keyed by market_id."""
    if not _CLOSING_FILE.exists():
        return {}
    snaps: dict[str, dict] = {}
    with _CLOSING_FILE.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                mid = rec.get("market_id")
                if mid:
                    snaps[mid] = rec
            except json.JSONDecodeError:
                continue
    return snaps


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def snapshot_to_dict(snap, market_info) -> dict:
    """Convert MarketSnapshot + MarketInfo to a JSON-serialisable dict."""
    return {
        "provider": snap.provider,
        "market_id": snap.market_id,
        "event_id": market_info.event_id,
        "question": market_info.question,
        "slug": market_info.slug,
        "status": market_info.status,
        "matched_home": snap.matched_home,
        "matched_away": snap.matched_away,
        "match_date": snap.match_date,
        "home_prob": snap.home_prob,
        "draw_prob": snap.draw_prob,
        "away_prob": snap.away_prob,
        "volume_24h": snap.volume_24h,
        "liquidity": snap.liquidity,
        "open_interest": snap.open_interest,
        "timestamp": snap.timestamp,
        "is_closing": snap.is_closing,
        "source_type": snap.source_type,
        "outcomes": [
            {
                "label": o.label,
                "role": o.role,
                "mid_price": o.mid_price,
                "best_bid": o.best_bid,
                "best_ask": o.best_ask,
                "spread": o.spread,
            }
            for o in snap.outcomes
        ],
    }


# ---------------------------------------------------------------------------
# Core snapshot runner
# ---------------------------------------------------------------------------

def run_snapshot(date_str: str, source_type: str = "pre_match") -> dict:
    """
    Take Polymarket market snapshots for all fixtures on date_str.
    Returns summary stats dict.
    """
    from src.integrations.polymarket.mapper import PolymarketProvider

    _ensure_dirs()
    provider = PolymarketProvider()
    predictions = load_predictions_for_date(date_str)

    if not predictions:
        logger.warning("No shadow predictions found for %s — skipping", date_str)
        return {"date": date_str, "mode": source_type, "written": 0, "failed": 0, "no_predictions": True}

    snap_file = _SNAPSHOTS_DIR / f"{date_str}.jsonl"
    closing_snaps = load_closing_snapshots()
    closing_updated: list[str] = []

    written = failed = 0

    with snap_file.open("a") as snap_out:
        closing_out = _CLOSING_FILE.open("a")
        try:
            for pred in predictions:
                home = pred.get("home_team") or pred.get("home") or ""
                away = pred.get("away_team") or pred.get("away") or ""
                if not home or not away:
                    continue

                market = provider.find_market(home, away, date_str)
                if not market:
                    logger.warning("No Polymarket market for %s vs %s", home, away)
                    failed += 1
                    continue

                snap = provider.get_snapshot(market, source_type=source_type)
                if not snap:
                    logger.warning("Snapshot failed for %s [%s]", market.question, market.market_id)
                    failed += 1
                    continue

                rec = snapshot_to_dict(snap, market)
                snap_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
                logger.info(
                    "%s | %s vs %s → H=%.1f%% D=%s A=%.1f%%",
                    source_type.upper(),
                    home, away,
                    (snap.home_prob or 0) * 100,
                    f"{(snap.draw_prob or 0)*100:.1f}%" if snap.draw_prob else "N/A",
                    (snap.away_prob or 0) * 100,
                )

                # Immutable closing snapshot — never overwrite
                if source_type == "closing" and market.market_id not in closing_snaps:
                    closing_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    closing_snaps[market.market_id] = rec
                    closing_updated.append(f"{home} vs {away}")
                    logger.info("Closing snapshot saved: %s vs %s", home, away)
        finally:
            closing_out.close()

    summary = {
        "date": date_str,
        "mode": source_type,
        "written": written,
        "failed": failed,
        "closing_saved": closing_updated,
    }
    logger.info("Snapshot done: %s", summary)
    return summary


# ---------------------------------------------------------------------------
# Settlement comparison
# ---------------------------------------------------------------------------

def run_settle(date_str: str | None = None) -> list[dict]:
    """
    Join settled fixtures with their Polymarket closing snapshots.
    Returns benchmark records (does not modify settlement data).
    """
    from reports.polymarket_report import build_benchmark_records

    settlements = load_all_settlements()
    if date_str:
        settlements = [s for s in settlements if s.get("match_date") == date_str]

    closing_snaps = load_closing_snapshots()
    records = build_benchmark_records(settlements, closing_snaps)
    logger.info("Settlement comparison: %d records", len(records))
    return records


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Polymarket Market Intelligence Observer (WP-POLY-1)"
    )
    ap.add_argument(
        "--date", type=str, default=None,
        help="Date YYYY-MM-DD (default: today UTC)"
    )
    ap.add_argument(
        "--mode", type=str, default="pre_match",
        choices=["pre_match", "closing", "settle", "report"],
        help="Operation mode",
    )
    args = ap.parse_args()

    date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if args.mode in ("pre_match", "closing"):
        summary = run_snapshot(date_str, source_type=args.mode)
        print(f"\n✅ Snapshot ({args.mode}) — {date_str}")
        print(f"   Written  : {summary['written']}")
        print(f"   Failed   : {summary['failed']}")
        if summary.get("closing_saved"):
            print(f"   Closing  : {', '.join(summary['closing_saved'])}")

    elif args.mode == "settle":
        records = run_settle(date_str)
        print(f"\n📊 Benchmark records: {len(records)}")
        for r in records[:10]:
            mkt = r.get("market_h")
            print(
                f"  {r['home_team']} vs {r['away_team']}  |  "
                f"actual={r['actual_outcome']}  |  "
                f"model={r['model_prediction']} ({r['model_confidence']:.0f}%)  |  "
                f"market={'—' if mkt is None else r['market_prediction']}"
            )

    elif args.mode == "report":
        from reports.polymarket_report import generate_all_reports
        generate_all_reports(date_str)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
