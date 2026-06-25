"""
ops/admin_arb.py — Arbitraj Detektörü (admin only)

Farklı bahisçilerdeki oranları karşılaştırıp matematiksel garanti
(arbitraj / surebet) fırsatı olup olmadığını hesaplar.

Matematik:
  implied_sum   = Σ(1/odds_i)
  arb_exists    = implied_sum < 1.0
  arb_margin    = (1 - implied_sum) × 100   [%]
  optimal_stake = (budget / odds_i) / implied_sum   [her sonuç için]
  guaranteed_return = budget / implied_sum
  guaranteed_profit = guaranteed_return - budget

Desteklenen piyasa tipleri:
  1X2  : home/draw/away (3 seçenek)
  2way : home/away  veya over/under  (2 seçenek)

Kullanım (CLI):
  # 1X2 arbitraj:
  python -m ops.admin_arb \
    --home "Bet365:2.10" --draw "Pinnacle:3.40" --away "1xBet:4.20" \
    --budget 1000

  # 2-way (over/under):
  python -m ops.admin_arb \
    --leg "Bet365:2.10" --leg "Pinnacle:1.85" \
    --budget 500

  # Sadece kontrol (bütçesiz):
  python -m ops.admin_arb --home "2.10" --draw "3.40" --away "4.20"
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass


@dataclass
class ArbLeg:
    label:    str         # sonuç etiketi (HOME / DRAW / AWAY / OVER / UNDER)
    book:     str         # bahisçi adı
    odds:     float       # decimal oran


@dataclass
class ArbResult:
    legs:           list[ArbLeg]
    implied_sum:    float
    arb_exists:     bool
    arb_margin:     float        # %
    budget:         float | None
    stakes:         list[float] | None   # her leg için yatırım TL
    guaranteed_ret: float | None
    guaranteed_pnl: float | None

    def summary(self) -> str:
        lines = [f"\n{'='*54}", "  ARBİTRAJ ANALİZİ", f"{'='*54}"]
        lines.append(f"  Piyasa tipi   : {len(self.legs)}-way")
        for leg in self.legs:
            book = f" ({leg.book})" if leg.book else ""
            lines.append(f"  {leg.label:<6}           : {leg.odds:.4f}{book}")
        lines.append(f"  Implied sum   : {self.implied_sum:.6f}")

        if self.arb_exists:
            lines.append(f"  ARB MARGIN    : +{self.arb_margin:.4f}%  ✅ ARBİTRAJ BULUNDU")
        else:
            overround = (self.implied_sum - 1.0) * 100
            lines.append(f"  Overround     : +{overround:.4f}%  ❌ Arbitraj yok")

        if self.budget and self.arb_exists and self.stakes:
            lines.append(f"\n  Bütçe         : {self.budget:.2f} TL")
            for leg, stake in zip(self.legs, self.stakes):
                book = f" @ {leg.book}" if leg.book else ""
                lines.append(f"  → {leg.label:<6}       : {stake:.2f} TL{book}  (@{leg.odds})")
            lines.append(f"\n  Garantili getiri : {self.guaranteed_ret:.2f} TL")
            lines.append(f"  Garantili kâr    : +{self.guaranteed_pnl:.2f} TL  (+{self.arb_margin:.4f}%)")
        elif self.budget and not self.arb_exists:
            lines.append(f"\n  (Arbitraj olmadığı için stake dağılımı hesaplanamaz)")

        lines.append(f"{'='*54}\n")
        return "\n".join(lines)


def _parse_leg(raw: str, label: str) -> ArbLeg:
    """
    Format: "BahisciAdi:1.85"  veya  "1.85"
    """
    raw = raw.strip()
    if ":" in raw:
        parts = raw.split(":", 1)
        book  = parts[0].strip()
        odds  = float(parts[1].strip())
    else:
        book = ""
        odds = float(raw)
    return ArbLeg(label=label, book=book, odds=odds)


def calc_arb(legs: list[ArbLeg], budget: float | None = None) -> ArbResult:
    if len(legs) < 2:
        raise ValueError("En az 2 leg gerekli")

    implied_sum = sum(1.0 / leg.odds for leg in legs)
    arb_exists  = implied_sum < 1.0
    arb_margin  = (1.0 - implied_sum) * 100 if arb_exists else 0.0

    stakes = None
    guaranteed_ret = None
    guaranteed_pnl = None

    if budget and budget > 0 and arb_exists:
        guaranteed_ret = budget / implied_sum
        guaranteed_pnl = guaranteed_ret - budget
        stakes = []
        for leg in legs:
            # Her leg'e düşen optimal stake
            s = guaranteed_ret / leg.odds
            stakes.append(round(s, 2))
        guaranteed_ret = round(guaranteed_ret, 2)
        guaranteed_pnl = round(guaranteed_pnl, 2)

    return ArbResult(
        legs=legs,
        implied_sum=round(implied_sum, 8),
        arb_exists=arb_exists,
        arb_margin=round(arb_margin, 6),
        budget=budget,
        stakes=stakes,
        guaranteed_ret=guaranteed_ret,
        guaranteed_pnl=guaranteed_pnl,
    )


def scan_arb_matrix(
    home_books: list[tuple[str, float]],
    draw_books: list[tuple[str, float]],
    away_books: list[tuple[str, float]],
    budget: float | None = None,
) -> ArbResult | None:
    """
    Birden fazla bahisçi için en iyi arb kombinasyonunu bul.
    home_books, draw_books, away_books: [(book_name, odds), ...] listesi
    En düşük implied_sum kombinasyonunu seçer.
    """
    best: ArbResult | None = None
    best_sum = float("inf")

    for h_book, h_odds in home_books:
        for d_book, d_odds in draw_books:
            for a_book, a_odds in away_books:
                legs = [
                    ArbLeg("HOME", h_book, h_odds),
                    ArbLeg("DRAW", d_book, d_odds),
                    ArbLeg("AWAY", a_book, a_odds),
                ]
                result = calc_arb(legs, budget)
                if result.implied_sum < best_sum:
                    best_sum = result.implied_sum
                    best = result

    return best


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Arbitraj detektörü — 1X2 veya 2-way piyasalar (admin)"
    )
    # 1X2 modu
    ap.add_argument("--home",   type=str, help='Ev sahibi oranı, örn. "Bet365:2.10"')
    ap.add_argument("--draw",   type=str, help='Beraberlik oranı')
    ap.add_argument("--away",   type=str, help='Deplasman oranı')
    # 2-way modu (--leg --leg)
    ap.add_argument("--leg",    type=str, action="append", metavar="book:odds",
                    help="2-way leg, tekrarlı kullanım (örn. --leg Bet365:2.10 --leg Pin:1.85)")
    # Bütçe
    ap.add_argument("--budget", type=float, default=None, help="Toplam yatırım TL")

    args = ap.parse_args()

    # 2-way modu
    if args.leg:
        legs = []
        labels = ["SEÇ1", "SEÇ2", "SEÇ3", "SEÇ4"]
        for i, raw in enumerate(args.leg):
            legs.append(_parse_leg(raw, labels[i] if i < len(labels) else f"L{i+1}"))
        result = calc_arb(legs, args.budget)
        print(result.summary())
        return 0

    # 1X2 modu
    if not all([args.home, args.draw, args.away]):
        print("HATA: 1X2 için --home, --draw, --away gerekli. "
              "2-way için --leg --leg kullanın.")
        ap.print_help()
        return 1

    legs = [
        _parse_leg(args.home, "HOME"),
        _parse_leg(args.draw, "DRAW"),
        _parse_leg(args.away, "AWAY"),
    ]
    result = calc_arb(legs, args.budget)
    print(result.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
