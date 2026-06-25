"""
ops/admin_bankroll.py — Admin Kasa Yöneticisi (admin only)

Kasa durumunu yönetir, bahis loglar, settlement işler.
State Supabase'de tutulur (admin_bankroll + admin_bets tabloları).
Her kritik işlemde TELEGRAM_ADMIN_CHAT_ID'ye (kişisel sohbet) bildirim gönderir.

Env vars (GitHub Secrets):
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  TELEGRAM_BOT_TOKEN
  TELEGRAM_ADMIN_CHAT_ID

Kullanım (GitHub Actions workflow_dispatch veya lokal):
  python -m ops.admin_bankroll --init 100              # kasayı kur
  python -m ops.admin_bankroll --status                # anlık durum
  python -m ops.admin_bankroll --status --tg           # durum → Telegram
  python -m ops.admin_bankroll --deposit 50            # para ekle
  python -m ops.admin_bankroll --withdraw 10           # para çek
  python -m ops.admin_bankroll --set-unit-pct 1.5      # birim %1.5

  python -m ops.admin_bankroll --add-bet \\
      --match "ARG vs BRA" --date 2026-07-01 \\
      --pick HOME_WIN --odds 1.85 --stake 5 \\
      --prob 0.62 --tier TIER_A

  python -m ops.admin_bankroll --settle <BET_ID> --result HOME_WIN
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import requests as _requests

logger = logging.getLogger(__name__)

_DEFAULT_UNIT_PCT = 1.0

_PICK_TR = {
    "HOME_WIN": "Ev Sahibi",
    "DRAW":     "Beraberlik",
    "AWAY_WIN": "Deplasman",
}
_TIER_EM = {"TIER_A": "🔴", "TIER_B": "🟡", "TIER_C": "⚪"}


# ---------------------------------------------------------------------------
# Supabase REST helpers
# ---------------------------------------------------------------------------

def _sb() -> tuple[str, dict]:
    url = os.environ["SUPABASE_URL"].strip().rstrip("/")
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
    hdrs = {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
    }
    return url, hdrs


def _load_state() -> dict:
    url, hdrs = _sb()
    r = _requests.get(
        f"{url}/rest/v1/admin_bankroll",
        headers=hdrs,
        params={"id": "eq.1", "select": "*"},
        timeout=10,
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise RuntimeError("Kasa bulunamadı. Önce: --init <TL>")
    row = rows[0]
    return {
        "initial":      float(row["initial_tl"]),
        "current":      float(row["current_tl"]),
        "unit_pct":     float(row["unit_pct"]),
        "unit_size":    round(float(row["current_tl"]) * float(row["unit_pct"]), 2),
        "updated_at":   row["updated_at"],
    }


def _save_state(state: dict) -> None:
    url, hdrs = _sb()
    payload = {
        "id":          1,
        "current_tl":  round(state["current"], 2),
        "initial_tl":  round(state["initial"], 2),
        "unit_pct":    round(state.get("unit_pct", _DEFAULT_UNIT_PCT / 100), 8),
        "updated_at":  datetime.now(timezone.utc).isoformat(),
    }
    _requests.post(
        f"{url}/rest/v1/admin_bankroll",
        headers={**hdrs, "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=payload,
        timeout=10,
    ).raise_for_status()


def _load_bets(status: str | None = None) -> list[dict]:
    url, hdrs = _sb()
    params: dict = {"select": "*", "order": "created_at.asc"}
    if status:
        params["status"] = f"eq.{status}"
    r = _requests.get(
        f"{url}/rest/v1/admin_bets",
        headers=hdrs,
        params=params,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def _insert_bet(bet: dict) -> None:
    url, hdrs = _sb()
    _requests.post(
        f"{url}/rest/v1/admin_bets",
        headers={**hdrs, "Prefer": "return=minimal"},
        json=bet,
        timeout=10,
    ).raise_for_status()


def _update_bet(bet_id: str, patch: dict) -> None:
    url, hdrs = _sb()
    _requests.patch(
        f"{url}/rest/v1/admin_bets",
        headers={**hdrs, "Prefer": "return=minimal"},
        params={"bet_id": f"eq.{bet_id}"},
        json=patch,
        timeout=10,
    ).raise_for_status()


# ---------------------------------------------------------------------------
# Telegram bildirimi
# ---------------------------------------------------------------------------

def _tg(text: str) -> None:
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        _requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as exc:
        logger.debug("TG bildirimi gönderilemedi: %s", exc)


def _tg_init(state: dict) -> None:
    unit = round(state["current"] * state["unit_pct"], 2)
    _tg(
        f"💼 <b>KASA KURULDU</b>\n\n"
        f"Başlangıç: <b>{state['current']:.2f} TL</b>\n"
        f"Birim boyutu: {unit:.2f} TL  (%{state['unit_pct']*100:.1f})\n"
        f"Sistem hazır — iyi şanslar! 🎯"
    )


def _tg_add_bet(bet: dict, state: dict) -> None:
    pick_tr  = _PICK_TR.get(bet["pick"], bet["pick"])
    tier     = bet.get("tier", "")
    tier_em  = _TIER_EM.get(tier, "⚪")
    edge_s   = f"{bet['edge']*100:+.2f}%" if bet.get("edge") is not None else "—"
    rec_s    = f"{bet['stake_rec']:.2f} TL" if bet.get("stake_rec") else "—"
    verdict  = bet.get("verdict", "")
    v_em     = "✅" if verdict == "BET" else "⚠️" if verdict == "MARGINAL" else "❌"

    _tg(
        f"🎯 <b>YENİ BAHİS</b> {tier_em} {tier}\n"
        f"📅 {bet['match_date']}  ·  {bet['match']}\n"
        f"Seçim: <b>{pick_tr}</b>  @{bet['odds']}\n"
        f"Stake: <b>{bet['stake']:.2f} TL</b>\n"
        f"Edge: {edge_s}  ·  HK tavsiye: {rec_s}  {v_em}\n"
        f"Kasa: {state['current']:.2f} TL  [{bet['bet_id']}]"
    )


def _tg_settle(bet: dict, pnl: float, state: dict) -> None:
    em      = "✅" if bet["status"] == "WON" else "❌"
    pick_tr = _PICK_TR.get(bet["pick"], bet["pick"])
    pnl_em  = "📈" if pnl > 0 else "📉"
    roi     = (state["current"] - state["initial"]) / state["initial"] * 100 if state["initial"] else 0

    _tg(
        f"{em} <b>SONUÇ</b> — {bet['match']}\n"
        f"Seçim: {pick_tr}  @{bet['odds']}\n"
        f"{pnl_em} P&L: <b>{pnl:+.2f} TL</b>\n"
        f"Kasa: {state['current']:.2f} TL  ·  ROI: {roi:+.2f}%"
    )


def _tg_status(state: dict, open_bets: list[dict]) -> None:
    cur    = state["current"]
    ini    = state["initial"]
    pnl    = cur - ini
    roi    = pnl / ini * 100 if ini > 0 else 0.0
    pnl_em = "📈" if pnl >= 0 else "📉"
    open_stake = sum(float(b["stake"]) for b in open_bets)

    lines = [
        f"💼 <b>KASA DURUMU</b>",
        f"Güncel: <b>{cur:.2f} TL</b>  ·  Başlangıç: {ini:.2f} TL",
        f"{pnl_em} Net P&L: <b>{pnl:+.2f} TL</b>  ({roi:+.2f}%)",
        f"Birim: {state['unit_size']:.2f} TL  (%{state['unit_pct']*100:.1f})",
        f"Açık bahis: {len(open_bets)} ({open_stake:.2f} TL kilitli)",
    ]
    if open_bets:
        lines.append("─" * 22)
        for b in open_bets:
            pick_tr = _PICK_TR.get(b["pick"], b["pick"])
            lines.append(f"  ⏳ {b['match']} | {pick_tr} @{b['odds']} | {float(b['stake']):.2f} TL")

    _tg("\n".join(lines))


def _tg_deposit_withdraw(action: str, amount: float, state: dict) -> None:
    em    = "💰" if action == "deposit" else "💸"
    label = "YATIRIM" if action == "deposit" else "ÇEKİM"
    _tg(
        f"{em} <b>{label}</b>: {amount:+.2f} TL\n"
        f"Güncel kasa: <b>{state['current']:.2f} TL</b>"
    )


# ---------------------------------------------------------------------------
# Kasa işlemleri
# ---------------------------------------------------------------------------

def init_bankroll(amount: float) -> dict:
    unit_pct = _DEFAULT_UNIT_PCT / 100
    state = {
        "initial":  round(amount, 2),
        "current":  round(amount, 2),
        "unit_pct": unit_pct,
    }
    _save_state(state)
    _tg_init(state)
    return state


def deposit(amount: float, notify: bool = True) -> dict:
    state = _load_state()
    state["current"] = round(state["current"] + amount, 2)
    state["initial"] = round(state["initial"] + amount, 2)
    _save_state(state)
    if notify:
        _tg_deposit_withdraw("deposit", amount, state)
    return state


def withdraw(amount: float, notify: bool = True) -> dict:
    state = _load_state()
    if amount > state["current"]:
        raise ValueError(f"Yetersiz bakiye: {state['current']:.2f} TL")
    state["current"] = round(state["current"] - amount, 2)
    _save_state(state)
    if notify:
        _tg_deposit_withdraw("withdraw", amount, state)
    return state


def set_unit_pct(pct: float) -> dict:
    state = _load_state()
    state["unit_pct"] = round(pct / 100, 6)
    _save_state(state)
    return state


# ---------------------------------------------------------------------------
# Bahis işlemleri
# ---------------------------------------------------------------------------

def _bet_id(match: str, date: str, pick: str) -> str:
    raw = f"{match}|{date}|{pick}"
    return "B" + hashlib.sha256(raw.encode()).hexdigest()[:10].upper()


def add_bet(
    match: str,
    date: str,
    pick: str,
    odds: float,
    stake: float,
    prob: float | None = None,
    tier: str = "",
    note: str = "",
) -> dict:
    from ops.admin_kelly import calc_kelly

    state  = _load_state()
    bet_id = _bet_id(match, date, pick)

    edge = stake_rec = verdict = None
    if prob is not None:
        kr        = calc_kelly(prob, odds, state["current"], state["unit_size"])
        edge      = kr.edge
        stake_rec = kr.stake_tl
        verdict   = kr.verdict

    now = datetime.now(timezone.utc).isoformat()
    bet = {
        "bet_id":     bet_id,
        "match":      match,
        "match_date": date,
        "pick":       pick,
        "odds":       round(odds, 4),
        "stake":      round(stake, 2),
        "prob":       round(prob, 4) if prob is not None else None,
        "tier":       tier,
        "note":       note,
        "status":     "OPEN",
        "pnl":        None,
        "settled_at": None,
        "created_at": now,
        # extra fields for TG (not stored in DB schema)
        "edge":       edge,
        "stake_rec":  stake_rec,
        "verdict":    verdict,
    }

    # kasa güncellemesi — stake reserve değil, sadece float tracking
    # (kasa = serbest nakit; açık bahis stake'i ayrıca izlenir)
    _insert_bet({k: v for k, v in bet.items()
                 if k in ("bet_id","match","match_date","pick","odds",
                          "stake","prob","tier","note","status","created_at")})
    _tg_add_bet(bet, state)
    return bet


def settle_bet(bet_id: str, result: str) -> dict:
    bets_open = _load_bets(status="OPEN")
    target = next((b for b in bets_open if b["bet_id"] == bet_id), None)
    if target is None:
        raise ValueError(f"Açık bahis bulunamadı: {bet_id}")

    stake = float(target["stake"])
    odds  = float(target["odds"])
    won   = (result == target["pick"])
    pnl   = round(stake * (odds - 1), 2) if won else round(-stake, 2)

    now = datetime.now(timezone.utc).isoformat()
    patch = {
        "status":     "WON" if won else "LOST",
        "pnl":        pnl,
        "settled_at": now,
    }
    _update_bet(bet_id, patch)

    state = _load_state()
    state["current"] = round(state["current"] + pnl, 2)
    _save_state(state)

    target.update(patch)
    _tg_settle(target, pnl, state)
    return target


# ---------------------------------------------------------------------------
# Durum yazdırma
# ---------------------------------------------------------------------------

def print_status() -> None:
    state     = _load_state()
    all_bets  = _load_bets()
    open_bets = [b for b in all_bets if b["status"] == "OPEN"]
    won_bets  = [b for b in all_bets if b["status"] == "WON"]
    lost_bets = [b for b in all_bets if b["status"] == "LOST"]
    total_pnl = sum(float(b["pnl"] or 0) for b in all_bets)
    ini       = state["initial"]
    cur       = state["current"]
    roi       = (cur - ini) / ini * 100 if ini > 0 else 0.0
    open_stake = sum(float(b["stake"]) for b in open_bets)

    print(f"\n{'='*52}")
    print(f"  KASA DURUMU  —  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*52}")
    print(f"  Güncel kasa       : {cur:>10.2f} TL")
    print(f"  Başlangıç         : {ini:>10.2f} TL")
    print(f"  Net P&L           : {total_pnl:>+10.2f} TL  ({roi:+.2f}%)")
    print(f"  Birim boyutu      : {state['unit_size']:>10.2f} TL  (%{state['unit_pct']*100:.1f})")
    print(f"  Toplam bahis      : {len(all_bets)}  "
          f"(W:{len(won_bets)}  L:{len(lost_bets)}  Açık:{len(open_bets)})")
    print(f"  Açık stake        : {open_stake:.2f} TL kilitli")
    print(f"{'='*52}\n")

    if open_bets:
        print("  AÇIK BAHİSLER:")
        for b in open_bets:
            print(f"    [{b['bet_id']}]  {b['match_date']}  {b['match']}")
            print(f"      {b['pick']} @{b['odds']}  ·  {float(b['stake']):.2f} TL")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Admin kasa yöneticisi (Supabase-backed)")
    ap.add_argument("--init",         type=float, metavar="TL")
    ap.add_argument("--status",       action="store_true")
    ap.add_argument("--tg",           action="store_true")
    ap.add_argument("--deposit",      type=float, metavar="TL")
    ap.add_argument("--withdraw",     type=float, metavar="TL")
    ap.add_argument("--set-unit-pct", type=float, metavar="PCT")
    ap.add_argument("--add-bet",      action="store_true")
    ap.add_argument("--match",        type=str)
    ap.add_argument("--date",         type=str)
    ap.add_argument("--pick",         type=str)
    ap.add_argument("--odds",         type=float)
    ap.add_argument("--stake",        type=float)
    ap.add_argument("--prob",         type=float)
    ap.add_argument("--tier",         type=str, default="")
    ap.add_argument("--note",         type=str, default="")
    ap.add_argument("--settle",       type=str, metavar="BET_ID")
    ap.add_argument("--result",       type=str)
    args = ap.parse_args()

    if args.init is not None:
        s = init_bankroll(args.init)
        unit = round(s["current"] * s["unit_pct"], 2)
        print(f"\n✅ Kasa kuruldu: {s['current']:.2f} TL  (birim: {unit:.2f} TL)\n")

    elif args.status:
        print_status()
        if args.tg:
            state     = _load_state()
            open_bets = _load_bets(status="OPEN")
            _tg_status(state, open_bets)
            print("📲 Durum Telegram'a gönderildi.")

    elif args.deposit is not None:
        s = deposit(args.deposit)
        print(f"\n✅ Yatırım yapıldı. Güncel kasa: {s['current']:.2f} TL\n")

    elif args.withdraw is not None:
        s = withdraw(args.withdraw)
        print(f"\n✅ Para çekildi. Güncel kasa: {s['current']:.2f} TL\n")

    elif args.set_unit_pct is not None:
        s = set_unit_pct(args.set_unit_pct)
        print(f"\n✅ Birim güncellendi: %{args.set_unit_pct}  =  {s['unit_size']:.2f} TL\n")

    elif args.add_bet:
        for field in ("match", "date", "pick", "odds", "stake"):
            if getattr(args, field, None) is None:
                print(f"HATA: --{field} zorunlu"); return 1
        b = add_bet(args.match, args.date, args.pick, args.odds,
                    args.stake, args.prob, args.tier, args.note)
        print(f"\n✅ Bahis eklendi: [{b['bet_id']}]  {b['match']}  {b['pick']} @{b['odds']}")
        if b.get("edge") is not None:
            print(f"   Edge: {b['edge']*100:+.2f}%  |  HK tavsiye: {b['stake_rec']:.2f} TL  |  {b['verdict']}")
        print()

    elif args.settle:
        if not args.result:
            print("HATA: --result zorunlu"); return 1
        b = settle_bet(args.settle, args.result)
        em = "✅" if b["status"] == "WON" else "❌"
        print(f"\n{em}  [{args.settle}]  {b['match']}  P&L: {b['pnl']:+.2f} TL\n")

    else:
        ap.print_help()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
