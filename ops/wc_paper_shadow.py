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
    """Resolve a team name from DB; fall back to the raw id if unavailable."""
    if team_id is None:
        return "?"
    try:
        row = db.fetchone("SELECT name FROM teams WHERE id = ?", (team_id,))
        if row and row.get("name"):
            return row["name"]
    except Exception:
        pass
    return f"ID {team_id}"


# ---------------------------------------------------------------------------
# API fallback helpers
# ---------------------------------------------------------------------------

def _fetch_football_data_org(date_str: str):
    """
    Fetch WC fixtures from football-data.org free tier.
    Returns list of match dicts on success (may be empty), None on failure.
    """
    import requests
    from config.settings import FOOTBALL_DATA_ORG_KEY

    headers = {"X-Auth-Token": FOOTBALL_DATA_ORG_KEY} if FOOTBALL_DATA_ORG_KEY else {}
    try:
        resp = requests.get(
            "https://api.football-data.org/v4/competitions/WC/matches",
            params={"dateFrom": date_str, "dateTo": date_str},
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 403:
            logger.warning("football-data.org 403 — WC may require a paid-tier key")
            return None
        if resp.status_code != 200:
            logger.warning("football-data.org HTTP %d", resp.status_code)
            return None

        skip = {"FINISHED", "AWARDED", "POSTPONED", "CANCELLED"}
        matches = []
        for m in resp.json().get("matches", []):
            if m.get("status") in skip:
                continue
            utc = m.get("utcDate", "")
            matches.append({
                "id": m["id"],
                "home_name": m.get("homeTeam", {}).get("name", "?"),
                "away_name": m.get("awayTeam", {}).get("name", "?"),
                "time": (utc[11:16] + " UTC") if len(utc) >= 16 else "—",
                "date": date_str,
            })
        logger.info("football-data.org: %d match(es) on %s", len(matches), date_str)
        return matches
    except Exception as e:
        logger.warning("football-data.org fetch failed: %s", e)
        return None


def _fetch_api_football(date_str: str):
    """
    Fetch WC 2026 fixtures from API-Football (league 1).
    Returns list of match dicts on success (may be empty), None on failure.
    """
    import requests
    from config.settings import API_FOOTBALL_KEY

    if not API_FOOTBALL_KEY:
        logger.info("API_FOOTBALL_KEY not set — skipping API-Football fallback")
        return None
    try:
        resp = requests.get(
            "https://v3.football.api-sports.io/fixtures",
            params={"league": "1", "season": "2026", "date": date_str},
            headers={
                "x-rapidapi-host": "v3.football.api-sports.io",
                "x-rapidapi-key": API_FOOTBALL_KEY,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("API-Football HTTP %d", resp.status_code)
            return None

        skip = {"FT", "AET", "PEN", "PST", "CANC", "ABD"}
        matches = []
        for f in resp.json().get("response", []):
            if f.get("fixture", {}).get("status", {}).get("short") in skip:
                continue
            fixture_date = f.get("fixture", {}).get("date", "")
            matches.append({
                "id": f["fixture"]["id"],
                "home_name": f.get("teams", {}).get("home", {}).get("name", "?"),
                "away_name": f.get("teams", {}).get("away", {}).get("name", "?"),
                "time": (fixture_date[11:16] + " UTC") if len(fixture_date) >= 16 else "—",
                "date": date_str,
            })
        logger.info("API-Football: %d match(es) on %s", len(matches), date_str)
        return matches
    except Exception as e:
        logger.warning("API-Football fetch failed: %s", e)
        return None


def fetch_wc_matches_from_api(date_str: str) -> tuple[list[dict], str]:
    """
    Try external APIs in order: football-data.org → API-Football.
    Returns (matches, source_label). source_label is empty string if all sources failed.
    """
    result = _fetch_football_data_org(date_str)
    if result is not None:
        return result, "football-data.org"

    result = _fetch_api_football(date_str)
    if result is not None:
        return result, "API-Football"

    return [], ""


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


def shadow_predict(match: dict) -> dict:
    """
    Produce a paper-trading prediction for one match using ShadowPredictor.

    Accepts a match dict (from DB or API fallback) carrying home_name /
    away_name.  Delegates to ops.shadow_predictor — never the production
    inference stack — so this function is safe to call alongside live jobs.
    """
    from ops.shadow_predictor import ShadowPredictor

    home = match.get("home_name") or "Unknown"
    away = match.get("away_name") or "Unknown"
    return ShadowPredictor().predict(home, away)


def format_match_block(db, match: dict, pred: dict) -> str:
    # API-sourced matches carry pre-resolved names; DB matches need a lookup.
    home = match.get("home_name") or _team_name(db, match.get("home_team_id"))
    away = match.get("away_name") or _team_name(db, match.get("away_team_id"))
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


def build_bulletin(
    db, matches: list[dict], date_str: str, source_status: str, data_source_label: str = ""
) -> str:
    source_note = f" · Veri: {data_source_label}" if data_source_label else ""
    header = (
        "🧪 <b>DÜNYA KUPASI — GÖLGE (SHADOW) KUPONU</b>\n"
        f"📅 {date_str}{source_note}\n"
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
            pred = shadow_predict(m)
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
        data_source_label = "DB"
        logger.info(
            "DB source: %s | %d match(es) for %s.",
            source_status, len(matches), args.date,
        )

        # Fall back to external API when DB is unusable OR has no matches today.
        needs_api = source_status in (SourceStatus.NO_TABLE, SourceStatus.DB_ERROR) or (
            source_status == SourceStatus.OK and not matches
        )
        if needs_api:
            reason = "no matches in DB" if source_status == SourceStatus.OK else source_status
            logger.info("Trying API fallback (%s)...", reason)
            api_matches, api_source = fetch_wc_matches_from_api(args.date)
            if api_source:  # at least one API responded (may still be 0 matches)
                matches = api_matches
                source_status = SourceStatus.OK
                data_source_label = api_source
                logger.info(
                    "API fallback succeeded: %d match(es) from %s",
                    len(api_matches), api_source,
                )
            else:
                data_source_label = ""
                logger.warning("All API fallbacks failed — keeping original source status.")

        bulletin = build_bulletin(db, matches, args.date, source_status, data_source_label)
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
