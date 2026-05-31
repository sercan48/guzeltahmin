"""Güzel Tahmin — Premium Telegram Bot v3."""

import logging

from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes,
)

from config.settings import TELEGRAM_BOT_TOKEN
from app.bot.admin import (
    admin_menu, add_user_cmd, remove_user_cmd, list_users_cmd,
    extend_cmd, broadcast_cmd, system_stats_cmd, admin_callback,
    detailed_report_cmd,
)
from app.bot.coupon_interactive import (
    start_coupon, league_selected, match_selected,
    bet_type_selected, pick_confirmed, review_coupon, coupon_callback,
    SELECT_LEAGUE, SELECT_MATCH, SELECT_BET_TYPE, CONFIRM_PICK, REVIEW_COUPON,
)
from app.bot.predictions import (
    predict_cmd, accuracy_cmd, schedule_predictions, send_report_cmd,
)
from app.bot.subscribers import SubscriptionManager
from src.db.base import get_backend

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message + subscription check."""
    user = update.effective_user
    db = get_backend()
    mgr = SubscriptionManager(db)
    sub = mgr.get_subscriber(user.id)

    if sub and mgr.is_premium(user.id):
        plan = sub.get("plan", "premium").upper()
        msg = (
            f"🎉 Hoş geldin, {user.first_name}!\n\n"
            f"🏆 Plan: *{plan}*\n\n"
            f"📋 Komutlar:\n"
            f"/tahmin — Günün tahminleri\n"
            f"/kupon — İnteraktif kupon oluştur\n"
            f"/basari — Doğruluk raporu\n"
            f"/yardim — Yardım"
        )
    else:
        msg = (
            f"👋 Merhaba, {user.first_name}!\n\n"
            f"🤖 *Güzel Tahmin* — AI Futbol Tahmin Sistemi\n\n"
            f"🔒 Premium özelliklere erişim için admin ile iletişime geçin.\n\n"
            f"📋 Mevcut komutlar:\n"
            f"/basari — Doğruluk istatistikleri\n"
            f"/yardim — Yardım"
        )

    mgr.log_activity(user.id, "/start")
    await update.message.reply_text(msg, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command."""
    msg = (
        "📖 *Güzel Tahmin — Yardım*\n"
        f"{'━'*28}\n\n"
        "🔹 /tahmin [lig] — Günün tahminleri\n"
        "   Lig kodları: E0, SP1, D1, I1, T1\n\n"
        "🔹 /kupon — İnteraktif kupon oluştur\n"
        "   Adım adım lig → maç → bahis seç\n\n"
        "🔹 /basari — Doğruluk raporu\n"
        "   Son 7/30 gün ve genel istatistik\n\n"
        "🔸 Admin:\n"
        "   /admin — Admin paneli\n"
        "   /ekle — Üye ekle\n"
        "   /sil — Üye sil\n"
        "   /uzat — Üyelik uzat\n"
        "   /duyuru — Toplu mesaj\n"
        "   /sistem — Sistem durumu\n"
        "   /send_report — Performans raporu yayınla\n\n"
        "🤖 Güzel Tahmin v3.0 | Ensemble AI"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def post_init(app: Application):
    """Post-init: set bot commands + schedule jobs."""
    commands = [
        BotCommand("start", "Başla"),
        BotCommand("tahmin", "Günün tahminleri"),
        BotCommand("kupon", "Kupon oluştur"),
        BotCommand("basari", "Doğruluk raporu"),
        BotCommand("yardim", "Yardım"),
        BotCommand("admin", "Admin paneli"),
    ]
    await app.bot.set_my_commands(commands)
    await schedule_predictions(app)
    logger.info("Bot initialized — commands set, jobs scheduled.")


def main():
    """Run the bot."""
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN not set in .env")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Coupon conversation handler
    coupon_conv = ConversationHandler(
        entry_points=[CommandHandler("kupon", start_coupon)],
        states={
            SELECT_LEAGUE: [
                CallbackQueryHandler(league_selected, pattern=r"^league_"),
            ],
            SELECT_MATCH: [
                CallbackQueryHandler(match_selected, pattern=r"^match_"),
                CallbackQueryHandler(coupon_callback, pattern=r"^coupon_"),
            ],
            SELECT_BET_TYPE: [
                CallbackQueryHandler(bet_type_selected, pattern=r"^bet_"),
                CallbackQueryHandler(coupon_callback, pattern=r"^coupon_"),
            ],
            CONFIRM_PICK: [
                CallbackQueryHandler(pick_confirmed, pattern=r"^pick_"),
                CallbackQueryHandler(coupon_callback, pattern=r"^coupon_"),
            ],
            REVIEW_COUPON: [
                CallbackQueryHandler(review_coupon, pattern=r"^coupon_"),
                CallbackQueryHandler(league_selected, pattern=r"^league_"),
            ],
        },
        fallbacks=[
            CommandHandler("iptal", lambda u, c: ConversationHandler.END),
            CommandHandler("kupon", start_coupon),
        ],
        per_message=False,
    )

    # User commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(coupon_conv)
    app.add_handler(CommandHandler("tahmin", predict_cmd))
    app.add_handler(CommandHandler("basari", accuracy_cmd))
    app.add_handler(CommandHandler("yardim", help_command))

    # Admin commands
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(CommandHandler("ekle", add_user_cmd))
    app.add_handler(CommandHandler("sil", remove_user_cmd))
    app.add_handler(CommandHandler("liste", list_users_cmd))
    app.add_handler(CommandHandler("uzat", extend_cmd))
    app.add_handler(CommandHandler("duyuru", broadcast_cmd))
    app.add_handler(CommandHandler("sistem", system_stats_cmd))
    app.add_handler(CommandHandler("rapor", detailed_report_cmd))
    app.add_handler(CommandHandler("send_report", send_report_cmd))

    # Admin callback handler
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^admin_"))

    # Post-init
    app.post_init = post_init

    logger.info("🤖 Güzel Tahmin Bot v3 starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
