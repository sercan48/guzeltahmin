"""
ops/result_backfiller.py — API-Football Sonuç Yedekleme (Backfill)

result_settler.py'nin settle edemediği geçmiş tarihli WC tahminlerini
API-Football v3 üzerinden tamamlar.

- shadow_settlements.jsonl'daki mevcut natural_key'leri kontrol eder
- Geçmiş tarihli ama settle edilmemiş tahminleri bulur
- API-Football /fixtures endpoint'inden WC 2026 sonuçlarını çeker
- Takım adı normalizasyonu + alias eşleştirmesi yapar
- Aynı format ve hesaplama mantığıyla settlement kaydı yazar
- shadow_accuracy.json'u tüm settlements üzerinden yeniden hesaplar

Kullanım:
    python -m ops.result_backfiller --settle       # bekleyen maçları settle et
    python -m ops.result_backfiller --dry-run      # neyi settle edeceğini göster
    python -m ops.result_backfiller --status       # özet rapor
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
from datetime import datetime, timezone, date
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

_PREDICTIONS_FILE  = Path("data/shadow_predictions.jsonl")
_SETTLEMENTS_FILE  = Path("data/shadow_settlements.jsonl")
_ACCURACY_FILE     = Path("data/shadow_accuracy.json")

_WC_LEAGUE_ID  = 1       # API-Football: FIFA World Cup
_WC_SEASON     = 2026
_SETTLER_VER   = "1.1-backfill"
_API_SOURCE    = "api-football/v3"

# ---------------------------------------------------------------------------
# Takım adı normalizasyonu + alias tablosu
# ---------------------------------------------------------------------------

# API-Football → bizim prediction natural_key formatı
# Sola: API-Football'dan gelen isim (lowercase)
# Sağa: bizim natural_key'de kullandığımız isim (lowercase)
_NAME_ALIASES: dict[str, str] = {
    # Kore
    "korea republic":       "south korea",
    "republic of korea":    "south korea",
    # ABD
    "usa":                  "united states",
    "united states of america": "united states",
    # Bosna
    "bosnia & herzegovina": "bosnia-herzegovina",
    "bosnia and herzegovina": "bosnia-herzegovina",
    # Kongo
    "dr congo":             "congo dr",
    "congo dr":             "congo dr",
    "democratic republic of the congo": "congo dr",
    "congo (dr)":           "congo dr",
    # Yeşil Burun Adaları
    "cape verde":           "cape verde islands",
    # Fildişi Sahili
    "ivory coast":          "ivory coast",
    "cote d'ivoire":        "ivory coast",
    "côte d'ivoire":        "ivory coast",
    # Haiti
    "haiti":                "haïti",
    # Curacao
    "curacao":              "curaçao",
    # Yeni Zelanda
    "new zealand":          "new zealand",
    # Nijerya
    "nigeria":              "nigeria",
}


def _norm(name: str) -> str:
    """Normalize team name for matching."""
    n = name.lower().strip()
    return _NAME_ALIASES.get(n, n)


def _name_match(api_name: str, pred_name: str) -> bool:
    """Fuzzy match between API team name and prediction team name."""
    a = _norm(api_name)
    p = _norm(pred_name)
    if a == p:
        return True
    # substring fallback
    if a in p or p in a:
        return True
    # word overlap (handles "United States" vs "United States of America")
    a_words = set(a.split())
    p_words = set(p.split())
    if len(a_words & p_words) >= 2:
        return True
    return False


# ---------------------------------------------------------------------------
# API-Football çekim
# ---------------------------------------------------------------------------

def _fetch_af_fixtures(from_date: str, to_date: str) -> list[dict]:
    """
    API-Football /v3/fixtures endpoint'inden WC 2026 maç sonuçlarını çeker.
    Dönüş: [{"home": str, "away": str, "date": str, "home_goals": int, "away_goals": int}]
    Sadece FT (Full Time) statusundaki maçlar döner.
    """
    key = os.getenv("API_FOOTBALL_KEY", "").strip()
    if not key:
        logger.warning("[AF] API_FOOTBALL_KEY eksik")
        return []

    try:
        import requests
        url     = "https://v3.football.api-sports.io/fixtures"
        headers = {
            "x-rapidapi-key":  key,
            "x-rapidapi-host": "v3.football.api-sports.io",
        }
        params = {
            "league":  _WC_LEAGUE_ID,
            "season":  _WC_SEASON,
            "from":    from_date,
            "to":      to_date,
            "status":  "FT",         # sadece oynananlar
            "timezone": "UTC",
        }
        resp = requests.get(url, headers=headers, params=params, timeout=20)
        if resp.status_code != 200:
            logger.warning("[AF] HTTP %d: %s", resp.status_code, resp.text[:120])
            return []

        data = resp.json()
        remaining = resp.headers.get("x-ratelimit-requests-remaining")
        if remaining:
            logger.info("[AF] Kalan kota: %s", remaining)

        results = []
        for fix in data.get("response", []):
            teams  = fix.get("teams", {})
            goals  = fix.get("goals", {})
            match_dt = fix.get("fixture", {}).get("date", "")[:10]  # YYYY-MM-DD

            home_name  = teams.get("home", {}).get("name", "")
            away_name  = teams.get("away", {}).get("name", "")
            home_goals = goals.get("home")
            away_goals = goals.get("away")

            if home_goals is None or away_goals is None:
                continue  # tamamlanmamış

            results.append({
                "home":       home_name,
                "away":       away_name,
                "date":       match_dt,
                "home_goals": int(home_goals),
                "away_goals": int(away_goals),
            })

        logger.info("[AF] %s → %s arası %d sonuç alındı", from_date, to_date, len(results))
        return results

    except Exception as exc:
        logger.warning("[AF] Hata: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Hesaplama yardımcıları
# ---------------------------------------------------------------------------

def _outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "HOME_WIN"
    if away_goals > home_goals:
        return "AWAY_WIN"
    return "DRAW"


def _log_loss(prob_pct: float) -> float:
    """prob_pct: 0-100 aralığında. Dönüş: -ln(p)."""
    p = max(prob_pct / 100.0, 1e-7)
    return round(-math.log(p), 5)


def _brier(probs: dict, actual_outcome: str) -> float:
    """Çok sınıflı Brier skoru katkısı."""
    outcome_map = {"HOME_WIN": "H", "DRAW": "D", "AWAY_WIN": "A"}
    score = 0.0
    for outcome, key in outcome_map.items():
        p = probs.get(key, 0) / 100.0
        actual = 1.0 if outcome == actual_outcome else 0.0
        score += (p - actual) ** 2
    return round(score, 6)


def _settlement_id(natural_key: str, actual_outcome: str, home_goals: int, away_goals: int) -> str:
    payload = f"{natural_key}|{actual_outcome}|{home_goals}|{away_goals}|backfill"
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Accuracy güncelleyici
# ---------------------------------------------------------------------------

def _recompute_accuracy() -> dict:
    """Tüm settlement kayıtlarından shadow_accuracy.json'u yeniden hesapla."""
    if not _SETTLEMENTS_FILE.exists():
        return {}

    records = [
        json.loads(l) for l in _SETTLEMENTS_FILE.read_text().splitlines() if l.strip()
    ]
    n = len(records)
    if n == 0:
        return {}

    n_correct      = sum(1 for r in records if r.get("correct"))
    n_draws_pred   = sum(1 for r in records if r.get("predicted_outcome") == "DRAW")
    n_draws_actual = sum(1 for r in records if r.get("actual_outcome") == "DRAW")

    total_ll    = sum(r.get("log_loss_contrib", 0) for r in records)
    total_brier = sum(r.get("brier_contrib", 0) for r in records)
    log_loss    = round(total_ll / n, 5)
    brier       = round(total_brier / n, 5)

    # ECE — 5 bin
    bins = [[] for _ in range(5)]
    for r in records:
        c = float(r.get("confidence", 50))
        idx = min(4, int(c / 20))
        bins[idx].append(r)

    ece = 0.0
    reliability_bins = []
    for i, b in enumerate(bins):
        if not b:
            continue
        mean_conf  = sum(float(r.get("confidence", 50)) for r in b) / len(b)
        accuracy   = sum(1 for r in b if r.get("correct")) / len(b) * 100
        band_lo    = i * 20
        band_hi    = band_lo + 19
        ece       += abs(mean_conf - accuracy) * len(b) / n
        reliability_bins.append({
            "band":      f"{band_lo}-{band_hi}",
            "n":         len(b),
            "mean_conf": round(mean_conf, 2),
            "accuracy":  round(accuracy, 2),
        })

    draw_rate_pred   = round(n_draws_pred / n * 100, 3) if n else 0
    draw_rate_actual = round(n_draws_actual / n * 100, 3) if n else 0
    draw_bias        = round(draw_rate_pred - draw_rate_actual, 3)

    flags = []
    if abs(draw_bias) > 5:
        flags.append(f"DRAW_CALIBRATION_CONFIRMED — bias={draw_bias:+.2f}pp")
    if ece > 0.08:
        flags.append(f"ECE_REVIEW_REQUIRED — ECE={ece:.4f} > 0.08 threshold")

    return {
        "generated_at":         datetime.now(timezone.utc).isoformat(),
        "n_settled":            n,
        "n_correct":            n_correct,
        "overall_accuracy_pct": round(n_correct / n * 100, 2) if n else 0,
        "log_loss":             log_loss,
        "n_draws_predicted":    n_draws_pred,
        "n_draws_actual":       n_draws_actual,
        "draw_rate_predicted":  draw_rate_pred,
        "draw_rate_actual":     draw_rate_actual,
        "draw_rate_bias":       draw_bias,
        "draw_bias_available":  True,
        "brier_score":          brier,
        "brier_available":      True,
        "ece":                  round(ece, 5),
        "ece_available":        True,
        "reliability_bins":     reliability_bins,
        "flags":                flags,
    }


# ---------------------------------------------------------------------------
# Ana backfill akışı
# ---------------------------------------------------------------------------

def run_backfill(dry_run: bool = False) -> int:
    """
    Bekleyen tahminleri API-Football ile settle et.
    Dönüş: settle edilen maç sayısı.
    """
    if not _PREDICTIONS_FILE.exists():
        logger.warning("shadow_predictions.jsonl bulunamadı")
        return 0

    predictions = [
        json.loads(l) for l in _PREDICTIONS_FILE.read_text().splitlines() if l.strip()
    ]

    # Mevcut settlement natural_key seti
    settled_keys: set[str] = set()
    existing_settlements: list[dict] = []
    if _SETTLEMENTS_FILE.exists():
        for line in _SETTLEMENTS_FILE.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                settled_keys.add(r["natural_key"])
                existing_settlements.append(r)

    today_str = date.today().isoformat()

    # Geçmiş tarihli ama settle edilmemiş tahminler
    pending = [
        p for p in predictions
        if p["match_date"] < today_str and p["natural_key"] not in settled_keys
    ]

    if not pending:
        logger.info("Bekleyen tahmin yok")
        return 0

    logger.info("%d bekleyen tahmin bulundu", len(pending))

    # API-Football'dan tarih aralığı için sonuçları çek
    dates      = sorted(set(p["match_date"] for p in pending))
    from_date  = dates[0]
    to_date    = dates[-1]

    if dry_run:
        logger.info("[DRY-RUN] %s → %s arasındaki bekleyen maçlar:", from_date, to_date)
        for p in sorted(pending, key=lambda x: x["match_date"]):
            logger.info("  %s  %s vs %s", p["match_date"], p["home_team"], p["away_team"])
        return len(pending)

    api_results = _fetch_af_fixtures(from_date, to_date)
    if not api_results:
        logger.warning("API'den sonuç alınamadı")
        return 0

    # Eşleştir ve yaz
    new_settlements: list[dict] = []
    matched_count  = 0
    no_match_count = 0

    for pred in sorted(pending, key=lambda x: x["match_date"]):
        p_home = pred["home_team"]
        p_away = pred["away_team"]
        p_date = pred["match_date"]

        # API sonuçları arasında eşleşme ara
        match = None
        for res in api_results:
            if res["date"] != p_date:
                continue
            if _name_match(res["home"], p_home) and _name_match(res["away"], p_away):
                match = res
                break

        if not match:
            logger.warning("Eşleşme YOK: %s  %s vs %s", p_date, p_home, p_away)
            no_match_count += 1
            continue

        hg = match["home_goals"]
        ag = match["away_goals"]
        actual_out = _outcome(hg, ag)
        probs      = pred.get("probabilities", {})
        prob_of_actual = probs.get(
            {"HOME_WIN": "H", "DRAW": "D", "AWAY_WIN": "A"}[actual_out], 0
        )

        settlement = {
            "record_type":       "SETTLEMENT",
            "settlement_id":     _settlement_id(pred["natural_key"], actual_out, hg, ag),
            "prediction_id":     pred["prediction_id"],
            "natural_key":       pred["natural_key"],
            "home_team":         p_home,
            "away_team":         p_away,
            "match_date":        p_date,
            "stage":             pred.get("stage", ""),
            "predicted_outcome": pred["predicted_outcome"],
            "actual_outcome":    actual_out,
            "correct":           pred["predicted_outcome"] == actual_out,
            "actual_score":      {"home": hg, "away": ag},
            "actual_draw":       actual_out == "DRAW",
            "probabilities":     probs,
            "xg":                pred.get("xg", {}),
            "prob_of_actual":    prob_of_actual,
            "log_loss_contrib":  _log_loss(prob_of_actual),
            "brier_contrib":     _brier(probs, actual_out),
            "confidence":        pred.get("confidence", 0),
            "tier":              pred.get("tier", "TIER_C"),
            "signal":            pred.get("signal", ""),
            "elo_gap":           pred.get("elo_gap", 0),
            "settled_at":        datetime.now(timezone.utc).isoformat(),
            "settler_version":   _SETTLER_VER,
            "api_source":        _API_SOURCE,
        }

        new_settlements.append(settlement)
        matched_count += 1
        result_em = "✅" if settlement["correct"] else "❌"
        logger.info(
            "%s  %-20s %d–%d %-20s  tahmin=%s gerçek=%s %s",
            p_date, p_home, hg, ag, p_away,
            pred["predicted_outcome"], actual_out, result_em,
        )

    if not new_settlements:
        logger.warning("Hiçbir maç eşleştirilemedi (matched=0, no_match=%d)", no_match_count)
        return 0

    # Dosyaya ekle
    with _SETTLEMENTS_FILE.open("a") as f:
        for s in new_settlements:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    logger.info("%d yeni settlement yazıldı (%d eşleşemedi)", matched_count, no_match_count)

    # Accuracy güncelle
    accuracy = _recompute_accuracy()
    _ACCURACY_FILE.write_text(json.dumps(accuracy, indent=2, ensure_ascii=False))
    logger.info(
        "Accuracy güncellendi: n=%d, acc=%.1f%%, Brier=%.4f",
        accuracy["n_settled"], accuracy["overall_accuracy_pct"], accuracy["brier_score"]
    )

    return matched_count


def run_status() -> None:
    """Özet rapor yaz."""
    predictions = [
        json.loads(l) for l in _PREDICTIONS_FILE.read_text().splitlines() if l.strip()
    ] if _PREDICTIONS_FILE.exists() else []

    settlements = [
        json.loads(l) for l in _SETTLEMENTS_FILE.read_text().splitlines() if l.strip()
    ] if _SETTLEMENTS_FILE.exists() else []

    settled_keys = {s["natural_key"] for s in settlements}
    today_str    = date.today().isoformat()

    past_preds   = [p for p in predictions if p["match_date"] < today_str]
    pending      = [p for p in past_preds if p["natural_key"] not in settled_keys]
    n_correct    = sum(1 for s in settlements if s.get("correct"))
    n            = len(settlements)

    print(f"\n📊 BACKFILL DURUM RAPORU — {today_str}")
    print(f"   Toplam tahmin: {len(predictions)}")
    print(f"   Geçmiş tarihli: {len(past_preds)}")
    print(f"   Settle edilmiş: {n}")
    print(f"   Bekleyen (settle edilmemiş): {len(pending)}")
    if n:
        print(f"   Doğruluk: {n_correct}/{n} = %{n_correct/n*100:.1f}")
    if pending:
        print(f"\n   Bekleyen maçlar:")
        for p in sorted(pending, key=lambda x: x["match_date"]):
            print(f"     {p['match_date']}  {p['home_team']} vs {p['away_team']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="API-Football backfill settlement")
    ap.add_argument("--settle",   action="store_true", help="Bekleyen maçları settle et")
    ap.add_argument("--dry-run",  action="store_true", help="Neyi settle edeceğini göster")
    ap.add_argument("--status",   action="store_true", help="Durum raporu")
    args = ap.parse_args()

    if args.status:
        run_status()
    elif args.dry_run:
        run_backfill(dry_run=True)
    elif args.settle:
        n = run_backfill(dry_run=False)
        print(f"\n✅ {n} maç settle edildi.")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
