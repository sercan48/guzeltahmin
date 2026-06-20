"""
ops/league_fixture_fetcher.py — Lig Fixture Çekici

8 hedef lig için birleşik kaynak katmanı:
  football-data.org free → PL, LaLiga, Bundesliga, SerieA, Ligue1
  API-Football free      → Eredivisie, SuperLig, PrimeiraLiga

Standart çıktı:
  {
    "fixture_id":        "fd:12345" veya "af:98765"
    "league_key":        "PL" | "LaLiga" | ...
    "season":            2025   (başladığı yıl)
    "home_team":         str
    "away_team":         str
    "kickoff_time":      "2026-08-16T14:00:00Z"   (ISO UTC)
    "source":            "football-data.org" | "api-football"
  }

Kullanım:
    from ops.league_fixture_fetcher import fetch_upcoming, fetch_recent_results

    fixtures = fetch_upcoming(days_ahead=2)          # yarın + öbür gün
    results  = fetch_recent_results(days_back=3)     # settlement için
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from ops.api_resilience import CircuitBreaker, RetryConfig, resilient_get
    _RESILIENCE_AVAILABLE = True
except ImportError:
    _RESILIENCE_AVAILABLE = False

# ---------------------------------------------------------------------------
# Lig → API eşleştirmeleri
# ---------------------------------------------------------------------------

# football-data.org free tier'da olan lig kodları
_FD_LEAGUES: dict[str, str] = {
    "PL":        "PL",   # Premier League
    "LaLiga":    "PD",   # Primera Division
    "Bundesliga":"BL1",  # 1. Bundesliga
    "SerieA":    "SA",   # Serie A
    "Ligue1":    "FL1",  # Ligue 1
}

# API-Football league ID'leri (2025-26 sezon)
_AF_LEAGUES: dict[str, int] = {
    "Eredivisie":   88,
    "SuperLig":     203,
    "PrimeiraLiga": 94,
}

_ALL_LEAGUES = list(_FD_LEAGUES) + list(_AF_LEAGUES)

_FD_BASE = "https://api.football-data.org/v4"
_AF_BASE = "https://v3.football.api-sports.io"

# Basit disk cache — API kredisi yakmamak için
_CACHE_DIR  = Path("data/fixture_cache")
_CACHE_MINS = 60   # 1 saat geçerli


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------

def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _date_range(days_from: int, days_to: int) -> tuple[str, str]:
    """Bugünden itibaren gün aralığı → (date_from, date_to) ISO."""
    now  = datetime.now(timezone.utc)
    dfrom = (now + timedelta(days=days_from)).strftime("%Y-%m-%d")
    dto   = (now + timedelta(days=days_to)).strftime("%Y-%m-%d")
    return dfrom, dto


def _current_season() -> int:
    """Aktif sezon yılı. Ağustos'tan önce = geçen yıl."""
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 8 else now.year - 1


def _cache_key(tag: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{tag}.json"


def _cache_load(tag: str) -> list[dict] | None:
    p = _cache_key(tag)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        saved_at = datetime.fromisoformat(data["saved_at"])
        if (datetime.now(timezone.utc) - saved_at).total_seconds() < _CACHE_MINS * 60:
            return data["fixtures"]
    except Exception:
        pass
    return None


def _cache_save(tag: str, fixtures: list[dict]) -> None:
    try:
        _cache_key(tag).write_text(json.dumps({
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "fixtures": fixtures,
        }, indent=2))
    except Exception as e:
        logger.debug("Cache yazma hatası: %s", e)


def _http_get(url: str, headers: dict, params: dict | None = None) -> dict | None:
    """Tek HTTP GET — api_resilience varsa onu kullan, yoksa requests."""
    if _RESILIENCE_AVAILABLE:
        cb  = CircuitBreaker("fixture_api")
        rc  = RetryConfig(max_attempts=3, backoff_base=3.0, backoff_factor=3.0)
        resp = resilient_get(url, params=params, headers=headers, timeout=15,
                             retry=rc, circuit_breaker=cb)
        if resp is None:
            return None
        if resp.status_code != 200:
            logger.warning("HTTP %d: %s", resp.status_code, url)
            return None
        return resp.json()
    else:
        import requests
        r = requests.get(url, params=params, headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        return r.json()


# ---------------------------------------------------------------------------
# football-data.org çekici
# ---------------------------------------------------------------------------

def _fd_headers() -> dict:
    key = os.getenv("FOOTBALL_DATA_ORG_KEY", "").strip()
    h = {"Accept": "application/json"}
    if key:
        h["X-Auth-Token"] = key
    return h


def _fd_fetch_fixtures(league_key: str, date_from: str, date_to: str) -> list[dict]:
    code = _FD_LEAGUES.get(league_key)
    if not code:
        return []

    data = _http_get(
        f"{_FD_BASE}/competitions/{code}/matches",
        headers=_fd_headers(),
        params={"dateFrom": date_from, "dateTo": date_to, "status": "SCHEDULED,TIMED"},
    )
    if not data:
        return []

    fixtures = []
    for m in data.get("matches", []):
        try:
            utc = m.get("utcDate", "")
            fixtures.append({
                "fixture_id":   f"fd:{m['id']}",
                "league_key":   league_key,
                "season":       _current_season(),
                "home_team":    m["homeTeam"]["name"],
                "away_team":    m["awayTeam"]["name"],
                "kickoff_time": utc if utc.endswith("Z") else utc + "Z",
                "source":       "football-data.org",
            })
        except Exception:
            continue

    logger.info("[FD] %s: %d fixture (%s→%s)", league_key, len(fixtures), date_from, date_to)
    return fixtures


def _fd_fetch_results(league_key: str, date_from: str, date_to: str) -> list[dict]:
    """Biten maçları çek (settlement için)."""
    code = _FD_LEAGUES.get(league_key)
    if not code:
        return []

    data = _http_get(
        f"{_FD_BASE}/competitions/{code}/matches",
        headers=_fd_headers(),
        params={"dateFrom": date_from, "dateTo": date_to, "status": "FINISHED"},
    )
    if not data:
        return []

    results = []
    for m in data.get("matches", []):
        try:
            score = m.get("score", {}).get("fullTime", {})
            results.append({
                "fixture_id":  f"fd:{m['id']}",
                "league_key":  league_key,
                "home_team":   m["homeTeam"]["name"],
                "away_team":   m["awayTeam"]["name"],
                "kickoff_time": m.get("utcDate", ""),
                "home_goals":  score.get("home"),
                "away_goals":  score.get("away"),
                "source":      "football-data.org",
            })
        except Exception:
            continue

    return results


# ---------------------------------------------------------------------------
# API-Football çekici
# ---------------------------------------------------------------------------

def _af_headers() -> dict:
    key = os.getenv("API_FOOTBALL_KEY", "").strip()
    return {"x-apisports-key": key, "Accept": "application/json"}


def _af_fetch_fixtures(league_key: str, date_from: str, date_to: str) -> list[dict]:
    league_id = _AF_LEAGUES.get(league_key)
    if not league_id:
        return []
    if not os.getenv("API_FOOTBALL_KEY", "").strip():
        logger.warning("[AF] API_FOOTBALL_KEY eksik — %s atlanıyor", league_key)
        return []

    season = _current_season()
    fixtures = []
    # API-Football date range: birden fazla gün için her tarihi ayrı istek gerektirebilir
    current = datetime.strptime(date_from, "%Y-%m-%d")
    end     = datetime.strptime(date_to,   "%Y-%m-%d")
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        data = _http_get(
            f"{_AF_BASE}/fixtures",
            headers=_af_headers(),
            params={"league": league_id, "season": season, "date": date_str, "status": "NS"},
        )
        if data:
            for fix in data.get("response", []):
                try:
                    fdt = fix["fixture"]["date"]  # ISO with tz
                    # Normalize to UTC Z
                    dt = datetime.fromisoformat(fdt.replace("Z", "+00:00"))
                    kickoff_utc = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    fixtures.append({
                        "fixture_id":   f"af:{fix['fixture']['id']}",
                        "league_key":   league_key,
                        "season":       season,
                        "home_team":    fix["teams"]["home"]["name"],
                        "away_team":    fix["teams"]["away"]["name"],
                        "kickoff_time": kickoff_utc,
                        "source":       "api-football",
                    })
                except Exception:
                    continue
        current += timedelta(days=1)

    logger.info("[AF] %s: %d fixture (%s→%s)", league_key, len(fixtures), date_from, date_to)
    return fixtures


def _af_fetch_results(league_key: str, date_from: str, date_to: str) -> list[dict]:
    league_id = _AF_LEAGUES.get(league_key)
    if not league_id or not os.getenv("API_FOOTBALL_KEY", "").strip():
        return []

    season = _current_season()
    results = []
    current = datetime.strptime(date_from, "%Y-%m-%d")
    end     = datetime.strptime(date_to,   "%Y-%m-%d")
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        data = _http_get(
            f"{_AF_BASE}/fixtures",
            headers=_af_headers(),
            params={"league": league_id, "season": season, "date": date_str, "status": "FT"},
        )
        if data:
            for fix in data.get("response", []):
                try:
                    goals = fix.get("goals", {})
                    results.append({
                        "fixture_id":  f"af:{fix['fixture']['id']}",
                        "league_key":  league_key,
                        "home_team":   fix["teams"]["home"]["name"],
                        "away_team":   fix["teams"]["away"]["name"],
                        "kickoff_time": fix["fixture"]["date"],
                        "home_goals":  goals.get("home"),
                        "away_goals":  goals.get("away"),
                        "source":      "api-football",
                    })
                except Exception:
                    continue
        current += timedelta(days=1)

    return results


# ---------------------------------------------------------------------------
# Genel API
# ---------------------------------------------------------------------------

def fetch_upcoming(
    league_keys: list[str] | None = None,
    days_ahead: int = 2,
    use_cache: bool = True,
) -> list[dict]:
    """
    Gelecek N güne ait fixture'ları çek.

    league_keys=None → 8 ligin tamamı
    Dönüş: standart fixture dict listesi, kickoff_time'a göre sıralı.
    """
    keys = league_keys or _ALL_LEAGUES
    date_from, date_to = _date_range(0, days_ahead)
    tag = f"upcoming_{'-'.join(sorted(keys))}_{date_from}_{date_to}"

    if use_cache:
        cached = _cache_load(tag)
        if cached is not None:
            logger.debug("Cache hit: %s (%d fixture)", tag, len(cached))
            return cached

    all_fixtures: list[dict] = []
    for lk in keys:
        if lk in _FD_LEAGUES:
            all_fixtures.extend(_fd_fetch_fixtures(lk, date_from, date_to))
        elif lk in _AF_LEAGUES:
            all_fixtures.extend(_af_fetch_fixtures(lk, date_from, date_to))

    all_fixtures.sort(key=lambda f: f["kickoff_time"])
    _cache_save(tag, all_fixtures)
    logger.info("fetch_upcoming: toplam %d fixture (%d lig)", len(all_fixtures), len(keys))
    return all_fixtures


def fetch_recent_results(
    league_keys: list[str] | None = None,
    days_back: int = 2,
    use_cache: bool = True,
) -> list[dict]:
    """
    Son N güne ait biten maç sonuçlarını çek (settlement için).
    """
    keys = league_keys or _ALL_LEAGUES
    date_from, date_to = _date_range(-days_back, -1)
    tag = f"results_{'-'.join(sorted(keys))}_{date_from}_{date_to}"

    if use_cache:
        cached = _cache_load(tag)
        if cached is not None:
            return cached

    all_results: list[dict] = []
    for lk in keys:
        if lk in _FD_LEAGUES:
            all_results.extend(_fd_fetch_results(lk, date_from, date_to))
        elif lk in _AF_LEAGUES:
            all_results.extend(_af_fetch_results(lk, date_from, date_to))

    _cache_save(tag, all_results)
    logger.info("fetch_recent_results: toplam %d sonuç (%d lig)", len(all_results), len(keys))
    return all_results


def active_leagues_today(league_keys: list[str] | None = None) -> list[str]:
    """
    Bugün veya yarın maçı olan ligleri döndür.
    Pipeline'ı gereksiz API çağrısından korur (off-season dönemleri).
    """
    fixtures = fetch_upcoming(league_keys, days_ahead=1, use_cache=True)
    return sorted({f["league_key"] for f in fixtures})
