"""
ops/admin_kelly.py — Half-Kelly EV Engine (admin only)

Buchdahl (2016) formüllerine dayalı stake hesaplama motoru.

Formüller:
  edge          = (model_prob × decimal_odds) - 1
  kelly_frac    = edge / (decimal_odds - 1)
  half_kelly    = kelly_frac × 0.5
  stake_TL      = half_kelly × bankroll
  risk_of_ruin  = ((1 - edge) / (1 + edge)) ^ (bankroll / unit)

Favourite-Longshot Bias düzeltmesi (Buchdahl Ch.4):
  Uzun oranlar (>4.0) gerçek probabiliteyi overstate eder.
  Düzeltme: model_prob × (1 - flb_factor) — sadece informatif, stake'e yansımaz.

Kullanım (CLI):
  python -m ops.admin_kelly --prob 0.62 --odds 1.85 --bankroll 11240
  python -m ops.admin_kelly --prob 0.62 --odds 1.85 --bankroll 11240 --unit 112.40
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass


# Minimum edge eşiği — altında "No Bet"
MIN_EDGE = 0.05
# Half-Kelly çarpanı
KELLY_MULTIPLIER = 0.5
# Favourite-Longshot Bias eşiği
FLB_THRESHOLD = 4.0
FLB_DISCOUNT = 0.03   # uzun oranda model_prob'dan %3 indirim (sadece uyarı)
# Türkiye yasal minimum kupon bedeli (İddaa/Misli/Bilyoner/Oley — 7258 sk.)
MIN_LEGAL_STAKE_TL = 50.0


@dataclass
class KellyResult:
    model_prob:    float
    decimal_odds:  float
    implied_prob:  float
    edge:          float
    kelly_frac:    float
    half_kelly:    float
    stake_pct:     float        # bankroll'un yüzdesi
    stake_tl:      float        # TL tutarı
    unit_count:    float        # birim sayısı
    risk_of_ruin:  float | None
    verdict:       str          # "BET" | "NO_BET" | "MARGINAL"
    flb_warning:   bool
    legal_warning: bool         # True when stake_tl < MIN_LEGAL_STAKE_TL (Türkiye yasal min.)

    def summary(self) -> str:
        lines = [
            f"  Model prob    : {self.model_prob*100:.1f}%",
            f"  Implied prob  : {self.implied_prob*100:.1f}%",
            f"  Edge          : {self.edge*100:+.2f}%",
            f"  Kelly frac    : {self.kelly_frac*100:.2f}%",
            f"  Half-Kelly    : {self.half_kelly*100:.2f}%  →  {self.stake_pct*100:.2f}% bankroll",
            f"  Stake         : {self.stake_tl:.2f} TL  ({self.unit_count:.2f}u)",
        ]
        if self.risk_of_ruin is not None:
            lines.append(f"  Risk of Ruin  : {self.risk_of_ruin*100:.4f}%")
        if self.flb_warning:
            lines.append(f"  ⚠ FLB uyarısı : Oran >{FLB_THRESHOLD:.0f} — uzun ihtimal, overbet riski")
        if self.legal_warning:
            min_bankroll = round(MIN_LEGAL_STAKE_TL / self.stake_pct) if self.stake_pct > 0 else 0
            lines.append(f"  ⛔ YASAL UYARI : Stake {self.stake_tl:.2f} TL < {MIN_LEGAL_STAKE_TL:.0f} TL min. kupon bedeli (7258 sk.)")
            lines.append(f"  ⛔              50 TL stake için kasa ≥ {min_bankroll:,.0f} TL gerekli")
        lines.append(f"  Karar         : {'✅ BET' if self.verdict == 'BET' else '⚠ MARJINAL' if self.verdict == 'MARGINAL' else '❌ NO BET'}")
        return "\n".join(lines)


def calc_kelly(
    model_prob: float,
    decimal_odds: float,
    bankroll: float,
    unit_size: float | None = None,
    min_edge: float = MIN_EDGE,
) -> KellyResult:
    """
    Ana Kelly hesaplama fonksiyonu.

    Args:
        model_prob   : Model tahmini olasılık (0-1 arası)
        decimal_odds : Oran (örn. 1.85)
        bankroll     : Anlık kasa (TL)
        unit_size    : Birim boyutu TL (None = bankroll × 0.01)
        min_edge     : Minimum edge eşiği

    Returns:
        KellyResult dataclass
    """
    if unit_size is None:
        unit_size = bankroll * 0.01

    implied_prob  = 1.0 / decimal_odds
    edge          = model_prob * decimal_odds - 1.0
    kelly_frac    = edge / (decimal_odds - 1.0) if decimal_odds > 1.0 else 0.0
    half_kelly    = kelly_frac * KELLY_MULTIPLIER
    # Negatif Kelly: No Bet
    half_kelly    = max(half_kelly, 0.0)
    stake_pct     = half_kelly
    stake_tl      = bankroll * stake_pct
    unit_count    = stake_tl / unit_size if unit_size > 0 else 0.0
    flb_warning   = decimal_odds > FLB_THRESHOLD

    # Risk of Ruin (sadece edge>0 ve unit_size>0 ise anlamlı)
    ror: float | None = None
    if edge > 0 and unit_size > 0 and bankroll > 0:
        try:
            ratio = (1 - edge) / (1 + edge)
            exponent = bankroll / unit_size
            ror = math.pow(ratio, exponent)
        except (ValueError, OverflowError):
            ror = None

    if edge >= min_edge and half_kelly > 0:
        verdict = "BET"
    elif edge > 0:
        verdict = "MARGINAL"
    else:
        verdict = "NO_BET"

    legal_warning = 0 < stake_tl < MIN_LEGAL_STAKE_TL

    return KellyResult(
        model_prob=model_prob,
        decimal_odds=decimal_odds,
        implied_prob=implied_prob,
        edge=round(edge, 6),
        kelly_frac=round(kelly_frac, 6),
        half_kelly=round(half_kelly, 6),
        stake_pct=round(stake_pct, 6),
        stake_tl=round(stake_tl, 2),
        unit_count=round(unit_count, 3),
        risk_of_ruin=round(ror, 8) if ror is not None else None,
        verdict=verdict,
        flb_warning=flb_warning,
        legal_warning=legal_warning,
    )


def calc_combo_kelly(
    picks: list[tuple[float, float]],   # [(model_prob, decimal_odds), ...]
    bankroll: float,
    unit_size: float | None = None,
) -> dict:
    """
    Kombine kupon için Kelly hesabı.
    picks: (model_prob, decimal_odds) çiftleri listesi.
    """
    if not picks:
        return {}

    if unit_size is None:
        unit_size = bankroll * 0.01

    combo_odds = 1.0
    combo_ev   = 1.0
    for prob, odds in picks:
        combo_odds *= odds
        combo_ev   *= (prob * odds)

    combo_edge     = combo_ev - 1.0
    kelly_frac     = combo_edge / (combo_odds - 1.0) if combo_odds > 1.0 else 0.0
    half_kelly     = max(kelly_frac * KELLY_MULTIPLIER, 0.0)
    stake_tl       = bankroll * half_kelly
    unit_count     = stake_tl / unit_size if unit_size > 0 else 0.0
    variance_flag  = len(picks) > 3

    verdict = "BET" if combo_edge >= MIN_EDGE and half_kelly > 0 else \
              "MARGINAL" if combo_edge > 0 else "NO_BET"

    return {
        "n_picks":       len(picks),
        "combo_odds":    round(combo_odds, 4),
        "combo_ev":      round(combo_ev, 6),
        "combo_edge":    round(combo_edge, 6),
        "kelly_frac":    round(kelly_frac, 6),
        "half_kelly":    round(half_kelly, 6),
        "stake_tl":      round(stake_tl, 2),
        "unit_count":    round(unit_count, 3),
        "variance_flag": variance_flag,
        "verdict":       verdict,
    }


def min_invest_for_target(
    decimal_odds: float,
    target_profit: float,
) -> float:
    """
    Hedef kâr için gereken minimum yatırım (tek bahis, belirli oran).
    profit = stake × (decimal_odds - 1)  →  stake = profit / (odds - 1)
    """
    if decimal_odds <= 1.0:
        return float("inf")
    return target_profit / (decimal_odds - 1.0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Half-Kelly EV hesaplama motoru (admin)")
    ap.add_argument("--prob",     type=float, required=True, help="Model olasılığı (0-1)")
    ap.add_argument("--odds",     type=float, required=True, help="Decimal oran (örn. 1.85)")
    ap.add_argument("--bankroll", type=float, required=True, help="Anlık kasa (TL)")
    ap.add_argument("--unit",     type=float, default=None,  help="Birim boyutu TL (default: bankroll×1%)")
    ap.add_argument("--min-edge", type=float, default=MIN_EDGE, help=f"Min edge eşiği (default: {MIN_EDGE})")
    args = ap.parse_args()

    r = calc_kelly(args.prob, args.odds, args.bankroll, args.unit, args.min_edge)
    print(f"\n{'='*50}")
    print(f"  KELLY HESABI")
    print(f"{'='*50}")
    print(r.summary())
    print(f"{'='*50}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
