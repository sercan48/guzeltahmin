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
    ODDS_API_KEY                The Odds API key (the-odds-api.com, free tier).
                                If absent, odds line is silently omitted from bulletin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

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


def _pick_secondary(pred: dict) -> tuple[str, float, str]:
    """
    Choose the highest-quality secondary market that is orthogonal to the
    primary 1X2 prediction.

    Returns (label, probability_0_to_1, market_type)

    Logic (priority order):
      1. TIER_A + confidence >= 55 → Double Chance (covers primary direction
         + draw; odds ~1.35, structural edge against draw blindspot)
      2. Both xG >= 0.9 AND P(BTTS) >= 0.45 → KG_VAR (both teams attack-
         minded; independent of who wins; odds ~1.80)
      3. Total xG >= 2.3 → 2.5 Üst (high-scoring game expected)
      4. Default → 2.5 Alt (defensive/low-xG game)
    """
    import math

    xg_h = float(pred.get("expected_goals_a", 0))
    xg_a = float(pred.get("expected_goals_b", 0))
    tier  = pred.get("tier", "TIER_C")
    conf  = float(pred.get("final_confidence", 0))
    primary = pred.get("raw_prediction", "")
    probs = {
        "H": float(pred.get("home_win_prob", 0)),
        "D": float(pred.get("draw_prob", 0)),
        "A": float(pred.get("away_win_prob", 0)),
    }

    # 1. Double Chance for strong favorites
    if tier == "TIER_A" and conf >= 55:
        if primary == "HOME_WIN":
            return "1X", (probs["H"] + probs["D"]) / 100, "DC"
        if primary == "AWAY_WIN":
            return "X2", (probs["D"] + probs["A"]) / 100, "DC"

    # 2. BTTS when both teams are genuinely attack-minded
    p_btts = (1.0 - math.exp(-xg_h)) * (1.0 - math.exp(-xg_a))
    if xg_h >= 0.9 and xg_a >= 0.9 and p_btts >= 0.45:
        return "KG_VAR", p_btts, "BTTS"

    # 3/4. Over/Under based on total xG
    total_xg = xg_h + xg_a
    p_under = sum(
        (total_xg ** k * math.exp(-total_xg)) / math.factorial(k)
        for k in range(3)   # P(0) + P(1) + P(2) = P(≤2)
    )
    p_over = 1.0 - p_under
    if total_xg >= 2.3:
        return "2.5_ÜST", p_over, "OU"
    return "2.5_ALT", p_under, "OU"


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
# Odds fetch (The Odds API — the-odds-api.com, free tier 500 req/month)
# ---------------------------------------------------------------------------

def _is_sniper(pred: dict, odds: dict | None) -> bool:
    """
    Sniper pick kriterleri (hepsi sağlanmalı):
    - TIER_A veya TIER_B (Elo farkı ≥ 50)
    - is_no_bet değil
    - final_confidence ≥ 62
    - Oran varsa: modelimizin olasılığı piyasa implied'ından yüksek (pozitif EV)
    """
    if pred.get("is_no_bet"):
        return False
    if pred.get("tier", "TIER_C") == "TIER_C":
        return False
    if float(pred.get("final_confidence", 0)) < 62:
        return False
    if odds:
        raw = pred.get("raw_prediction", "")
        key_map = {"HOME_WIN": "h", "DRAW": "d", "AWAY_WIN": "a"}
        mkt_key = key_map.get(raw)
        if mkt_key and odds.get(mkt_key):
            market_prob = 1.0 / odds[mkt_key]
            model_prob  = float(pred.get(
                {"HOME_WIN": "home_win_prob", "DRAW": "draw_prob", "AWAY_WIN": "away_win_prob"}.get(raw, "home_win_prob"), 0
            )) / 100.0
            if model_prob <= market_prob:
                return False
    return True


_PREDICTIONS_LOG = Path("data/shadow_predictions.jsonl")
_PREDICTIONS_FILE = Path("data/shadow_predictions.jsonl")   # eski compat alias
_QUOTA_FILE       = Path("data/odds_quota.json")
_QUOTA_THRESHOLD  = 50  # kredi bu sayının altına düşünce fetch durdur


def _has_matches_today(date_str: str) -> bool:
    """Bugün tahmin edilmiş maç var mı? Yoksa API kredisi yakma."""
    f_path = _PREDICTIONS_LOG if _PREDICTIONS_LOG.exists() else _PREDICTIONS_FILE
    if not f_path.exists():
        return True  # dosya yoksa güvenli taraf: fetch et
    with open(f_path) as f:
        for line in f:
            try:
                if json.loads(line).get("match_date") == date_str:
                    return True
            except Exception:
                continue
    return False


def _quota_ok() -> bool:
    """data/odds_quota.json'dan kalan krediyi oku. Dosya yoksa izin ver."""
    if not _QUOTA_FILE.exists():
        return True
    try:
        data = json.loads(_QUOTA_FILE.read_text())
        return int(data.get("remaining", 9999)) >= _QUOTA_THRESHOLD
    except Exception:
        return True


def _save_quota(remaining: int, used: int) -> None:
    """API response header'ından gelen kota bilgisini diske kaydet."""
    try:
        _QUOTA_FILE.write_text(json.dumps({
            "remaining":  remaining,
            "used":       used,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2))
    except Exception as e:
        logger.warning("Quota dosyası yazılamadı: %s", e)


def _cache_max_age_hours(earliest_kickoff_utc: datetime | None) -> float:
    """
    CLV-aware cache geçerlilik süresi.
    Maça 2 saatten az kalmışsa 0 döndür (her zaman taze çek).
    n8n'e geçildiğinde ve günde 2 kez çalıştırıldığında devreye girer.
    """
    if earliest_kickoff_utc is None:
        return 8.0
    hours_left = (earliest_kickoff_utc - datetime.now(timezone.utc)).total_seconds() / 3600
    if hours_left <= 2:
        return 0.0   # kapanış oranı penceresi — cache bypass
    if hours_left <= 6:
        return 1.5
    return 8.0


def _norm_team(name: str) -> str:
    """Normalize a team name for fuzzy matching across API sources."""
    import unicodedata
    n = unicodedata.normalize("NFD", name.lower())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")  # strip accents
    # common canonical substitutions
    subs = {
        "usa": "united states", "us": "united states",
        "south korea": "korea republic", "republic of ireland": "ireland",
        "ivory coast": "cote d'ivoire", "cote divoire": "cote d'ivoire",
        "cape verde": "cape verde islands",
        "curacao": "curacao",
    }
    for src, dst in subs.items():
        n = n.replace(src, dst)
    return n.strip()


def fetch_odds_the_odds_api(date_str: str) -> dict[tuple[str, str], dict]:
    """
    Fetch 1X2 + Over/Under 2.5 + BTTS decimal odds from The Odds API.

    Returns {(home_norm, away_norm): {
        'h', 'd', 'a',                    # 1X2 ortalama ondalık oran
        'over_2_5', 'under_2_5',          # Totals market (None eğer yoksa)
        'btts_yes', 'btts_no',            # BTTS market (None eğer yoksa)
    }}

    Maliyet: 3 market × 1 bölge = 3 kredi / çağrı.
    Hiç raise etmez — delivery asla kırılmamalı.
    """
    import requests

    # ── GUARD 1: bugün maç yoksa kredi yakma ───────────────────────────────
    if not _has_matches_today(date_str):
        logger.info("Odds API: %s için maç bulunamadı, skip.", date_str)
        return {}

    # ── GUARD 2: aylık kota kritik eşiğin altındaysa durdur ───────────────
    if not _quota_ok():
        logger.warning(
            "Odds API: kota %d kredi altında, bu çalışma skip edildi.", _QUOTA_THRESHOLD
        )
        return {}

    odds_key = os.getenv("ODDS_API_KEY", "").strip()
    if not odds_key:
        return {}

    # WC 2026 aktif — sport key sırası: önce standart, sonra yıl-bazlı
    _SPORT_KEYS = [
        "soccer_fifa_world_cup",
        "soccer_fifa_world_cup_2026",
    ]

    resp = None
    try:
        for sport_key in _SPORT_KEYS:
            resp = requests.get(
                f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/",
                params={
                    "apiKey":     odds_key,
                    "regions":    "eu",
                    "markets":    "h2h",      # sadece h2h — kredi tasarrufu
                    "oddsFormat": "decimal",
                },
                timeout=10,
            )
            if resp.status_code == 401:
                logger.warning("Odds API: geçersiz key (401)")
                return {}
            if resp.status_code == 422:
                logger.warning("Odds API: 422 — %s denenecek diğer key: %s",
                               sport_key, resp.text[:120])
                continue  # bir sonraki sport_key'i dene
            if resp.status_code != 200:
                logger.warning("Odds API: HTTP %d (%s)", resp.status_code, sport_key)
                continue
            break  # başarılı
        else:
            logger.warning("Odds API: hiçbir sport_key çalışmadı — oranlar alınamadı")
            return {}

        # ── kota takibi: dosyaya yaz (bir sonraki run okuyacak) ───────────
        try:
            remaining = int(resp.headers.get("x-requests-remaining", "9999"))
            used      = int(resp.headers.get("x-requests-used", "0"))
            _save_quota(remaining, used)
            logger.info("Odds API: %d kullanıldı, %d kaldı", used, remaining)
        except (ValueError, TypeError):
            pass

        result: dict[tuple[str, str], dict] = {}

        for event in resp.json():
            h_norm = _norm_team(event.get("home_team", ""))
            a_norm = _norm_team(event.get("away_team", ""))
            home_key = event["home_team"].lower()
            away_key = event["away_team"].lower()

            h_list, d_list, a_list = [], [], []
            over_list, under_list  = [], []
            btts_yes_list, btts_no_list = [], []

            for bk in event.get("bookmakers", []):
                for mkt in bk.get("markets", []):
                    key = mkt.get("key", "")

                    if key == "h2h":
                        outcomes = {o["name"].lower(): o["price"]
                                    for o in mkt.get("outcomes", [])}
                        if home_key in outcomes:
                            h_list.append(outcomes[home_key])
                        if away_key in outcomes:
                            a_list.append(outcomes[away_key])
                        if "draw" in outcomes:
                            d_list.append(outcomes["draw"])

                    elif key == "totals":
                        for o in mkt.get("outcomes", []):
                            # The Odds API totals: {"name":"Over","point":2.5,"price":1.85}
                            if o.get("name", "").lower() == "over" and o.get("point") == 2.5:
                                over_list.append(o["price"])
                            elif o.get("name", "").lower() == "under" and o.get("point") == 2.5:
                                under_list.append(o["price"])

                    elif key == "btts":
                        for o in mkt.get("outcomes", []):
                            name = o.get("name", "").lower()
                            if name == "yes":
                                btts_yes_list.append(o["price"])
                            elif name == "no":
                                btts_no_list.append(o["price"])

            if h_list and a_list and d_list:
                def _avg(lst: list) -> float | None:
                    return round(sum(lst) / len(lst), 2) if lst else None

                result[(h_norm, a_norm)] = {
                    "h":         _avg(h_list),
                    "d":         _avg(d_list),
                    "a":         _avg(a_list),
                    "over_2_5":  _avg(over_list),
                    "under_2_5": _avg(under_list),
                    "btts_yes":  _avg(btts_yes_list),
                    "btts_no":   _avg(btts_no_list),
                }

        logger.info("Odds API: %d maç için oran çekildi", len(result))
        return result

    except Exception as e:
        logger.warning("Odds API fetch başarısız: %s", e)
        return {}


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

def _lookup_odds(home: str, away: str, odds_map: dict) -> dict | None:
    """Find odds for a match using normalized team-name matching."""
    if not odds_map:
        return None
    h_norm = _norm_team(home)
    a_norm = _norm_team(away)
    # exact normalized match first
    if (h_norm, a_norm) in odds_map:
        return odds_map[(h_norm, a_norm)]
    # substring fallback: one team name contains the other (handles short vs long variants)
    for (oh, oa), o in odds_map.items():
        if (h_norm in oh or oh in h_norm) and (a_norm in oa or oa in a_norm):
            return o
    return None


def format_match_block(db, match: dict, pred: dict, odds_map: dict | None = None) -> str:
    """Format one match's prediction as an HTML Telegram block."""
    home    = match.get("home_name") or _team_name(db, match.get("home_team_id"))
    away    = match.get("away_name") or _team_name(db, match.get("away_team_id"))
    kickoff = match.get("time") or "—"
    tier    = pred.get("tier", "—")
    odds    = _lookup_odds(home, away, odds_map or {})

    sniper = _is_sniper(pred, odds)
    sniper_prefix = "⭐ " if sniper else ""

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
    if odds:
        odds_line = f"\n💰 <b>Oran (ort.):</b> 1: {odds['h']} | X: {odds['d']} | 2: {odds['a']}"
        if odds.get("over_2_5"):
            odds_line += f"  |  2.5Ü: {odds['over_2_5']}"
        if odds.get("btts_yes"):
            odds_line += f"  |  KG+: {odds['btts_yes']}"
    else:
        odds_line = ""

    # --- derivative markets (read-only, display only) -----------------------
    from src.model.wc_intelligence_engine import btts_predict, over_under_predict

    xg_h = float(pred.get("expected_goals_a", 0))
    xg_a = float(pred.get("expected_goals_b", 0))
    btts  = btts_predict(xg_h, xg_a)
    ou    = over_under_predict(xg_h, xg_a, line=2.5)

    h_pct = round(float(pred.get("home_win_prob", 0)), 1)
    d_pct = round(float(pred.get("draw_prob", 0)), 1)
    a_pct = round(float(pred.get("away_win_prob", 0)), 1)
    dc_1x = round(h_pct + d_pct, 1)
    dc_x2 = round(d_pct + a_pct, 1)
    dc_12 = round(h_pct + a_pct, 1)

    # --- primary / secondary selection ------------------------------------
    sec_label, sec_prob, sec_type = _pick_secondary(pred)
    _sec_display = {
        "1X":      f"Çifte Şans 1X  %{dc_1x}",
        "X2":      f"Çifte Şans X2  %{dc_x2}",
        "12":      f"Çifte Şans 12  %{dc_12}",
        "KG_VAR":  f"KG Var  %{btts['btts_yes']}",
        "2.5_ÜST": f"2.5 Üst  %{ou['over_2.5']}",
        "2.5_ALT": f"2.5 Alt  %{ou['under_2.5']}",
    }
    sec_str = _sec_display.get(sec_label, sec_label)

    return (
        f"{sniper_prefix}🏟️ <b>{home}</b> vs <b>{away}</b>  —  🕒 {kickoff}  [{tier}]\n"
        f"🛡️ <b>Statü:</b> {status}\n"
        f"🥇 <b>Ana Seçim:</b> {_pick_label(pred.get('raw_prediction', 'DRAW'))}  "
        f"<i>(%{round(float(pred.get('final_confidence', 0)), 1)} güven)</i>\n"
        f"🥈 <b>İkincil Seçim:</b> {sec_str}\n"
        f"─────────────────────\n"
        f"1️⃣ %{h_pct} | ❌ %{d_pct} | 2️⃣ %{a_pct}\n"
        f"📊 <i>xG: {xg_h} - {xg_a}</i>"
        f"{odds_line}\n"
        f"⚽ KG Var %{btts['btts_yes']} · 2.5Ü %{ou['over_2.5']} · "
        f"1X %{dc_1x} · X2 %{dc_x2}"
        + (f"\n🎯 <b>SNIPER — Piyasa EV pozitif</b>" if sniper else "")
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

    # --- fetch odds (graceful — never breaks delivery) ---------------------
    odds_map = fetch_odds_the_odds_api(date_str)

    # --- generate predictions ----------------------------------------------
    match_results: list[dict] = []
    blocks: list[str] = []

    sniper_summaries: list[str] = []

    for m in matches:
        home = m.get("home_name") or _team_name(db, m.get("home_team_id"))
        away = m.get("away_name") or _team_name(db, m.get("away_team_id"))
        pred = shadow_predict(m)

        odds   = _lookup_odds(home, away, odds_map)
        sniper = _is_sniper(pred, odds)

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
            "is_sniper":     sniper,
            "odds":          odds,
        })
        blocks.append(format_match_block(db, m, pred, odds_map))

        # --- CLV log: tahmin anındaki oranları kaydet -----------------------
        if pred["raw_prediction"] not in ("NO_DATA",):
            log_entry = {
                "match_date":    date_str,
                "session_id":    session_id,
                "home":          home,
                "away":          away,
                "kickoff":       m.get("time", "—"),
                "prediction":    pred["raw_prediction"],
                "predicted_prob_home": round(pred["home_win_prob"] / 100, 4),
                "predicted_prob_draw": round(pred["draw_prob"] / 100, 4),
                "predicted_prob_away": round(pred["away_win_prob"] / 100, 4),
                "confidence":    pred["final_confidence"],
                "tier":          pred.get("tier", "—"),
                "is_sniper":     sniper,
                "market_odds_h": odds["h"] if odds else None,
                "market_odds_d": odds["d"] if odds else None,
                "market_odds_a": odds["a"] if odds else None,
                "logged_at":     datetime.now(timezone.utc).isoformat(),
            }
            try:
                _PREDICTIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
                with open(_PREDICTIONS_LOG, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.warning("Predictions log yazılamadı: %s", e)

        if sniper and pred["raw_prediction"] not in ("NO_DATA",):
            raw = pred["raw_prediction"]
            lbl = _pick_label(raw)
            conf = round(float(pred["final_confidence"]), 1)
            mkt_key = {"HOME_WIN": "h", "DRAW": "d", "AWAY_WIN": "a"}.get(raw, "h")
            mkt_odd = f"@{odds[mkt_key]}" if odds and odds.get(mkt_key) else ""
            sniper_summaries.append(
                f"  ⭐ <b>{home} - {away}</b>  {lbl}  {mkt_odd}  <i>({conf}% güven)</i>"
            )

    # --- sniper özet başlığa ekle -----------------------------------------
    if sniper_summaries:
        sniper_block = (
            f"\n🎯 <b>SNIPER SEÇİMLER ({len(sniper_summaries)} maç)</b>\n"
            + "\n".join(sniper_summaries)
            + "\n<i>Piyasaya göre pozitif EV — maç başlamadan önce oynayın</i>\n"
        )
    else:
        sniper_block = "\n<i>Bugün sniper kriterleri karşılayan maç yok.</i>\n"

    if not blocks:
        bulletin = header + sniper_block + "\n⚠️ Maçlar bulundu ancak tahmin üretilemedi."
    else:
        bulletin = header + sniper_block + "\n" + "\n\n".join(blocks)

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
