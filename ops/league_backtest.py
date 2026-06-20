#!/usr/bin/env python3
"""
ops/league_backtest.py  —  LB-1

Historical backtesting of the Poisson+Elo prediction model against 2024/25
domestic league fixtures.  Produces calibration stats compatible with
shadow_accuracy.json format, so the same analysis tooling applies.

Data sources
  Club Elo  : http://api.clubelo.com/ (free, no auth, cached monthly)
  Fixtures  : API-Football v3 (needs API_FOOTBALL_KEY)  OR  local CSV

Usage
    python ops/league_backtest.py --league PL --season 2024
    python ops/league_backtest.py --league PL,LaLiga,Bundesliga --season 2024
    python ops/league_backtest.py --all --season 2024
    python ops/league_backtest.py --league PL --season 2024 --csv path/to/file.csv

CSV format (header required)
    date,home,away,home_goals,away_goals
    2024-08-16,Arsenal,Wolverhampton Wanderers,2,0

Output
    data/league_backtest/PL_2024.json     accuracy dict (shadow_accuracy.json schema)
    data/league_backtest/PL_2024.jsonl    settlement log (one line per match)
    data/cache/club_elo/YYYY-MM.csv       Club Elo cache (auto-reused)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from src.model.wc_intelligence_engine import (
    TeamFeatures,
    compute_xg,
    compute_1x2_poisson,
    _name_hash,
)

# ---------------------------------------------------------------------------
# League registry
# ---------------------------------------------------------------------------

LEAGUES: dict[str, dict] = {
    "PL":         {"id": 39,  "country": "England",     "name": "Premier League"},
    "LaLiga":     {"id": 140, "country": "Spain",       "name": "La Liga"},
    "Bundesliga": {"id": 78,  "country": "Germany",     "name": "Bundesliga"},
    "SerieA":     {"id": 135, "country": "Italy",       "name": "Serie A"},
    "Ligue1":     {"id": 61,  "country": "France",      "name": "Ligue 1"},
    "Eredivisie": {"id": 88,  "country": "Netherlands", "name": "Eredivisie"},
    "SuperLig":   {"id": 203, "country": "Turkey",      "name": "Süper Lig"},
    "PrimeiraLiga": {"id": 94, "country": "Portugal", "name": "Primeira Liga"},
}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR      = Path(__file__).parent.parent / "data"
BACKTEST_DIR  = DATA_DIR / "league_backtest"
ELO_CACHE_DIR = DATA_DIR / "cache" / "club_elo"

DATA_DIR.mkdir(exist_ok=True)
BACKTEST_DIR.mkdir(exist_ok=True)
ELO_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Club Elo client  (http://api.clubelo.com/ — free, no auth)
# ---------------------------------------------------------------------------

_CLUBELO_BASE = "http://api.clubelo.com"
_elo_month_cache: dict[str, dict[str, float]] = {}   # {YYYY-MM: {club: elo}}


def _load_clubelo_month(date_str: str) -> dict[str, float]:
    """
    Fetch (or load from cache) Club Elo ratings for the 1st of the given month.
    Returns {lowercase_club_name: elo}.
    """
    ym       = date_str[:7]
    month_d  = f"{ym}-01"
    cache_f  = ELO_CACHE_DIR / f"{ym}.csv"

    if cache_f.exists():
        raw = cache_f.read_text(encoding="utf-8")
    else:
        url = f"{_CLUBELO_BASE}/{month_d}"
        print(f"  [ClubElo] {url}")
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                raw = r.read().decode("utf-8")
            cache_f.write_text(raw, encoding="utf-8")
        except Exception as exc:
            print(f"  [ClubElo] WARNING: {exc}")
            return {}

    result: dict[str, float] = {}
    reader = csv.DictReader(io.StringIO(raw))
    for row in reader:
        try:
            result[row["Club"].strip().lower()] = float(row["Elo"])
        except Exception:
            continue
    return result


def _get_club_elo(team_name: str, date_str: str) -> float | None:
    """Return Club Elo for a team at the given match date (month-level precision).

    Lookup priority:
    1. Live api.clubelo.com data (monthly cached)
    2. _STATIC_ELO table (approximate 2024/25 ratings — fallback when API unavailable)
    """
    ym = date_str[:7]
    if ym not in _elo_month_cache:
        _elo_month_cache[ym] = _load_clubelo_month(date_str)

    ratings = _elo_month_cache[ym]
    key     = team_name.lower().strip()

    # 1. Exact match from live API cache
    if key in ratings:
        return ratings[key]

    # 2. Partial / substring match from live API cache
    for k, v in ratings.items():
        if key in k or k in key:
            return v

    # 3. Static table — exact match
    if key in _STATIC_ELO:
        return _STATIC_ELO[key]

    # 4. Static table — substring match
    for k, v in _STATIC_ELO.items():
        if key in k or k in key:
            return v

    return None


# ---------------------------------------------------------------------------
# Club match predictor (Poisson + real Club Elo)
# ---------------------------------------------------------------------------

_CLUB_AVG_ELO   = 1600.0
_CLUB_ELO_SCALE = 300.0
_CLUB_BASE_GOALS = 1.35   # club avg > WC avg (1.25)
_HOME_ADV_ATT   = 0.08    # home side attack boost (~0.1 xG advantage)
_DEFAULT_ELO    = 1500.0  # fallback for unknown clubs

# ---------------------------------------------------------------------------
# Dixon-Coles correction (domestic leagues only)
# ---------------------------------------------------------------------------
# Varsayılan ρ (Dixon & Coles 1997 — İngiliz ligi veri seti).
# Her lig kendi tarihsel beraberlik yapısına göre ayarlanır; sabit -0.13
# tüm liglerde ~-3pp draw bias kaymasına neden olduğundan lig-spesifik sözlük kullanılır.
_DC_RHO = -0.13   # fallback

# Lig bazlı ρ:  negatif → draw olasılığını artırır.
# Kalibrasyon mantığı:
#   avg_baseline_draw_bias  >  +2pp  →  |ρ| büyük (-0.15)   daha fazla draw gerekiyor
#   avg_baseline_draw_bias  ≈   0pp  →  |ρ| orta  (-0.08)
#   avg_baseline_draw_bias  < -2pp  →  |ρ| küçük (-0.03)   zaten fazla draw var
_LEAGUE_DC_RHO: dict[str, float] = {
    "PL":           -0.10,   # avg baseline +1.05pp — orta düzeltme
    "LaLiga":       -0.08,   # avg baseline +0.25pp — hafif
    "Bundesliga":   -0.08,   # avg baseline -0.05pp — neredeyse sıfır
    "SerieA":       -0.15,   # avg baseline +2.40pp — güçlü düzeltme gerekiyor
    "Ligue1":       -0.03,   # avg baseline -2.60pp — PSG baskısı, az draw → minimal
    "Eredivisie":   -0.08,   # avg baseline +0.70pp — hafif
    "SuperLig":     -0.10,   # avg baseline +1.25pp — orta
    "PrimeiraLiga": -0.10,   # avg baseline +1.55pp — orta
}


def _dc_tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    """Adjustment factor for the four low-scoring scorelines (DC eq. 2)."""
    if x == 0 and y == 0:
        return 1.0 - lam * mu * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 0 and y == 1:
        return 1.0 + lam * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def _compute_1x2_dc(xg_h: float, xg_a: float, rho: float = _DC_RHO, max_goals: int = 8) -> tuple[float, float, float]:
    """
    1X2 probabilities using a Poisson score matrix with Dixon-Coles correction.
    Renormalises after applying τ so probabilities sum to 1.
    """
    matrix: dict[tuple[int, int], float] = {}
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p_ij = (
                math.exp(-xg_h) * (xg_h ** i) / math.factorial(i) *
                math.exp(-xg_a) * (xg_a ** j) / math.factorial(j)
            )
            matrix[(i, j)] = p_ij * _dc_tau(i, j, xg_h, xg_a, rho)

    total = sum(matrix.values())
    ph = sum(v for (i, j), v in matrix.items() if i > j) / total
    pd = sum(v for (i, j), v in matrix.items() if i == j) / total
    pa = sum(v for (i, j), v in matrix.items() if i < j) / total
    return round(ph, 4), round(pd, 4), round(pa, 4)


# ---------------------------------------------------------------------------
# Form momentum tracker
# ---------------------------------------------------------------------------
_FORM_WINDOW = 5   # son N maç; momentum etkisi fazla uzağa yayılmasın


def _get_form_mult(team_key: str, tracker: dict[str, list[dict]]) -> float:
    """
    Son _FORM_WINDOW maç sonucuna göre saldırı/savunma çarpanı.
    Aralık: 0.88 (çok kötü form) → 1.12 (çok iyi form). Yeterli veri yoksa 1.0.
    """
    recent = tracker.get(team_key, [])[-_FORM_WINDOW:]
    if not recent:
        return 1.0
    form_score = sum(r["pts"] for r in recent) / (3 * len(recent))  # 0‒1
    return 0.88 + 0.24 * form_score


def _update_form(tracker: dict[str, list[dict]], team_key: str, gf: int, ga: int) -> None:
    """Maç sonucunu form geçmişine ekle."""
    pts = 3 if gf > ga else (1 if gf == ga else 0)
    tracker.setdefault(team_key, []).append({"pts": pts, "gf": gf, "ga": ga})

# Static Club Elo table — approximate 2024/25 season ratings.
# Used as fallback when api.clubelo.com is unreachable (e.g. cloud env).
# Keys are lowercase; aliases handle common football-data.co.uk name variants.
_STATIC_ELO: dict[str, float] = {
    # ── Premier League ──────────────────────────────────────────────────────
    "manchester city": 2020.0, "man city": 2020.0,
    "liverpool": 1970.0,
    "arsenal": 1950.0,
    "chelsea": 1870.0,
    "aston villa": 1880.0,
    "tottenham": 1850.0, "tottenham hotspur": 1850.0, "spurs": 1850.0,
    "newcastle": 1830.0, "newcastle united": 1830.0,
    "manchester united": 1830.0, "man united": 1830.0, "man utd": 1830.0,
    "brighton": 1800.0, "brighton & hove albion": 1800.0,
    "west ham": 1780.0, "west ham united": 1780.0,
    "brentford": 1760.0,
    "fulham": 1760.0,
    "wolverhampton": 1740.0, "wolves": 1740.0, "wolverhampton wanderers": 1740.0,
    "crystal palace": 1740.0,
    "nottingham forest": 1730.0, "nott'm forest": 1730.0, "nottm forest": 1730.0,
    "bournemouth": 1720.0,
    "everton": 1720.0,
    "leicester": 1700.0, "leicester city": 1700.0,
    "ipswich": 1680.0, "ipswich town": 1680.0,
    "southampton": 1670.0,
    # ── La Liga ─────────────────────────────────────────────────────────────
    "real madrid": 2060.0,
    "barcelona": 2000.0, "fc barcelona": 2000.0,
    "atletico madrid": 1950.0, "atlético madrid": 1950.0, "atl. madrid": 1950.0,
    "athletic club": 1830.0, "athletic bilbao": 1830.0,
    "real sociedad": 1820.0,
    "villarreal": 1810.0,
    "real betis": 1800.0, "betis": 1800.0,
    "sevilla": 1790.0,
    "girona": 1780.0,
    "valencia": 1750.0,
    "osasuna": 1740.0,
    "mallorca": 1720.0,
    "celta vigo": 1730.0, "celta de vigo": 1730.0,
    "rayo vallecano": 1720.0,
    "getafe": 1710.0,
    "alaves": 1690.0, "deportivo alaves": 1690.0,
    "las palmas": 1690.0,
    "leganes": 1680.0, "leganés": 1680.0,
    "valladolid": 1670.0, "real valladolid": 1670.0,
    "espanyol": 1710.0,
    # ── Bundesliga ──────────────────────────────────────────────────────────
    "bayern munich": 2030.0, "fc bayern munchen": 2030.0, "fc bayern münchen": 2030.0,
    "bayer leverkusen": 1980.0, "leverkusen": 1980.0,
    "borussia dortmund": 1920.0, "dortmund": 1920.0, "b. dortmund": 1920.0,
    "rb leipzig": 1900.0,
    "vfb stuttgart": 1850.0, "stuttgart": 1850.0,
    "eintracht frankfurt": 1840.0, "ein frankfurt": 1840.0, "frankfurt": 1840.0,
    "sc freiburg": 1800.0, "freiburg": 1800.0,
    "vfl wolfsburg": 1800.0, "wolfsburg": 1800.0,
    "tsg hoffenheim": 1780.0, "hoffenheim": 1780.0,
    "werder bremen": 1780.0, "sv werder bremen": 1780.0,
    "borussia monchengladbach": 1760.0, "m'gladbach": 1760.0, "monchengladbach": 1760.0,
    "union berlin": 1760.0, "1. fc union berlin": 1760.0,
    "fc augsburg": 1740.0, "augsburg": 1740.0,
    "1. fsv mainz 05": 1750.0, "mainz": 1750.0, "fsv mainz": 1750.0,
    "fc heidenheim": 1720.0, "heidenheim": 1720.0,
    "vfl bochum": 1700.0, "bochum": 1700.0,
    "holstein kiel": 1680.0, "kiel": 1680.0,
    "fc st. pauli": 1700.0, "st. pauli": 1700.0, "st pauli": 1700.0,
    "fc koln": 1750.0, "1. fc köln": 1750.0, "koln": 1750.0, "köln": 1750.0,
    "hamburger sv": 1760.0, "hamburg": 1760.0, "hsv": 1760.0,
    # ── Serie A ─────────────────────────────────────────────────────────────
    "inter milan": 1980.0, "internazionale": 1980.0, "inter": 1980.0,
    "atalanta": 1930.0, "atalanta bc": 1930.0,
    "napoli": 1900.0, "ssc napoli": 1900.0,
    "ac milan": 1900.0, "milan": 1900.0,
    "juventus": 1900.0,
    "lazio": 1840.0, "ss lazio": 1840.0,
    "roma": 1840.0, "as roma": 1840.0,
    "fiorentina": 1830.0, "acf fiorentina": 1830.0,
    "bologna": 1820.0, "bologna fc": 1820.0,
    "torino": 1780.0, "torino fc": 1780.0,
    "monza": 1730.0, "ac monza": 1730.0,
    "empoli": 1730.0, "empoli fc": 1730.0,
    "lecce": 1710.0, "us lecce": 1710.0,
    "hellas verona": 1710.0, "verona": 1710.0,
    "genoa": 1720.0, "genoa cfc": 1720.0,
    "udinese": 1750.0, "udinese calcio": 1750.0,
    "cagliari": 1720.0, "cagliari calcio": 1720.0,
    "venezia": 1680.0, "venezia fc": 1680.0,
    "parma": 1700.0, "parma calcio": 1700.0,
    "como": 1680.0, "como 1907": 1680.0,
    "sassuolo": 1700.0, "us sassuolo": 1700.0,
    "pisa": 1690.0, "ac pisa": 1690.0,
    "cremonese": 1680.0, "us cremonese": 1680.0,
    # ── Ligue 1 ─────────────────────────────────────────────────────────────
    "paris saint-germain": 2040.0, "psg": 2040.0, "paris sg": 2040.0,
    "lille": 1840.0, "losc lille": 1840.0,
    "monaco": 1870.0, "as monaco": 1870.0,
    "marseille": 1830.0, "olympique marseille": 1830.0, "olympique de marseille": 1830.0,
    "lyon": 1820.0, "olympique lyonnais": 1820.0,
    "nice": 1800.0, "ogc nice": 1800.0,
    "lens": 1790.0, "rc lens": 1790.0,
    "rennes": 1780.0, "stade rennais": 1780.0,
    "brest": 1780.0, "stade brest": 1780.0,
    "strasbourg": 1750.0, "rc strasbourg": 1750.0,
    "reims": 1740.0, "stade de reims": 1740.0,
    "toulouse": 1730.0, "toulouse fc": 1730.0,
    "nantes": 1730.0, "fc nantes": 1730.0,
    "montpellier": 1710.0, "montpellier hsc": 1710.0,
    "le havre": 1700.0,
    "saint-etienne": 1720.0, "st etienne": 1720.0,
    "angers": 1690.0, "angers sco": 1690.0,
    "auxerre": 1700.0, "aj auxerre": 1700.0,
    "lorient": 1720.0, "fc lorient": 1720.0,
    "metz": 1700.0, "fc metz": 1700.0,
    "paris fc": 1710.0,
    # ── Eredivisie ──────────────────────────────────────────────────────────
    "psv": 1900.0, "psv eindhoven": 1900.0,
    "feyenoord": 1860.0,
    "ajax": 1830.0, "afc ajax": 1830.0,
    "az": 1800.0, "az alkmaar": 1800.0,
    "twente": 1780.0, "fc twente": 1780.0,
    "fc utrecht": 1760.0, "utrecht": 1760.0,
    "sparta rotterdam": 1730.0, "sparta": 1730.0,
    "sc heerenveen": 1730.0, "heerenveen": 1730.0,
    "nec": 1720.0, "nec nijmegen": 1720.0,
    "go ahead eagles": 1710.0,
    "heracles": 1700.0, "heracles almelo": 1700.0,
    "rkc waalwijk": 1700.0, "rkc": 1700.0,
    "fc groningen": 1710.0, "groningen": 1710.0,
    "excelsior": 1690.0, "sbv excelsior": 1690.0,
    "pec zwolle": 1690.0, "zwolle": 1690.0,
    "almere city": 1680.0, "almere": 1680.0,
    "fortuna sittard": 1690.0, "fortuna": 1690.0, "for sittard": 1690.0,
    "waalwijk": 1700.0,
    "nac breda": 1680.0, "nac": 1680.0,
    "willem ii": 1690.0,
    "telstar": 1660.0,
    "volendam": 1670.0, "fc volendam": 1670.0,
    # ── Süper Lig ───────────────────────────────────────────────────────────
    "galatasaray": 1880.0,
    "fenerbahce": 1870.0, "fenerbahçe": 1870.0,
    "besiktas": 1820.0, "beşiktaş": 1820.0,
    "trabzonspor": 1790.0,
    "istanbul basaksehir": 1780.0, "basaksehir": 1780.0, "başakşehir": 1780.0,
    "kasimpasa": 1730.0, "kasımpaşa": 1730.0,
    "sivasspor": 1720.0,
    "alanyaspor": 1720.0,
    "antalyaspor": 1710.0,
    "kayserispor": 1700.0,
    "rizespor": 1690.0, "caykur rizespor": 1690.0,
    "samsunspor": 1700.0,
    "hatayspor": 1690.0,
    "konyaspor": 1710.0,
    "gaziantep": 1690.0, "gaziantep fk": 1690.0,
    "adana demirspor": 1700.0,
    "eyupspor": 1700.0, "eyüpspor": 1700.0,
    "fatih karagumruk": 1700.0, "karagumruk": 1700.0,
    "pendikspor": 1660.0,
    "istanbulspor": 1660.0,
    "ankaragücü": 1690.0, "ankaragucu": 1690.0,
    "bodrumspor": 1660.0,
    "goztepe": 1690.0, "göztepe": 1690.0,
    "sakaryaspor": 1660.0,
    "ad. demirspor": 1700.0,
    "buyuksehyr": 1780.0, "buyuksehir": 1780.0,
    "genclerbirligi": 1660.0, "gençlerbirliği": 1660.0,
    "kocaelispor": 1660.0,
    # ── Primeira Liga ───────────────────────────────────────────────────────
    "benfica": 1900.0, "sl benfica": 1900.0,
    "porto": 1890.0, "fc porto": 1890.0,
    "sporting cp": 1900.0, "sporting": 1900.0, "sporting clube de portugal": 1900.0,
    "sc braga": 1800.0, "braga": 1800.0,
    "vitoria sc": 1750.0, "vitoria guimaraes": 1750.0, "vitória sc": 1750.0,
    "estoril": 1700.0, "estoril praia": 1700.0,
    "gil vicente": 1700.0,
    "moreirense": 1690.0,
    "rio ave": 1700.0,
    "farense": 1680.0, "sc farense": 1680.0,
    "boavista": 1710.0, "boavista fc": 1710.0,
    "nacional": 1680.0, "cd nacional": 1680.0,
    "casa pia": 1690.0, "casa pia ac": 1690.0,
    "famalicao": 1690.0, "fc famalicão": 1690.0,
    "estrela amadora": 1680.0,
    "vizela": 1670.0, "fc vizela": 1670.0,
    "arouca": 1690.0, "fc arouca": 1690.0,
    "chaves": 1680.0, "gd chaves": 1680.0,
    "santa clara": 1690.0, "cd santa clara": 1690.0,
    "avs": 1670.0,
    "sp lisbon": 1900.0,
    "alverca": 1670.0, "fc alverca": 1670.0,
    "tondela": 1670.0, "cd tondela": 1670.0,
    # ── La Liga aliases (football-data.co.uk short names) ───────────────────
    "ath bilbao": 1830.0,
    "ath madrid": 1950.0,
    "espanol": 1710.0,
    "levante": 1700.0, "levante ud": 1700.0,
    "elche": 1680.0, "elche cf": 1680.0,
    "oviedo": 1680.0, "real oviedo": 1680.0,
    # ── Premier League aliases ───────────────────────────────────────────────
    "burnley": 1700.0,
    "leeds": 1730.0, "leeds united": 1730.0,
    "sunderland": 1700.0, "sunderland afc": 1700.0,
}


def _club_features(team_name: str, elo: float, *, is_home: bool) -> TeamFeatures:
    """Build TeamFeatures from a Club Elo rating (no style bias — blank slate)."""
    en       = (elo - _CLUB_AVG_ELO) / _CLUB_ELO_SCALE
    home_att = _HOME_ADV_ATT if is_home else 0.0

    attack_strength  = max(0.50, 1.0 + en * 0.25 + home_att)
    defense_weakness = max(0.40, 1.0 - en * 0.20)

    h          = _name_hash(team_name.lower().strip())
    hash_var   = ((h >> 4) & 0xFF) / 255.0 * 3.0 - 1.5
    form_score = min(10.0, max(0.0, 5.0 + en * 3.0 + hash_var))

    return TeamFeatures(
        name=team_name,
        elo=elo,
        attack_strength=attack_strength,
        defense_weakness=defense_weakness,
        form_score=form_score,
        fatigue=2.0,
    )


def predict_club_match(
    home_name: str, home_elo: float,
    away_name: str, away_elo: float,
    home_form_mult: float = 1.0,
    away_form_mult: float = 1.0,
    dc_rho: float = _DC_RHO,
) -> dict:
    """
    Poisson 1X2 prediction for a club match using real Club Elo ratings.
    Applies Dixon-Coles correction (lig-spesifik ρ) + form momentum multipliers.
    Returns the same dict schema as WCOutcomePredictor.predict().
    """
    home_f = _club_features(home_name, home_elo, is_home=True)
    away_f = _club_features(away_name, away_elo, is_home=False)

    xg_h = max(0.20, _CLUB_BASE_GOALS * home_f.attack_strength * away_f.defense_weakness * home_form_mult)
    xg_a = max(0.20, _CLUB_BASE_GOALS * away_f.attack_strength * home_f.defense_weakness * away_form_mult)

    ph, pd, pa = _compute_1x2_dc(xg_h, xg_a, rho=dc_rho)

    if ph >= pa and ph >= pd:
        prediction = "HOME_WIN"
    elif pa > ph and pa >= pd:
        prediction = "AWAY_WIN"
    else:
        prediction = "DRAW"

    probs  = sorted([ph, pd, pa], reverse=True)
    conf   = round(max(30.0, min(92.0, probs[0] * 80.0 + (probs[0] - probs[1]) * 60.0)), 1)
    elo_gap = abs(home_elo - away_elo)

    return {
        "raw_prediction":   prediction,
        "home_win_prob":    round(ph * 100, 1),
        "draw_prob":        round(pd * 100, 1),
        "away_win_prob":    round(pa * 100, 1),
        "expected_goals_a": round(xg_h, 3),
        "expected_goals_b": round(xg_a, 3),
        "confidence":       conf,
        "elo_home":         home_elo,
        "elo_away":         away_elo,
        "elo_gap":          round(elo_gap, 1),
    }


# ---------------------------------------------------------------------------
# Fixture sources
# ---------------------------------------------------------------------------

_APIF_BASE = "https://v3.football.api-sports.io"


def _apif_get(path: str) -> dict:
    key = os.environ.get("API_FOOTBALL_KEY", "")
    req = urllib.request.Request(
        f"{_APIF_BASE}{path}",
        headers={"x-apisports-key": key},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_fixtures_api(league_id: int, season: int) -> list[dict]:
    """Fetch finished fixtures from API-Football v3."""
    key = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if not key:
        print("  [API-Football] API_FOOTBALL_KEY not set — skip API fetch.")
        return []

    print(f"  [API-Football] league={league_id} season={season} …")
    try:
        data = _apif_get(f"/fixtures?league={league_id}&season={season}&status=FT")
    except Exception as exc:
        print(f"  [API-Football] ERROR: {exc}")
        return []

    fixtures = []
    for fix in data.get("response", []):
        try:
            fixtures.append({
                "date":       fix["fixture"]["date"][:10],
                "home":       fix["teams"]["home"]["name"],
                "away":       fix["teams"]["away"]["name"],
                "home_goals": int(fix["goals"]["home"]),
                "away_goals": int(fix["goals"]["away"]),
            })
        except Exception:
            continue

    print(f"  [API-Football] {len(fixtures)} finished fixtures")
    return fixtures


def _parse_fdc_date(raw: str) -> str:
    """Convert football-data.co.uk DD/MM/YY or DD/MM/YYYY to YYYY-MM-DD."""
    raw = raw.strip()
    for fmt in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            from datetime import datetime
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw  # already ISO or unknown — pass through


def fetch_fixtures_csv(csv_path: str) -> list[dict]:
    """Load fixtures from a local CSV.

    Accepts two formats (auto-detected from header):
    1. Native: date,home,away,home_goals,away_goals
    2. football-data.co.uk: Date,HomeTeam,AwayTeam,FTHG,FTAG,...
    """
    fixtures = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        is_fdc = "HomeTeam" in headers  # football-data.co.uk layout
        for row in reader:
            try:
                if is_fdc:
                    hg = row.get("FTHG", "").strip()
                    ag = row.get("FTAG", "").strip()
                    if not hg or not ag:
                        continue
                    fixtures.append({
                        "date":       _parse_fdc_date(row["Date"]),
                        "home":       row["HomeTeam"].strip(),
                        "away":       row["AwayTeam"].strip(),
                        "home_goals": int(hg),
                        "away_goals": int(ag),
                    })
                else:
                    fixtures.append({
                        "date":       row["date"].strip(),
                        "home":       row["home"].strip(),
                        "away":       row["away"].strip(),
                        "home_goals": int(row["home_goals"]),
                        "away_goals": int(row["away_goals"]),
                    })
            except Exception:
                continue
    fmt_label = "football-data.co.uk" if is_fdc else "native"
    print(f"  [CSV/{fmt_label}] {len(fixtures)} fixtures from {csv_path}")
    return fixtures


# ---------------------------------------------------------------------------
# Accuracy computation (mirrors result_settler.compute_accuracy)
# ---------------------------------------------------------------------------

def _outcome(hg: int, ag: int) -> str:
    if hg > ag:
        return "HOME_WIN"
    if ag > hg:
        return "AWAY_WIN"
    return "DRAW"


def compute_accuracy(settlements: list[dict], league_key: str, season: int) -> dict:
    """Produce an accuracy dict in shadow_accuracy.json schema."""
    n = len(settlements)
    if n == 0:
        return {"n_settled": 0, "league": league_key, "season": season,
                "generated_at": datetime.now(timezone.utc).isoformat()}

    n_correct     = sum(1 for s in settlements if s["correct"])
    n_draws_pred  = sum(1 for s in settlements if s["predicted_outcome"] == "DRAW")
    n_draws_act   = sum(1 for s in settlements if s["actual_outcome"] == "DRAW")
    acc           = round(n_correct / n * 100, 2)

    draw_rate_predicted = round(sum(s["probabilities"]["D"] for s in settlements) / n, 3)
    draw_rate_actual    = round(n_draws_act / n * 100, 3)
    draw_rate_bias      = round(draw_rate_actual - draw_rate_predicted, 3)

    labels = ["HOME_WIN", "DRAW", "AWAY_WIN"]
    cm: dict[str, dict[str, int]] = {p: {a: 0 for a in labels} for p in labels}
    for s in settlements:
        cm[s["predicted_outcome"]][s["actual_outcome"]] += 1

    brier_score = round(sum(s["brier_contrib"] for s in settlements) / n, 5)
    log_loss    = round(sum(s["log_loss_contrib"] for s in settlements) / n, 5)

    bands = [("90-92", 90, 93), ("70-89", 70, 90), ("50-69", 50, 70), ("30-49", 30, 50)]
    by_band: dict[str, dict] = {}
    for label, lo, hi in bands:
        pool = [s for s in settlements if lo <= s["confidence"] < hi]
        by_band[label] = {
            "n":        len(pool),
            "mean_conf": round(sum(s["confidence"] for s in pool) / len(pool), 2) if pool else None,
            "correct":  sum(1 for s in pool if s["correct"]),
            "accuracy": round(sum(1 for s in pool if s["correct"]) / len(pool) * 100, 2)
                        if pool else None,
        }

    ece = None
    ece_available = n >= 50
    reliability_bins: list[dict] = []
    if ece_available:
        ece_sum = 0.0
        for lo, hi in [(30,40),(40,50),(50,60),(60,70),(70,80),(80,93)]:
            pool = [s for s in settlements if lo <= s["confidence"] < hi]
            if not pool:
                reliability_bins.append({"band": f"{lo}-{hi}", "n": 0,
                                         "mean_conf": None, "accuracy": None})
                continue
            mean_c   = sum(s["confidence"] for s in pool) / len(pool)
            accuracy = sum(1 for s in pool if s["correct"]) / len(pool)
            ece_sum += abs(accuracy - mean_c / 100.0) * (len(pool) / n)
            reliability_bins.append({
                "band": f"{lo}-{hi}", "n": len(pool),
                "mean_conf": round(mean_c, 2),
                "accuracy":  round(accuracy * 100, 2),
            })
        ece = round(ece_sum, 5)

    elo_found   = sum(1 for s in settlements if s.get("elo_found", True))
    elo_missing = n - elo_found

    flags: list[str] = []
    if abs(draw_rate_bias) >= 5.0:
        flags.append(
            f"DRAW_BIAS — actual={draw_rate_actual:.1f}% "
            f"pred={draw_rate_predicted:.1f}% bias={draw_rate_bias:+.1f}pp"
        )
    if elo_missing > n * 0.10:
        flags.append(
            f"ELO_COVERAGE_WARN — {elo_missing}/{n} teams used default elo={_DEFAULT_ELO}"
        )
    if acc < 40.0:
        flags.append(f"ACCURACY_BELOW_RANDOM — {acc:.1f}%")
    if brier_score > 0.50:
        flags.append(f"BRIER_HIGH — {brier_score:.4f}")

    return {
        "generated_at":         datetime.now(timezone.utc).isoformat(),
        "source":               "league_backtest",
        "league":               league_key,
        "season":               season,
        "n_settled":            n,
        "n_correct":            n_correct,
        "overall_accuracy_pct": acc,
        "log_loss":             log_loss,
        "brier_score":          brier_score,
        "n_draws_predicted":    n_draws_pred,
        "n_draws_actual":       n_draws_act,
        "draw_rate_predicted":  draw_rate_predicted,
        "draw_rate_actual":     draw_rate_actual,
        "draw_rate_bias":       draw_rate_bias,
        "confusion_matrix":     cm,
        "by_confidence_band":   by_band,
        "ece":                  ece,
        "ece_available":        ece_available,
        "reliability_bins":     reliability_bins,
        "elo_coverage":         {"found": elo_found, "missing": elo_missing, "total": n},
        "flags":                flags,
    }


# ---------------------------------------------------------------------------
# Backtest runner
# ---------------------------------------------------------------------------

def run_backtest(
    league_key: str,
    season: int,
    *,
    csv_path: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Fetch fixtures, predict, settle, and write results for one league/season."""
    meta = LEAGUES.get(league_key)
    if meta is None:
        raise ValueError(f"Unknown league: {league_key}. Valid: {list(LEAGUES)}")

    print(f"\n{'='*60}")
    print(f"BACKTEST  {meta['name']} ({league_key})  season {season}/{season+1}")
    print("=" * 60)

    if csv_path:
        fixtures = fetch_fixtures_csv(csv_path)
    else:
        fixtures = fetch_fixtures_api(meta["id"], season)

    if not fixtures:
        print("  No fixtures.  Use --csv or set API_FOOTBALL_KEY.")
        return {"n_settled": 0, "league": league_key, "season": season}

    fixtures.sort(key=lambda x: x["date"])
    print(f"  Total fixtures : {len(fixtures)}")

    dc_rho       = _LEAGUE_DC_RHO.get(league_key, _DC_RHO)
    print(f"  DC ρ           : {dc_rho}  ({league_key})")

    settlements: list[dict] = []
    elo_miss   = 0
    form_tracker: dict[str, list[dict]] = {}   # maç bazlı momentum takibi

    for fix in fixtures:
        home_elo_val = _get_club_elo(fix["home"], fix["date"])
        away_elo_val = _get_club_elo(fix["away"], fix["date"])
        elo_found    = home_elo_val is not None and away_elo_val is not None
        if not elo_found:
            elo_miss += 1
        home_elo_val = home_elo_val or _DEFAULT_ELO
        away_elo_val = away_elo_val or _DEFAULT_ELO

        home_key = fix["home"].lower().strip()
        away_key = fix["away"].lower().strip()
        home_fm  = _get_form_mult(home_key, form_tracker)
        away_fm  = _get_form_mult(away_key, form_tracker)

        pred   = predict_club_match(fix["home"], home_elo_val, fix["away"], away_elo_val,
                                    home_form_mult=home_fm, away_form_mult=away_fm,
                                    dc_rho=dc_rho)
        actual = _outcome(fix["home_goals"], fix["away_goals"])
        predicted = pred["raw_prediction"]
        correct   = (predicted == actual)

        ph  = pred["home_win_prob"] / 100
        pd_ = pred["draw_prob"] / 100
        pa  = pred["away_win_prob"] / 100
        probs = {"H": round(ph * 100, 1), "D": round(pd_ * 100, 1), "A": round(pa * 100, 1)}

        p_actual = max({"HOME_WIN": ph, "DRAW": pd_, "AWAY_WIN": pa}[actual], 1e-9)
        log_loss_contrib = round(-math.log(p_actual), 5)
        o_H = 1.0 if actual == "HOME_WIN" else 0.0
        o_D = 1.0 if actual == "DRAW"     else 0.0
        o_A = 1.0 if actual == "AWAY_WIN" else 0.0
        brier_contrib = round((ph - o_H)**2 + (pd_ - o_D)**2 + (pa - o_A)**2, 6)

        settlements.append({
            "date":              fix["date"],
            "home_team":         fix["home"],
            "away_team":         fix["away"],
            "home_goals":        fix["home_goals"],
            "away_goals":        fix["away_goals"],
            "predicted_outcome": predicted,
            "actual_outcome":    actual,
            "correct":           correct,
            "probabilities":     probs,
            "confidence":        pred["confidence"],
            "elo_home":          home_elo_val,
            "elo_away":          away_elo_val,
            "elo_gap":           pred["elo_gap"],
            "elo_found":         elo_found,
            "log_loss_contrib":  log_loss_contrib,
            "brier_contrib":     brier_contrib,
            "home_form_mult":    round(home_fm, 3),
            "away_form_mult":    round(away_fm, 3),
            "dc_rho":            dc_rho,
        })

        # Form tracker'ı bu maçın gerçek sonucuyla güncelle (sıra önemli)
        _update_form(form_tracker, home_key, fix["home_goals"], fix["away_goals"])
        _update_form(form_tracker, away_key, fix["away_goals"], fix["home_goals"])

    if elo_miss:
        print(f"  Elo misses : {elo_miss}/{len(fixtures)} → default {_DEFAULT_ELO}")

    acc = compute_accuracy(settlements, league_key, season)

    if not dry_run:
        stem        = f"{league_key}_{season}"
        acc_path    = BACKTEST_DIR / f"{stem}.json"
        settle_path = BACKTEST_DIR / f"{stem}.jsonl"
        acc_path.write_text(json.dumps(acc, indent=2))
        with open(settle_path, "w") as f:
            for s in settlements:
                f.write(json.dumps(s, separators=(",", ":")) + "\n")
        print(f"  → {acc_path}")
        print(f"  → {settle_path}")

    n   = acc.get("n_settled", 0)
    pct = acc.get("overall_accuracy_pct", "n/a")
    bri = acc.get("brier_score", "n/a")
    bias = acc.get("draw_rate_bias")
    bias_str = f"{bias:+.1f}pp" if isinstance(bias, float) else "?"
    print(f"\n  n={n}  accuracy={pct}%  brier={bri}  draw_bias={bias_str}")
    for flag in acc.get("flags", []):
        print(f"  ⚠  {flag}")

    return acc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="League historical backtest")
    p.add_argument("--league",   default="PL",
                   help=f"League key(s), comma-separated. Choices: {','.join(LEAGUES)}")
    p.add_argument("--season",   type=int, default=2024,
                   help="Season start year (2024 → 2024/25)")
    p.add_argument("--all",      action="store_true", help="Run all 7 leagues")
    p.add_argument("--csv",      default="",
                   help="Local CSV fixture file (date,home,away,home_goals,away_goals)")
    p.add_argument("--dry-run",  action="store_true", help="Print stats, skip file writes")
    args = p.parse_args(argv)

    targets = list(LEAGUES.keys()) if args.all else [k.strip() for k in args.league.split(",")]

    results: dict[str, dict] = {}
    for league_key in targets:
        try:
            csv_path = args.csv if (len(targets) == 1 and args.csv) else None
            results[league_key] = run_backtest(
                league_key, args.season,
                csv_path=csv_path,
                dry_run=args.dry_run,
            )
        except Exception as exc:
            print(f"ERROR [{league_key}]: {exc}")
            results[league_key] = {"error": str(exc)}

    if len(targets) > 1:
        print(f"\n{'='*60}")
        print(f"{'League':<18} {'n':>6} {'Acc%':>8} {'DrawBias':>10} {'Brier':>8}")
        print("-" * 54)
        for key, acc in results.items():
            if "error" in acc:
                print(f"{key:<18}  ERROR: {acc['error']}")
                continue
            name  = LEAGUES.get(key, {}).get("name", key)
            n     = acc.get("n_settled", 0)
            pct   = f"{acc.get('overall_accuracy_pct','?')}%"
            bias  = acc.get("draw_rate_bias")
            bias_s = f"{bias:+.1f}pp" if isinstance(bias, float) else "?"
            bri   = acc.get("brier_score", "?")
            print(f"{name:<18} {n:>6} {pct:>8} {bias_s:>10} {bri:>8}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
