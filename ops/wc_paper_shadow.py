"""
World Cup Paper / Shadow delivery — Personal Shadow Delivery Mode.

Shadow mode (Phase 11): prediction engine runs live but NO real bets are
placed.  Delivers a clearly-labelled shadow bulletin plus a structured run
report to a single personal Telegram channel.

Safety constraints
------------------
* Read-only: never mutates production `matches` flags.
* Personal only: single TELEGRAM_PERSONAL_CHANNEL, no routing / paywall.
* No DB schema changes; no new services.
* Production modules (M1–M11, L3–L5) are never imported here.

Usage:
    python -m ops.wc_paper_shadow              # dry-run: stdout only
    python -m ops.wc_paper_shadow --deliver    # send to personal channel
    python -m ops.wc_paper_shadow --date 2026-06-14 --deliver

Environment:
    TELEGRAM_BOT_TOKEN          Bot token (required for --deliver).
    TELEGRAM_PERSONAL_CHANNEL   Personal chat id (required for --deliver).
    FOOTBALL_DATA_ORG_KEY       Fixture source — falls back gracefully if absent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("wc_paper_shadow")

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------

def _pick_label(prediction: str) -> str:
    return {
        "HOME_WIN": "1 (Ev Sahibi)",
        "AWAY_WIN": "2 (Deplasman)",
        "DRAW":     "X (Beraberlik)",
    }.get(prediction, prediction)


def _team_name(db, team_id) -> str:
    """Resolve a team name from DB; fall back to the raw id string."""
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
# Tier assignment
# ---------------------------------------------------------------------------

def _assign_tier(pred: dict) -> str:
    """
    TIER_A: Elo gap ≥ 150 — strong directional signal.
    TIER_B: Elo gap  50–149 — moderate signal.
    TIER_C: Elo gap  < 50  — too close, no-bet territory.
    """
    diff = abs(pred.get("elo_home", 0) - pred.get("elo_away", 0))
    if diff >= 150:
        return "TIER_A"
    if diff >= 50:
        return "TIER_B"
    return "TIER_C"


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

def _compute_replay_hash(match_results: list[dict]) -> str:
    """
    Deterministic SHA-256 over all prediction fields.
    Two runs on the same fixture list must produce the same hash.
    """
    payload = [
        {
            "prediction":    r["prediction"],
            "home_win_prob": r["home_win_prob"],
            "draw_prob":     r["draw_prob"],
            "away_win_prob": r["away_win_prob"],
            "tier":          r["tier"],
        }
        for r in match_results
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def _build_run_report(
    session_id: str,
    date_str: str,
    data_source_label: str,
    shadow_fallback_active: bool,
    source_status: str,
    match_results: list[dict],
) -> dict:
    """Build the structured JSON run report."""
    n = len(match_results)
    valid = [r for r in match_results if r["prediction"] != "NO_DATA"]

    pred_dist = {"HOME_WIN": 0, "DRAW": 0, "AWAY_WIN": 0, "NO_DATA": 0}
    tier_dist = {"TIER_A": 0, "TIER_B": 0, "TIER_C": 0}
    conf_buckets = {"<40": 0, "40-60": 0, ">=60": 0}

    for r in match_results:
        key = r["prediction"]
        pred_dist[key] = pred_dist.get(key, 0) + 1
        if r["tier"] in tier_dist:
            tier_dist[r["tier"]] += 1
        c = r.get("confidence")
        if c is not None:
            if c < 40:
                conf_buckets["<40"] += 1
            elif c < 60:
                conf_buckets["40-60"] += 1
            else:
                conf_buckets[">=60"] += 1

    draw_rate = round(pred_dist.get("DRAW", 0) / n * 100, 1) if n else 0.0
    confs = [r["confidence"] for r in valid if r.get("confidence") is not None]

    return {
        "session_id":            session_id,
        "date":                  date_str,
        "mode":                  "SHADOW_PAPER",
        "data_source":           data_source_label or "none",
        "source_status":         source_status,
        "shadow_fallback_active": shadow_fallback_active,
        "total_fixtures":        n,
        "predictions":           match_results,
        "distribution":          pred_dist,
        "draw_rate_pct":         draw_rate,
        "confidence": {
            "min":     min(confs) if confs else None,
            "max":     max(confs) if confs else None,
            "mean":    round(sum(confs) / len(confs), 1) if confs else None,
            "buckets": conf_buckets,
        },
        "tier_distribution": tier_dist,
        "replay_hash":       _compute_replay_hash(match_results),
        "settlement_summary": {
            "settled": 0,
            "pending": n,
            "note":    "No settled matches — all fixtures are upcoming.",
        },
    }


def _format_report_summary(report: dict) -> str:
    """Compact Telegram-ready run report (plain HTML, no tables)."""
    dist = report["distribution"]
    conf = report["confidence"]
    tier = report["tier_distribution"]
    fb   = "⚠️ SHADOW_FALLBACK_ACTIVE" if report["shadow_fallback_active"] else "🔗 Live feed active"

    return (
        "📋 <b>SHADOW RUN REPORT</b>\n"
        f"🔑 <code>{report['session_id']}</code>\n"
        f"📅 {report['date']} · Kaynak: {report['data_source']}\n"
        f"{fb}\n\n"
        f"<b>Fixtures:</b> {report['total_fixtures']}\n"
        f"<b>Dağılım:</b> "
        f"1:{dist.get('HOME_WIN', 0)}  "
        f"X:{dist.get('DRAW', 0)}  "
        f"2:{dist.get('AWAY_WIN', 0)}  "
        f"NO_DATA:{dist.get('NO_DATA', 0)}\n"
        f"<b>Draw rate:</b> {report['draw_rate_pct']}%\n"
        f"<b>Güven:</b> "
        f"min={conf.get('min', '—')}%  "
        f"mean={conf.get('mean', '—')}%  "
        f"max={conf.get('max', '—')}%\n"
        f"<b>Tier:</b> A:{tier.get('TIER_A', 0)}  B:{tier.get('TIER_B', 0)}  C:{tier.get('TIER_C', 0)}\n"
        f"<b>Replay hash:</b> <code>{report['replay_hash'][:16]}</code>\n"
        f"<b>Settlement:</b> {report['settlement_summary']['note']}"
    )


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
                "id":        m["id"],
                "home_name": m.get("homeTeam", {}).get("name", "?"),
                "away_name": m.get("awayTeam", {}).get("name", "?"),
                "time":      (utc[11:16] + " UTC") if len(utc) >= 16 else "—",
                "date":      date_str,
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
                "x-rapidapi-key":  API_FOOTBALL_KEY,
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
                "id":        f["fixture"]["id"],
                "home_name": f.get("teams", {}).get("home", {}).get("name", "?"),
                "away_name": f.get("teams", {}).get("away", {}).get("name", "?"),
                "time":      (fixture_date[11:16] + " UTC") if len(fixture_date) >= 16 else "—",
                "date":      date_str,
            })
        logger.info("API-Football: %d match(es) on %s", len(matches), date_str)
        return matches
    except Exception as e:
        logger.warning("API-Football fetch failed: %s", e)
        return None


def fetch_wc_matches_from_api(date_str: str) -> tuple[list[dict], str]:
    """
    Try external APIs in order: football-data.org → API-Football.
    Returns (matches, source_label). source_label is '' if all sources failed.
    """
    result = _fetch_football_data_org(date_str)
    if result is not None:
        return result, "football-data.org"

    result = _fetch_api_football(date_str)
    if result is not None:
        return result, "API-Football"

    return [], ""


# ---------------------------------------------------------------------------
# DB source
# ---------------------------------------------------------------------------

class SourceStatus:
    """Outcome of a match-fetch attempt."""
    OK       = "ok"        # table exists, query ran — zero or more rows
    NO_TABLE = "no_table"  # matches table doesn't exist in this DB
    DB_ERROR = "db_error"  # connection or unexpected query error


def get_todays_wc_matches(db, date_str: str) -> tuple[list[dict], str]:
    """
    Read-only fetch of unsettled matches scheduled for `date_str`.

    Returns (rows, SourceStatus.*) so callers can distinguish:
      OK        — data source healthy; rows may be empty (no matches today)
      NO_TABLE  — matches table absent (DB not initialised)
      DB_ERROR  — connection / query failure
    """
    try:
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


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def shadow_predict(match: dict) -> dict:
    """
    Produce a paper-trading prediction for one match using ShadowPredictor.

    Accepts a match dict (DB or API) carrying home_name / away_name.
    Delegates to ops.shadow_predictor — never the production inference stack.
    Adds tier assignment to the returned dict.

    On failure: returns a NO_DATA sentinel dict (never raises).
    """
    from ops.shadow_predictor import ShadowPredictor

    home = match.get("home_name") or _team_name_safe(match)
    away = match.get("away_name") or "Unknown"
    try:
        pred = ShadowPredictor().predict(home, away)
        pred["tier"] = _assign_tier(pred)
        return pred
    except RuntimeError:
        # ShadowPredictor raises RuntimeError when real feeds are active.
        raise
    except Exception as exc:
        logger.error("shadow_predict failed for %s v %s: %s", home, away, exc)
        return {
            "raw_prediction":  "NO_DATA",
            "final_confidence": 0.0,
            "home_win_prob":   0.0,
            "draw_prob":       0.0,
            "away_win_prob":   0.0,
            "expected_goals_a": 0.0,
            "expected_goals_b": 0.0,
            "is_no_bet":       True,
            "market_note":     "NO_DATA",
            "elo_home":        0.0,
            "elo_away":        0.0,
            "tier":            "—",
        }


def _team_name_safe(match: dict) -> str:
    """Return a printable team name from a DB match dict without a DB handle."""
    tid = match.get("home_team_id")
    return f"ID {tid}" if tid else "Unknown"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_match_block(db, match: dict, pred: dict) -> str:
    """Format one match's prediction as an HTML Telegram block."""
    home    = match.get("home_name") or _team_name(db, match.get("home_team_id"))
    away    = match.get("away_name") or _team_name(db, match.get("away_team_id"))
    kickoff = match.get("time") or "—"
    tier    = pred.get("tier", "—")

    if pred.get("raw_prediction") == "NO_DATA":
        return (
            f"🏟️ <b>{home}</b> vs <b>{away}</b>  —  🕒 {kickoff}  [{tier}]\n"
            f"⚠️ <b>Statü:</b> VERİ YOK (NO_DATA)\n"
            f"<i>Bu maç için tahmin üretilemedi.</i>"
        )

    if pred.get("is_no_bet"):
        status = f"⛔ OYNANMAZ ({pred.get('market_note') or 'No-Bet'})"
    else:
        status = "✅ İZLENİYOR"

    return (
        f"🏟️ <b>{home}</b> vs <b>{away}</b>  —  🕒 {kickoff}  [{tier}]\n"
        f"🛡️ <b>Statü:</b> {status}\n"
        f"🎯 <b>Model Seçimi:</b> {_pick_label(pred.get('raw_prediction', 'DRAW'))}\n"
        f"⚽ <b>Güven:</b> %{round(float(pred.get('final_confidence', 0)), 1)}\n"
        f"1️⃣ %{round(float(pred.get('home_win_prob', 0)), 1)} | "
        f"❌ %{round(float(pred.get('draw_prob', 0)), 1)} | "
        f"2️⃣ %{round(float(pred.get('away_win_prob', 0)), 1)}\n"
        f"📊 <i>xG: {pred.get('expected_goals_a', 0)} - {pred.get('expected_goals_b', 0)}</i>"
    )


# ---------------------------------------------------------------------------
# Core session runner
# ---------------------------------------------------------------------------

def run_shadow_session(
    db,
    matches: list[dict],
    date_str: str,
    source_status: str,
    data_source_label: str = "",
) -> tuple[str, dict]:
    """
    Run the complete shadow delivery session.

    Returns (bulletin_text, json_report).

    bulletin_text — HTML for Telegram.
    json_report   — structured dict with full run metadata.

    This is the canonical entry point for both the trigger script and the
    daily bot job.  build_bulletin() is a thin backward-compatible shim
    around this function.
    """
    shadow_fallback_active = not bool(
        os.getenv("API_FOOTBALL_KEY", "").strip()
        and os.getenv("ODDS_API_KEY", "").strip()
    )
    if shadow_fallback_active:
        logger.info("SHADOW_FALLBACK_ACTIVE — deterministic Elo-based ShadowPredictor in use")

    session_id  = f"shadow_{date_str}_{datetime.now().strftime('%H%M%S')}"
    source_note = f" · Veri: {data_source_label}" if data_source_label else ""
    header = (
        "🧪 <b>DÜNYA KUPASI — GÖLGE (SHADOW) KUPONU</b>\n"
        f"📅 {date_str}{source_note}\n"
        "<i>Kağıt üzerinde (paper) takip — gerçek bahis YOK. "
        "Yalnızca sinyal kalitesi gözlemleniyor.</i>\n"
    )

    # --- early-exit cases --------------------------------------------------
    def _empty_report(note: str) -> dict:
        return _build_run_report(
            session_id, date_str, data_source_label,
            shadow_fallback_active, source_status, [],
        ) | {"_note": note}

    if source_status == SourceStatus.NO_TABLE:
        bulletin = (
            header
            + "\n⚠️ <b>Veri kaynağı hazır değil.</b>\n"
            "<i>matches tablosu bu ortamda bulunamadı. "
            "Gerçek üretim DB'sine karşı çalıştırılması gerekiyor "
            "veya önce scripts/update_db_wc2026.py ile DB başlatılmalı.</i>"
        )
        return bulletin, _empty_report("no_table")

    if source_status == SourceStatus.DB_ERROR:
        bulletin = (
            header
            + "\n❌ <b>Veritabanı bağlantı hatası.</b>\n"
            "<i>Maçlar okunamadı. DB yapılandırmasını ve bağlantıyı kontrol edin.</i>"
        )
        return bulletin, _empty_report("db_error")

    if not matches:
        return (
            header + "\n📭 Bugün için planlanmış Dünya Kupası maçı bulunamadı.",
            _empty_report("no_matches"),
        )

    # --- generate predictions ----------------------------------------------
    match_results: list[dict] = []
    blocks: list[str] = []

    for m in matches:
        home = m.get("home_name") or _team_name(db, m.get("home_team_id"))
        away = m.get("away_name") or _team_name(db, m.get("away_team_id"))
        pred = shadow_predict(m)

        match_results.append({
            "match":         f"{home} v {away}",
            "kickoff":       m.get("time", "—"),
            "prediction":    pred["raw_prediction"],
            "home_win_prob": pred["home_win_prob"],
            "draw_prob":     pred["draw_prob"],
            "away_win_prob": pred["away_win_prob"],
            "confidence":    pred["final_confidence"],
            "tier":          pred.get("tier", "—"),
            "is_no_bet":     pred.get("is_no_bet", False),
        })
        blocks.append(format_match_block(db, m, pred))

    if not blocks:
        bulletin = header + "\n⚠️ Maçlar bulundu ancak tahmin üretilemedi."
    else:
        bulletin = header + "\n" + "\n\n".join(blocks)

    report = _build_run_report(
        session_id, date_str, data_source_label,
        shadow_fallback_active, source_status, match_results,
    )

    logger.info(
        "Session %s: %d fixture(s) | dist %s | draw_rate %.1f%% | replay_hash %s",
        session_id,
        report["total_fixtures"],
        report["distribution"],
        report["draw_rate_pct"],
        report["replay_hash"][:12],
    )

    return bulletin, report


# ---------------------------------------------------------------------------
# Backward-compatible shim (used by app/bot/predictions.py)
# ---------------------------------------------------------------------------

def build_bulletin(
    db,
    matches: list[dict],
    date_str: str,
    source_status: str,
    data_source_label: str = "",
) -> str:
    """Return bulletin HTML text only.  JSON report is discarded."""
    bulletin, _ = run_shadow_session(db, matches, date_str, source_status, data_source_label)
    return bulletin


# ---------------------------------------------------------------------------
# Telegram delivery
# ---------------------------------------------------------------------------

def send_telegram(token: str, chat_id: str, text: str) -> dict:
    import requests

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id":                chat_id,
            "text":                   text,
            "parse_mode":             "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="World Cup personal shadow delivery")
    parser.add_argument(
        "--deliver",
        action="store_true",
        help="Send bulletin + report to TELEGRAM_PERSONAL_CHANNEL.",
    )
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="Match date YYYY-MM-DD (default: today).",
    )
    parser.add_argument(
        "--json-out",
        metavar="FILE",
        help="Write JSON report to FILE in addition to stdout.",
    )
    args = parser.parse_args()

    from src.db.base import get_backend

    db = get_backend()
    db.connect()
    try:
        matches, source_status = get_todays_wc_matches(db, args.date)
        data_source_label = "DB"
        logger.info("DB source: %s | %d match(es) for %s.", source_status, len(matches), args.date)

        needs_api = source_status in (SourceStatus.NO_TABLE, SourceStatus.DB_ERROR) or (
            source_status == SourceStatus.OK and not matches
        )
        if needs_api:
            reason = "no matches in DB" if source_status == SourceStatus.OK else source_status
            logger.info("Trying API fallback (%s)...", reason)
            api_matches, api_source = fetch_wc_matches_from_api(args.date)
            if api_source:
                matches           = api_matches
                source_status     = SourceStatus.OK
                data_source_label = api_source
                logger.info("API fallback succeeded: %d match(es) from %s", len(api_matches), api_source)
            else:
                data_source_label = ""
                logger.warning("All API fallbacks failed — keeping original source status.")

        bulletin, report = run_shadow_session(db, matches, args.date, source_status, data_source_label)

    finally:
        db.close()

    # --- JSON output -------------------------------------------------------
    report_json = json.dumps(report, indent=2, default=str)

    if args.json_out:
        with open(args.json_out, "w") as fh:
            fh.write(report_json)
        logger.info("JSON report written to %s", args.json_out)

    if not args.deliver:
        print("\n--- DRY RUN (no message sent; pass --deliver to send) ---\n")
        print(bulletin)
        print("\n--- JSON REPORT ---\n")
        print(report_json)
        return 0

    # --- Telegram delivery -------------------------------------------------
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_PERSONAL_CHANNEL", "").strip()
    missing = [n for n, v in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_PERSONAL_CHANNEL", chat_id)) if not v]
    if missing:
        logger.error("Missing required env var(s): %s", ", ".join(missing))
        return 1

    send_telegram(token, chat_id, bulletin)
    logger.info("Shadow bulletin delivered to %s.", chat_id)

    send_telegram(token, chat_id, _format_report_summary(report))
    logger.info("Run report summary delivered to %s.", chat_id)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
