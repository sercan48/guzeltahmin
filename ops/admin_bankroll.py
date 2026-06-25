"""
ops/admin_bankroll.py — Admin Kasa Yöneticisi (admin only)

Kasa durumunu yönetir, bahis loglar, settlement işler.

Veri:
  data/admin/bankroll_state.json  — anlık kasa
  data/admin/bets.jsonl           — bahis logu

Kullanım:
  python -m ops.admin_bankroll --init 10000              # kasayı kur
  python -m ops.admin_bankroll --status                  # anlık durum
  python -m ops.admin_bankroll --set-unit-pct 1.5        # birim %1.5
  python -m ops.admin_bankroll --deposit 2000            # para ekle
  python -m ops.admin_bankroll --withdraw 500            # para çek

  # Bahis ekle (sadece log — gerçek bahsi sen açarsın):
  python -m ops.admin_bankroll --add-bet \
      --match "ARG vs BRA" --date 2026-07-01 \
      --pick HOME_WIN --odds 1.85 --stake 235 \
      --prob 0.62 --tier TIER_A

  # Bahis settle et:
  python -m ops.admin_bankroll --settle BET_ID --result HOME_WIN
"""
from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ops.admin_kelly import calc_kelly

_ADMIN_DIR   = Path("data/admin")
_STATE_F     = _ADMIN_DIR / "bankroll_state.json"
_BETS_F      = _ADMIN_DIR / "bets.jsonl"

_DEFAULT_UNIT_PCT = 1.0   # bankroll'un %1'i


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if not _STATE_F.exists():
        raise FileNotFoundError(
            "Kasa bulunamadı. Önce: python -m ops.admin_bankroll --init <TL>"
        )
    return json.loads(_STATE_F.read_text(encoding="utf-8"))


def _save_state(state: dict) -> None:
    _ADMIN_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_F.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


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


def _append_bet(bet: dict) -> None:
    _ADMIN_DIR.mkdir(parents=True, exist_ok=True)
    with open(_BETS_F, "a", encoding="utf-8") as f:
        f.write(json.dumps(bet, ensure_ascii=False) + "\n")


def _rewrite_bets(bets: list[dict]) -> None:
    _ADMIN_DIR.mkdir(parents=True, exist_ok=True)
    with open(_BETS_F, "w", encoding="utf-8") as f:
        for b in bets:
            f.write(json.dumps(b, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Kasa işlemleri
# ---------------------------------------------------------------------------

def init_bankroll(amount: float) -> dict:
    state = {
        "initial":       round(amount, 2),
        "current":       round(amount, 2),
        "unit_pct":      _DEFAULT_UNIT_PCT,
        "unit_size":     round(amount * _DEFAULT_UNIT_PCT / 100, 2),
        "peak":          round(amount, 2),
        "trough":        round(amount, 2),
        "max_drawdown":  0.0,
        "n_bets":        0,
        "n_open":        0,
        "total_staked":  0.0,
        "total_pnl":     0.0,
        "created_at":    datetime.now(timezone.utc).isoformat(),
        "updated_at":    datetime.now(timezone.utc).isoformat(),
    }
    _save_state(state)
    return state


def _update_drawdown(state: dict) -> None:
    cur = state["current"]
    if cur > state["peak"]:
        state["peak"] = cur
    if cur < state["trough"]:
        state["trough"] = cur
    if state["peak"] > 0:
        dd = (state["peak"] - cur) / state["peak"] * 100
        state["max_drawdown"] = max(state.get("max_drawdown", 0.0), round(dd, 2))


def deposit(amount: float) -> dict:
    state = _load_state()
    state["current"] += round(amount, 2)
    state["initial"] += round(amount, 2)   # net yatırım bazı
    state["unit_size"] = round(state["current"] * state["unit_pct"] / 100, 2)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)
    return state


def withdraw(amount: float) -> dict:
    state = _load_state()
    if amount > state["current"]:
        raise ValueError(f"Yetersiz bakiye: {state['current']:.2f} TL")
    state["current"] -= round(amount, 2)
    state["unit_size"] = round(state["current"] * state["unit_pct"] / 100, 2)
    _update_drawdown(state)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)
    return state


def set_unit_pct(pct: float) -> dict:
    state = _load_state()
    state["unit_pct"]  = round(pct, 3)
    state["unit_size"] = round(state["current"] * pct / 100, 2)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)
    return state


# ---------------------------------------------------------------------------
# Bahis işlemleri
# ---------------------------------------------------------------------------

def _bet_id(match: str, date: str, pick: str) -> str:
    raw = f"{match}|{date}|{pick}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def add_bet(
    match: str,
    date: str,
    pick: str,           # HOME_WIN / DRAW / AWAY_WIN
    odds: float,
    stake: float,
    prob: float | None = None,
    tier: str = "",
    note: str = "",
) -> dict:
    state = _load_state()
    bet_id = _bet_id(match, date, pick)

    kelly_r = None
    if prob is not None:
        kr = calc_kelly(prob, odds, state["current"], state["unit_size"])
        kelly_r = {
            "edge":        kr.edge,
            "half_kelly":  kr.half_kelly,
            "stake_tl_recommended": kr.stake_tl,
            "verdict":     kr.verdict,
        }

    bet = {
        "bet_id":       bet_id,
        "match":        match,
        "date":         date,
        "pick":         pick,
        "odds":         round(odds, 4),
        "stake":        round(stake, 2),
        "model_prob":   round(prob, 4) if prob is not None else None,
        "kelly":        kelly_r,
        "tier":         tier,
        "note":         note,
        "status":       "OPEN",
        "result":       None,
        "pnl":          None,
        "settled_at":   None,
        "created_at":   datetime.now(timezone.utc).isoformat(),
    }

    # Kasa güncelle
    state["current"]     -= round(stake, 2)   # stake kilitlendi
    state["total_staked"] = round(state.get("total_staked", 0) + stake, 2)
    state["n_bets"]       = state.get("n_bets", 0) + 1
    state["n_open"]       = state.get("n_open", 0) + 1
    state["unit_size"]    = round(state["current"] * state["unit_pct"] / 100, 2)
    state["updated_at"]   = datetime.now(timezone.utc).isoformat()

    _append_bet(bet)
    _save_state(state)
    return bet


def settle_bet(bet_id: str, result: str) -> dict:
    """
    result: HOME_WIN | DRAW | AWAY_WIN
    """
    bets  = _load_bets()
    state = _load_state()

    target = None
    for b in bets:
        if b["bet_id"] == bet_id:
            target = b
            break
    if target is None:
        raise ValueError(f"Bahis bulunamadı: {bet_id}")
    if target["status"] != "OPEN":
        raise ValueError(f"Bahis zaten kapatılmış: {bet_id}")

    stake = target["stake"]
    odds  = target["odds"]
    won   = (result == target["pick"])
    pnl   = round(stake * (odds - 1), 2) if won else round(-stake, 2)

    target["status"]     = "WON" if won else "LOST"
    target["result"]     = result
    target["pnl"]        = pnl
    target["settled_at"] = datetime.now(timezone.utc).isoformat()

    # Kasa güncelle — stake geri + kâr/zarar
    state["current"]   = round(state["current"] + stake + pnl, 2)
    state["total_pnl"] = round(state.get("total_pnl", 0) + pnl, 2)
    state["n_open"]    = max(state.get("n_open", 0) - 1, 0)
    state["unit_size"] = round(state["current"] * state["unit_pct"] / 100, 2)
    _update_drawdown(state)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()

    _rewrite_bets(bets)
    _save_state(state)
    return target


# ---------------------------------------------------------------------------
# Durum yazdırma
# ---------------------------------------------------------------------------

def print_status() -> None:
    state  = _load_state()
    bets   = _load_bets()
    cur    = state["current"]
    ini    = state["initial"]
    pnl    = state.get("total_pnl", 0)
    roi    = pnl / ini * 100 if ini > 0 else 0.0
    staked = state.get("total_staked", 0)

    open_bets = [b for b in bets if b["status"] == "OPEN"]
    open_stake = sum(b["stake"] for b in open_bets)

    print(f"\n{'='*52}")
    print(f"  KASA DURUMU  —  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*52}")
    print(f"  Güncel kasa       : {cur:>10.2f} TL")
    print(f"  Başlangıç         : {ini:>10.2f} TL")
    print(f"  Net P&L           : {pnl:>+10.2f} TL  ({roi:+.2f}%)")
    print(f"  Toplam yatırılan  : {staked:>10.2f} TL")
    print(f"  Birim boyutu      : {state['unit_size']:>10.2f} TL  (%{state['unit_pct']})")
    print(f"  Peak              : {state.get('peak', cur):>10.2f} TL")
    print(f"  Max drawdown      : {state.get('max_drawdown', 0):>9.2f}%")
    print(f"  Toplam bahis      : {state.get('n_bets', 0)}")
    print(f"  Açık bahis        : {state.get('n_open', 0)}  ({open_stake:.2f} TL kilitli)")
    print(f"{'='*52}\n")

    if open_bets:
        print("  AÇIK BAHİSLER:")
        for b in open_bets:
            print(f"    [{b['bet_id']}] {b['date']} {b['match']} | "
                  f"{b['pick']} @{b['odds']} | {b['stake']:.2f} TL")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Admin kasa yöneticisi")
    ap.add_argument("--init",         type=float, metavar="TL",   help="Kasayı başlat")
    ap.add_argument("--status",       action="store_true",          help="Kasa durumu")
    ap.add_argument("--deposit",      type=float, metavar="TL",   help="Para ekle")
    ap.add_argument("--withdraw",     type=float, metavar="TL",   help="Para çek")
    ap.add_argument("--set-unit-pct", type=float, metavar="PCT",  help="Birim % ayarla")

    ap.add_argument("--add-bet",   action="store_true", help="Bahis ekle")
    ap.add_argument("--match",     type=str,   help="Maç adı")
    ap.add_argument("--date",      type=str,   help="Tarih YYYY-MM-DD")
    ap.add_argument("--pick",      type=str,   help="HOME_WIN|DRAW|AWAY_WIN")
    ap.add_argument("--odds",      type=float, help="Decimal oran")
    ap.add_argument("--stake",     type=float, help="Yatırım TL")
    ap.add_argument("--prob",      type=float, help="Model olasılığı (0-1)")
    ap.add_argument("--tier",      type=str,   default="")
    ap.add_argument("--note",      type=str,   default="")

    ap.add_argument("--settle",    type=str,   metavar="BET_ID", help="Bahis settle et")
    ap.add_argument("--result",    type=str,   help="HOME_WIN|DRAW|AWAY_WIN")

    args = ap.parse_args()

    if args.init is not None:
        s = init_bankroll(args.init)
        print(f"\n✅ Kasa kuruldu: {s['current']:.2f} TL  (birim: {s['unit_size']:.2f} TL)\n")

    elif args.status:
        print_status()

    elif args.deposit is not None:
        s = deposit(args.deposit)
        print(f"\n✅ Yatırım yapıldı. Güncel kasa: {s['current']:.2f} TL\n")

    elif args.withdraw is not None:
        s = withdraw(args.withdraw)
        print(f"\n✅ Para çekildi. Güncel kasa: {s['current']:.2f} TL\n")

    elif args.set_unit_pct is not None:
        s = set_unit_pct(args.set_unit_pct)
        print(f"\n✅ Birim güncellendi: %{s['unit_pct']}  =  {s['unit_size']:.2f} TL\n")

    elif args.add_bet:
        for field in ("match", "date", "pick", "odds", "stake"):
            if getattr(args, field, None) is None:
                print(f"HATA: --{field} zorunlu"); return 1
        b = add_bet(args.match, args.date, args.pick, args.odds, args.stake,
                    args.prob, args.tier, args.note)
        print(f"\n✅ Bahis eklendi: [{b['bet_id']}] {b['match']} | {b['pick']} @{b['odds']}")
        if b["kelly"]:
            k = b["kelly"]
            print(f"   Edge: {k['edge']*100:+.2f}%  |  Tavsiye stake: {k['stake_tl_recommended']:.2f} TL  |  {k['verdict']}")
        print()

    elif args.settle:
        if not args.result:
            print("HATA: --result zorunlu"); return 1
        b = settle_bet(args.settle, args.result)
        em = "✅" if b["status"] == "WON" else "❌"
        print(f"\n{em} Settle: [{args.settle}] {b['match']} | {b['result']} | P&L: {b['pnl']:+.2f} TL\n")

    else:
        ap.print_help()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
