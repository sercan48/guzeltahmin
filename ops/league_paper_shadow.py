"""
ops/league_paper_shadow.py — Lig Bülteni Formatteri + Teslimat

WC shadow modülünün lig eşdeğeri.  Kelly skoru hesaplanmış tahmin
JSON'unu Telegram'a gönderilecek HTML mesajlarına dönüştürür.

Kullanım (CLI):
    python -m ops.league_paper_shadow --dry-run --json data/sample_league_session.json
    python -m ops.league_paper_shadow --deliver --json data/live_league_session.json

Kullanım (modül):
    from ops.league_paper_shadow import format_league_bulletin, send_league_bulletin

    messages = format_league_bulletin(session_json)
    # messages: list[str]  — her biri ayrı Telegram mesajı (≤4096 karakter)

Giriş JSON yapısı (session_json):
    {
      "session_id": "league_PL_2026-06-21_140512",
      "bulletin_date": "2026-06-21",
      "league_key": "PL",
      "league_name": "Premier League",
      "season": 2025,
      "bankroll": 1000.0,          # opsiyonel; varsayılan 1000
      "backtest_accuracy": 50.3,   # opsiyonel; bülten başlığında gösterilir
      "backtest_brier": 0.625,     # opsiyonel
      "predictions": [ ... ],      # aşağıda açıklandı
      "summary": { ... }           # opsiyonel; yoksa predictions'dan hesaplanır
    }

Her prediction kaydı:
    {
      "home_team": "Arsenal",
      "away_team": "Manchester City",
      "kickoff_time": "2026-06-21T15:00:00Z",  # UTC ISO
      "predicted_outcome": "HOME_WIN",           # HOME_WIN | DRAW | AWAY_WIN
      "probabilities": {"H": 48.4, "D": 26.4, "A": 25.1},
      "confidence": 52.0,
      "elo_home": 1950, "elo_away": 1980, "elo_gap": 30,
      "xg_home": 1.45, "xg_away": 1.20,
      "home_form_mult": 1.08, "away_form_mult": 0.94,
      "dc_rho": -0.10,
      "tier": "TIER_B",          # TIER_A | TIER_B | TIER_C
      "is_sniper": true,
      "is_no_bet": false,
      "odds": {                  # opsiyonel; The Odds API verisinden
        "h": 2.30, "d": 3.40, "a": 2.90,
        "over_2_5": 1.85, "under_2_5": 1.95,
        "btts_yes": 1.80, "btts_no": 2.00,
        "src": "pinnacle"        # kaynak bookmaker
      },
      "clv": null                # kapanış oranı gelince doldurulur
    }
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

_BANKROLL_DEFAULT  = 1_000.0    # iç sıralama için tutulur — ekranda gösterilmez
_SNIPER_CONF_MIN   = 62.0       # sniper minimum güven
_SNIPER_ODDS_MAX   = 4.50       # longshot filtresi (Buchdahl bias analizi)
_TIER_A_ELO_GAP    = 150        # Elo farkı ≥ 150 → TIER_A
_TIER_B_ELO_GAP    = 50         # Elo farkı ≥ 50  → TIER_B
_TG_MAX_LEN        = 4_096      # Telegram mesaj karakter limiti

_LEAGUE_NAMES: dict[str, str] = {
    "PL":           "Premier League",
    "LaLiga":       "La Liga",
    "Bundesliga":   "Bundesliga",
    "SerieA":       "Serie A",
    "Ligue1":       "Ligue 1",
    "Eredivisie":   "Eredivisie",
    "SuperLig":     "Süper Lig",
    "PrimeiraLiga": "Primeira Liga",
}

_LEAGUE_FLAGS: dict[str, str] = {
    "PL":           "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "LaLiga":       "🇪🇸",
    "Bundesliga":   "🇩🇪",
    "SerieA":       "🇮🇹",
    "Ligue1":       "🇫🇷",
    "Eredivisie":   "🇳🇱",
    "SuperLig":     "🇹🇷",
    "PrimeiraLiga": "🇵🇹",
}


# ---------------------------------------------------------------------------
# Matematiksel yardımcılar
# ---------------------------------------------------------------------------

def _poisson_prob(lam: float, k: int) -> float:
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def _btts_prob(xg_h: float, xg_a: float) -> float:
    """Her iki takımın da gol atma olasılığı (bağımsız Poisson)."""
    p_h_scores = 1.0 - math.exp(-xg_h)
    p_a_scores = 1.0 - math.exp(-xg_a)
    return round(p_h_scores * p_a_scores * 100, 1)


def _ou_prob(xg_h: float, xg_a: float, line: float = 2.5) -> tuple[float, float]:
    """Üst/Alt olasılıkları. Dönüş: (over_pct, under_pct)."""
    total = xg_h + xg_a
    threshold = int(line)  # 2.5 → 2 (P(≤2) = Alt)
    p_under = sum(
        _poisson_prob(total, k) for k in range(threshold + 1)
    )
    return round((1.0 - p_under) * 100, 1), round(p_under * 100, 1)


def _kelly_fraction(prob: float, decimal_odds: float) -> float:
    """
    Tam Kelly fraksiyonu: f* = (b·p - q) / b
      b = decimal_odds - 1
      p = kazanma olasılığı (0-1)
      q = 1 - p
    """
    if decimal_odds <= 1.0 or prob <= 0:
        return 0.0
    b = decimal_odds - 1.0
    q = 1.0 - prob
    f = (b * prob - q) / b
    return max(0.0, round(f, 4))


def _edge_pct(prob: float, decimal_odds: float) -> float:
    """Beklenen değer yüzdesi: EV% = (prob × odds - 1) × 100."""
    if decimal_odds <= 0:
        return 0.0
    return round((prob * decimal_odds - 1.0) * 100, 1)


def _value_score(kelly_fraction: Optional[float]) -> Optional[float]:
    """
    Kelly fraksiyonunu 0–10 arası kamuya açık 'Değer Skoru'na dönüştürür.
    Formül: min(round(K × 100, 1), 10.0)
    Finansal yönlendirme içermez; sıralama + fırsat yoğunluğu göstergesidir.
    """
    if kelly_fraction is None or kelly_fraction <= 0:
        return None
    return min(round(kelly_fraction * 100, 1), 10.0)


# ---------------------------------------------------------------------------
# Tier + seçim yardımcıları
# ---------------------------------------------------------------------------

def _tier_from_elo_gap(elo_gap: float) -> str:
    if elo_gap >= _TIER_A_ELO_GAP:
        return "TIER_A"
    if elo_gap >= _TIER_B_ELO_GAP:
        return "TIER_B"
    return "TIER_C"


def _tier_emoji(tier: str) -> str:
    return {"TIER_A": "🔴", "TIER_B": "🟡", "TIER_C": "⚪"}.get(tier, "⚪")


def _pred_label(outcome: str) -> str:
    return {
        "HOME_WIN": "1 (Ev Sahibi)",
        "DRAW":     "X (Beraberlik)",
        "AWAY_WIN": "2 (Deplasman)",
    }.get(outcome, outcome)


def _pred_short(outcome: str) -> str:
    return {"HOME_WIN": "1", "DRAW": "X", "AWAY_WIN": "2"}.get(outcome, "?")


def _secondary_pick(
    predicted_outcome: str,
    probs: dict,
    xg_h: float,
    xg_a: float,
    tier: str,
    confidence: float,
) -> tuple[str, float, str]:
    """
    Ortogonal ikincil pazar seç.
    Dönüş: (etiket, olasılık_0_to_1, pazar_tipi)

    Öncelik:
      1. DRAW tahmini → xG<2.0: Alt 2.5 | xG≥2.0: Çifte Şans
      2. TIER_A + conf≥55 → Çifte Şans (yön kayıplarını örtüyor)
      3. KG_VAR (her iki takım da saldırgan, P_BTTS≥0.45)
      4. xG≥2.3 → Üst 2.5
      5. Varsayılan → Alt 2.5
    """
    total_xg = xg_h + xg_a
    p_over, p_under_raw = _ou_prob(xg_h, xg_a, 2.5)
    p_under_frac = p_under_raw / 100.0

    h = probs.get("H", 0) / 100.0
    d = probs.get("D", 0) / 100.0
    a = probs.get("A", 0) / 100.0

    if predicted_outcome == "DRAW":
        if total_xg < 2.0:
            return "2.5 Alt", p_under_frac, "OU"
        return ("1X", h + d, "DC") if h >= a else ("X2", d + a, "DC")

    if tier == "TIER_A" and confidence >= 55:
        if predicted_outcome == "HOME_WIN":
            return "1X", h + d, "DC"
        if predicted_outcome == "AWAY_WIN":
            return "X2", d + a, "DC"

    p_btts = (1.0 - math.exp(-xg_h)) * (1.0 - math.exp(-xg_a))
    if xg_h >= 0.9 and xg_a >= 0.9 and p_btts >= 0.45:
        return "KG Var", p_btts, "BTTS"

    if total_xg >= 2.3:
        return "2.5 Üst", p_over / 100.0, "OU"
    return "2.5 Alt", p_under_frac, "OU"


# ---------------------------------------------------------------------------
# Kelly hesaplama (prediction'dan zenginleştir)
# ---------------------------------------------------------------------------

def _enrich_kelly(pred: dict, bankroll: float) -> dict:
    """
    pred üzerine Kelly skorlarını ekler ve zenginleştirilmiş dict döndürür.
    Orijinal dict'i değiştirmez.
    """
    p = dict(pred)  # kopya

    probs     = p.get("probabilities", {})
    outcome   = p.get("predicted_outcome", "")
    odds_data = p.get("odds") or {}

    prob_map = {"HOME_WIN": probs.get("H", 0), "DRAW": probs.get("D", 0), "AWAY_WIN": probs.get("A", 0)}
    odds_key = {"HOME_WIN": "h", "DRAW": "d", "AWAY_WIN": "a"}.get(outcome, "h")
    prob_dec  = prob_map.get(outcome, 0) / 100.0
    decimal_odds = odds_data.get(odds_key)

    if decimal_odds and decimal_odds > 1.0 and prob_dec > 0:
        kf    = _kelly_fraction(prob_dec, decimal_odds)
        ev    = _edge_pct(prob_dec, decimal_odds)
        is_sn = (
            not p.get("is_no_bet", False)
            and p.get("tier", "TIER_C") != "TIER_C"
            and float(p.get("confidence", 0)) >= _SNIPER_CONF_MIN
            and decimal_odds <= _SNIPER_ODDS_MAX
            and ev > 0
        )
    else:
        kf    = None
        ev    = None
        is_sn = False

    if "is_sniper" not in p:
        p["is_sniper"] = is_sn
    if "tier" not in p:
        p["tier"] = _tier_from_elo_gap(float(p.get("elo_gap", 0)))

    p["_kelly_fraction"] = kf
    p["_value_score"]    = _value_score(kf)   # 0–10 görsel skor; TL/fraksiyon gösterilmez
    p["_edge_pct"]       = ev
    return p


# ---------------------------------------------------------------------------
# Görsel yardımcılar
# ---------------------------------------------------------------------------

def _value_score_bar(score: Optional[float], width: int = 10) -> str:
    """
    0–10 değer skoru için ASCII görsel çubuk.
    10 puan = tüm bloklar dolu.
    """
    if score is None or score <= 0:
        return "░" * width
    filled = max(1, min(width, round(score)))
    return "█" * filled + "░" * (width - filled)


def _form_arrow(mult: float) -> str:
    if mult >= 1.07:
        return "🔥"
    if mult >= 1.03:
        return "↗"
    if mult <= 0.93:
        return "❄️"
    if mult <= 0.97:
        return "↘"
    return "→"


def _confidence_bar(conf: float) -> str:
    """5 bloklu güven çubuğu."""
    filled = max(0, min(5, round(conf / 20)))
    return "▓" * filled + "░" * (5 - filled)


def _kickoff_local(kickoff_iso: str) -> str:
    """UTC ISO → 'HH:MM UTC' formatı."""
    try:
        dt = datetime.fromisoformat(kickoff_iso.replace("Z", "+00:00"))
        return dt.strftime("%H:%M UTC")
    except Exception:
        return kickoff_iso


def _clv_label(clv: Optional[float]) -> str:
    if clv is None:
        return "—"
    sign = "+" if clv >= 0 else ""
    emoji = "✅" if clv > 0 else ("⚠️" if clv == 0 else "❌")
    return f"{emoji} {sign}{clv:.3f}"


# ---------------------------------------------------------------------------
# Bireysel maç kartı formatter
# ---------------------------------------------------------------------------

def format_match_card(pred: dict, bankroll: float = _BANKROLL_DEFAULT) -> str:
    """
    Tek maç için Telegram HTML kartı üretir.

    Bölümler:
      ── Başlık (Tier · Sniper etiketi · Takımlar · Saat)
      ── Ana tahmin + güven çubuğu
      ── Olasılık üçlüsü (1/X/2)
      ── Model girdileri (Elo · xG · Form · DC-ρ)
      ── Piyasa oranları (varsa)
      ── Model Fırsat Endeksi (Değer Skoru · EV · Güven)
      ── İkincil pazar
      ── Türev tahminler (KG/OU/DC)
      ── CLV (kapanış oranı gelince)
    """
    p = _enrich_kelly(pred, bankroll)

    home    = p.get("home_team", "?")
    away    = p.get("away_team", "?")
    kickoff = _kickoff_local(p.get("kickoff_time", ""))
    outcome = p.get("predicted_outcome", "?")
    probs   = p.get("probabilities", {})
    conf    = float(p.get("confidence", 0))
    tier    = p.get("tier", "TIER_C")
    odds    = p.get("odds") or {}

    elo_h   = float(p.get("elo_home", 0))
    elo_a   = float(p.get("elo_away", 0))
    elo_gap = float(p.get("elo_gap", abs(elo_h - elo_a)))
    xg_h    = float(p.get("xg_home", p.get("expected_goals_a", 0)))
    xg_a    = float(p.get("xg_away", p.get("expected_goals_b", 0)))
    fm_h    = float(p.get("home_form_mult", 1.0))
    fm_a    = float(p.get("away_form_mult", 1.0))
    dc_rho  = float(p.get("dc_rho", -0.10))

    is_sniper   = p.get("is_sniper", False)
    is_no_bet   = p.get("is_no_bet", False)
    value_score = p.get("_value_score")     # 0–10 görsel skor (Kelly tabanlı, TL yok)
    ev          = p.get("_edge_pct")
    clv         = p.get("clv")

    h_pct = round(probs.get("H", 0), 1)
    d_pct = round(probs.get("D", 0), 1)
    a_pct = round(probs.get("A", 0), 1)

    p_over, p_under = _ou_prob(xg_h, xg_a, 2.5)
    p_btts          = _btts_prob(xg_h, xg_a)
    dc_1x           = round(h_pct + d_pct, 1)
    dc_x2           = round(d_pct + a_pct, 1)

    sec_label, sec_prob_frac, sec_type = _secondary_pick(
        outcome, probs, xg_h, xg_a, tier, conf
    )
    sec_pct = round(sec_prob_frac * 100, 1)

    tier_em  = _tier_emoji(tier)
    conf_bar = _confidence_bar(conf)

    # ── Başlık satırı ───────────────────────────────────────────────────────
    sniper_tag = "  ⭐ <b>SNIPER</b>" if is_sniper else ""
    no_bet_tag = "  ⛔ <b>OYNANMAZ</b>" if is_no_bet else ""
    status_tag = sniper_tag or no_bet_tag or ""

    header = (
        f"┌─────────────────────────────────┐\n"
        f"│ {tier_em} {tier}{status_tag}\n"
        f"│ ⚽ <b>{home}</b>  vs  <b>{away}</b>\n"
        f"│ 🕒 {kickoff}\n"
        f"└─────────────────────────────────┘"
    )

    # ── Ana tahmin ──────────────────────────────────────────────────────────
    odds_key = {"HOME_WIN": "h", "DRAW": "d", "AWAY_WIN": "a"}.get(outcome, "h")
    mkt_odds = odds.get(odds_key)
    mkt_tag  = f"  @{mkt_odds}" if mkt_odds else ""

    pred_block = (
        f"\n🎯 <b>ANA TAHMİN:</b>  {_pred_label(outcome)}{mkt_tag}\n"
        f"   Güven: <b>{conf:.1f}%</b>  {conf_bar}"
    )

    # ── Olasılık üçlüsü ─────────────────────────────────────────────────────
    prob_block = (
        f"\n\n1️⃣ %{h_pct}   ❌ %{d_pct}   2️⃣ %{a_pct}"
    )

    # ── Model girdileri ──────────────────────────────────────────────────────
    form_h_str = f"{_form_arrow(fm_h)} ×{fm_h:.2f}"
    form_a_str = f"{_form_arrow(fm_a)} ×{fm_a:.2f}"
    model_block = (
        f"\n\n🔵 <b>Elo:</b> {elo_h:.0f} vs {elo_a:.0f}  "
        f"<i>(fark: {elo_gap:.0f})</i>\n"
        f"⚡ <b>xG:</b> {xg_h:.2f} – {xg_a:.2f}\n"
        f"📈 <b>Form:</b> Ev {form_h_str}  ·  Dep {form_a_str}\n"
        f"🔩 <b>DC-ρ:</b> {dc_rho:+.2f}"
    )

    # ── Piyasa oranları ──────────────────────────────────────────────────────
    if odds.get("h") and odds.get("d") and odds.get("a"):
        src_tag = f"  <i>({odds.get('src', 'ort.')})</i>" if odds.get("src") else ""
        odds_line = (
            f"\n\n💰 <b>PIYASA ORANLARI</b>{src_tag}\n"
            f"   1: <b>{odds['h']}</b>   X: <b>{odds['d']}</b>   2: <b>{odds['a']}</b>"
        )
        if odds.get("over_2_5"):
            odds_line += f"   |   2.5Ü: {odds['over_2_5']}"
        if odds.get("under_2_5"):
            odds_line += f"  ·  2.5A: {odds['under_2_5']}"
        if odds.get("btts_yes"):
            odds_line += f"\n   KG+: {odds['btts_yes']}"
        if odds.get("btts_no"):
            odds_line += f"  ·  KG−: {odds['btts_no']}"
    else:
        odds_line = "\n\n💰 <i>Piyasa oranı henüz alınamadı.</i>"

    # ── Model Fırsat Endeksi ─────────────────────────────────────────────────
    if value_score is not None and ev is not None and ev > 0:
        vs_bar = _value_score_bar(value_score)
        ev_str = f"<b>+%{ev:.1f}</b> ✅"
        mfe_block = (
            f"\n\n💎 <b>MODEL FIRSAT ENDEKSİ</b>\n"
            f"   Değer Skoru:  <b>{value_score:.1f} / 10</b>  {vs_bar}\n"
            f"   Beklenen Değer (EV):  {ev_str}\n"
            f"   Model Güveni:  {conf_bar}  <i>({conf:.1f}%)</i>"
        )
    elif ev is not None and ev <= 0:
        mfe_block = (
            f"\n\n💎 <b>MODEL FIRSAT ENDEKSİ</b>\n"
            f"   Değer Skoru:  —  <i>(piyasa EV negatif)</i>\n"
            f"   Model Güveni:  {conf_bar}  <i>({conf:.1f}%)</i>"
        )
    else:
        mfe_block = (
            f"\n\n💎 <b>MODEL FIRSAT ENDEKSİ</b>\n"
            f"   Değer Skoru:  —  <i>(piyasa oranı bekleniyor)</i>\n"
            f"   Model Güveni:  {conf_bar}  <i>({conf:.1f}%)</i>"
        )

    # ── İkincil pazar ────────────────────────────────────────────────────────
    sec_type_tags = {"OU": "⚖️", "DC": "🛡️", "BTTS": "🎯"}
    sec_em = sec_type_tags.get(sec_type, "")
    sec_block = (
        f"\n\n🥈 <b>İKİNCİL PAZAR:</b>  {sec_em} {sec_label}  %{sec_pct}"
    )

    # ── Türev tahminler ──────────────────────────────────────────────────────
    deriv_block = (
        f"\n\n📊 <b>TÜREV TAHMİNLER</b>\n"
        f"   KG Var: %{p_btts}  ·  2.5 Üst: %{p_over}  ·  2.5 Alt: %{p_under}\n"
        f"   1X: %{dc_1x}  ·  X2: %{dc_x2}"
    )

    # ── CLV (kapanış oranı) ──────────────────────────────────────────────────
    clv_block = f"\n\n📌 <b>CLV:</b> {_clv_label(clv)}"

    return (
        header
        + pred_block
        + prob_block
        + model_block
        + odds_line
        + mfe_block
        + sec_block
        + deriv_block
        + clv_block
    )


# ---------------------------------------------------------------------------
# Başlık + Sniper özeti
# ---------------------------------------------------------------------------

def _format_header(data: dict) -> str:
    """
    Bülten başlığı: lig adı, tarih, sniper listesi, istatistiksel uyarı.
    """
    league_key  = data.get("league_key", "?")
    league_name = data.get("league_name", _LEAGUE_NAMES.get(league_key, league_key))
    flag        = _LEAGUE_FLAGS.get(league_key, "🏆")
    date_str    = data.get("bulletin_date", "")
    bankroll    = float(data.get("bankroll", _BANKROLL_DEFAULT))
    bt_acc      = data.get("backtest_accuracy")
    bt_brier    = data.get("backtest_brier")

    preds = data.get("predictions", [])
    enriched = [_enrich_kelly(p, bankroll) for p in preds]

    sniper_list = [p for p in enriched if p.get("is_sniper")]
    no_bet_list = [p for p in enriched if p.get("is_no_bet")]
    tier_dist   = {"TIER_A": 0, "TIER_B": 0, "TIER_C": 0}
    pred_dist   = {"HOME_WIN": 0, "DRAW": 0, "AWAY_WIN": 0}
    confs       = []

    for p in enriched:
        t = p.get("tier", "TIER_C")
        tier_dist[t] = tier_dist.get(t, 0) + 1
        o = p.get("predicted_outcome", "")
        pred_dist[o] = pred_dist.get(o, 0) + 1
        if p.get("confidence"):
            confs.append(float(p["confidence"]))

    avg_conf   = round(sum(confs) / len(confs), 1) if confs else 0
    draw_pct   = round(pred_dist.get("DRAW", 0) / max(len(enriched), 1) * 100, 1)

    # ── Başlık kutusu ───────────────────────────────────────────────────────
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"{flag}  <b>{league_name.upper()} — TAHMİN BÜLTENİ</b>",
        f"📅 {date_str}   🧪 <i>Paper Trading (shadow)</i>",
    ]
    if bt_acc is not None:
        brier_tag = f"  ·  Brier {bt_brier:.3f}" if bt_brier else ""
        lines.append(f"📈 Backtest Acc: <b>{bt_acc:.1f}%</b>{brier_tag}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # ── Sniper özet ─────────────────────────────────────────────────────────
    if sniper_list:
        lines.append(f"\n🎯 <b>ELİT LİSTE  ({len(sniper_list)} maç)</b>")
        for p in sniper_list:
            home  = p.get("home_team", "?")
            away  = p.get("away_team", "?")
            short = _pred_short(p.get("predicted_outcome", ""))
            conf  = float(p.get("confidence", 0))
            odds  = p.get("odds") or {}
            ok    = {"HOME_WIN": "h", "DRAW": "d", "AWAY_WIN": "a"}.get(p.get("predicted_outcome", ""), "h")
            mkt   = f"@{odds[ok]}" if odds.get(ok) else ""
            ev    = p.get("_edge_pct")
            vs    = p.get("_value_score")
            ev_s  = f"  EV +%{ev:.1f}" if ev and ev > 0 else ""
            vs_s  = f"  💎{vs:.1f}" if vs else ""
            lines.append(
                f"  ⭐ <b>{home} – {away}</b>  ›  <b>{short}</b>  {mkt}"
                f"  <i>({conf:.0f}%{ev_s}{vs_s})</i>"
            )
        lines.append("<i>Model fırsat endeksi pozitif — ayrıntı için maç kartına bak.</i>")
    else:
        lines.append("\n<i>Bugün elit liste kriterleri karşılayan maç yok.</i>")

    # ── Genel bakış ─────────────────────────────────────────────────────────
    lines.append(
        f"\n📋 <b>GENEL BAKIŞ</b>\n"
        f"Maç: <b>{len(enriched)}</b>   "
        f"Elit: <b>{len(sniper_list)}</b>   "
        f"Oynanmaz: <b>{len(no_bet_list)}</b>\n"
        f"Tier  🔴A: {tier_dist['TIER_A']}  🟡B: {tier_dist['TIER_B']}  ⚪C: {tier_dist['TIER_C']}\n"
        f"Dağılım  1: {pred_dist['HOME_WIN']}  X: {pred_dist['DRAW']}  2: {pred_dist['AWAY_WIN']}  "
        f"(Beraberlik %{draw_pct})\n"
        f"Ort. Güven: <b>{avg_conf}%</b>"
    )

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Oturum özeti (son mesaj)
# ---------------------------------------------------------------------------

def _format_session_footer(data: dict) -> str:
    """Kapanış mesajı: istatistiksel anlamlılık uyarısı + session ID."""
    n_total  = len(data.get("predictions", []))
    sid      = data.get("session_id", "—")
    date_str = data.get("bulletin_date", "")

    if n_total < 100:
        significance_note = (
            "⚠️ <b>İstatistiksel Anlamlılık Uyarısı</b>\n"
            f"n={n_total} — Bu oran istatistiksel anlam taşımaz.\n"
            "Sinyal için minimum n=100 (yield>10%) veya n=500 gerekir.\n"
            "<i>(Kaynak: Buchdahl, football-data.co.uk araştırması)</i>"
        )
    else:
        significance_note = (
            f"✅ Oturum: n={n_total} maç · istatistiksel sinyal zonu."
        )

    return (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{significance_note}\n\n"
        f"🔑 Session: <code>{sid}</code>\n"
        f"📅 {date_str}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


# ---------------------------------------------------------------------------
# Ana formatter — birden fazla Telegram mesajı döndürür
# ---------------------------------------------------------------------------

def format_league_bulletin(data: dict) -> list[str]:
    """
    Kelly skoru hesaplanmış oturum JSON'unu Telegram HTML mesajlarına çevirir.

    Dönüş: list[str]
      - messages[0]  : başlık + sniper özeti + genel bakış
      - messages[1…n]: her maç için ayrı kart
      - messages[-1] : oturum kapanış özeti

    Her mesaj ≤4096 karakter (Telegram limiti).
    """
    bankroll = float(data.get("bankroll", _BANKROLL_DEFAULT))
    messages: list[str] = []

    # 1. Başlık
    messages.append(_format_header(data))

    # 2. Maç kartları
    for pred in data.get("predictions", []):
        card = format_match_card(pred, bankroll)
        if len(card) > _TG_MAX_LEN:
            card = card[:_TG_MAX_LEN - 50] + "\n\n<i>[mesaj kesildi — limit]</i>"
        messages.append(card)

    # 3. Kapanış özeti
    messages.append(_format_session_footer(data))

    return messages


# ---------------------------------------------------------------------------
# Telegram teslimat
# ---------------------------------------------------------------------------

def send_league_bulletin(
    messages: list[str],
    token: str,
    chat_id: str,
) -> None:
    """Tüm mesajları sırayla Telegram'a gönderir. Hata fırlatır."""
    import requests

    for i, text in enumerate(messages, 1):
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id":                  chat_id,
                "text":                     text,
                "parse_mode":               "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram hata (mesaj {i}/{len(messages)}): {data}")
        logger.info("Mesaj %d/%d gönderildi.", i, len(messages))


# ---------------------------------------------------------------------------
# Çıktı örneği (--example-output)
# ---------------------------------------------------------------------------

_SAMPLE_SESSION: dict = {
    "session_id":        "league_PL_2026-06-21_140512",
    "bulletin_date":     "2026-06-21",
    "league_key":        "PL",
    "league_name":       "Premier League",
    "season":            2025,
    "bankroll":          1000.0,
    "backtest_accuracy": 50.3,
    "backtest_brier":    0.625,
    "predictions": [
        {
            "home_team": "Arsenal", "away_team": "Manchester City",
            "kickoff_time": "2026-06-21T15:00:00Z",
            "predicted_outcome": "HOME_WIN",
            "probabilities": {"H": 48.4, "D": 26.4, "A": 25.1},
            "confidence": 64.0,
            "elo_home": 1950, "elo_away": 1980, "elo_gap": 30,
            "xg_home": 1.45, "xg_away": 1.20,
            "home_form_mult": 1.10, "away_form_mult": 0.92,
            "dc_rho": -0.10, "tier": "TIER_A",
            "is_sniper": True, "is_no_bet": False,
            "odds": {"h": 2.30, "d": 3.40, "a": 2.90,
                     "over_2_5": 1.85, "under_2_5": 1.95,
                     "btts_yes": 1.78, "btts_no": 2.02, "src": "pinnacle"},
            "clv": None,
        },
        {
            "home_team": "Liverpool", "away_team": "Chelsea",
            "kickoff_time": "2026-06-21T17:30:00Z",
            "predicted_outcome": "AWAY_WIN",
            "probabilities": {"H": 30.0, "D": 28.0, "A": 42.0},
            "confidence": 45.0,
            "elo_home": 1900, "elo_away": 1870, "elo_gap": 30,
            "xg_home": 1.60, "xg_away": 1.80,
            "home_form_mult": 0.95, "away_form_mult": 1.06,
            "dc_rho": -0.10, "tier": "TIER_B",
            "is_sniper": False, "is_no_bet": False,
            "odds": {"h": 2.10, "d": 3.30, "a": 3.60,
                     "over_2_5": 1.72, "under_2_5": 2.10,
                     "btts_yes": 1.68, "btts_no": 2.15, "src": "betfair"},
            "clv": None,
        },
        {
            "home_team": "Tottenham", "away_team": "Everton",
            "kickoff_time": "2026-06-21T14:00:00Z",
            "predicted_outcome": "HOME_WIN",
            "probabilities": {"H": 55.0, "D": 24.0, "A": 21.0},
            "confidence": 37.0,
            "elo_home": 1810, "elo_away": 1650, "elo_gap": 160,
            "xg_home": 2.10, "xg_away": 0.90,
            "home_form_mult": 1.02, "away_form_mult": 0.98,
            "dc_rho": -0.10, "tier": "TIER_A",
            "is_sniper": False, "is_no_bet": True,
            "odds": None, "clv": None,
        },
    ],
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Lig shadow bülteni formatter + teslimatı")
    parser.add_argument("--deliver",        action="store_true", help="Telegram'a gönder")
    parser.add_argument("--json",           metavar="FILE",      help="Girdi JSON dosyası")
    parser.add_argument("--example-output", action="store_true", help="Örnek çıktıyı stdout'a yaz")
    args = parser.parse_args()

    if args.example_output or not args.json:
        data = _SAMPLE_SESSION
        print("──────── ÖRNEK ÇIKTI (DRY-RUN) ────────\n")
    else:
        with open(args.json, encoding="utf-8") as fh:
            data = json.load(fh)

    messages = format_league_bulletin(data)

    if not args.deliver:
        for i, msg in enumerate(messages, 1):
            print(f"\n{'═'*50}")
            print(f"MESAJ {i}/{len(messages)}")
            print(f"{'═'*50}")
            print(msg)
        print(f"\nToplam {len(messages)} mesaj, --deliver ile gönderilebilir.")
        return 0

    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_PERSONAL_CHANNEL", "").strip()
    missing = [n for n, v in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_PERSONAL_CHANNEL", chat_id)) if not v]
    if missing:
        logger.error("Eksik env var: %s", ", ".join(missing))
        return 1

    send_league_bulletin(messages, token, chat_id)
    logger.info("%d mesaj gönderildi.", len(messages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
