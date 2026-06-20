"""
ops/league_live_predictor.py — Lig Canlı Tahmin Pipeline

1. Bugün + yarın için aktif liglerin fixture'larını çeker (league_fixture_fetcher).
2. Her maç için Club Elo çeker (league_backtest._get_club_elo).
3. predict_club_match() ile Poisson+DC tahmini üretir.
4. The Odds API'den oranları çeker ve eşleştirir.
5. Kelly/EV/tier zenginleştirmesi yapar (league_paper_shadow._enrich_kelly).
6. league_paper_shadow.format_league_bulletin() ile Telegram mesajı oluşturur.
7. Supabase league_predictions tablosuna yazar (isteğe bağlı).

Kullanım:
    python -m ops.league_live_predictor --dry-run          # Telegram göndermez
    python -m ops.league_live_predictor --deliver          # Telegram'a gönder
    python -m ops.league_live_predictor --league SuperLig  # Tek lig
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# The Odds API — lig sport key haritası
# ---------------------------------------------------------------------------

_ODDS_SPORT_KEYS: dict[str, str] = {
    "PL":           "soccer_epl",
    "LaLiga":       "soccer_spain_la_liga",
    "Bundesliga":   "soccer_germany_bundesliga",
    "SerieA":       "soccer_italy_serie_a",
    "Ligue1":       "soccer_france_ligue_one",
    "Eredivisie":   "soccer_netherlands_eredivisie",
    "SuperLig":     "soccer_turkey_super_league",
    "PrimeiraLiga": "soccer_portugal_primeira_liga",
}

_DEFAULT_ELO = 1700.0

# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------

def _norm(name: str) -> str:
    return name.lower().strip()


def _fetch_league_odds(league_key: str) -> dict[tuple[str, str], dict]:
    """
    The Odds API'den lig oranlarını çek.
    Dönüş: {(norm_home, norm_away): {h, d, a, over_2_5, under_2_5, btts_yes, btts_no, src}}
    """
    odds_key = os.getenv("ODDS_API_KEY", "").strip()
    if not odds_key:
        logger.debug("[Odds] ODDS_API_KEY yok — oranlar atlandı")
        return {}

    sport_key = _ODDS_SPORT_KEYS.get(league_key)
    if not sport_key:
        return {}

    try:
        import requests
        url    = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
        params = {
            "apiKey":     odds_key,
            "regions":    "eu",
            "markets":    "h2h,totals,btts",
            "oddsFormat": "decimal",
        }
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            logger.warning("[Odds] HTTP %d — %s", resp.status_code, sport_key)
            return {}

        remaining = resp.headers.get("x-requests-remaining")
        used      = resp.headers.get("x-requests-used")
        if remaining:
            logger.info("[Odds] %s kalan kredi: %s (kullanılan: %s)", league_key, remaining, used)

    except Exception as exc:
        logger.warning("[Odds] İstek hatası: %s", exc)
        return {}

    result: dict[tuple[str, str], dict] = {}
    for event in resp.json():
        hn = _norm(event.get("home_team", ""))
        an = _norm(event.get("away_team", ""))

        h_list, d_list, a_list = [], [], []
        over_list, under_list  = [], []
        btts_y, btts_n         = [], []

        hk = event["home_team"].lower()
        ak = event["away_team"].lower()

        for bk in event.get("bookmakers", []):
            for mkt in bk.get("markets", []):
                key = mkt.get("key", "")
                if key == "h2h":
                    outcomes = {o["name"].lower(): o["price"] for o in mkt.get("outcomes", [])}
                    if hk in outcomes: h_list.append(outcomes[hk])
                    if ak in outcomes: a_list.append(outcomes[ak])
                    if "draw" in outcomes: d_list.append(outcomes["draw"])
                elif key == "totals":
                    for o in mkt.get("outcomes", []):
                        if o.get("point") == 2.5:
                            if o.get("name", "").lower() == "over":  over_list.append(o["price"])
                            elif o.get("name", "").lower() == "under": under_list.append(o["price"])
                elif key == "btts":
                    for o in mkt.get("outcomes", []):
                        n = o.get("name", "").lower()
                        if n == "yes": btts_y.append(o["price"])
                        elif n == "no":  btts_n.append(o["price"])

        if not h_list:
            continue

        def _med(lst: list) -> float | None:
            if not lst: return None
            s = sorted(lst)
            m = len(s) // 2
            return round(s[m] if len(s) % 2 else (s[m-1]+s[m])/2, 3)

        result[(hn, an)] = {
            "h":          _med(h_list),
            "d":          _med(d_list),
            "a":          _med(a_list),
            "over_2_5":   _med(over_list),
            "under_2_5":  _med(under_list),
            "btts_yes":   _med(btts_y),
            "btts_no":    _med(btts_n),
            "src":        "the-odds-api",
        }

    logger.info("[Odds] %s: %d maç oranı alındı", league_key, len(result))
    return result


def _match_odds(
    home: str,
    away: str,
    odds_map: dict[tuple[str, str], dict],
) -> dict | None:
    """Takım adını odds_map'teki anahtarla eşleştir (toleranslı)."""
    hn = _norm(home)
    an = _norm(away)

    if (hn, an) in odds_map:
        return odds_map[(hn, an)]

    for (oh, oa), v in odds_map.items():
        if (hn in oh or oh in hn) and (an in oa or oa in an):
            return v

    return None


# ---------------------------------------------------------------------------
# Supabase yazar
# ---------------------------------------------------------------------------

def _write_to_supabase(predictions: list[dict]) -> None:
    url  = os.getenv("SUPABASE_URL", "").strip()
    key  = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        logger.debug("[Supabase] bağlantı bilgisi yok — atlandı")
        return

    try:
        import requests
        endpoint = f"{url}/rest/v1/league_predictions"
        headers  = {
            "apikey":        key,
            "Authorization": f"Bearer {key}",
            "Content-Type":  "application/json",
            "Prefer":        "resolution=merge-duplicates",
        }
        rows = []
        for p in predictions:
            rows.append({
                "fixture_id":        p.get("fixture_id"),
                "league_key":        p.get("league_key"),
                "season":            p.get("season"),
                "home_team":         p.get("home_team"),
                "away_team":         p.get("away_team"),
                "kickoff_time":      p.get("kickoff_time"),
                "predicted_outcome": p.get("predicted_outcome"),
                "home_win_prob":     p.get("home_win_prob"),
                "draw_prob":         p.get("draw_prob"),
                "away_win_prob":     p.get("away_win_prob"),
                "xg_home":           p.get("xg_home"),
                "xg_away":           p.get("xg_away"),
                "confidence":        p.get("confidence"),
                "elo_home":          p.get("elo_home"),
                "elo_away":          p.get("elo_away"),
                "elo_gap":           p.get("elo_gap"),
                "tier":              p.get("tier"),
                "is_sniper":         p.get("is_sniper", False),
                "odds_h":            (p.get("odds") or {}).get("h"),
                "odds_d":            (p.get("odds") or {}).get("d"),
                "odds_a":            (p.get("odds") or {}).get("a"),
                "value_score":       p.get("_value_score"),
                "edge_pct":          p.get("_edge_pct"),
                "source":            p.get("source"),
                "created_at":        datetime.now(timezone.utc).isoformat(),
            })
        resp = requests.post(endpoint, json=rows, headers=headers, timeout=15)
        if resp.status_code in (200, 201):
            logger.info("[Supabase] %d tahmin yazıldı", len(rows))
        else:
            logger.warning("[Supabase] HTTP %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("[Supabase] Hata: %s", exc)


# ---------------------------------------------------------------------------
# Ana tahmin döngüsü
# ---------------------------------------------------------------------------

def run_league_predictions(
    league_keys: list[str] | None = None,
    days_ahead: int = 1,
    deliver: bool = False,
) -> list[dict]:
    """
    Belirtilen ligler için tahmin üret ve isteğe bağlı Telegram'a gönder.
    Dönüş: üretilen tüm tahmin dict listesi.
    """
    from ops.league_fixture_fetcher import fetch_upcoming, active_leagues_today
    from ops.league_backtest import (
        _get_club_elo, predict_club_match,
        _LEAGUE_DC_RHO, _DEFAULT_ELO,
    )
    from ops.league_paper_shadow import (
        _enrich_kelly, _tier_from_elo_gap,
        format_league_bulletin, send_league_bulletin,
        _BANKROLL_DEFAULT,
    )

    today   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    active  = active_leagues_today(league_keys)
    if not active:
        logger.info("Bugün aktif lig yok. Çıkılıyor.")
        return []

    logger.info("Aktif ligler: %s", ", ".join(active))

    all_predictions: list[dict] = []

    for lk in active:
        fixtures = fetch_upcoming(league_keys=[lk], days_ahead=days_ahead)
        if not fixtures:
            logger.info("[%s] Yaklaşan fixture yok", lk)
            continue

        logger.info("[%s] %d fixture bulundu", lk, len(fixtures))
        odds_map = _fetch_league_odds(lk)
        dc_rho   = _LEAGUE_DC_RHO.get(lk, -0.10)

        league_preds: list[dict] = []

        for fix in fixtures:
            home = fix["home_team"]
            away = fix["away_team"]
            home_elo = _get_club_elo(home, today) or _DEFAULT_ELO
            away_elo = _get_club_elo(away, today) or _DEFAULT_ELO

            result = predict_club_match(
                home_name=home, home_elo=home_elo,
                away_name=away, away_elo=away_elo,
                dc_rho=dc_rho,
            )

            elo_gap = abs(home_elo - away_elo)
            tier    = _tier_from_elo_gap(elo_gap)

            pred = {
                "fixture_id":        fix["fixture_id"],
                "league_key":        lk,
                "season":            fix["season"],
                "home_team":         home,
                "away_team":         away,
                "kickoff_time":      fix["kickoff_time"],
                "predicted_outcome": result["raw_prediction"],
                "probabilities": {
                    "H": result["home_win_prob"],
                    "D": result["draw_prob"],
                    "A": result["away_win_prob"],
                },
                "home_win_prob": result["home_win_prob"],
                "draw_prob":     result["draw_prob"],
                "away_win_prob": result["away_win_prob"],
                "xg_home":       result["expected_goals_a"],
                "xg_away":       result["expected_goals_b"],
                "confidence":    result["confidence"],
                "elo_home":      home_elo,
                "elo_away":      away_elo,
                "elo_gap":       elo_gap,
                "tier":          tier,
                "is_no_bet":     False,
                "source":        fix["source"],
            }

            odds = _match_odds(home, away, odds_map)
            if odds:
                pred["odds"] = odds

            pred = _enrich_kelly(pred, _BANKROLL_DEFAULT)
            league_preds.append(pred)

        if not league_preds:
            continue

        from ops.league_backtest import LEAGUES
        league_meta = LEAGUES.get(lk, {})
        session = {
            "session_id":    f"league_{lk}_{today}",
            "bulletin_date": today,
            "league_key":    lk,
            "league_name":   league_meta.get("name", lk),
            "season":        league_preds[0]["season"],
            "predictions":   league_preds,
        }

        messages = format_league_bulletin(session)

        if deliver:
            token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip() or \
                      os.getenv("TELEGRAM_PERSONAL_CHANNEL", "").strip()
            if token and chat_id:
                send_league_bulletin(messages, token, chat_id)
            else:
                logger.warning("[%s] Telegram bilgileri eksik — gönderilmedi", lk)
        else:
            for msg in messages:
                print(msg)
                print("─" * 60)

        all_predictions.extend(league_preds)

    if all_predictions:
        _write_to_supabase(all_predictions)

        out_path = Path("data/league_predictions") / f"{today}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(
            {"date": today, "predictions": all_predictions},
            indent=2, ensure_ascii=False,
        ))
        logger.info("Tahminler kaydedildi: %s", out_path)

    return all_predictions


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Lig canlı tahmin pipeline'ı")
    ap.add_argument("--dry-run",  action="store_true", help="Telegram göndermez, stdout'a basar")
    ap.add_argument("--deliver",  action="store_true", help="Telegram'a gönder")
    ap.add_argument("--league",   help="Tek lig (örn: SuperLig). Virgülle birden fazla")
    ap.add_argument("--days",     type=int, default=1, help="Kaç gün ilerisine bak (varsayılan 1)")
    args = ap.parse_args()

    league_keys = None
    if args.league:
        league_keys = [lk.strip() for lk in args.league.split(",")]

    preds = run_league_predictions(
        league_keys=league_keys,
        days_ahead=args.days,
        deliver=args.deliver,
    )
    print(f"\nToplam {len(preds)} tahmin üretildi.")


if __name__ == "__main__":
    main()
