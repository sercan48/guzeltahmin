"""
ops/settlement_notifier.py — Maç Sonuç Bildirimcisi

Settlement verisi üzerinden her sonuçlanan maç için kompakt bir
Telegram kartı gönderir:

  ⚽ Arjantin 3-0 Cezayir  [TIER_A]
  🥇 Ev Sahibi ✅  ·  🥈 2.5 Alt ✅

Hangi settlement'ların gönderildiği data/notified_settlements.json
dosyasında takip edilir; script yeniden çalıştırıldığında aynı
maç tekrar gönderilmez.

Kullanım:
    python -m ops.settlement_notifier              # dry-run: stdout
    python -m ops.settlement_notifier --deliver    # Telegram gönder
    python -m ops.settlement_notifier --all        # tüm geçmiş (dry-run)
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

_SETTLEMENTS_FILE  = Path("data/shadow_settlements.jsonl")
_NOTIFIED_FILE     = Path("data/notified_settlements.json")
_TG_MAX_LEN        = 4_096


# ---------------------------------------------------------------------------
# İkincil seçim yeniden hesaplama (kural modu — oran gerektirmez)
# ---------------------------------------------------------------------------

def _recompute_secondary(s: dict) -> tuple[str, str]:
    """
    Settlement kaydından ikincil pazarı yeniden hesapla.
    Dönüş: (etiket, pazar_tipi)  — kural modu, EV hesabı yok.
    """
    probs   = s.get("probabilities", {})
    xg      = s.get("xg", {})
    xg_h    = float(xg.get("home", 0))
    xg_a    = float(xg.get("away", 0))
    tier    = s.get("tier", "TIER_C")
    conf    = float(s.get("confidence", 0))
    primary = s.get("predicted_outcome", "")
    h_prob  = float(probs.get("H", 0))
    a_prob  = float(probs.get("A", 0))
    d_prob  = float(probs.get("D", 0))

    total_xg = xg_h + xg_a
    p_under  = sum((total_xg**k * math.exp(-total_xg)) / math.factorial(k) for k in range(3))
    p_over   = 1.0 - p_under
    p_btts   = (1.0 - math.exp(-max(xg_h, 0.01))) * (1.0 - math.exp(-max(xg_a, 0.01)))

    if primary == "DRAW":
        if total_xg < 2.0:
            return "2.5 Alt", "OU"
        return ("1X", "DC") if h_prob >= a_prob else ("X2", "DC")

    if tier == "TIER_A" and conf >= 45:
        if primary == "HOME_WIN":
            return "1X", "DC"
        if primary == "AWAY_WIN":
            return "X2", "DC"

    if xg_h >= 0.9 and xg_a >= 0.9 and p_btts >= 0.45:
        return "KG Var", "BTTS"

    if p_over > p_under:
        return "2.5 Üst", "OU"
    return "2.5 Alt", "OU"


def _secondary_correct(label: str, actual_score: dict, actual_outcome: str) -> bool:
    """İkincil seçimin doğruluğunu fiili skora göre değerlendir."""
    hg = int(actual_score.get("home", 0))
    ag = int(actual_score.get("away", 0))
    total = hg + ag

    if label == "2.5 Alt":    return total < 3
    if label == "2.5 Üst":   return total >= 3
    if label == "KG Var":     return hg > 0 and ag > 0
    if label == "KG Yok":     return hg == 0 or ag == 0
    if label == "1X":          return actual_outcome in ("HOME_WIN", "DRAW")
    if label == "X2":          return actual_outcome in ("DRAW", "AWAY_WIN")
    if label == "12":          return actual_outcome in ("HOME_WIN", "AWAY_WIN")
    return False


# ---------------------------------------------------------------------------
# Etiket yardımcıları
# ---------------------------------------------------------------------------

_PRIMARY_LABELS = {
    "HOME_WIN": "Ev Sahibi",
    "DRAW":     "Beraberlik",
    "AWAY_WIN": "Deplasman",
}

_TIER_EM = {"TIER_A": "🔴", "TIER_B": "🟡", "TIER_C": "⚪"}


def _outcome_label(outcome: str) -> str:
    return _PRIMARY_LABELS.get(outcome, outcome)


# ---------------------------------------------------------------------------
# Tek maç kartı
# ---------------------------------------------------------------------------

def _match_card(s: dict) -> str:
    """
    Sonuçlanan tek maç için kompakt Telegram HTML kartı.

      ⚽ <b>Arjantin</b> 3–0 <b>Cezayir</b>  🔴 TIER_A
      🥇 Ev Sahibi ✅   ·   🥈 2.5 Alt ✅
    """
    home   = s.get("home_team", "?")
    away   = s.get("away_team", "?")
    score  = s.get("actual_score", {})
    hg     = score.get("home", "?")
    ag     = score.get("away", "?")
    tier   = s.get("tier", "TIER_C")
    tier_em = _TIER_EM.get(tier, "⚪")

    # Ana seçim
    primary_ok = s.get("correct", False)
    actual_out = s.get("actual_outcome", "")
    pred_out   = s.get("predicted_outcome", "")
    primary_em = "✅" if primary_ok else "❌"
    primary_lbl = _outcome_label(pred_out)
    # Yanlışsa gerçek sonucu da göster
    if not primary_ok:
        primary_lbl += f" → {_outcome_label(actual_out)}"

    # İkincil seçim
    sec_label, sec_type = _recompute_secondary(s)
    sec_ok = _secondary_correct(sec_label, score, actual_out)
    sec_em = "✅" if sec_ok else "❌"

    return (
        f"⚽ <b>{home}</b> {hg}–{ag} <b>{away}</b>  {tier_em} {tier}\n"
        f"   🥇 {primary_lbl} {primary_em}   ·   🥈 {sec_label} {sec_em}"
    )


# ---------------------------------------------------------------------------
# Grup mesajı (birden fazla maç)
# ---------------------------------------------------------------------------

def _build_message(settlements: list[dict], date_str: str) -> str:
    """Tek tarihli maç grubunu Telegram mesajına dönüştür."""
    lines = [f"📋 <b>MAÇSONUÇLARI — {date_str}</b>\n"]

    ana_ok  = sum(1 for s in settlements if s.get("correct"))
    sec_results = []
    for s in settlements:
        lines.append(_match_card(s))
        sec_label, _ = _recompute_secondary(s)
        sec_ok = _secondary_correct(
            sec_label, s.get("actual_score", {}), s.get("actual_outcome", "")
        )
        sec_results.append(sec_ok)

    sec_ok_count = sum(sec_results)
    n = len(settlements)
    lines.append(
        f"\n📊 <b>Özet:</b> {ana_ok}/{n} ana ✅   ·   {sec_ok_count}/{n} ikincil ✅"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram gönderim
# ---------------------------------------------------------------------------

def _send_telegram(text: str, token: str, chat_id: str) -> bool:
    try:
        import requests
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": "HTML",
        }, timeout=15)
        if resp.status_code == 200:
            return True
        logger.warning("[TG] HTTP %d: %s", resp.status_code, resp.text[:120])
        return False
    except Exception as exc:
        logger.warning("[TG] Hata: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Ana akış
# ---------------------------------------------------------------------------

def _load_notified() -> set[str]:
    if not _NOTIFIED_FILE.exists():
        return set()
    try:
        return set(json.loads(_NOTIFIED_FILE.read_text()))
    except Exception:
        return set()


def _save_notified(ids: set[str]) -> None:
    _NOTIFIED_FILE.parent.mkdir(parents=True, exist_ok=True)
    _NOTIFIED_FILE.write_text(json.dumps(sorted(ids), indent=2))


def run_notifier(deliver: bool = False, all_history: bool = False) -> None:
    if not _SETTLEMENTS_FILE.exists():
        logger.info("Settlement dosyası yok — atlandı")
        return

    settlements = [
        json.loads(line)
        for line in _SETTLEMENTS_FILE.read_text().splitlines()
        if line.strip()
    ]
    if not settlements:
        logger.info("Settlement kaydı yok")
        return

    notified = set() if all_history else _load_notified()

    # Henüz bildirilmemiş settlement'ları bul
    new_settlements = [
        s for s in settlements
        if s.get("settlement_id") and s["settlement_id"] not in notified
    ]

    if not new_settlements:
        logger.info("Bildirilecek yeni sonuç yok")
        return

    logger.info("%d yeni sonuç bulundu", len(new_settlements))

    # Tarihe göre grupla ve gönder
    from collections import defaultdict
    by_date: dict[str, list] = defaultdict(list)
    for s in sorted(new_settlements, key=lambda x: x.get("match_date", "")):
        by_date[s["match_date"]].append(s)

    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = (
        os.getenv("TELEGRAM_CHAT_ID", "").strip()
        or os.getenv("TELEGRAM_PERSONAL_CHANNEL", "").strip()
    )

    newly_notified: set[str] = set()

    for date_str, group in sorted(by_date.items()):
        msg = _build_message(group, date_str)

        if deliver and token and chat_id:
            ok = _send_telegram(msg, token, chat_id)
            if ok:
                for s in group:
                    newly_notified.add(s["settlement_id"])
                logger.info("[TG] %s: %d maç gönderildi", date_str, len(group))
            else:
                logger.warning("[TG] %s gönderilemedi", date_str)
        else:
            print(msg)
            print("─" * 50)
            for s in group:
                newly_notified.add(s["settlement_id"])

    if not all_history:
        _save_notified(notified | newly_notified)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Maç sonuç bildirimcisi")
    ap.add_argument("--deliver",  action="store_true", help="Telegram'a gönder")
    ap.add_argument("--all",      action="store_true", help="Tüm geçmişi gönder (dry-run)")
    args = ap.parse_args()
    run_notifier(deliver=args.deliver, all_history=args.all)


if __name__ == "__main__":
    main()
