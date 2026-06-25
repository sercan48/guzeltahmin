"""
ops/admin_report.py — P&L Raporu + Monte Carlo Projeksiyon (admin only)

Bahis logunu okuyup kapsamlı performans raporu üretir.

Kullanım:
  python -m ops.admin_report                    # tam rapor
  python -m ops.admin_report --last 20          # son N bahis
  python -m ops.admin_report --projection       # Monte Carlo büyüme tahmini
  python -m ops.admin_report --tier TIER_A      # tier bazlı filtre
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from pathlib import Path

_ADMIN_DIR  = Path("data/admin")
_BETS_F     = _ADMIN_DIR / "bets.jsonl"
_STATE_F    = _ADMIN_DIR / "bankroll_state.json"
_CLV_F      = Path("data/clv_log.jsonl")

_MC_SIMS    = 10_000
_MC_N_BETS  = 500   # projeksiyon için kaç bahis


# ---------------------------------------------------------------------------
# Yükleyiciler
# ---------------------------------------------------------------------------

def _load_bets() -> list[dict]:
    if not _BETS_F.exists():
        return []
    rows = []
    for line in _BETS_F.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _load_state() -> dict | None:
    if not _STATE_F.exists():
        return None
    try:
        return json.loads(_STATE_F.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_clv() -> list[dict]:
    if not _CLV_F.exists():
        return []
    rows = []
    for line in _CLV_F.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


# ---------------------------------------------------------------------------
# Hesaplama yardımcıları
# ---------------------------------------------------------------------------

def _safe_pct(num, den) -> float | None:
    if not den:
        return None
    return round(num / den * 100, 2)


def _summary_block(bets: list[dict], label: str = "TÜMÜ") -> dict:
    settled = [b for b in bets if b["status"] in ("WON", "LOST")]
    if not settled:
        return {"label": label, "n": 0, "note": "Settle edilmiş bahis yok"}

    n      = len(settled)
    won    = [b for b in settled if b["status"] == "WON"]
    staked = sum(b["stake"] for b in settled)
    pnl    = sum(b["pnl"] for b in settled if b["pnl"] is not None)
    roi    = pnl / staked * 100 if staked else 0.0

    # Streak analizi
    results = ["W" if b["status"] == "WON" else "L" for b in settled]
    cur_streak = max_win = max_loss = 1
    streak_type = results[-1] if results else "W"
    win_run = loss_run = 0
    for r in results:
        if r == "W":
            win_run += 1; loss_run = 0
            max_win = max(max_win, win_run)
        else:
            loss_run += 1; win_run = 0
            max_loss = max(max_loss, loss_run)

    # Son streak
    last = results[-1] if results else "-"
    s = 1
    for i in range(len(results) - 2, -1, -1):
        if results[i] == last:
            s += 1
        else:
            break

    # Ortalama oran ve edge
    edges = [b.get("kelly", {}).get("edge") for b in settled if b.get("kelly") and b["kelly"].get("edge") is not None]
    avg_edge = round(sum(edges) / len(edges) * 100, 2) if edges else None

    avg_odds = round(sum(b["odds"] for b in settled) / n, 4)

    return {
        "label":       label,
        "n":           n,
        "won":         len(won),
        "win_rate":    _safe_pct(len(won), n),
        "staked":      round(staked, 2),
        "pnl":         round(pnl, 2),
        "roi":         round(roi, 2),
        "avg_odds":    avg_odds,
        "avg_edge":    avg_edge,
        "max_win_streak":  max_win,
        "max_loss_streak": max_loss,
        "current_streak":  f"{s}{last}",
    }


# ---------------------------------------------------------------------------
# Monte Carlo projeksiyon
# ---------------------------------------------------------------------------

def _monte_carlo(
    win_rate: float,   # 0-1
    avg_odds: float,
    half_kelly: float, # stake / bankroll
    initial: float,
    n_bets: int = _MC_N_BETS,
    n_sims: int = _MC_SIMS,
    seed: int = 42,
) -> dict:
    """
    n_sims kez n_bets bahis simüle et.
    Her bahiste Bernoulli(win_rate) → kâr/zarar.
    """
    rng = random.Random(seed)
    final_bankrolls = []
    ruin_count = 0

    for _ in range(n_sims):
        bankroll = initial
        ruined   = False
        for _ in range(n_bets):
            stake = bankroll * half_kelly
            if rng.random() < win_rate:
                bankroll += stake * (avg_odds - 1)
            else:
                bankroll -= stake
            if bankroll <= initial * 0.05:   # %5 altı = ruin
                ruined = True
                break
        if ruined:
            ruin_count += 1
            final_bankrolls.append(initial * 0.05)
        else:
            final_bankrolls.append(bankroll)

    final_bankrolls.sort()
    n = len(final_bankrolls)

    p10  = final_bankrolls[int(n * 0.10)]
    p25  = final_bankrolls[int(n * 0.25)]
    p50  = final_bankrolls[int(n * 0.50)]
    p75  = final_bankrolls[int(n * 0.75)]
    p90  = final_bankrolls[int(n * 0.90)]
    mean = sum(final_bankrolls) / n

    def roi(v): return (v - initial) / initial * 100

    return {
        "n_sims":       n_sims,
        "n_bets":       n_bets,
        "ruin_pct":     round(ruin_count / n_sims * 100, 2),
        "p10_roi":      round(roi(p10), 1),
        "p25_roi":      round(roi(p25), 1),
        "p50_roi":      round(roi(p50), 1),
        "p75_roi":      round(roi(p75), 1),
        "p90_roi":      round(roi(p90), 1),
        "mean_roi":     round(roi(mean), 1),
    }


# ---------------------------------------------------------------------------
# CLV kalibrasyon raporu
# ---------------------------------------------------------------------------

def _clv_block(clv_records: list[dict]) -> dict | None:
    with_clv = [r for r in clv_records if r.get("clv") is not None]
    if not with_clv:
        return None

    positive   = [r for r in with_clv if r["clv"] > 0]
    avg_clv    = sum(r["clv"] for r in with_clv) / len(with_clv)
    snipers    = [r for r in clv_records if r.get("is_sniper")]
    sn_clv     = [r for r in snipers if r.get("clv") is not None]
    sn_avg_clv = sum(r["clv"] for r in sn_clv) / len(sn_clv) if sn_clv else None

    return {
        "n_with_clv":     len(with_clv),
        "avg_clv":        round(avg_clv, 4),
        "pct_positive":   _safe_pct(len(positive), len(with_clv)),
        "sniper_n_clv":   len(sn_clv),
        "sniper_avg_clv": round(sn_avg_clv, 4) if sn_avg_clv is not None else None,
        "interpretation": (
            "✅ Pozitif CLV — model piyasadan önde" if avg_clv > 0
            else "❌ Negatif CLV — model piyasaya göre geri"
        ),
    }


# ---------------------------------------------------------------------------
# Ana rapor yazdırma
# ---------------------------------------------------------------------------

def print_report(last_n: int | None = None, tier: str | None = None,
                 show_projection: bool = False) -> None:
    bets  = _load_bets()
    state = _load_state()
    clv   = _load_clv()

    if not bets:
        print("\nHenüz bahis kaydı yok (data/admin/bets.jsonl boş)\n")
        return

    settled = [b for b in bets if b["status"] in ("WON", "LOST")]
    open_b  = [b for b in bets if b["status"] == "OPEN"]

    # Filtrele
    if tier:
        settled = [b for b in settled if b.get("tier") == tier]
    if last_n:
        settled = settled[-last_n:]

    all_s  = _summary_block(settled, "TÜMÜ")
    tier_a = _summary_block([b for b in settled if b.get("tier") == "TIER_A"], "TIER_A")
    tier_b = _summary_block([b for b in settled if b.get("tier") == "TIER_B"], "TIER_B")
    tier_c = _summary_block([b for b in settled if b.get("tier") == "TIER_C"], "TIER_C")

    def _block_lines(s: dict) -> list[str]:
        if s.get("n", 0) == 0:
            return [f"  {s['label']}: veri yok"]
        return [
            f"  {s['label']:<8}: {s['won']}/{s['n']}  "
            f"win%:{s['win_rate']}%  ROI:{s['roi']:+.2f}%  "
            f"P&L:{s['pnl']:+.2f}TL  streak:{s['current_streak']}"
        ]

    print(f"\n{'='*62}")
    print(f"  ADMIN P&L RAPORU")
    if last_n:    print(f"  (Son {last_n} bahis)")
    if tier:      print(f"  (Filtre: {tier})")
    print(f"{'='*62}")

    if state:
        roi_total = (state.get("total_pnl", 0) / state["initial"] * 100
                     if state.get("initial") else 0)
        print(f"  Kasa       : {state['current']:.2f} TL  "
              f"(başlangıç: {state['initial']:.2f} TL)")
        print(f"  Net P&L    : {state.get('total_pnl', 0):+.2f} TL  ({roi_total:+.2f}%)")
        print(f"  Birim      : {state['unit_size']:.2f} TL  (%{state['unit_pct']})")
        print(f"  Max DD     : {state.get('max_drawdown', 0):.2f}%")
        print(f"  Açık bahis : {len(open_b)}")
        print(f"  {'─'*55}")

    for s in [all_s, tier_a, tier_b, tier_c]:
        for line in _block_lines(s):
            print(line)

    if all_s.get("n", 0) > 0:
        print(f"\n  Toplam yatırılan : {all_s['staked']:.2f} TL")
        print(f"  Ort. oran        : {all_s['avg_odds']}")
        if all_s.get("avg_edge") is not None:
            print(f"  Ort. edge        : {all_s['avg_edge']:+.2f}%")
        print(f"  En uzun galibiyet serisi : {all_s['max_win_streak']}")
        print(f"  En uzun kayıp serisi     : {all_s['max_loss_streak']}")

    # CLV kalibrasyon
    clv_block = _clv_block(clv)
    if clv_block:
        print(f"\n  {'─'*55}")
        print(f"  CLV KALİBRASYONU (shadow_settlements bazlı)")
        print(f"  n_with_clv    : {clv_block['n_with_clv']}")
        print(f"  Ort. CLV      : {clv_block['avg_clv']:+.4f}")
        print(f"  % Pozitif CLV : {clv_block['pct_clv']  if 'pct_clv' in clv_block else clv_block['pct_positive']}%")
        print(f"  Yorum         : {clv_block['interpretation']}")
        if clv_block["sniper_avg_clv"] is not None:
            print(f"  Sniper CLV    : {clv_block['sniper_avg_clv']:+.4f}  (n={clv_block['sniper_n_clv']})")

    # Monte Carlo projeksiyon
    if show_projection and all_s.get("n", 0) >= 10:
        win_rate  = all_s["won"] / all_s["n"]
        avg_odds  = all_s["avg_odds"]
        # Half-Kelly yaklaşık değeri: geçmiş stake/bankroll oranı
        hk_approx = (all_s["staked"] / all_s["n"]) / state["current"] if state else 0.02
        hk_approx = min(hk_approx, 0.10)  # max %10 cap

        mc = _monte_carlo(win_rate, avg_odds, hk_approx,
                          state["current"] if state else 10000,
                          _MC_N_BETS, _MC_SIMS)

        print(f"\n  {'─'*55}")
        print(f"  MONTE CARLO PROJEKSİYON  ({mc['n_sims']:,} sim, {mc['n_bets']} bahis)")
        print(f"  Parametreler: win%={win_rate*100:.1f}%, avg_oran={avg_odds}, HK≈{hk_approx*100:.1f}%")
        print(f"  10. persentil ROI : {mc['p10_roi']:+.1f}%")
        print(f"  25. persentil ROI : {mc['p25_roi']:+.1f}%")
        print(f"  50. persentil ROI : {mc['p50_roi']:+.1f}%  ← medyan")
        print(f"  75. persentil ROI : {mc['p75_roi']:+.1f}%")
        print(f"  90. persentil ROI : {mc['p90_roi']:+.1f}%")
        print(f"  Ortalama ROI      : {mc['mean_roi']:+.1f}%")
        print(f"  Ruin ihtimali     : {mc['ruin_pct']}%")
        print(f"\n  ⚠ n={all_s['n']} gerçek bahis — projeksiyon henüz spekülatif!")

    print(f"{'='*62}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Admin P&L raporu + projeksiyon")
    ap.add_argument("--last",       type=int,  default=None,  help="Son N bahis")
    ap.add_argument("--tier",       type=str,  default=None,  help="TIER_A/B/C filtre")
    ap.add_argument("--projection", action="store_true",       help="Monte Carlo projeksiyon")
    args = ap.parse_args()

    print_report(args.last, args.tier, args.projection)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
