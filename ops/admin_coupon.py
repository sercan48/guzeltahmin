"""
ops/admin_coupon.py — Kupon Oluşturucu (admin only)

Tekli veya kombine (akümülatör) kupon analizi.
Model tahminleriyle entegre — shadow_predictions.jsonl'dan otomatik prob çeker.

Kullanım:
  # Tekli bahis analizi:
  python -m ops.admin_coupon \
    --single --match "ARG vs BRA" --date 2026-07-01 \
    --pick HOME_WIN --odds 1.85 --prob 0.62 --bankroll 11240

  # Kombine kupon — her pick "prob@odds" veya "match|pick|prob@odds":
  python -m ops.admin_coupon \
    --combo \
    --pick "0.65@1.70" --pick "0.58@1.90" --pick "0.70@1.55" \
    --bankroll 11240 --label "Grup B kupon"

  # Min yatırım / hedef kâr modu:
  python -m ops.admin_coupon \
    --combo \
    --pick "0.65@1.70" --pick "0.58@1.90" \
    --target-profit 500 --bankroll 11240

  # shadow_predictions.jsonl'dan HIGH_EDGE tahminleri çek:
  python -m ops.admin_coupon --from-signals --date 2026-07-01 --bankroll 11240
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from ops.admin_kelly import calc_kelly, calc_combo_kelly, min_invest_for_target

_PREDICTIONS_F = Path("data/shadow_predictions.jsonl")

_MAX_COMBO_WARN = 3   # N>3 kombine → yüksek varyans uyarısı


# ---------------------------------------------------------------------------
# Pick parser
# ---------------------------------------------------------------------------

@dataclass
class Pick:
    label:  str     # maç / açıklama
    pick:   str     # HOME_WIN / DRAW / AWAY_WIN
    prob:   float   # model olasılığı (0-1)
    odds:   float   # decimal oran
    tier:   str = ""


def _parse_pick_str(raw: str, idx: int) -> Pick:
    """
    Formatlar:
      "0.65@1.70"                     → sadece prob@odds
      "ARG vs BRA|HOME_WIN|0.65@1.70" → tam format
    """
    raw = raw.strip()
    if "|" in raw:
        parts = raw.split("|")
        label = parts[0].strip() if len(parts) > 0 else f"Pick{idx+1}"
        pick  = parts[1].strip() if len(parts) > 1 else "HOME_WIN"
        pnl   = parts[2].strip() if len(parts) > 2 else "0.5@2.00"
    else:
        label = f"Pick{idx+1}"
        pick  = "HOME_WIN"
        pnl   = raw

    if "@" not in pnl:
        raise ValueError(f"Geçersiz format: '{raw}' — 'prob@odds' bekleniyor")
    prob_s, odds_s = pnl.split("@", 1)
    return Pick(label=label, pick=pick, prob=float(prob_s), odds=float(odds_s))


# ---------------------------------------------------------------------------
# Tekli bahis analizi
# ---------------------------------------------------------------------------

def analyze_single(
    match: str,
    pick: str,
    prob: float,
    odds: float,
    bankroll: float,
    unit_size: float | None = None,
    tier: str = "",
) -> None:
    kr = calc_kelly(prob, odds, bankroll, unit_size)

    print(f"\n{'='*56}")
    print(f"  TEKLİ BAHİS ANALİZİ")
    print(f"{'='*56}")
    print(f"  Maç     : {match}")
    print(f"  Seçim   : {pick}  {f'[{tier}]' if tier else ''}")
    print(f"  Oran    : {odds}")
    print(kr.summary())
    print(f"{'='*56}\n")


# ---------------------------------------------------------------------------
# Kombine kupon analizi
# ---------------------------------------------------------------------------

def analyze_combo(
    picks: list[Pick],
    bankroll: float,
    unit_size: float | None = None,
    target_profit: float | None = None,
    label: str = "",
) -> None:
    if unit_size is None:
        unit_size = bankroll * 0.01

    # Her pick için tekli Kelly
    print(f"\n{'='*60}")
    print(f"  KOMBİNE KUPON ANALİZİ  {f'— {label}' if label else ''}")
    print(f"{'='*60}")

    for i, p in enumerate(picks, 1):
        kr = calc_kelly(p.prob, p.odds, bankroll, unit_size)
        em = "✅" if kr.verdict == "BET" else "⚠" if kr.verdict == "MARGINAL" else "❌"
        print(f"  {i}. {p.label}  [{p.pick}]")
        print(f"     Oran: {p.odds}  |  Prob: {p.prob*100:.1f}%  |  Edge: {kr.edge*100:+.2f}%  {em}")

    # Kombine Kelly
    pick_tuples = [(p.prob, p.odds) for p in picks]
    ck = calc_combo_kelly(pick_tuples, bankroll, unit_size)

    print(f"\n  {'─'*50}")
    print(f"  Kombine oran       : {ck['combo_odds']:.4f}")
    print(f"  Kombine EV         : {ck['combo_ev']:.4f}")
    print(f"  Kombine edge       : {ck['combo_edge']*100:+.2f}%")
    print(f"  Half-Kelly stake   : {ck['stake_tl']:.2f} TL  ({ck['unit_count']:.2f}u)")

    if ck.get("variance_flag"):
        print(f"  ⚠ YÜKSEK VARYANS: {len(picks)} leg — kombine EV pozitif ama varyans çok yüksek!")

    verdict_em = "✅" if ck["verdict"] == "BET" else "⚠" if ck["verdict"] == "MARGINAL" else "❌"
    print(f"  Karar              : {verdict_em} {ck['verdict']}")

    # Min yatırım / hedef kâr
    if target_profit is not None:
        combo_odds = ck["combo_odds"]
        min_inv    = min_invest_for_target(combo_odds, target_profit)
        print(f"\n  Hedef kâr          : {target_profit:.2f} TL")
        print(f"  Min yatırım        : {min_inv:.2f} TL  (bu oran için)")
        print(f"  HK öneri stake     : {ck['stake_tl']:.2f} TL  (Half-Kelly, bankroll bazlı)")

    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# shadow_predictions.jsonl'dan HIGH_EDGE tahmini çek
# ---------------------------------------------------------------------------

def from_signals(date: str, bankroll: float, unit_size: float | None = None) -> None:
    if not _PREDICTIONS_F.exists():
        print("shadow_predictions.jsonl bulunamadı"); return

    picks: list[Pick] = []
    for line in _PREDICTIONS_F.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue

        if r.get("match_date") != date:
            continue
        if r.get("signal") != "HIGH_EDGE":
            continue

        probs = r.get("probabilities", {})
        raw   = r.get("predicted_outcome", "")
        letter= {"HOME_WIN": "H", "DRAW": "D", "AWAY_WIN": "A"}.get(raw)
        prob  = probs.get(letter, 0) / 100.0 if letter and probs else None

        mkt_field = {"HOME_WIN": "market_odds_h", "DRAW": "market_odds_d", "AWAY_WIN": "market_odds_a"}.get(raw)
        odds = r.get(mkt_field) if mkt_field else None

        if prob is None or odds is None:
            continue

        label = f"{r.get('home_team','?')} vs {r.get('away_team','?')}"
        picks.append(Pick(label=label, pick=raw, prob=prob, odds=odds, tier=r.get("tier","")))

    if not picks:
        print(f"\n[{date}] HIGH_EDGE + market_odds içeren tahmin bulunamadı.")
        print("  (Bulletin henüz odds eklemediyse beklemeye devam edin)\n")
        return

    print(f"\n{date} tarihinde {len(picks)} HIGH_EDGE tahmini bulundu:\n")
    for p in picks:
        analyze_single(p.label, p.pick, p.prob, p.odds, bankroll, unit_size, p.tier)

    if len(picks) >= 2:
        print("\n  ── Aynı seçimlerle kombine kupon analizi ──")
        analyze_combo(picks, bankroll, unit_size)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Kupon oluşturucu ve analizci (admin)")

    ap.add_argument("--single",        action="store_true",   help="Tekli bahis modu")
    ap.add_argument("--combo",         action="store_true",   help="Kombine kupon modu")
    ap.add_argument("--from-signals",  action="store_true",   help="shadow_predictions HIGH_EDGE'den çek")

    ap.add_argument("--match",    type=str,   default="",  help="Maç adı (single)")
    ap.add_argument("--date",     type=str,   default="",  help="Tarih YYYY-MM-DD")
    ap.add_argument("--pick",     type=str,   action="append", default=[],
                    help="Seçim: HOME_WIN|DRAW|AWAY_WIN (single) veya prob@odds (combo)")
    ap.add_argument("--odds",     type=float, help="Oran (single için)")
    ap.add_argument("--prob",     type=float, help="Model prob 0-1 (single için)")
    ap.add_argument("--tier",     type=str,   default="")
    ap.add_argument("--bankroll", type=float, required=True, help="Kasa TL")
    ap.add_argument("--unit",     type=float, default=None,  help="Birim TL (default bankroll×1%)")
    ap.add_argument("--target-profit", type=float, default=None, help="Hedef kâr TL (min-invest)")
    ap.add_argument("--label",    type=str,   default="",    help="Kupon etiketi")

    args = ap.parse_args()

    if args.single:
        if not args.prob or not args.odds:
            print("HATA: --prob ve --odds gerekli"); return 1
        pick_lbl = args.pick[0] if args.pick else "HOME_WIN"
        analyze_single(args.match, pick_lbl, args.prob, args.odds,
                       args.bankroll, args.unit, args.tier)

    elif args.combo:
        if not args.pick:
            print("HATA: En az bir --pick gerekli"); return 1
        picks = [_parse_pick_str(p, i) for i, p in enumerate(args.pick)]
        analyze_combo(picks, args.bankroll, args.unit, args.target_profit, args.label)

    elif args.from_signals:
        if not args.date:
            print("HATA: --date gerekli"); return 1
        from_signals(args.date, args.bankroll, args.unit)

    else:
        ap.print_help()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
