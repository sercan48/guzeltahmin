"""Daily predictions, free channel, self-learning jobs, and channel posting."""

import logging
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import (
    TELEGRAM_CHANNEL_ID, TELEGRAM_FREE_CHANNEL_ID,
    LIVE_BETTING_ENABLED, SELF_LEARNING_ENABLED,
    TELEGRAM_VIP_JOIN_LINK,
)
from config.leagues import ACTIVE_LEAGUES
from app.bot.admin import premium_only
from app.bot.formatters import (
    format_prediction, format_daily_summary, format_accuracy_report,
    confidence_emoji,
)
from src.db.base import get_backend

logger = logging.getLogger(__name__)


# ─────────────────────────────────────
# SEND DEDUP (idempotency guard)
# ─────────────────────────────────────
# Prevents a scheduled broadcast from being sent more than once for the same
# UTC day — protects against duplicate job registration, bot restarts inside
# the scheduled minute, or a second bot instance briefly overlapping.

import os as _os

_SENT_MARKER_DIR = _os.path.join("data", "cache", "sent_markers")


def _already_sent_today(key: str) -> bool:
    """Return True if a broadcast with `key` was already sent today (UTC)."""
    day = datetime.utcnow().strftime("%Y-%m-%d")
    marker = _os.path.join(_SENT_MARKER_DIR, f"{key}_{day}.marker")
    return _os.path.exists(marker)


def _mark_sent_today(key: str) -> None:
    """Record that a broadcast with `key` was sent today (UTC)."""
    day = datetime.utcnow().strftime("%Y-%m-%d")
    _os.makedirs(_SENT_MARKER_DIR, exist_ok=True)
    marker = _os.path.join(_SENT_MARKER_DIR, f"{key}_{day}.marker")
    try:
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(datetime.utcnow().isoformat())
    except OSError as e:
        logger.warning(f"Could not write sent marker {marker}: {e}")



# ─────────────────────────────────────
# USER COMMANDS
# ─────────────────────────────────────

@premium_only
async def predict_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User command: /tahmin [league_code] — Premium only."""
    db = get_backend()
    db.connect()
    try:
        args = context.args
        league_filter = args[0].upper() if args else None
        if league_filter and league_filter not in ACTIVE_LEAGUES:
            leagues_text = ", ".join(f"`{c}`" for c in ACTIVE_LEAGUES)
            await update.message.reply_text(
                f"Gecersiz lig kodu.\nAktif ligler: {leagues_text}",
                parse_mode="Markdown",
            )
            return

        where = "AND m.league_code = ?" if league_filter else ""
        params = (league_filter,) if league_filter else ()

        predictions = db.fetchall(f"""
            SELECT p.*, m.date, m.league_code,
                   t1.name as home_team, t2.name as away_team
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            JOIN teams t1 ON m.home_team_id = t1.id
            JOIN teams t2 ON m.away_team_id = t2.id
            WHERE DATE(m.date) >= DATE('now') AND DATE(m.date) <= DATE('now', '+1 day')
            AND m.ft_result IS NULL
            {where}
            ORDER BY p.confidence_score DESC
        """, params)

        if not predictions:
            await update.message.reply_text("Bugun/yarin icin tahmin bulunamadi.")
            return

        header = "Gunun Tahminleri"
        if league_filter:
            league = ACTIVE_LEAGUES[league_filter]
            header += f" - {league.name}"

        messages = [header + "\n"]
        for pred in predictions[:10]:
            pred_dict = {
                "home_team": pred["home_team"],
                "away_team": pred["away_team"],
                "predicted_result": pred.get("predicted_result", "?"),
                "confidence": pred.get("confidence_score", 0) or 0,
                "h_prob": pred.get("home_win_prob", 0) or 0,
                "d_prob": pred.get("draw_prob", 0) or 0,
                "a_prob": pred.get("away_win_prob", 0) or 0,
            }
            messages.append(format_prediction(pred_dict))
            messages.append("")

        # Premium feature teaser
        if not LIVE_BETTING_ENABLED:
            messages.append("---")
            messages.append("CANLI BAHIS REHBERI yakinda Premium'da!")
            messages.append("Her mac icin kosullu canli oneriler + stop-loss")

        text = "\n".join(messages)
        if len(text) > 4000:
            text = text[:4000] + "\n\n... daha fazlasi icin lig filtresi kullanin."

        await update.message.reply_text(text)
    finally:
        db.close()


async def accuracy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User command: /basari — accuracy report (everyone can see)."""
    db = get_backend()
    db.connect()
    try:
        now = datetime.now()
        d7 = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        d30 = (now - timedelta(days=30)).strftime("%Y-%m-%d")

        def _safe_acc(row):
            if not row or not row["total"]:
                return 0, 0
            return round(row["correct"] / row["total"] * 100, 1), row["total"]

        # Check if we have posted matches in DB to compute stats on
        has_posted = db.fetchone("SELECT COUNT(*) as c FROM predictions WHERE was_posted = 1 AND actual_result IS NOT NULL")["c"] > 0
        posted_cond = "AND was_posted = 1" if has_posted else ""
        posted_p_cond = "AND p.was_posted = 1" if has_posted else ""

        overall = db.fetchone(f"""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN predicted_result = actual_result THEN 1 ELSE 0 END) as correct
            FROM predictions WHERE actual_result IS NOT NULL {posted_cond}
        """)
        last7 = db.fetchone(f"""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN p.predicted_result = p.actual_result THEN 1 ELSE 0 END) as correct
            FROM predictions p JOIN matches m ON p.match_id = m.id
            WHERE p.actual_result IS NOT NULL {posted_p_cond} AND m.date >= ?
        """, (d7,))
        last30 = db.fetchone(f"""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN p.predicted_result = p.actual_result THEN 1 ELSE 0 END) as correct
            FROM predictions p JOIN matches m ON p.match_id = m.id
            WHERE p.actual_result IS NOT NULL {posted_p_cond} AND m.date >= ?
        """, (d30,))

        o_acc, o_total = _safe_acc(overall)
        s7_acc, s7_total = _safe_acc(last7)
        s30_acc, s30_total = _safe_acc(last30)

        per_league = {}
        league_rows = db.fetchall(f"""
            SELECT m.league_code, COUNT(*) as total,
                   SUM(CASE WHEN p.predicted_result = p.actual_result THEN 1 ELSE 0 END) as correct
            FROM predictions p JOIN matches m ON p.match_id = m.id
            WHERE p.actual_result IS NOT NULL {posted_p_cond} GROUP BY m.league_code
        """)
        if league_rows:
            for r in league_rows:
                if r["total"] > 0:
                    per_league[r["league_code"]] = round(r["correct"] / r["total"] * 100, 1)

        stats = {
            "overall": o_acc, "total_predictions": o_total,
            "last_7_days": s7_acc, "last_7_total": s7_total,
            "last_30_days": s30_acc, "last_30_total": s30_total,
            "per_league": per_league,
        }
        await update.message.reply_text(format_accuracy_report(stats))
    finally:
        db.close()


def _get_promo_footer(db) -> str:
    """Generate dynamic promotional footer based on recent stats."""
    # Calculate yesterday's stats
    yesterday_stats = db.fetchone("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN predicted_result = actual_result THEN 1 ELSE 0 END) as correct
        FROM predictions
        WHERE DATE(created_at) = DATE('now', '-1 day')
          AND actual_result IS NOT NULL
    """)
    
    total = yesterday_stats["total"] if yesterday_stats else 0
    correct = yesterday_stats["correct"] if yesterday_stats else 0
    
    if total > 0:
        stats_hook = f"Dün {correct}/{total} tahmin tutturduk! 🎯"
    else:
        # Fallback to last 7 days stats
        last_7_days = db.fetchone("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN predicted_result = actual_result THEN 1 ELSE 0 END) as correct
            FROM predictions
            WHERE created_at >= datetime('now', '-7 days')
              AND actual_result IS NOT NULL
        """)
        t7 = last_7_days["total"] if last_7_days else 0
        c7 = last_7_days["correct"] if last_7_days else 0
        if t7 > 0:
            stats_hook = f"Son 7 günde %{c7/t7*100:.0f} başarı oranı yakaladık! 🚀"
        else:
            stats_hook = "Ensemble AI modellerimizle yüksek başarı oranı! 📈"
            
    import random
    if random.random() < 0.5:
        vip_cta = f"\n🔗 Katılım: [Güzel Tahmin VIP Kanalı]({TELEGRAM_VIP_JOIN_LINK})"
    else:
        vip_cta = ""
        
    footer = (
        f"📢 *{stats_hook}*\n"
        f"Günün tüm analizlerine, özel hazır kuponlara ve canlı rehbere erişmek için hemen VIP kanalımıza katılın! 👇{vip_cta}"
    )
    return footer


def _calculate_yesterday_performance(db) -> dict:
    """Calculate accuracy and flat-staking ROI for yesterday's matches."""
    rows = db.fetchall("""
        SELECT p.predicted_result, p.actual_result, 
               o.home_odds, o.draw_odds, o.away_odds
        FROM predictions p
        JOIN matches m ON p.match_id = m.id
        LEFT JOIN odds o ON p.match_id = o.match_id
        WHERE p.actual_result IS NOT NULL
          AND p.predicted_result IN ('H', 'D', 'A')
          AND DATE(m.date) = DATE('now', '-1 day')
    """)
    total = len(rows)
    correct = sum(1 for r in rows if r["predicted_result"] == r["actual_result"])
    accuracy = (correct / total * 100) if total > 0 else 0.0
    
    staked = 0.0
    profit = 0.0
    for r in rows:
        pr = r["predicted_result"]
        ar = r["actual_result"]
        ho = r["home_odds"]
        do = r["draw_odds"]
        ao = r["away_odds"]
        odds = ho if pr == "H" else (do if pr == "D" else ao)
        if not odds or odds <= 1.0:
            odds = 1.80
        staked += 1.0
        if pr == ar:
            profit += (odds - 1.0)
        else:
            profit -= 1.0
            
    roi = (profit / staked * 100) if staked > 0 else 0.0
    return {
        "total": total,
        "correct": correct,
        "accuracy": accuracy,
        "staked": staked,
        "profit": profit,
        "roi": roi
    }


def _calculate_performance_for_period(db, days: int) -> dict:
    """Calculate accuracy and flat-staking ROI for predictions in the last N days."""
    rows = db.fetchall("""
        SELECT p.predicted_result, p.actual_result, 
               o.home_odds, o.draw_odds, o.away_odds
        FROM predictions p
        JOIN matches m ON p.match_id = m.id
        LEFT JOIN odds o ON p.match_id = o.match_id
        WHERE p.actual_result IS NOT NULL
          AND p.predicted_result IN ('H', 'D', 'A')
          AND m.date >= DATE('now', ?) AND m.date <= DATE('now')
    """, (f"-{days} days",))
    
    total = len(rows)
    correct = sum(1 for r in rows if r["predicted_result"] == r["actual_result"])
    accuracy = (correct / total * 100) if total > 0 else 0.0
    
    staked = 0.0
    profit = 0.0
    for r in rows:
        pr = r["predicted_result"]
        ar = r["actual_result"]
        ho = r["home_odds"]
        do = r["draw_odds"]
        ao = r["away_odds"]
        odds = ho if pr == "H" else (do if pr == "D" else ao)
        if not odds or odds <= 1.0:
            odds = 1.80
        staked += 1.0
        if pr == ar:
            profit += (odds - 1.0)
        else:
            profit -= 1.0
            
    roi = (profit / staked * 100) if staked > 0 else 0.0
    return {
        "total": total,
        "correct": correct,
        "accuracy": accuracy,
        "staked": staked,
        "profit": profit,
        "roi": roi
    }


async def free_daily_pick_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled: Post 1 or 2 highest confidence predictions to general channel as teaser."""
    if not TELEGRAM_FREE_CHANNEL_ID:
        return

    db = get_backend()
    db.connect()
    try:
        # Find top 2 highest confidence predictions for today
        predictions = db.fetchall("""
            SELECT p.*, m.date, m.league_code,
                   m.home_team_id, m.away_team_id,
                   t1.name as home_team, t2.name as away_team
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            JOIN teams t1 ON m.home_team_id = t1.id
            JOIN teams t2 ON m.away_team_id = t2.id
            WHERE DATE(m.date) = DATE('now') AND m.ft_result IS NULL
            AND p.confidence_score >= 60
            ORDER BY p.confidence_score DESC
            LIMIT 2
        """)

        if not predictions:
            return

        from app.bot.formatters import format_match_analysis_card
        from src.agents.data_agent import get_team_status_from_db

        promo_footer = _get_promo_footer(db)

        for p in predictions:
            # Fetch live odds for this match
            odds_row = db.fetchone("""
                SELECT o.home_odds, o.draw_odds, o.away_odds,
                       o.over25_odds, o.under25_odds
                FROM odds o WHERE o.match_id = ?
                ORDER BY o.id DESC LIMIT 1
            """, (p["match_id"],))

            market_odds = {}
            if odds_row:
                market_odds = {
                    "h": odds_row.get("home_odds"),
                    "d": odds_row.get("draw_odds"),
                    "a": odds_row.get("away_odds"),
                    "o25": odds_row.get("over25_odds"),
                    "u25": odds_row.get("under25_odds"),
                }

            home_status = get_team_status_from_db(db, p["home_team_id"])
            away_status = get_team_status_from_db(db, p["away_team_id"])

            pred_dict = {
                "home_team": p["home_team"],
                "away_team": p["away_team"],
                "league_code": p.get("league_code", ""),
                "predicted_result": p.get("predicted_result", "?"),
                "confidence": p.get("confidence_score", 0) or 0,
                "home_win_prob": p.get("home_win_prob", 0) or 0,
                "draw_prob": p.get("draw_prob", 0) or 0,
                "away_win_prob": p.get("away_win_prob", 0) or 0,
                "over25_prob": p.get("over25_prob"),
                "btts_prob": p.get("btts_prob"),
                "model_agreement": p.get("model_agreement"),
                "value_margin": p.get("value_margin", 0),
                "home_lambda": p.get("home_lambda"),
                "away_lambda": p.get("away_lambda"),
                "model_type": p.get("model_type", "Ensemble"),
                "_odds": market_odds if market_odds else None,
                "home_status": home_status,
                "away_status": away_status,
            }

            card_text = format_match_analysis_card(pred_dict, is_free=True, promo_footer=promo_footer)
            await context.bot.send_message(chat_id=TELEGRAM_FREE_CHANNEL_ID, text=card_text, parse_mode="Markdown")
            
            # Mark as posted
            db.execute("UPDATE predictions SET was_posted = 1 WHERE id = ?", (p["id"],))

        logger.info(f"Free teaser predictions posted: {len(predictions)} matches")
    except Exception as e:
        logger.error(f"Failed to post free teaser predictions: {e}")
    finally:
        db.close()


async def daily_predictions_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled: Post odds-centric predictions to premium channel individually."""
    if not TELEGRAM_CHANNEL_ID:
        return

    db = get_backend()
    db.connect()
    try:
        predictions = db.fetchall("""
            SELECT p.*, m.date, m.league_code,
                   m.home_team_id, m.away_team_id,
                   t1.name as home_team, t2.name as away_team
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            JOIN teams t1 ON m.home_team_id = t1.id
            JOIN teams t2 ON m.away_team_id = t2.id
            WHERE DATE(m.date) = DATE('now') AND m.ft_result IS NULL
            AND p.confidence_score >= 55
            ORDER BY p.confidence_score DESC
        """)

        if not predictions:
            return

        from app.bot.formatters import format_match_analysis_card
        from src.agents.data_agent import get_team_status_from_db

        for p in predictions:
            # Fetch live odds for this match
            odds_row = db.fetchone("""
                SELECT o.home_odds, o.draw_odds, o.away_odds,
                       o.over25_odds, o.under25_odds
                FROM odds o WHERE o.match_id = ?
                ORDER BY o.id DESC LIMIT 1
            """, (p["match_id"],))

            market_odds = {}
            if odds_row:
                market_odds = {
                    "h": odds_row.get("home_odds"),
                    "d": odds_row.get("draw_odds"),
                    "a": odds_row.get("away_odds"),
                    "o25": odds_row.get("over25_odds"),
                    "u25": odds_row.get("under25_odds"),
                }

            home_status = get_team_status_from_db(db, p["home_team_id"])
            away_status = get_team_status_from_db(db, p["away_team_id"])

            pred_dict = {
                "home_team": p["home_team"],
                "away_team": p["away_team"],
                "league_code": p.get("league_code", ""),
                "predicted_result": p.get("predicted_result", "?"),
                "confidence": p.get("confidence_score", 0) or 0,
                "home_win_prob": p.get("home_win_prob", 0) or 0,
                "draw_prob": p.get("draw_prob", 0) or 0,
                "away_win_prob": p.get("away_win_prob", 0) or 0,
                "over25_prob": p.get("over25_prob"),
                "btts_prob": p.get("btts_prob"),
                "model_agreement": p.get("model_agreement"),
                "value_margin": p.get("value_margin", 0),
                "home_lambda": p.get("home_lambda"),
                "away_lambda": p.get("away_lambda"),
                "model_type": p.get("model_type", "Ensemble"),
                "_odds": market_odds if market_odds else None,
                "home_status": home_status,
                "away_status": away_status,
            }

            card_text = format_match_analysis_card(pred_dict, is_free=False)
            await context.bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=card_text, parse_mode="Markdown")
            
            # Mark as posted
            db.execute("UPDATE predictions SET was_posted = 1 WHERE id = ?", (p["id"],))

        logger.info(f"Premium predictions posted: {len(predictions)} matches")
    except Exception as e:
        logger.error(f"Failed to post premium predictions: {e}")
    finally:
        db.close()


async def social_proof_report_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled: Calculate daily/weekly ROI and broadcast report to general channel."""
    if not TELEGRAM_FREE_CHANNEL_ID:
        return

    # Idempotency guard: send at most once per UTC day even if the job fires
    # twice (duplicate registration, restart within the scheduled minute, or a
    # second overlapping instance).
    if _already_sent_today("social_proof_report"):
        logger.info("Social proof report already sent today — skipping duplicate.")
        return

    db = get_backend()
    db.connect()
    try:
        daily = _calculate_yesterday_performance(db)
        weekly = _calculate_performance_for_period(db, 7)

        from app.bot.formatters import format_performance_report
        report_text = format_performance_report(daily, weekly)

        await context.bot.send_message(
            chat_id=TELEGRAM_FREE_CHANNEL_ID,
            text=report_text,
            parse_mode="Markdown"
        )
        _mark_sent_today("social_proof_report")
        logger.info("Social proof report posted to free channel.")
    except Exception as e:
        logger.error(f"Failed to post social proof report: {e}")
    finally:
        db.close()


from app.bot.admin import admin_only

@admin_only
async def send_report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command: /send_report — broadcast social proof report to general channel."""
    db = get_backend()
    db.connect()
    try:
        daily = _calculate_yesterday_performance(db)
        weekly = _calculate_performance_for_period(db, 7)
        
        from app.bot.formatters import format_performance_report
        report_text = format_performance_report(daily, weekly)
        
        if TELEGRAM_FREE_CHANNEL_ID:
            await context.bot.send_message(
                chat_id=TELEGRAM_FREE_CHANNEL_ID,
                text=report_text,
                parse_mode="Markdown"
            )
            await update.message.reply_text("✅ Rapor başarıyla ücretsiz kanalda yayınlandı!")
        else:
            await update.message.reply_text("❌ Hata: TELEGRAM_FREE_CHANNEL_ID tanımlı değil.")
    except Exception as e:
        logger.error(f"Manual send_report failed: {e}")
        await update.message.reply_text(f"❌ Rapor gönderilirken hata oluştu: {e}")
    finally:
        db.close()


def _split_message(text: str, max_len: int) -> list[str]:
    """Split a long message at line boundaries."""
    lines = text.split("\n")
    chunks, current = [], []
    length = 0
    for line in lines:
        if length + len(line) + 1 > max_len and current:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


async def post_results_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled: Post yesterday's results to both channels."""
    db = get_backend()
    db.connect()
    try:
        results = db.fetchall("""
            SELECT p.predicted_result, p.actual_result, p.confidence_score,
                   t1.name as home_team, t2.name as away_team
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            JOIN teams t1 ON m.home_team_id = t1.id
            JOIN teams t2 ON m.away_team_id = t2.id
            WHERE DATE(m.date) = DATE('now', '-1 day')
            AND p.actual_result IS NOT NULL
            ORDER BY p.confidence_score DESC
        """)

        if not results:
            return

        correct = sum(1 for r in results if r["predicted_result"] == r["actual_result"])
        total = len(results)
        acc = round(correct / total * 100, 1)

        lines = [
            f"DUNUN SONUCLARI",
            "=" * 28,
            f"Dogruluk: %{acc} ({correct}/{total})\n",
        ]

        for r in results:
            icon = "[OK]" if r["predicted_result"] == r["actual_result"] else "[X]"
            lines.append(
                f"{icon} {r['home_team']} vs {r['away_team']}: "
                f"{r['predicted_result']}->{r['actual_result']}"
            )

        text = "\n".join(lines)

        # Post to premium channel
        if TELEGRAM_CHANNEL_ID:
            try:
                await context.bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=text)
            except Exception as e:
                logger.error(f"Failed to post results to premium: {e}")

        # Post to free channel (builds credibility)
        if TELEGRAM_FREE_CHANNEL_ID:
            free_lines = lines[:4]  # Header + accuracy only
            free_lines.append(f"\nDetaylar Premium kanalda!")
            try:
                await context.bot.send_message(
                    chat_id=TELEGRAM_FREE_CHANNEL_ID,
                    text="\n".join(free_lines),
                )
            except Exception as e:
                logger.error(f"Failed to post results to free: {e}")

    except Exception as e:
        logger.error(f"Failed to post results: {e}")
    finally:
        db.close()


# ─────────────────────────────────────
# SELF-LEARNING: OTOMATIK IYILESTIRME
# ─────────────────────────────────────

async def self_learning_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled: Run ML self-learning cycle after results come in."""
    if not SELF_LEARNING_ENABLED:
        return

    try:
        from src.model.ml_feedback_loop import run_learning_cycle, format_learning_report

        report = run_learning_cycle()
        if "error" in report:
            logger.error(f"Self-learning error: {report['error']}")
            return

        # Notify admins
        from config.settings import TELEGRAM_ADMIN_IDS
        if TELEGRAM_ADMIN_IDS:
            text = format_learning_report(report)
            for admin_id in TELEGRAM_ADMIN_IDS:
                try:
                    await context.bot.send_message(chat_id=admin_id, text=text)
                except Exception:
                    pass

        # If retrain needed, alert admins
        if report.get("retrain_needed"):
            for admin_id in TELEGRAM_ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text="[!] RETRAIN SINYALI!\n"
                             "Model performansi dusmus.\n"
                             "Komut: python scripts/train_ensemble.py",
                    )
                except Exception:
                    pass

        logger.info(f"Self-learning cycle complete: {report.get('overall', {}).get('accuracy', 0):.1%}")

    except Exception as e:
        logger.error(f"Self-learning job error: {e}")


# ─────────────────────────────────────
# SCHEDULE ALL JOBS
# ─────────────────────────────────────

async def daily_db_sync_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled: Sync active season matches and results."""
    logger.info("Starting daily DB sync job...")
    try:
        # 1. Download active season CSVs
        from scripts.download_data import download_all
        download_all(season_filter="2526")
        
        # 2. Ingest into database
        from src.db.base import get_backend
        from src.ingestion.csv_loader import load_season
        from src.preprocessing.schema_mapper import ingest_matches_to_db
        from config.leagues import LEAGUES
        
        db = get_backend()
        db.connect()
        try:
            total_inserted = 0
            for league_code in LEAGUES:
                df = load_season("2526", league_code)
                if df is not None and len(df) > 0:
                    count = ingest_matches_to_db(df, db)
                    total_inserted += count
            logger.info(f"Daily DB sync complete. Ingested/updated {total_inserted} matches.")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed daily DB sync job: {e}")


async def daily_pipeline_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled: Run prediction pipeline for today's matches."""
    logger.info("Starting daily prediction pipeline job...")
    try:
        from scripts.run_production_pipeline import run_production
        run_production()
        logger.info("Daily prediction pipeline job completed.")
    except Exception as e:
        logger.error(f"Failed daily prediction pipeline job: {e}")


async def schedule_predictions(app):
    """Set up all daily scheduled jobs (Turkey time — UTC+3)."""
    job_queue = app.job_queue
    if job_queue is None:
        logger.warning("Job queue not available")
        return

    from datetime import time as dt_time, timezone, timedelta
    TZ_TR = timezone(timedelta(hours=3))

    # Idempotent scheduling: remove any jobs already registered under these
    # names so a second call to schedule_predictions() (e.g. re-init) cannot
    # leave duplicate daily jobs that would each broadcast.
    _JOB_NAMES = [
        "daily_db_sync", "daily_pipeline", "daily_results", "daily_news_morning",
        "premium_predictions", "social_proof_report", "free_daily_pick",
        "daily_news_evening", "wc_preliminary", "world_cup_scheduler",
        "wc_night_slip", "self_learning",
    ]
    removed = 0
    for _name in _JOB_NAMES:
        for _job in job_queue.get_jobs_by_name(_name):
            _job.schedule_removal()
            removed += 1
    if removed:
        logger.warning(f"Cleared {removed} pre-existing scheduled job(s) before re-scheduling.")

    # 08:00 TR — Sync active season data (results/upcoming matches)
    job_queue.run_daily(daily_db_sync_job, time=dt_time(8, 0, tzinfo=TZ_TR), name="daily_db_sync")

    # 08:30 TR — Run ML predictions & Omni-market pipeline
    job_queue.run_daily(daily_pipeline_job, time=dt_time(8, 30, tzinfo=TZ_TR), name="daily_pipeline")

    # 09:00 TR — Yesterday's results (both channels)
    job_queue.run_daily(post_results_job, time=dt_time(9, 0, tzinfo=TZ_TR), name="daily_results")

    # 09:30 TR — Free channel: morning news
    job_queue.run_daily(daily_news_job, time=dt_time(9, 30, tzinfo=TZ_TR), name="daily_news_morning")

    # 10:00 TR — Premium channel: all predictions (odds-centric)
    job_queue.run_daily(daily_predictions_job, time=dt_time(10, 0, tzinfo=TZ_TR), name="premium_predictions")

    # 10:00 TR — Free channel: Performance/ROI social proof report
    job_queue.run_daily(social_proof_report_job, time=dt_time(10, 0, tzinfo=TZ_TR), name="social_proof_report")

    # 10:30 TR — Free channel: single best pick
    job_queue.run_daily(free_daily_pick_job, time=dt_time(10, 30, tzinfo=TZ_TR), name="free_daily_pick")

    # 18:00 TR — Free channel: evening news
    job_queue.run_daily(daily_news_job, time=dt_time(18, 0, tzinfo=TZ_TR), name="daily_news_evening")

    # 18:30 TR — World Cup Phase 1: Preliminary Bulletin
    job_queue.run_daily(wc_preliminary_job, time=dt_time(18, 30, tzinfo=TZ_TR), name="wc_preliminary")

    # 22:00 TR — World Cup Scheduler (T-45 lineup scheduling)
    job_queue.run_daily(schedule_world_cup_lineup_fetches, time=dt_time(22, 0, tzinfo=TZ_TR), name="world_cup_scheduler")

    # 23:00 TR — World Cup Phase 2: Night Slip
    job_queue.run_daily(wc_night_slip_job, time=dt_time(23, 0, tzinfo=TZ_TR), name="wc_night_slip")

    # 23:30 TR — Self-learning cycle (after all matches + WC night slip)
    job_queue.run_daily(self_learning_job, time=dt_time(23, 30, tzinfo=TZ_TR), name="self_learning")

    logger.info(
        "Scheduled jobs (TR time): "
        "08:00 DB sync, 08:30 pipeline, 09:00 results, 09:30 news morning, "
        "10:00 premium, 10:30 free pick, 18:00 news evening, 18:30 WC prelim, "
        "22:00 WC scheduler, 23:00 WC night, 23:30 self-learning"
    )
async def daily_news_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs periodically to post daily football news to the free channel, funneling users to VIP."""
    try:
        from src.features.news_fetcher import generate_news_bulletin
        from config.settings import TELEGRAM_ADMIN_IDS
        
        bulletin = generate_news_bulletin()
        if bulletin:
            # Post to FREE channel
            await context.bot.send_message(chat_id=TELEGRAM_FREE_CHANNEL_ID, text=bulletin, parse_mode="HTML")
            logger.info("News bulletin sent to free channel.")
        else:
            logger.warning("News bulletin generation returned None. Sending alert to admins.")
            if TELEGRAM_ADMIN_IDS:
                alert_text = (
                    "⚠️ <b>[Güzel Tahmin Haber Bülteni Hatası]</b>\n\n"
                    "Haber bülteni oluşturulurken Gemini API'si 3 denemede de hata verdi veya boş döndü. "
                    "Bülten ücretsiz kanalda paylaşılamadı.\n"
                    "Lütfen API durumunu veya bağlantıyı kontrol edin."
                )
                for admin_id in TELEGRAM_ADMIN_IDS:
                    try:
                        await context.bot.send_message(chat_id=admin_id, text=alert_text, parse_mode="HTML")
                        logger.info(f"Sent news error alert to admin: {admin_id}")
                    except Exception as admin_err:
                        logger.error(f"Failed to send alert to admin {admin_id}: {admin_err}")
    except Exception as e:
        logger.error(f"Error sending news bulletin: {e}")

async def wc_preliminary_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs at 18:30 to send preliminary predictions for tomorrow's early AM matches."""
    await _execute_tier_job(context, "preliminary", "🏆 <b>DÜNYA KUPASI 18:30 BÜLTENİ</b> (Muhtemel Kadrolar)")

async def wc_night_slip_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs at 23:00 to send night slip predictions with market trends."""
    await _execute_tier_job(context, "night_slip", "🌙 <b>DÜNYA KUPASI GECE KUPONU</b> (Piyasa Trendleri Eklendi)")

async def _execute_tier_job(context: ContextTypes.DEFAULT_TYPE, phase: str, title: str):
    from datetime import datetime, timedelta
    from src.db.base import get_backend
    from src.model.wc_three_tier_inference import run_tier_inference
    
    db = get_backend()
    db.connect()
    try:
        # Matches happening late tonight are technically 'tomorrow'
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        matches = db.fetchall("SELECT id, home_team_id, away_team_id, time FROM matches WHERE DATE(date) = ? AND ft_result IS NULL", (tomorrow,))
        
        for m in matches:
            if not m.get("time"): continue
            
            try:
                # Run inference for the phase
                res = run_tier_inference(m["id"], phase)
                
                # Format output
                msg = (
                    f"{title}\n\n"
                    f"Maç ID: {m.get('home_team_id', 1001)} vs {m.get('away_team_id', 1002)} - Saat: {m['time']}\n"
                    f"🛡️ <b>Statü:</b> {res.get('market_note', 'Piyasa Bekleniyor')}\n"
                    f"⚽ <b>Güven Oranı:</b> %{res.get('confidence_score', 80)}\n"
                    f"1️⃣ Ev Sahibi: %{res.get('home_win_prob', 0)} | ❌ Beraberlik: %{res.get('draw_prob', 0)} | 2️⃣ Deplasman: %{res.get('away_win_prob', 0)}\n\n"
                    f"<i>(Not: Kesin tahmin maçtan 45 dk önce resmi kadrolarla paylaşılacaktır.)</i>"
                )
                
                await context.bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=msg, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Error processing {phase} for match {m['id']}: {e}")
                
    except Exception as e:
        logger.error(f"Error in {phase} job: {e}")
    finally:
        db.close()
async def schedule_world_cup_lineup_fetches(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every night at 22:00.
    Finds TOMORROW'S World Cup matches and schedules a fetch lineup job exactly 45 mins before kickoff.
    This handles matches that might start early in the morning (Turkey time).
    """
    from datetime import datetime, timedelta
    from src.db.base import get_backend
    
    db = get_backend()
    db.connect()
    try:
        # We look for all scheduled matches TOMORROW.
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        matches = db.fetchall("SELECT id, api_match_id, date, time FROM matches WHERE DATE(date) = ? AND ft_result IS NULL", (tomorrow,))
        
        count = 0
        for m in matches:
            if not m.get("time"):
                continue
                
            # Stage 1: Send Early Fixture Notification
            home_id = m.get("home_team_id", 1001)
            away_id = m.get("away_team_id", 1002)
            
            early_msg = (
                f"🏆 <b>DÜNYA KUPASI GÜNÜN FİKSTÜRÜ</b> 🏆\n\n"
                f"Maç: ID {home_id} vs ID {away_id}\n"
                f"Saat: {m['time']}\n\n"
                f"<i>📝 Yapay zeka destekli analizlerimiz ve kesin tahminlerimiz, "
                f"takımların ilk 11'leri açıklandığında (maça yaklaşık 45 dakika kala) sizlerle paylaşılacaktır. Bizi takipte kalın!</i>"
            )
            
            # Send early fixture notification
            async def _send_early_notification(ctx: ContextTypes.DEFAULT_TYPE):
                await ctx.bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=ctx.job.data, parse_mode="HTML")

            context.job_queue.run_once(
                _send_early_notification,
                when=1,
                data=early_msg,
            )
            
            kickoff_str = f"{m['date']} {m['time']}"
            kickoff_time = datetime.strptime(kickoff_str, "%Y-%m-%d %H:%M:%S")
            fetch_time = kickoff_time - timedelta(minutes=45)
            
            # Stage 2: Schedule T-45 Lineup & Monte Carlo Prediction
            if fetch_time > datetime.now():
                context.job_queue.run_once(
                    execute_wc_lineup_fetch, 
                    when=fetch_time, 
                    data={"match_id": m["id"], "api_match_id": m["api_match_id"]},
                    name=f"wc_lineup_{m['id']}"
                )
                count += 1
                
        logger.info(f"Scheduled World Cup lineup fetch for {count} matches today.")
    except Exception as e:
        logger.error(f"Error scheduling WC lineups: {e}")
    finally:
        db.close()

async def execute_wc_lineup_fetch(context: ContextTypes.DEFAULT_TYPE):
    """Triggered dynamically 45 mins before kickoff."""
    job_data = context.job.data
    match_id = job_data["match_id"]
    api_match_id = job_data["api_match_id"]
    
    logger.info(f"Executing T-45 lineup fetch for Match {match_id}")
    
    # 1. Fetch Lineups
    from src.features.wc_lineup_fetcher import fetch_and_save_lineups
    success = fetch_and_save_lineups(match_id, api_match_id)
    
    if success:
        logger.info(f"Successfully saved lineups for match {match_id}. Triggering Prediction Engine...")
        
        # 2. Trigger World Cup Engine
        from src.features.world_cup_engine import match_probability
        from src.features.wc_market_delta import calculate_market_delta
        from src.features.wc_confidence_calibrator import calibrate_confidence
        from src.db.base import get_backend
        
        db = get_backend()
        db.connect()
        try:
            # Monte Carlo Probability calculation
            probs = match_probability(db.cursor, match_id, 1001, 1002, 5)
            
            # Determine the model's raw prediction pick
            raw_prediction = "DRAW"
            if probs["home_win_prob"] > 45:
                raw_prediction = "HOME_WIN"
            elif probs["away_win_prob"] > 45:
                raw_prediction = "AWAY_WIN"
                
            # Sharp Money Adjustment
            market_delta = calculate_market_delta(db.cursor, match_id)
            calibrated = calibrate_confidence(raw_prediction, probs.get('confidence_score', 80), market_delta)
            
            # Update the confidence score visually
            final_confidence = calibrated["final_confidence"]
            market_note = calibrated["market_note"]
            
            # Formatting tags based on Calibration outcome
            tag = "✅ GÜVENİLİR"
            if calibrated["is_no_bet"]:
                tag = f"⛔ OYNANMAZ ({market_note})"
            elif market_note:
                tag = f"{market_note}"
            
            final_msg = (
                f"🚨 <b>DÜNYA KUPASI ANALİZİ GELDİ!</b> 🚨\n\n"
                f"Yapay zeka modelimiz, güncel saha dizilişlerini, takım formlarını ve dış faktörleri analiz ederek maç sonucunu öngörmüştür:\n\n"
                f"🛡️ <b>Statü:</b> {tag}\n"
                f"⚽ <b>Nihai Güven Oranı:</b> %{final_confidence}\n\n"
                f"1️⃣ Ev Sahibi Kazanır: %{probs.get('home_win_prob', 0)}\n"
                f"❌ Beraberlik: %{probs.get('draw_prob', 0)}\n"
                f"2️⃣ Deplasman Kazanır: %{probs.get('away_win_prob', 0)}\n\n"
                f"📊 <i>Tahmini Skor (Beklenen Gol): Ev({probs.get('expected_goals_a', 0)}) - Dep({probs.get('expected_goals_b', 0)})</i>"
            )
            
            # Post to Telegram
            await context.bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=final_msg,
                parse_mode="HTML",
            )
            
        except Exception as e:
            logger.error(f"Error generating Monte Carlo prediction: {e}")
        finally:
            db.close()
    else:
        logger.warning(f"Failed to fetch lineups for match {match_id} at T-45")
