"""
Paper-shadow World Cup 2026 prediction tracker.

Loads today's WC 2026 group-stage fixtures, runs the Monte Carlo + XGB
ensemble on each, and either prints the bulletin (dry run) or delivers it to
TELEGRAM_PERSONAL_CHANNEL.

Usage:
    python3 -m ops.wc_paper_shadow                       # dry run
    python3 -m ops.wc_paper_shadow --deliver             # post to channel
    python3 -m ops.wc_paper_shadow --date 2026-06-14    # specific date
"""

import argparse
import json
import logging
import os
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.wc_monte_carlo import TeamStats, run_monte_carlo_simulation
from src.model.wc_ensemble_inference import run_ensemble_inference

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Approximate Elo ratings for WC 2026 qualified teams ──────────────────────
_ELO: dict[str, int] = {
    "Argentina": 2060,
    "France": 2005,
    "Spain": 1990,
    "England": 1978,
    "Brazil": 1965,
    "Portugal": 1958,
    "Germany": 1948,
    "Netherlands": 1922,
    "Belgium": 1905,
    "Croatia": 1875,
    "Uruguay": 1858,
    "Switzerland": 1845,
    "Denmark": 1828,
    "Morocco": 1838,
    "Italy": 1852,
    "Colombia": 1822,
    "Mexico": 1818,
    "USA": 1805,
    "Senegal": 1788,
    "Japan": 1778,
    "Canada": 1762,
    "Ecuador": 1728,
    "South Korea": 1705,
    "Serbia": 1695,
    "Ukraine": 1705,
    "Iran": 1682,
    "Australia": 1672,
    "Poland": 1722,
    "Ghana": 1642,
    "Cameroon": 1648,
    "Tunisia": 1662,
    "Nigeria": 1715,
    "Saudi Arabia": 1642,
    "Qatar": 1598,
    "Costa Rica": 1642,
    "Paraguay": 1682,
    "Venezuela": 1692,
    "Bolivia": 1618,
    "Panama": 1632,
    "Honduras": 1612,
    "Jamaica": 1602,
    "New Zealand": 1592,
    "Uzbekistan": 1662,
    "Austria": 1732,
    "Turkey": 1752,
    "Romania": 1682,
    "Hungary": 1692,
    "Slovakia": 1682,
    "Mali": 1682,
    "Egypt": 1702,
    "Algeria": 1682,
    "Albania": 1638,
}

# ── WC 2026 Group Stage Fixture List ─────────────────────────────────────────
# (date, home, away, group)
# 48-team format: 16 groups × 3 teams, 3 matchdays each.
# Dates approximate based on official WC 2026 scheduling blocks.
_SCHEDULE: list[tuple[str, str, str, str]] = [
    # ── June 11 (Day 1) ──────────────────────────────────────────────────────
    ("2026-06-11", "Mexico",      "Ecuador",     "B"),
    ("2026-06-11", "Canada",      "Colombia",    "A"),
    # ── June 12 (Day 2) ──────────────────────────────────────────────────────
    ("2026-06-12", "USA",         "Bolivia",     "C"),
    ("2026-06-12", "Italy",       "Albania",     "D"),
    ("2026-06-12", "Argentina",   "Venezuela",   "E"),
    ("2026-06-12", "Spain",       "Honduras",    "F"),
    # ── June 13 (Day 3) ──────────────────────────────────────────────────────
    ("2026-06-13", "France",      "Japan",       "G"),
    ("2026-06-13", "Germany",     "Saudi Arabia","H"),
    ("2026-06-13", "Brazil",      "Nigeria",     "I"),
    ("2026-06-13", "Netherlands", "Cameroon",    "J"),
    # ── June 14 (Day 4) ──────────────────────────────────────────────────────
    ("2026-06-14", "England",     "Serbia",      "K"),
    ("2026-06-14", "Portugal",    "Morocco",     "L"),
    ("2026-06-14", "Uruguay",     "Iran",        "M"),
    ("2026-06-14", "Belgium",     "Australia",   "N"),
    # ── June 15 (Day 5) ──────────────────────────────────────────────────────
    ("2026-06-15", "Mexico",      "Jamaica",     "B"),
    ("2026-06-15", "Canada",      "Paraguay",    "A"),
    ("2026-06-15", "USA",         "Costa Rica",  "C"),
    ("2026-06-15", "Italy",       "Ecuador",     "D"),
    # ── June 16 (Day 6) ──────────────────────────────────────────────────────
    ("2026-06-16", "Argentina",   "Bolivia",     "E"),
    ("2026-06-16", "Spain",       "New Zealand", "F"),
    ("2026-06-16", "France",      "Nigeria",     "G"),
    ("2026-06-16", "Germany",     "Egypt",       "H"),
    # ── June 17 (Day 7) ──────────────────────────────────────────────────────
    ("2026-06-17", "Brazil",      "Cameroon",    "I"),
    ("2026-06-17", "Netherlands", "Japan",       "J"),
    ("2026-06-17", "England",     "Ukraine",     "K"),
    ("2026-06-17", "Portugal",    "Turkey",      "L"),
    # ── June 18 (Day 8) ──────────────────────────────────────────────────────
    ("2026-06-18", "Uruguay",     "Saudi Arabia","M"),
    ("2026-06-18", "Belgium",     "Croatia",     "N"),
    ("2026-06-18", "South Korea", "Senegal",     "O"),
    ("2026-06-18", "Denmark",     "Poland",      "P"),
    # ── June 19 (Day 9) ──────────────────────────────────────────────────────
    ("2026-06-19", "Mexico",      "Canada",      "A"),
    ("2026-06-19", "USA",         "Colombia",    "A"),
    ("2026-06-19", "Ecuador",     "Jamaica",     "B"),
    # ── June 20 (Day 10) ─────────────────────────────────────────────────────
    ("2026-06-20", "Argentina",   "Colombia",    "E"),
    ("2026-06-20", "France",      "Saudi Arabia","G"),
    ("2026-06-20", "Germany",     "Japan",       "H"),
    # ── June 21 (Day 11) ─────────────────────────────────────────────────────
    ("2026-06-21", "England",     "Morocco",     "L"),
    ("2026-06-21", "Brazil",      "Netherlands", "J"),
    ("2026-06-21", "Portugal",    "Serbia",      "K"),
    # ── June 22 (Day 12) ─────────────────────────────────────────────────────
    ("2026-06-22", "Belgium",     "Uruguay",     "M"),
    ("2026-06-22", "Spain",       "Italy",       "F"),
    ("2026-06-22", "Croatia",     "Denmark",     "N"),
]


def _elo(team: str) -> int:
    return _ELO.get(team, 1650)


def _make_stats(team: str, is_home: bool) -> TeamStats:
    elo = _elo(team)
    home_advantage = 25 if is_home else 0
    strength_factor = (elo - 1700) / 200
    return TeamStats(
        elo=elo + home_advantage,
        att_vs_def_delta=2.0 + strength_factor,
        synergy=5.0,
        fatigue=0.0,
    )


def _pick(home_p: float, draw_p: float, away_p: float) -> str:
    best = max(home_p, draw_p, away_p)
    if best == home_p:
        return "MS 1"
    if best == draw_p:
        return "MS X"
    return "MS 2"


def _bar(conf: float, length: int = 10) -> str:
    filled = max(0, min(length, round(length * conf / 100)))
    return "█" * filled + "░" * (length - filled)


def _confidence_emoji(conf: float) -> str:
    if conf >= 80:
        return "🔥"
    if conf >= 65:
        return "✅"
    if conf >= 50:
        return "⚡"
    return "⚠️"


# ── Core prediction ───────────────────────────────────────────────────────────

def predict_matches(target_date: str) -> list[dict]:
    """Run ensemble inference for all WC fixtures on target_date."""
    fixtures = [(h, a, g) for d, h, a, g in _SCHEDULE if d == target_date]
    if not fixtures:
        return []

    results = []
    for home, away, group in fixtures:
        home_stats = _make_stats(home, is_home=True)
        away_stats = _make_stats(away, is_home=False)

        try:
            res = run_ensemble_inference(home_stats, away_stats)
        except Exception as exc:
            log.error("Inference failed for %s vs %s: %s", home, away, exc)
            continue

        h_prob = res["home_win_prob"]
        d_prob = res["draw_prob"]
        a_prob = res["away_win_prob"]

        results.append({
            "home": home,
            "away": away,
            "group": group,
            "pick": _pick(h_prob, d_prob, a_prob),
            "home_prob": h_prob,
            "draw_prob": d_prob,
            "away_prob": a_prob,
            "xg_home": res["expected_goals_a"],
            "xg_away": res["expected_goals_b"],
            "confidence": res["confidence_score"],
            "ensemble_status": res["ensemble_status"],
            "mc_weight": res["mc_weight"],
            "xgb_weight": res["xgb_weight"],
        })

    # Sort by confidence descending
    results.sort(key=lambda x: x["confidence"], reverse=True)
    return results


# ── Formatting ────────────────────────────────────────────────────────────────

def format_bulletin(picks: list[dict], target_date: str) -> str:
    lines = [
        "🏆 DÜNYA KUPASI 2026 — PAPER SHADOW",
        f"📅 {target_date}  |  {len(picks)} maç",
        "━" * 36,
    ]

    if not picks:
        lines.append("\nBugün için fixture bulunamadı.")
        return "\n".join(lines)

    for p in picks:
        conf = p["confidence"]
        emoji = _confidence_emoji(conf)
        lines += [
            "",
            f"🟡 Grup {p['group']}  {p['home']} 🆚 {p['away']}",
            f"🎯 TAHMİN: {p['pick']}",
            f"{emoji} Güven: %{conf:.0f}  {_bar(conf)}",
            f"📊 1=%{p['home_prob']:.1f}  X=%{p['draw_prob']:.1f}  2=%{p['away_prob']:.1f}",
            f"⚽ xG: {p['xg_home']:.2f} – {p['xg_away']:.2f}",
            f"🤖 {p['ensemble_status']} (MC {p['mc_weight']:.0f}% / XGB {p['xgb_weight']:.0f}%)",
            "─" * 36,
        ]

    lines += [
        "",
        "📌 PAPER SHADOW — dahili, yayınlanmadı.",
        "🤖 Güzel Tahmin Ensemble AI v2",
    ]
    return "\n".join(lines)


# ── Telegram delivery (raw HTTP, no library dep) ──────────────────────────────

def _send_telegram(text: str, token: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read())
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API error: {body}")
    log.info("Delivered to chat_id=%s (message_id=%s)", chat_id, body["result"]["message_id"])


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="WC 2026 Paper Shadow Tracker")
    parser.add_argument("--deliver", action="store_true",
                        help="Post bulletin to TELEGRAM_PERSONAL_CHANNEL")
    parser.add_argument("--date", default=None,
                        help="Target date (YYYY-MM-DD). Default: today.")
    args = parser.parse_args()

    target_date = args.date or date.today().isoformat()
    log.info("WC paper shadow — %s", target_date)

    picks = predict_matches(target_date)
    bulletin = format_bulletin(picks, target_date)

    print("\n" + bulletin + "\n")

    if args.deliver:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        channel = os.environ.get("TELEGRAM_PERSONAL_CHANNEL", "")
        if not token:
            log.error("TELEGRAM_BOT_TOKEN is not set.")
            sys.exit(1)
        if not channel:
            log.error("TELEGRAM_PERSONAL_CHANNEL is not set.")
            sys.exit(1)

        log.info("Delivering to channel %s …", channel)
        _send_telegram(bulletin, token, channel)
        log.info("Done.")


if __name__ == "__main__":
    main()
