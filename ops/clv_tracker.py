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

    # Build prediction lookup — natural_key önce, sonra takım adı
    pred_by_nk:  dict[str, dict] = {}
    pred_by_key: dict[str, dict] = {}
    for p in predictions:
        nk = p.get("natural_key")
        if nk:
            pred_by_nk[nk] = p
        home = p.get("home_team") or p.get("home", "")
        away = p.get("away_team") or p.get("away", "")
        if home and away:
            pred_by_key[_match_key(home, away)] = p

    records = []
    for s in settlements:
        home   = s.get("home_team", "")
        away   = s.get("away_team", "")
        actual = s.get("actual_outcome", "")
        if not actual or not home:
            continue

        # Prediction kaydını bul
        nk   = s.get("natural_key")
        pred = pred_by_nk.get(nk) if nk else None
        if not pred:
            pred = pred_by_key.get(_match_key(home, away))
        if not pred:
            continue

        # Ana tahmin
        raw = pred.get("predicted_outcome") or pred.get("prediction", "")
        if not raw:
            continue

        # Olasılık — önce probabilities dict, sonra bireysel alanlar
        probs = pred.get("probabilities", {})
        prob_letter = {"HOME_WIN": "H", "DRAW": "D", "AWAY_WIN": "A"}.get(raw)
        if probs and prob_letter:
            pred_prob_pct = probs.get(prob_letter)
        else:
            prob_field = {
                "HOME_WIN": "predicted_prob_home",
                "DRAW":     "predicted_prob_draw",
                "AWAY_WIN": "predicted_prob_away",
            }.get(raw)
            raw_val = pred.get(prob_field) if prob_field else None
            # Fraction (0-1) veya pct (0-100) olabilir
            if raw_val is not None:
                pred_prob_pct = raw_val * 100 if raw_val <= 1.0 else raw_val
            else:
                pred_prob_pct = None

        pred_prob_frac = pred_prob_pct / 100.0 if pred_prob_pct is not None else None

        # Piyasa oranları — bulletin tarafından prediction kaydına eklenir
        mkt_field = {"HOME_WIN": "market_odds_h", "DRAW": "market_odds_d", "AWAY_WIN": "market_odds_a"}.get(raw)
        mkt_odds  = pred.get(mkt_field) if mkt_field else None

        clv_val = _clv(pred_prob_frac, mkt_odds) if pred_prob_frac is not None else None
        correct = (raw == actual)

        # is_sniper: explicit field, yoksa signal proxy
        is_sniper = pred.get("is_sniper")
        if is_sniper is None:
            is_sniper = (
                pred.get("signal") == "HIGH_EDGE"
                and pred.get("tier") in ("TIER_A", "TIER_B")
            )

        records.append({
            "date":           s.get("match_date"),
            "home":           home,
            "away":           away,
            "prediction":     raw,
            "actual":         actual,
            "correct":        correct,
            "confidence":     pred.get("confidence"),
            "tier":           pred.get("tier"),
            "is_sniper":      bool(is_sniper),
            "predicted_prob": pred_prob_frac,
            "market_odds":    mkt_odds,
            "market_implied": round(1.0 / mkt_odds, 4) if mkt_odds else None,
            "clv":            clv_val,
            "clv_positive":   (clv_val > 0) if clv_val is not None else None,
        })

    return records


def build_summary(records: list[dict]) -> dict:
    n = len(records)
    if not n:
        return {"n": 0, "note": "Henüz CLV kaydı yok — settlement/prediction eşleşmesi bulunamadı"}

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
            else "CLV negatif → model piyasaya göre geri" if avg_clv is not None and avg_clv <= 0
            else "CLV hesaplanamadı — piyasa oranı eksik"
        ),
        "stat_note": (
            f"n={n} kayıt ({len(with_clv)} oran mevcut). "
            f"İstatistiksel anlam için n≥500 gerekli."
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
    avg = summary.get('avg_clv')
    print(f"  Ortalama CLV      : {f'{avg:+.4f}' if avg is not None else '—'}")
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
