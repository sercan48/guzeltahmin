"""
World Cup Paper / Shadow delivery (minimal).

Shadow mode (Phase 11): the prediction engine runs live but NO real bets are
placed. This script pulls today's World Cup predictions from the existing
World Cup engine and delivers a clearly-labelled "shadow" bulletin to a
personal Telegram channel for paper-trading observation.

It is intentionally read-only: it never mutates the production `matches`
flags, so running it alongside the live bot is safe.

Usage:
    python -m ops.wc_paper_shadow              # dry-run: print to stdout
    python -m ops.wc_paper_shadow --deliver    # send to the personal channel
    python -m ops.wc_paper_shadow --date 2026-06-14 --deliver

Environment:
    TELEGRAM_BOT_TOKEN          Bot token (required for --deliver).
    TELEGRAM_PERSONAL_CHANNEL   Target chat id for the shadow bulletin
                                (required for --deliver).
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("wc_paper_shadow")

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv is optional at runtime
    pass


def _pick_label(prediction: str) -> str:
    return {
        "HOME_WIN": "1 (Ev Sahibi)",
        "AWAY_WIN": "2 (Deplasman)",
        "DRAW": "X (Beraberlik)",
    }.get(prediction, prediction)


def _team_name(db, team_id) -> str:
    """Resolve a team name; fall back to the raw id if unavailable."""
    if team_id is None:
        return "?"
    try:
        row = db.fetchone("SELECT name FROM teams WHERE id = ?", (team_id,))
        if row and row.get("name"):
            return row["name"]
    except Exception:
        pass
    return f"ID {team_id}"


class SourceStatus:
    """Outcome of a match-fetch attempt."""

    OK = "ok"           # table exists, query ran — zero or more rows
    NO_TABLE = "no_table"   # matches table doesn't exist in this DB
    DB_ERROR = "db_error"   # connection or unexpected query error


def get_todays_wc_matches(db, date_str: str) -> tuple[list[dict], str]:
    """
    Read-only fetch of unsettled matches scheduled for `date_str`.

    Returns (rows, SourceStatus.*) so callers can distinguish:
      OK        — data source is healthy; rows may be empty (genuinely no matches)
      NO_TABLE  — matches table is absent (DB not initialised / wrong environment)
      DB_ERROR  — connection or query failure
    """
    try:
        # Check table existence first so the error is explicit, not a silent empty list.
        if not db.table_exists("matches"):
            logger.warning(
                "Table 'matches' not found in DB. "
                "Run scripts/init_db.py or scripts/update_db_wc2026.py to initialise."
            )
            return [], SourceStatus.NO_TABLE

        rows = db.fetchall(
            "SELECT id, home_team_id, away_team_id, date, time "
            "FROM matches WHERE DATE(date) = ? AND ft_result IS NULL "
            "ORDER BY time",
            (date_str,),
        )
        return rows, SourceStatus.OK

    except Exception as e:
        logger.error("DB error while reading matches for %s: %s", date_str, e)
        return [], SourceStatus.DB_ERROR


def shadow_predict(match_id: int) -> dict:
    """
    Run the existing World Cup ensemble + calibration for a match WITHOUT any
    database side effects (no `is_*_run` flag writes). Mirrors the
    'preliminary' tier path of `run_tier_inference`.
    """
    from src.features.wc_confidence_calibrator import calibrate_confidence
    from src.model.wc_ensemble_inference import run_ensemble_inference
    from src.model.wc_three_tier_inference import (
        extract_team_stats,
        get_expected_lineups,
    )

    lineups = get_expected_lineups(match_id)
    team_a = extract_team_stats(lineups[0])
    team_b = extract_team_stats(lineups[1])

    ensemble = run_ensemble_inference(team_a, team_b)

    raw_prediction = "DRAW"
    if ensemble["home_win_prob"] > 45:
        raw_prediction = "HOME_WIN"
    elif ensemble["away_win_prob"] > 45:
        raw_prediction = "AWAY_WIN"

    # Shadow preliminary phase: no market delta applied.
    calibrated = calibrate_confidence(
        model_prediction=raw_prediction,
        confidence_score=ensemble["confidence_score"],
        market_delta={"home_delta": 0.0, "away_delta": 0.0},
    )

    return {**ensemble, **calibrated, "raw_prediction": raw_prediction}


def format_match_block(db, match: dict, pred: dict) -> str:
    home = _team_name(db, match.get("home_team_id"))
    away = _team_name(db, match.get("away_team_id"))
    kickoff = match.get("time") or "—"

    if pred.get("is_no_bet"):
        status = f"⛔ OYNANMAZ ({pred.get('market_note') or 'No-Bet'})"
    else:
        status = "✅ İZLENİYOR"

    return (
        f"🏟️ <b>{home}</b> vs <b>{away}</b>  —  🕒 {kickoff}\n"
        f"🛡️ <b>Statü:</b> {status}\n"
        f"🎯 <b>Model Seçimi:</b> {_pick_label(pred.get('raw_prediction', 'DRAW'))}\n"
        f"⚽ <b>Güven:</b> %{round(float(pred.get('final_confidence', 0)), 1)}\n"
        f"1️⃣ %{round(float(pred.get('home_win_prob', 0)), 1)} | "
        f"❌ %{round(float(pred.get('draw_prob', 0)), 1)} | "
        f"2️⃣ %{round(float(pred.get('away_win_prob', 0)), 1)}\n"
        f"📊 <i>xG: {pred.get('expected_goals_a', 0)} - {pred.get('expected_goals_b', 0)}</i>"
    )


def build_bulletin(db, matches: list[dict], date_str: str, source_status: str) -> str:
    header = (
        "🧪 <b>DÜNYA KUPASI — GÖLGE (SHADOW) KUPONU</b>\n"
        f"📅 {date_str}\n"
        "<i>Kağıt üzerinde (paper) takip — gerçek bahis YOK. "
        "Yalnızca sinyal kalitesi gözlemleniyor.</i>\n"
    )

    if source_status == SourceStatus.NO_TABLE:
        return (
            header
            + "\n⚠️ <b>Veri kaynağı hazır değil.</b>\n"
            "<i>matches tablosu bu ortamda bulunamadı. "
            "Gerçek üretim DB'sine karşı çalıştırılması gerekiyor "
            "veya önce scripts/update_db_wc2026.py ile DB başlatılmalı.</i>"
        )

    if source_status == SourceStatus.DB_ERROR:
        return (
            header
            + "\n❌ <b>Veritabanı bağlantı hatası.</b>\n"
            "<i>Maçlar okunamadı. DB yapılandırmasını ve bağlantıyı kontrol edin.</i>"
        )

    # source_status == OK
    if not matches:
        return header + "\n📭 Bugün için planlanmış Dünya Kupası maçı bulunamadı."

    blocks = []
    for m in matches:
        try:
            pred = shadow_predict(m["id"])
            blocks.append(format_match_block(db, m, pred))
        except Exception as e:
            logger.error("Prediction failed for match %s: %s", m.get("id"), e)

    if not blocks:
        return header + "\n⚠️ Maçlar bulundu ancak tahmin üretilemedi."

    return header + "\n" + "\n\n".join(blocks)


def send_telegram(token: str, chat_id: str, text: str) -> dict:
    import requests

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="World Cup paper/shadow delivery")
    parser.add_argument(
        "--deliver",
        action="store_true",
        help="Actually send to the personal Telegram channel (otherwise dry-run).",
    )
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="Match date (YYYY-MM-DD). Defaults to today.",
    )
    args = parser.parse_args()

    from src.db.base import get_backend

    db = get_backend()
    db.connect()
    try:
        matches, source_status = get_todays_wc_matches(db, args.date)
        logger.info(
            "Source: %s | Found %d match(es) for %s.",
            source_status, len(matches), args.date,
        )
        bulletin = build_bulletin(db, matches, args.date, source_status)
    finally:
        db.close()

    if not args.deliver:
        print("\n--- DRY RUN (no message sent; pass --deliver to send) ---\n")
        print(bulletin)
        return 0

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_PERSONAL_CHANNEL", "").strip()
    missing = [
        name
        for name, val in (
            ("TELEGRAM_BOT_TOKEN", token),
            ("TELEGRAM_PERSONAL_CHANNEL", chat_id),
        )
        if not val
    ]
    if missing:
        logger.error("Missing required env var(s): %s", ", ".join(missing))
        return 1

    send_telegram(token, chat_id, bulletin)
    logger.info("Shadow bulletin delivered to %s.", chat_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
