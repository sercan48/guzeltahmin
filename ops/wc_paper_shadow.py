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

try:
    import requests as _requests_mod
    from ops.api_resilience import CircuitBreaker, RetryConfig, resilient_get as _resilient_get
    _RESILIENCE_AVAILABLE = True
except ImportError:
    _RESILIENCE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------

def _pick_label(prediction: str) -> str:
    return {
        "HOME_WIN": "1 (Ev Sahibi)",
        "AWAY_WIN": "2 (Deplasman)",
        "DRAW":     "X (Beraberlik)",
    }.get(prediction, prediction)


def _ev(prob_frac: float, decimal_odds: float | None) -> float | None:
    """Beklenen değer yüzdesi. Oran yoksa None."""
    if not decimal_odds or decimal_odds <= 1.0:
        return None
    return prob_frac * decimal_odds - 1.0


def _pick_secondary(pred: dict, odds: dict | None = None) -> tuple[str, float, str]:
    """
    Birincil 1X2 tahminiyle ortogonal en iyi ikincil pazarı seç.

    Returns (label, probability_0_to_1, market_type)

    EV Modu (odds varsa):
      Tüm aday pazarların EV'i hesaplanır; en yüksek pozitif EV kazanır.
      1X için çok düşük oranlar (-EV) Alt 2.5 gibi seçeneklerin önünü açar.
      Hiçbir aday pozitif EV üretmiyorsa kural moduna düşer.

    Kural Modu (odds yoksa, öncelik sırası):
      1. DRAW tahmini → xG<2.0: Alt | xG≥2.0: Çifte Şans
      2. TIER_A + conf≥45 → Çifte Şans (1X / X2)
      3. xg_h≥0.9 ve xg_a≥0.9 ve P(BTTS)≥0.45 → KG Var
      4. p_over > p_under → 2.5 Üst
      5. Varsayılan → 2.5 Alt
    """
    import math

    xg_h = float(pred.get("expected_goals_a", 0))
    xg_a = float(pred.get("expected_goals_b", 0))
    tier    = pred.get("tier", "TIER_C")
    conf    = float(pred.get("final_confidence", 0))
    primary = pred.get("raw_prediction", "")
    probs   = {
        "H": float(pred.get("home_win_prob", 0)),
        "D": float(pred.get("draw_prob", 0)),
        "A": float(pred.get("away_win_prob", 0)),
    }

    total_xg = xg_h + xg_a
    p_under  = sum(
        (total_xg ** k * math.exp(-total_xg)) / math.factorial(k)
        for k in range(3)
    )
    p_over = 1.0 - p_under
    p_btts = (1.0 - math.exp(-xg_h)) * (1.0 - math.exp(-xg_a))
    p_1x   = (probs["H"] + probs["D"]) / 100.0
    p_x2   = (probs["D"] + probs["A"]) / 100.0

    # ── EV Modu ──────────────────────────────────────────────────────────────
    # Oran varsa tüm aday pazarlar EV ile sıralanır.
    # Bu mod 1X/KG_VAR gibi düşük oranlı seçeneklerin negatif EV'ini görür;
    # örn. TIER_A maçta 1X odds≈1.05 → EV≈-14%, Alt odds≈1.70 → EV≈+1.3%.
    if odds:
        # primary 1X2 ile aynı pazar tipine girmeyecek adaylar
        skip_key = {"HOME_WIN": "h", "DRAW": "d", "AWAY_WIN": "a"}.get(primary)
        candidates: list[tuple[float, str, float, str]] = []  # (ev, label, prob, mtype)

        def _add(label, prob, mtype, odds_val):
            ev = _ev(prob, odds_val)
            if ev is not None:
                candidates.append((ev, label, prob, mtype))

        if primary != "DRAW":
            dc_key = "1X" if primary == "HOME_WIN" else "X2"
            dc_prob = p_1x if primary == "HOME_WIN" else p_x2
            # DC odds: genellikle bookmaker'da "double chance" olarak yok;
            # 1X2 oranlarından türet: P(1X) / (1/h + 1/d) yaklaşımı yerine
            # doğrudan under/btts/ou oranlarını kullan
            _add("2.5_ALT",  p_under, "OU",   odds.get("under_2_5"))
            _add("2.5_ÜST",  p_over,  "OU",   odds.get("over_2_5"))
            _add("KG_VAR",   p_btts,  "BTTS", odds.get("btts_yes"))
            _add("KG_YOK",   1-p_btts,"BTTS", odds.get("btts_no"))
        else:
            # DRAW tahmini: DC hem birincili hem de ikincili kapsar → OU kullan
            _add("2.5_ALT",  p_under, "OU",   odds.get("under_2_5"))
            _add("2.5_ÜST",  p_over,  "OU",   odds.get("over_2_5"))

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            best_ev, best_label, best_prob, best_mtype = candidates[0]
            if best_ev > 0:
                return best_label, best_prob, best_mtype
        # Tüm adaylar negatif EV veya oran eksik → kural moduna düş

    # ── Kural Modu ───────────────────────────────────────────────────────────
    if primary == "DRAW":
        if total_xg < 2.0:
            return "2.5_ALT", p_under, "OU"
        return ("1X", p_1x, "DC") if probs["H"] >= probs["A"] else ("X2", p_x2, "DC")

    if tier == "TIER_A" and conf >= 45:
        if primary == "HOME_WIN":
            return "1X", p_1x, "DC"
        if primary == "AWAY_WIN":
            return "X2", p_x2, "DC"

    if xg_h >= 0.9 and xg_a >= 0.9 and p_btts >= 0.45:
        return "KG_VAR", p_btts, "BTTS"

    if p_over > p_under:
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


def _confidence_bar(conf: float) -> str:
    """5 bloklu güven çubuğu."""
    filled = max(0, min(5, round(conf / 20)))
    return "▓" * filled + "░" * (5 - filled)


def _value_score_bar(score, width: int = 10) -> str:
    """0–10 değer skoru için ASCII görsel çubuk."""
    if score is None or score <= 0:
        return "░" * width
    filled = max(1, min(width, round(score)))
    return "█" * filled + "░" * (width - filled)


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

_SNIPER_ODDS_MAX = 4.5  # longshot bias tuzağı — bu eşiğin üstü sniper olamaz

def _is_sniper(pred: dict, odds: dict | None) -> bool:
    """
    Sniper pick kriterleri (hepsi sağlanmalı):
    - TIER_A veya TIER_B (Elo farkı ≥ 50)
    - is_no_bet değil
    - final_confidence ≥ 62
    - Oran varsa: piyasa oranı ≤ 4.5 (longshot bias filtresi)
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
            mkt_odds = odds[mkt_key]
            if mkt_odds > _SNIPER_ODDS_MAX:   # longshot trap
                return False
            market_prob = 1.0 / mkt_odds
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
    _cb = CircuitBreaker("odds_api") if _RESILIENCE_AVAILABLE else None
    _rc = RetryConfig(max_attempts=3, backoff_base=5.0, backoff_factor=5.0) if _RESILIENCE_AVAILABLE else None
    try:
        for sport_key in _SPORT_KEYS:
            url    = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
            params = {
                "apiKey":     odds_key,
                "regions":    "eu",
                "markets":    "h2h",
                "oddsFormat": "decimal",
            }

            if _RESILIENCE_AVAILABLE:
                resp = _resilient_get(url, params=params, timeout=10,
                                      retry=_rc, circuit_breaker=_cb)
                if resp is None:
                    # Devre açık ya da tüm denemeler tükendi
                    return {}
            else:
                import requests as _req
                resp = _req.get(url, params=params, timeout=10)

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

def _merge_odds_into_predictions(
    date_str: str,
    home: str,
    away: str,
    odds: dict,
    is_sniper: bool,
) -> None:
    """
    shadow_predictions.jsonl'daki eşleşen kaydı market_odds_h/d/a ile günceller.
    Kayıt bulunamazsa sessizce geçer — delivery asla kırılmamalı.
    """
    if not _PREDICTIONS_LOG.exists():
        return
    home_n = home.lower().strip()
    away_n = away.lower().strip()
    updated = False
    lines_out: list[str] = []

    try:
        with open(_PREDICTIONS_LOG, encoding="utf-8") as fh:
            for raw_line in fh:
                raw_line = raw_line.rstrip("\n")
                if not raw_line.strip():
                    continue
                try:
                    r = json.loads(raw_line)
                except Exception:
                    lines_out.append(raw_line)
                    continue

                r_home = (r.get("home_team") or r.get("home", "")).lower().strip()
                r_away = (r.get("away_team") or r.get("away", "")).lower().strip()
                r_date = r.get("match_date") or r.get("date", "")

                if r_date == date_str and r_home == home_n and r_away == away_n:
                    r["market_odds_h"]    = odds.get("h")
                    r["market_odds_d"]    = odds.get("d")
                    r["market_odds_a"]    = odds.get("a")
                    r["is_sniper"]        = is_sniper
                    r["odds_fetched_at"]  = datetime.now(timezone.utc).isoformat()
                    updated = True

                lines_out.append(json.dumps(r, ensure_ascii=False))

        if updated:
            _PREDICTIONS_LOG.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning("Prediction odds güncellenemedi (%s vs %s): %s", home, away, exc)


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

    sniper    = _is_sniper(pred, odds)
    draw_prob = float(pred.get("draw_prob", 0))
    # Shadow bulgusu (n=60): hataların %76'sı kaçırılan beraberlik; bunların
    # %81'i düşük skorlu (0-0/1-1). En kötüsü BÜYÜK FAVORİ maçları: Elo farkı
    # ≥180 olan 20 maçın %35'i berabere bitti. Poisson bu maçlara DÜŞÜK
    # beraberlik olasılığı verdiği için (örn. Spain-Cape Verde D=%15.4 → 0-0)
    # generic draw_prob>18 eşiği tuzakları kaçırıyordu. İkinci tetikleyici =
    # favori tuzağı. NOT: bu yalnızca teslimat katmanı bayrağı — olasılık,
    # güven ve modele DOKUNMAZ.
    _elo_gap_block = abs(float(pred.get("elo_home", 0)) - float(pred.get("elo_away", 0)))
    fav_trap  = _elo_gap_block >= 180.0 and not pred.get("is_no_bet", False)
    draw_risk = (draw_prob > 18.0 or fav_trap) and not pred.get("is_no_bet", False)
    if draw_risk:
        sniper = False  # Seçenek B: beraberlik riski olan tahminler sniper'dan çıkar

    if pred.get("raw_prediction") == "NO_DATA":
        return (
            f"🏟️ <b>{home}</b> vs <b>{away}</b>  —  🕒 {kickoff}  [{tier}]\n"
            f"⚠️ <b>Statü:</b> VERİ YOK (NO_DATA)\n"
            f"<i>Bu maç için tahmin üretilemedi.</i>"
        )

    outcome   = pred.get("raw_prediction", "DRAW")
    conf      = float(pred.get("final_confidence", 0))
    xg_h      = float(pred.get("expected_goals_a", 0))
    xg_a      = float(pred.get("expected_goals_b", 0))
    elo_h     = float(pred.get("elo_home", 0))
    elo_a     = float(pred.get("elo_away", 0))
    elo_gap   = abs(elo_h - elo_a)
    is_no_bet = bool(pred.get("is_no_bet", False))

    h_pct = round(float(pred.get("home_win_prob", 0)), 1)
    d_pct = round(draw_prob, 1)
    a_pct = round(float(pred.get("away_win_prob", 0)), 1)
    dc_1x = round(h_pct + d_pct, 1)
    dc_x2 = round(d_pct + a_pct, 1)

    from src.model.wc_intelligence_engine import btts_predict, over_under_predict
    btts = btts_predict(xg_h, xg_a)
    ou   = over_under_predict(xg_h, xg_a, line=2.5)

    # Kelly / EV / Değer Skoru
    mkt_odds_val = (odds or {}).get(
        {"HOME_WIN": "h", "DRAW": "d", "AWAY_WIN": "a"}.get(outcome, "h")
    )
    model_prob = {"HOME_WIN": h_pct / 100, "DRAW": d_pct / 100, "AWAY_WIN": a_pct / 100}.get(outcome, 0.0)
    if mkt_odds_val and mkt_odds_val > 1.0 and model_prob > 0:
        b      = mkt_odds_val - 1.0
        kf     = max(0.0, (b * model_prob - (1.0 - model_prob)) / b)
        ev_pct = round((model_prob * mkt_odds_val - 1.0) * 100, 1)
        vs     = min(round(kf * 100, 1), 10.0) if kf > 0 else None
    else:
        ev_pct = None
        vs     = None

    tier_em  = {"TIER_A": "🔴", "TIER_B": "🟡", "TIER_C": "⚪"}.get(tier, "⚪")
    conf_bar = _confidence_bar(conf)

    # ── Başlık ──────────────────────────────────────────────────────────────────
    sniper_tag = "  ⭐ <b>SNIPER</b>" if sniper else ""
    no_bet_tag = "  ⛔ <b>OYNANMAZ</b>" if is_no_bet else ""
    risk_tag   = "  ⚠️ <b>BER.RİSKİ</b>" if draw_risk else ""
    status_tag = sniper_tag + no_bet_tag + risk_tag

    header = (
        f"┌─────────────────────────────────┐\n"
        f"│ {tier_em} {tier}{status_tag}\n"
        f"│ ⚽ <b>{home}</b>  vs  <b>{away}</b>\n"
        f"│ 🕒 {kickoff}\n"
        f"└─────────────────────────────────┘"
    )

    # ── Ana tahmin ───────────────────────────────────────────────────────────────
    mkt_tag    = f"  @{mkt_odds_val}" if mkt_odds_val else ""
    pred_block = (
        f"\n🎯 <b>ANA TAHMİN:</b>  {_pick_label(outcome)}{mkt_tag}\n"
        f"   Güven: <b>{conf:.1f}%</b>  {conf_bar}"
    )

    # ── Olasılık üçlüsü ──────────────────────────────────────────────────────────
    prob_block = f"\n\n1️⃣ %{h_pct}   ❌ %{d_pct}   2️⃣ %{a_pct}"

    # ── Model girdileri ──────────────────────────────────────────────────────────
    model_block = (
        f"\n\n🔵 <b>Elo:</b> {elo_h:.0f} vs {elo_a:.0f}  <i>(fark: {elo_gap:.0f})</i>\n"
        f"⚡ <b>xG:</b> {xg_h:.2f} – {xg_a:.2f}"
    )

    # ── Piyasa oranları ──────────────────────────────────────────────────────────
    if odds and odds.get("h") and odds.get("d") and odds.get("a"):
        odds_line = (
            f"\n\n💰 <b>PIYASA ORANLARI</b>\n"
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

    # ── Model Fırsat Endeksi ─────────────────────────────────────────────────────
    if vs is not None and ev_pct is not None and ev_pct > 0:
        vs_bar    = _value_score_bar(vs)
        mfe_block = (
            f"\n\n💎 <b>MODEL FIRSAT ENDEKSİ</b>\n"
            f"   Değer Skoru:  <b>{vs:.1f} / 10</b>  {vs_bar}\n"
            f"   Beklenen Değer (EV):  <b>+%{ev_pct:.1f}</b> ✅\n"
            f"   Model Güveni:  {conf_bar}  <i>({conf:.1f}%)</i>"
        )
    elif ev_pct is not None and ev_pct <= 0:
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

    # ── İkincil seçim ────────────────────────────────────────────────────────────
    sec_label, sec_prob, sec_type = _pick_secondary(pred, odds=odds)
    sec_pct  = round(sec_prob * 100, 1)
    sec_em   = {"OU": "⚖️", "DC": "🛡️", "BTTS": "🎯"}.get(sec_type, "")
    sec_disp = {
        "2.5_ÜST": "2.5 Üst", "2.5_ALT": "2.5 Alt",
        "KG_VAR": "KG Var",   "KG_YOK": "KG Yok",
        "1X": "1X", "X2": "X2", "12": "12",
    }.get(sec_label, sec_label)
    sec_block = f"\n\n🥈 <b>İKİNCİL PAZAR:</b>  {sec_em} {sec_disp}  %{sec_pct}"

    # ── Türev tahminler ──────────────────────────────────────────────────────────
    deriv_block = (
        f"\n\n📊 <b>TÜREV TAHMİNLER</b>\n"
        f"   KG Var: %{btts['btts_yes']}  ·  2.5 Üst: %{ou['over_2.5']}  ·  2.5 Alt: %{ou['under_2.5']}\n"
        f"   1X: %{dc_1x}  ·  X2: %{dc_x2}"
    )

    # ── Favori tuzağı uyarısı (teslimat katmanı — modeli değiştirmez) ────────────
    fav_trap_block = (
        f"\n\n⚠️ <b>FAVORİ TUZAĞI</b>\n"
        f"   <i>Elo farkı yüksek ({elo_gap:.0f}). Shadow takibinde büyük favori "
        f"maçlarının ~%35'i berabere bitti; zayıf takım savunmaya yığılıyor. "
        f"Model beraberliği yapısal olarak az tahmin eder — ana tahmini "
        f"temkinli değerlendir.</i>"
        if fav_trap else ""
    )

    # ── CLV ─────────────────────────────────────────────────────────────────────
    clv_block = "\n\n📌 <b>CLV:</b> — <i>(kapanış oranı bekleniyor)</i>"

    return (
        header
        + pred_block
        + prob_block
        + model_block
        + odds_line
        + mfe_block
        + sec_block
        + deriv_block
        + fav_trap_block
        + clv_block
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

        # --- CLV: piyasa oranlarını mevcut prediction kaydına ekle ----------
        if pred["raw_prediction"] not in ("NO_DATA",) and odds:
            _merge_odds_into_predictions(date_str, home, away, odds, sniper)

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

# Telegram, mesaj başına 4096 karakter sınırı uygular. Yoğun maç günlerinde
# (grup aşaması 6-8 maç) bülten bu sınırı aşıp 'message is too long' hatasıyla
# komple düşüyordu → o günün tüm tahminleri kaybediliyordu. Bültenleri satır
# sınırlarında parçalara böleriz: HTML etiketleri satır içinde dengeli olduğu
# için bölme etiketleri bozmaz. Güvenli marj için 4096 yerine 3900 kullanılır.
_TELEGRAM_LIMIT = 3900


def _split_for_telegram(text: str, limit: int = _TELEGRAM_LIMIT) -> list[str]:
    """Split text into Telegram-safe chunks on newline boundaries.

    Her satır kendi içinde dengeli HTML etiketleri taşıdığından satır
    sınırında bölmek parse_mode=HTML'i bozmaz. Tek bir satır sınırı aşarsa
    (nadir) sert bölme uygulanır.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    cur = ""
    for line in text.split("\n"):
        if len(line) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            for i in range(0, len(line), limit):
                chunks.append(line[i:i + limit])
            continue
        candidate = line if not cur else cur + "\n" + line
        if len(candidate) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks


def send_telegram(token: str, chat_id: str, text: str) -> dict:
    import time

    import requests

    chunks = _split_for_telegram(text)
    last: dict = {}
    for idx, chunk in enumerate(chunks):
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id":                chat_id,
                "text":                   chunk,
                "parse_mode":             "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        last = data
        if idx < len(chunks) - 1:
            time.sleep(0.5)  # sıra korunsun + rate-limit'e takılmasın
    return last


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
