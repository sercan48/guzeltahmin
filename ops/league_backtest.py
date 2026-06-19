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
    """Return Club Elo for a team at the given match date (month-level precision)."""
    ym = date_str[:7]
    if ym not in _elo_month_cache:
        _elo_month_cache[ym] = _load_clubelo_month(date_str)

    ratings = _elo_month_cache[ym]
    key     = team_name.lower().strip()

    if key in ratings:
        return ratings[key]

    # Partial / substring match (handles "Man City" → "manchester city" etc.)
    for k, v in ratings.items():
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
) -> dict:
    """
    Poisson 1X2 prediction for a club match using real Club Elo ratings.
    Returns the same dict schema as WCOutcomePredictor.predict().
    """
    home_f = _club_features(home_name, home_elo, is_home=True)
    away_f = _club_features(away_name, away_elo, is_home=False)

    xg_h = max(0.20, _CLUB_BASE_GOALS * home_f.attack_strength * away_f.defense_weakness)
    xg_a = max(0.20, _CLUB_BASE_GOALS * away_f.attack_strength * home_f.defense_weakness)

    ph, pd, pa = compute_1x2_poisson(xg_h, xg_a)

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


def fetch_fixtures_csv(csv_path: str) -> list[dict]:
    """Load fixtures from a local CSV (columns: date,home,away,home_goals,away_goals)."""
    fixtures = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                fixtures.append({
                    "date":       row["date"].strip(),
                    "home":       row["home"].strip(),
                    "away":       row["away"].strip(),
                    "home_goals": int(row["home_goals"]),
                    "away_goals": int(row["away_goals"]),
                })
            except Exception:
                continue
    print(f"  [CSV] {len(fixtures)} fixtures from {csv_path}")
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

    settlements: list[dict] = []
    elo_miss = 0

    for fix in fixtures:
        home_elo_val = _get_club_elo(fix["home"], fix["date"])
        away_elo_val = _get_club_elo(fix["away"], fix["date"])
        elo_found    = home_elo_val is not None and away_elo_val is not None
        if not elo_found:
            elo_miss += 1
        home_elo_val = home_elo_val or _DEFAULT_ELO
        away_elo_val = away_elo_val or _DEFAULT_ELO

        pred   = predict_club_match(fix["home"], home_elo_val, fix["away"], away_elo_val)
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
        })

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
