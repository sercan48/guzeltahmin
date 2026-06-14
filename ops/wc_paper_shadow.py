"""
One-shot CLI that delivers today's World Cup paper-portfolio shadow picks
to a personal Telegram channel.

Usage:
    TELEGRAM_BOT_TOKEN=<token> \\
    TELEGRAM_PERSONAL_CHANNEL=-1003912144811 \\
    python3 -m ops.wc_paper_shadow --deliver
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta

import requests

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


def _tg_send(token: str, chat_id: str, text: str) -> bool:
    url = _TELEGRAM_URL.format(token=token)
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
    except requests.RequestException as exc:
        logger.error("Network error sending to Telegram: %s", exc)
        return False
    if not resp.ok:
        logger.error("Telegram API %s: %s", resp.status_code, resp.text[:200])
        return False
    return True


def _fetch_predictions(db) -> list:
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        return db.fetchall(
            """
            SELECT p.predicted_result, p.confidence_score,
                   p.home_win_prob, p.draw_prob, p.away_win_prob,
                   p.over25_prob, p.btts_prob, p.value_margin,
                   m.date, m.time, m.league_code,
                   t1.name AS home_team, t2.name AS away_team,
                   o.home_odds, o.draw_odds, o.away_odds
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            JOIN teams t1 ON m.home_team_id = t1.id
            JOIN teams t2 ON m.away_team_id = t2.id
            LEFT JOIN (
                SELECT match_id, home_odds, draw_odds, away_odds
                FROM odds
                WHERE id IN (SELECT MAX(id) FROM odds GROUP BY match_id)
            ) o ON p.match_id = o.match_id
            WHERE DATE(m.date) IN (?, ?)
              AND m.ft_result IS NULL
              AND p.confidence_score >= 55
            ORDER BY p.confidence_score DESC
            """,
            (today, tomorrow),
        )
    except Exception as exc:
        logger.warning("DB sorgu hatası: %s", exc)
        return []


def _odds_fmt(val) -> str:
    return f"{val:.2f}" if val and val > 1 else "—"


def _result_label(code: str) -> str:
    return {"H": "MS 1 (Ev)", "D": "MS X (Beraberlik)", "A": "MS 2 (Dep)"}.get(code, code or "?")


def _paper_stake(conf: float) -> str:
    if conf >= 80:
        return "🔥 3 birim"
    if conf >= 70:
        return "✅ 2 birim"
    if conf >= 60:
        return "⚡ 1 birim"
    return "⚠️ ½ birim"


def _build_bulletin(rows: list) -> str:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [
        f"🏆 <b>DÜNYA KUPASI PAPER SHADOW — {now}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"<i>Toplam {len(rows)} tahmin | yalnızca kişisel takip</i>",
        "",
    ]

    for i, row in enumerate(rows, 1):
        result = row.get("predicted_result") or "?"
        conf = row.get("confidence_score") or 0
        h_prob = (row.get("home_win_prob") or 0) * 100
        d_prob = (row.get("draw_prob") or 0) * 100
        a_prob = (row.get("away_win_prob") or 0) * 100
        o25 = (row.get("over25_prob") or 0) * 100
        btts = (row.get("btts_prob") or 0) * 100

        h_odds = row.get("home_odds")
        d_odds = row.get("draw_odds")
        a_odds = row.get("away_odds")
        pick_odds = {"H": h_odds, "D": d_odds, "A": a_odds}.get(result)

        pick_prob_frac = {"H": row.get("home_win_prob", 0) or 0,
                          "D": row.get("draw_prob", 0) or 0,
                          "A": row.get("away_win_prob", 0) or 0}.get(result, 0)

        ev_str = ""
        if pick_odds and pick_odds > 1 and pick_prob_frac:
            ev = pick_prob_frac * pick_odds - 1.0
            ev_str = f" | EV: {ev * 100:+.1f}%"

        match_date = (row.get("date") or "")[:10]
        match_time = (row.get("time") or "").strip()
        time_str = f"{match_date} {match_time}".strip() if match_time else match_date

        lines += [
            f"<b>{i}. {row.get('home_team', '?')} vs {row.get('away_team', '?')}</b>",
            f"   📅 {time_str}  |  {row.get('league_code', '??')}",
            f"   🎯 TAHMİN: {_result_label(result)}",
            f"   🔥 Güven: %{conf:.0f}  |  Oran: {_odds_fmt(pick_odds)}{ev_str}",
            f"   📊 1=%{h_prob:.0f}  X=%{d_prob:.0f}  2=%{a_prob:.0f}",
            f"   💰 Paper stake: {_paper_stake(conf)}",
        ]
        if o25 >= 50:
            lines.append(f"   ⚽ Ü2.5 ihtimali: %{o25:.0f}")
        if btts >= 50:
            lines.append(f"   🔄 KG Var ihtimali: %{btts:.0f}")
        lines.append("")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "🤖 <i>Güzel Tahmin Paper Shadow — gerçek para değil</i>",
    ]
    return "\n".join(lines)


def _chunk(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, buf = [], []
    length = 0
    for line in text.split("\n"):
        if length + len(line) + 1 > limit and buf:
            chunks.append("\n".join(buf))
            buf, length = [], 0
        buf.append(line)
        length += len(line) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def deliver() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    channel = os.environ.get("TELEGRAM_PERSONAL_CHANNEL", "")

    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is not set")
        sys.exit(1)
    if not channel:
        logger.error("TELEGRAM_PERSONAL_CHANNEL is not set")
        sys.exit(1)

    from src.db.base import get_backend

    db = get_backend()
    db.connect()
    try:
        rows = _fetch_predictions(db)
    finally:
        db.close()

    if not rows:
        msg = (
            "🏆 <b>DÜNYA KUPASI PAPER SHADOW</b>\n\n"
            "Bugün için yeterli güvenli tahmin bulunamadı (eşik: %55)."
        )
        _tg_send(token, channel, msg)
        logger.info("No predictions found; empty bulletin sent.")
        return

    bulletin = _build_bulletin(rows)
    parts = _chunk(bulletin)
    for part in parts:
        ok = _tg_send(token, channel, part)
        if not ok:
            sys.exit(1)

    logger.info("Paper shadow bulletin delivered to %s (%d matches, %d part(s)).",
                channel, len(rows), len(parts))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="WC Paper Shadow — deliver today's shadow picks to a personal Telegram channel"
    )
    parser.add_argument("--deliver", action="store_true",
                        help="Fetch predictions and send the bulletin now")
    args = parser.parse_args()

    if args.deliver:
        deliver()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
