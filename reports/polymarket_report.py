"""
reports/polymarket_report.py — Polymarket Benchmark Report Generator (WP-POLY-1)

Generates:
  POLYMARKET_DAILY_REPORT_{date}.json  — per-date market snapshot summary
  POLYMARKET_BENCHMARK.json            — aggregate model vs market accuracy
  POLYMARKET_DELTA_REPORT.json         — probability delta analysis
  POLYMARKET_CLOSING_SNAPSHOTS.jsonl   — managed by ops/polymarket_snapshot.py

All reports are OBSERVATIONAL ONLY.
Do NOT use these outputs to recalibrate or modify prediction engine logic.
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
_DATA_DIR = _ROOT / "data" / "polymarket"
_OUT_DIR = _DATA_DIR / "reports"
_SNAPSHOTS_DIR = _DATA_DIR / "snapshots"
_CLOSING_FILE = _DATA_DIR / "closing_snapshots.jsonl"


def _ensure_dirs() -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _brier_contrib(prob_of_correct: float) -> float:
    """Brier score contribution for one outcome: (1 - p_correct)²."""
    return round((1.0 - max(0.0, min(1.0, prob_of_correct))) ** 2, 6)


def _prob_of_actual(h: float, d: float, a: float, actual: str) -> float:
    """Return the probability (0-1) assigned to the actual outcome."""
    mapping = {"HOME_WIN": h / 100.0, "DRAW": d / 100.0, "AWAY_WIN": a / 100.0}
    return mapping.get(actual, 0.0)


def _top_outcome(h: float | None, d: float | None, a: float | None) -> str | None:
    """Return the outcome with the highest probability, or None if all None."""
    probs = {}
    if h is not None:
        probs["HOME_WIN"] = h
    if d is not None:
        probs["DRAW"] = d
    if a is not None:
        probs["AWAY_WIN"] = a
    return max(probs, key=probs.get) if probs else None  # type: ignore[arg-type]


def _safe_mean(lst: list[float]) -> float | None:
    return round(statistics.mean(lst), 4) if lst else None


def _safe_stdev(lst: list[float]) -> float | None:
    return round(statistics.stdev(lst), 4) if len(lst) >= 2 else None


def _distribution_stats(values: list[float]) -> dict:
    if not values:
        return {}
    return {
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "mean": round(statistics.mean(values), 4),
        "median": round(statistics.median(values), 4),
        "stdev": _safe_stdev(values),
        "n": len(values),
    }


# ---------------------------------------------------------------------------
# Benchmark record builder
# ---------------------------------------------------------------------------

def build_benchmark_records(
    settlements: list[dict],
    closing_snaps: dict[str, dict],
) -> list[dict]:
    """
    Join settled match records with their Polymarket closing snapshots.
    Returns a list of flat benchmark dicts ready for report generation.

    Args:
        settlements:   Records from shadow_settlements.jsonl
        closing_snaps: {market_id: snap_dict} from closing_snapshots.jsonl

    Returns:
        List of benchmark record dicts (no side effects, no writes).
    """
    # Index closing snapshots by normalised team-name key
    snap_by_key: dict[str, dict] = {}
    for snap in closing_snaps.values():
        mh = (snap.get("matched_home") or "").lower().strip()
        ma = (snap.get("matched_away") or "").lower().strip()
        md = snap.get("match_date") or ""
        if mh and ma and md:
            snap_by_key[f"{mh}|{ma}|{md}"] = snap

    records: list[dict] = []

    for s in settlements:
        nat_key = (
            s.get("natural_key")
            or (
                f"{s.get('home_team','').lower()}"
                f"|{s.get('away_team','').lower()}"
                f"|{s.get('match_date','')}"
            )
        ).lower().strip()

        home = s.get("home_team", "")
        away = s.get("away_team", "")
        date = s.get("match_date", "")
        actual = s.get("actual_outcome", "")
        probs = s.get("probabilities") or {}

        model_h = float(probs.get("H", 0))
        model_d = float(probs.get("D", 0))
        model_a = float(probs.get("A", 0))
        model_pred = s.get("predicted_outcome", "")
        model_conf = float(s.get("confidence", 0))
        model_correct = bool(s.get("correct", False))

        model_p_actual = _prob_of_actual(model_h, model_d, model_a, actual)
        model_brier = _brier_contrib(model_p_actual)
        model_abs_err = round(abs(model_p_actual - 1.0), 6)

        # Match closing snapshot
        closing = snap_by_key.get(nat_key)
        market_h = market_d = market_a = None
        market_pred = market_correct = None
        market_brier = market_abs_err = None
        market_source = "polymarket"
        closing_snap_id = ""

        if closing:
            closing_snap_id = closing.get("market_id", "")
            rh = closing.get("home_prob")
            rd = closing.get("draw_prob")
            ra = closing.get("away_prob")
            market_h = round(float(rh) * 100, 2) if rh is not None else None
            market_d = round(float(rd) * 100, 2) if rd is not None else None
            market_a = round(float(ra) * 100, 2) if ra is not None else None
            market_pred = _top_outcome(market_h, market_d, market_a)
            market_correct = (market_pred == actual) if market_pred else None

            m_p_actual = _prob_of_actual(
                market_h or 0, market_d or 0, market_a or 0, actual
            )
            market_brier = _brier_contrib(m_p_actual)
            market_abs_err = round(abs(m_p_actual - 1.0), 6)

        delta_h = round(model_h - market_h, 2) if market_h is not None else None
        delta_d = round(model_d - market_d, 2) if market_d is not None else None
        delta_a = round(model_a - market_a, 2) if market_a is not None else None

        records.append({
            "natural_key": nat_key,
            "home_team": home,
            "away_team": away,
            "match_date": date,
            "actual_outcome": actual,
            # Model
            "model_h": model_h,
            "model_d": model_d,
            "model_a": model_a,
            "model_prediction": model_pred,
            "model_confidence": model_conf,
            "model_correct": model_correct,
            "model_brier": model_brier,
            "model_abs_error": model_abs_err,
            # Market
            "market_h": market_h,
            "market_d": market_d,
            "market_a": market_a,
            "market_prediction": market_pred,
            "market_correct": market_correct,
            "market_brier": market_brier,
            "market_abs_error": market_abs_err,
            "market_source": market_source,
            # Deltas (model − market, pp)
            "delta_h": delta_h,
            "delta_d": delta_d,
            "delta_a": delta_a,
            "closing_snapshot_id": closing_snap_id,
        })

    return records


# ---------------------------------------------------------------------------
# Report generators
# ---------------------------------------------------------------------------

def generate_daily_report(date_str: str, snapshots: list[dict]) -> dict:
    """
    Generate POLYMARKET_DAILY_REPORT_{date}.json from today's market snapshots.
    """
    _ensure_dirs()

    matched = [s for s in snapshots if s.get("home_prob") is not None]
    h_probs = [s["home_prob"] for s in matched]
    d_probs = [s["draw_prob"] for s in matched if s.get("draw_prob") is not None]
    a_probs = [s["away_prob"] for s in matched if s.get("away_prob") is not None]
    vols = [s["volume_24h"] for s in matched if s.get("volume_24h")]
    liqs = [s["liquidity"] for s in matched if s.get("liquidity")]

    report = {
        "report_type": "POLYMARKET_DAILY_REPORT",
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_fixtures": len(snapshots),
        "n_matched": len(matched),
        "n_unmatched": len(snapshots) - len(matched),
        "avg_home_prob": _safe_mean(h_probs),
        "avg_draw_prob": _safe_mean(d_probs),
        "avg_away_prob": _safe_mean(a_probs),
        "total_volume_24h_usd": round(sum(vols), 2) if vols else None,
        "avg_liquidity_usd": _safe_mean(liqs),
        "snapshots": snapshots,
    }

    out = _OUT_DIR / f"POLYMARKET_DAILY_REPORT_{date_str}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    logger.info("Daily report → %s", out)
    return report


def generate_benchmark(records: list[dict]) -> dict:
    """
    Generate POLYMARKET_BENCHMARK.json comparing model vs market across all settled fixtures.
    OBSERVATIONAL ONLY.
    """
    _ensure_dirs()

    paired = [r for r in records if r.get("market_h") is not None]
    n_total = len(records)
    n_paired = len(paired)

    model_acc = (
        round(sum(1 for r in records if r["model_correct"]) / n_total, 4)
        if n_total else None
    )
    market_acc = (
        round(sum(1 for r in paired if r["market_correct"]) / n_paired, 4)
        if n_paired else None
    )

    model_briefs = [r["model_brier"] for r in records if r.get("model_brier") is not None]
    mkt_briefs = [r["market_brier"] for r in paired if r.get("market_brier") is not None]

    # Draw analysis (OBSERVATIONAL — do not recalibrate)
    draw_settled = [r for r in paired if r.get("actual_outcome") == "DRAW"]
    model_d_vals = [r["model_d"] for r in draw_settled if r.get("model_d") is not None]
    mkt_d_vals = [r["market_d"] for r in draw_settled if r.get("market_d") is not None]

    draw_corr = None
    if len(model_d_vals) >= 3 and len(mkt_d_vals) >= 3:
        try:
            draw_corr = round(statistics.correlation(model_d_vals, mkt_d_vals), 4)
        except Exception:
            pass

    benchmark = {
        "report_type": "POLYMARKET_BENCHMARK",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_settled_total": n_total,
        "n_with_market_data": n_paired,
        "model": {
            "accuracy": model_acc,
            "brier_mean": _safe_mean(model_briefs),
        },
        "market": {
            "accuracy": market_acc,
            "brier_mean": _safe_mean(mkt_briefs),
            "source": "polymarket",
        },
        "draw_analysis": {
            "n_draw_outcomes": len(draw_settled),
            "model_draw_prob": _distribution_stats(model_d_vals),
            "market_draw_prob": _distribution_stats(mkt_d_vals),
            "model_vs_market_draw_mean_diff": (
                round((_safe_mean(model_d_vals) or 0) - (_safe_mean(mkt_d_vals) or 0), 4)
                if model_d_vals and mkt_d_vals else None
            ),
            "draw_prob_correlation": draw_corr,
            "note": "OBSERVATIONAL ONLY — do not recalibrate from this data",
        },
        "records": records,
    }

    out = _OUT_DIR / "POLYMARKET_BENCHMARK.json"
    out.write_text(json.dumps(benchmark, indent=2, ensure_ascii=False))
    logger.info("Benchmark report → %s", out)
    return benchmark


def generate_delta_report(records: list[dict]) -> dict:
    """
    Generate POLYMARKET_DELTA_REPORT.json: model vs market probability differences.
    """
    _ensure_dirs()

    paired = [r for r in records if r.get("delta_h") is not None]

    dh = [r["delta_h"] for r in paired if r["delta_h"] is not None]
    dd = [r["delta_d"] for r in paired if r.get("delta_d") is not None]
    da = [r["delta_a"] for r in paired if r.get("delta_a") is not None]

    def _match_abs_delta(r: dict) -> float:
        pred = r.get("model_prediction", "")
        if pred == "HOME_WIN":
            return abs(r.get("delta_h") or 0)
        if pred == "DRAW":
            return abs(r.get("delta_d") or 0)
        if pred == "AWAY_WIN":
            return abs(r.get("delta_a") or 0)
        return 0.0

    by_delta = sorted(paired, key=_match_abs_delta, reverse=True)

    top_disagreements = [
        {
            "match": f"{r['home_team']} vs {r['away_team']}",
            "date": r["match_date"],
            "model_prediction": r.get("model_prediction"),
            "market_prediction": r.get("market_prediction"),
            "actual_outcome": r.get("actual_outcome"),
            "model_h": r.get("model_h"),
            "model_d": r.get("model_d"),
            "model_a": r.get("model_a"),
            "market_h": r.get("market_h"),
            "market_d": r.get("market_d"),
            "market_a": r.get("market_a"),
            "delta_h": r.get("delta_h"),
            "delta_d": r.get("delta_d"),
            "delta_a": r.get("delta_a"),
        }
        for r in by_delta[:10]
    ]

    top_agreements = [
        {
            "match": f"{r['home_team']} vs {r['away_team']}",
            "date": r["match_date"],
            "model_prediction": r.get("model_prediction"),
            "market_prediction": r.get("market_prediction"),
            "actual_outcome": r.get("actual_outcome"),
            "delta_h": r.get("delta_h"),
            "delta_d": r.get("delta_d"),
            "delta_a": r.get("delta_a"),
        }
        for r in reversed(by_delta[-10:])
    ]

    report = {
        "report_type": "POLYMARKET_DELTA_REPORT",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_paired": len(paired),
        "delta_h_pp": {
            "mean": _safe_mean(dh),
            "stdev": _safe_stdev(dh),
            "min": round(min(dh), 2) if dh else None,
            "max": round(max(dh), 2) if dh else None,
        },
        "delta_d_pp": {
            "mean": _safe_mean(dd),
            "stdev": _safe_stdev(dd),
            "min": round(min(dd), 2) if dd else None,
            "max": round(max(dd), 2) if dd else None,
        },
        "delta_a_pp": {
            "mean": _safe_mean(da),
            "stdev": _safe_stdev(da),
            "min": round(min(da), 2) if da else None,
            "max": round(max(da), 2) if da else None,
        },
        "top_disagreements": top_disagreements,
        "top_agreements": top_agreements,
        "note": "OBSERVATIONAL ONLY — deltas must not feed back into prediction engine",
    }

    out = _OUT_DIR / "POLYMARKET_DELTA_REPORT.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    logger.info("Delta report → %s", out)
    return report


def generate_all_reports(date_str: str) -> None:
    """Generate all three Polymarket benchmark reports for a given date."""
    from ops.polymarket_snapshot import (
        load_all_settlements,
        load_closing_snapshots,
    )

    _ensure_dirs()

    # Daily snapshot summary
    snap_file = _SNAPSHOTS_DIR / f"{date_str}.jsonl"
    daily_snaps: list[dict] = []
    if snap_file.exists():
        with snap_file.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        daily_snaps.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    generate_daily_report(date_str, daily_snaps)

    # Benchmark & delta (all settled fixtures)
    settlements = [
        s for s in load_all_settlements()
        if not date_str or s.get("match_date") == date_str
    ]
    closing_snaps = load_closing_snapshots()
    records = build_benchmark_records(settlements, closing_snaps)
    generate_benchmark(records)
    generate_delta_report(records)

    print(f"\n✅ Polymarket reports generated → {_OUT_DIR}")
    print(f"   Settlements : {len(settlements)}")
    print(f"   Benchmarked : {sum(1 for r in records if r['market_h'] is not None)}")
