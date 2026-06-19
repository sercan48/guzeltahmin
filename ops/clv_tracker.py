"""
CLV Tracker — Closing Line Value hesaplama ve raporlama.

Her settlement sonrası çalışır:
  1. shadow_settlements.jsonl  → gerçek sonuçlar
  2. shadow_predictions.jsonl  → tahmin anındaki oranlar
  Cross-match yaparak CLV hesaplar.

CLV formülü (Buchdahl 2016):
  market_implied_prob = 1 / market_odds
  clv = predicted_prob - market_implied_prob
  Pozitif → model piyasadan daha fazla ihtimal verdi (edge bulundu).

Çıktı:
  data/clv_log.jsonl       — maç bazlı CLV kayıtları
  data/clv_summary.json    — özet metrikler (ortalama CLV, % pozitif)

Kullanım:
  python ops/clv_tracker.py           # stdout rapor
  python ops/clv_tracker.py --update  # clv_log.jsonl güncelle
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR        = Path(__file__).parent.parent / "data"
SETTLEMENTS_F   = DATA_DIR / "shadow_settlements.jsonl"
PREDICTIONS_F   = DATA_DIR / "shadow_predictions.jsonl"
CLV_LOG_F       = DATA_DIR / "clv_log.jsonl"
CLV_SUMMARY_F   = DATA_DIR / "clv_summary.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    return rows


def _norm(name: str) -> str:
    return name.lower().strip()


def _match_key(home: str, away: str) -> str:
    return f"{_norm(home)}||{_norm(away)}"


def _clv(predicted_prob: float, market_odds: float | None) -> float | None:
    """CLV = predicted_prob - (1 / market_odds). None if no odds."""
    if not market_odds or market_odds <= 1.0:
        return None
    return round(predicted_prob - (1.0 / market_odds), 4)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def compute_clv_records() -> list[dict]:
    """
    Cross-reference settlements with predictions to build CLV records.
    Returns list of dicts, one per settled match with known prediction.
    """
    settlements = _load_jsonl(SETTLEMENTS_F)
    predictions = _load_jsonl(PREDICTIONS_F)

    # Build prediction lookup: match_key → prediction entry
    pred_map: dict[str, dict] = {}
    for p in predictions:
        key = _match_key(p.get("home", ""), p.get("away", ""))
        pred_map[key] = p  # son yazılan kazanır (aynı maçın tekrar tahmini)

    records = []
    for s in settlements:
        home   = s.get("home", "")
        away   = s.get("away", "")
        actual = s.get("actual", "")
        if not actual or not home:
            continue

        key  = _match_key(home, away)
        pred = pred_map.get(key)
        if not pred:
            continue

        raw       = pred.get("prediction", "")
        mkt_key   = {"HOME_WIN": "market_odds_h", "DRAW": "market_odds_d", "AWAY_WIN": "market_odds_a"}.get(raw)
        prob_key  = {"HOME_WIN": "predicted_prob_home", "DRAW": "predicted_prob_draw", "AWAY_WIN": "predicted_prob_away"}.get(raw)
        mkt_odds  = pred.get(mkt_key) if mkt_key else None
        pred_prob = pred.get(prob_key) if prob_key else None

        clv_val = _clv(pred_prob, mkt_odds) if pred_prob is not None else None
        correct = (raw == actual)

        records.append({
            "date":           s.get("date"),
            "home":           home,
            "away":           away,
            "prediction":     raw,
            "actual":         actual,
            "correct":        correct,
            "confidence":     pred.get("confidence"),
            "tier":           pred.get("tier"),
            "is_sniper":      pred.get("is_sniper", False),
            "predicted_prob": pred_prob,
            "market_odds":    mkt_odds,
            "market_implied": round(1.0 / mkt_odds, 4) if mkt_odds else None,
            "clv":            clv_val,
            "clv_positive":   (clv_val > 0) if clv_val is not None else None,
        })

    return records


def build_summary(records: list[dict]) -> dict:
    n = len(records)
    if not n:
        return {"n": 0, "note": "Henüz CLV kaydı yok"}

    with_clv    = [r for r in records if r["clv"] is not None]
    positive    = [r for r in with_clv if r["clv_positive"]]
    correct     = [r for r in records if r["correct"]]
    snipers     = [r for r in records if r["is_sniper"]]
    sniper_ok   = [r for r in snipers if r["correct"]]

    avg_clv = round(sum(r["clv"] for r in with_clv) / len(with_clv), 4) if with_clv else None
    pct_pos = round(len(positive) / len(with_clv) * 100, 1) if with_clv else None

    return {
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "n":                 n,
        "n_with_odds":       len(with_clv),
        "avg_clv":           avg_clv,
        "pct_clv_positive":  pct_pos,
        "accuracy_all":      round(len(correct) / n * 100, 1),
        "n_sniper":          len(snipers),
        "sniper_accuracy":   round(len(sniper_ok) / len(snipers) * 100, 1) if snipers else None,
        "interpretation": (
            "CLV pozitif ortalama → model piyasadan önde" if avg_clv and avg_clv > 0
            else "CLV negatif → model piyasaya göre geri"
        ),
        "stat_note": (
            f"n={n} istatistiksel anlam taşımıyor (n≥500 gerekli kâr/zarar için). "
            f"CLV n={len(with_clv)} pick ile ön sinyal verir."
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="CLV Tracker — Closing Line Value raporu")
    p.add_argument("--update", action="store_true", help="clv_log.jsonl ve clv_summary.json yaz")
    args = p.parse_args()

    records = compute_clv_records()
    summary = build_summary(records)

    print(f"\n{'='*55}")
    print(f"  CLV TRACKER — {summary.get('generated_at', '')[:10]}")
    print(f"{'='*55}")
    print(f"  Toplam kayıt      : {summary.get('n', 0)}")
    print(f"  Oranı olan pick   : {summary.get('n_with_odds', 0)}")
    print(f"  Ortalama CLV      : {summary.get('avg_clv', '—')}")
    print(f"  % Pozitif CLV     : {summary.get('pct_clv_positive', '—')}%")
    print(f"  Doğruluk          : {summary.get('accuracy_all', '—')}%")
    print(f"  Sniper pick       : {summary.get('n_sniper', 0)}")
    print(f"  Sniper doğruluk   : {summary.get('sniper_accuracy', '—')}%")
    print(f"  Not               : {summary.get('stat_note', '')}")
    print(f"{'='*55}\n")

    if records:
        print("Son 5 kayıt:")
        for r in records[-5:]:
            clv_str = f"{r['clv']:+.3f}" if r['clv'] is not None else "—"
            ok = "✓" if r["correct"] else "✗"
            sn = "⭐" if r["is_sniper"] else " "
            print(f"  {sn}{ok} {r['date']} {r['home'][:12]:<12} vs {r['away'][:12]:<12}  "
                  f"{r['prediction']:<9}  CLV={clv_str}")

    if args.update:
        DATA_DIR.mkdir(exist_ok=True)
        with open(CLV_LOG_F, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        CLV_SUMMARY_F.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        print(f"\n[CLV] {len(records)} kayıt → {CLV_LOG_F}")
        print(f"[CLV] Özet → {CLV_SUMMARY_F}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
