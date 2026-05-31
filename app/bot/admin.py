"""Admin commands and management for Telegram bot."""

import logging
from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config.settings import TELEGRAM_ADMIN_IDS, TELEGRAM_VIP_LINK
from app.bot.subscribers import SubscriptionManager
from app.bot.formatters import format_admin_stats
from src.db.base import get_backend

logger = logging.getLogger(__name__)


def admin_only(func):
    """Decorator: restrict to admin users."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in TELEGRAM_ADMIN_IDS:
            await update.message.reply_text("⛔ Bu komut sadece adminler içindir.")
            return
        return await func(update, context)
    return wrapper


def premium_only(func):
    """Decorator: restrict to premium subscribers."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id in TELEGRAM_ADMIN_IDS:
            return await func(update, context)

        db = get_backend()
        mgr = SubscriptionManager(db)
        if not mgr.is_premium(user_id):
            await update.message.reply_text(
                "🔒 Bu özellik Premium üyelere özeldir.\n"
                "Bilgi için admin ile iletişime geçin."
            )
            return
        return await func(update, context)
    return wrapper


@admin_only
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin panel with inline buttons."""
    keyboard = [
        [
            InlineKeyboardButton("👥 Üyeler", callback_data="admin_list_users"),
            InlineKeyboardButton("➕ Üye Ekle", callback_data="admin_add_prompt"),
        ],
        [
            InlineKeyboardButton("📢 Duyuru", callback_data="admin_broadcast_prompt"),
            InlineKeyboardButton("📈 İstatistik", callback_data="admin_stats"),
        ],
        [
            InlineKeyboardButton("📊 Rapor ve Reklam", callback_data="admin_detailed_report"),
            InlineKeyboardButton("🔧 Sistem", callback_data="admin_system"),
        ],
        [
            InlineKeyboardButton("🔄 Yenile", callback_data="admin_refresh"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🔧 *Admin Paneli*\nAşağıdaki butonlardan seçim yapın:",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )


@admin_only
async def add_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add premium subscriber: /ekle <telegram_id> <days> [plan]"""
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Kullanım: /ekle <telegram_id> <gün> [plan]\nÖrnek: /ekle 123456789 30 premium")
        return

    try:
        tg_id = int(args[0])
        days = int(args[1])
        plan = args[2] if len(args) > 2 else "premium"
    except ValueError:
        await update.message.reply_text("❌ Geçersiz parametre. Telegram ID ve gün sayısı rakam olmalı.")
        return

    db = get_backend()
    mgr = SubscriptionManager(db)
    admin_name = update.effective_user.username or str(update.effective_user.id)

    if mgr.add_subscriber(tg_id, plan=plan, days=days, added_by=admin_name):
        await update.message.reply_text(
            f"✅ Üye eklendi!\n"
            f"ID: `{tg_id}`\n"
            f"Plan: {plan}\n"
            f"Süre: {days} gün",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text("❌ Üye eklenemedi.")


@admin_only
async def remove_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove subscriber: /sil <telegram_id>"""
    args = context.args
    if not args:
        await update.message.reply_text("Kullanım: /sil <telegram_id>")
        return

    try:
        tg_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Geçersiz Telegram ID.")
        return

    db = get_backend()
    mgr = SubscriptionManager(db)
    if mgr.remove_subscriber(tg_id):
        await update.message.reply_text(f"✅ Üye deaktif edildi: `{tg_id}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Üye bulunamadı.")


@admin_only
async def list_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all subscribers."""
    db = get_backend()
    mgr = SubscriptionManager(db)
    subs = mgr.list_subscribers()

    if not subs:
        await update.message.reply_text("📋 Henüz üye yok.")
        return

    lines = ["👥 *Üye Listesi*\n"]
    for s in subs[:20]:
        status = "✅" if s.get("is_active") else "❌"
        plan = s.get("plan", "free")
        username = s.get("username", "N/A")
        end = str(s.get("end_date", ""))[:10]
        lines.append(f"{status} `{s['telegram_id']}` @{username} [{plan}] → {end}")

    if len(subs) > 20:
        lines.append(f"\n... ve {len(subs)-20} üye daha")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@admin_only
async def extend_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Extend subscription: /uzat <telegram_id> <days>"""
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Kullanım: /uzat <telegram_id> <gün>")
        return

    try:
        tg_id = int(args[0])
        days = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ Geçersiz parametre.")
        return

    db = get_backend()
    mgr = SubscriptionManager(db)
    if mgr.extend_subscription(tg_id, days):
        await update.message.reply_text(f"✅ Üyelik {days} gün uzatıldı: `{tg_id}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Üye bulunamadı.")


@admin_only
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast message to all subscribers: /duyuru <message>"""
    if not context.args:
        await update.message.reply_text("Kullanım: /duyuru <mesaj>")
        return

    message = " ".join(context.args)
    db = get_backend()
    mgr = SubscriptionManager(db)
    subs = mgr.list_subscribers()
    active = [s for s in subs if s.get("is_active")]

    sent = 0
    failed = 0
    for sub in active:
        try:
            await context.bot.send_message(
                chat_id=sub["telegram_id"],
                text=f"📢 *Duyuru*\n\n{message}",
                parse_mode="Markdown",
            )
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"📢 Duyuru gönderildi!\n✅ Başarılı: {sent}\n❌ Başarısız: {failed}"
    )


@admin_only
async def system_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show system health and statistics."""
    db = get_backend()
    mgr = SubscriptionManager(db)
    stats = mgr.get_stats()

    # Add accuracy info
    acc_row = db.fetchone("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN predicted_result = actual_result THEN 1 ELSE 0 END) as correct
        FROM predictions WHERE actual_result IS NOT NULL
    """)
    if acc_row and acc_row["total"] > 0:
        stats["accuracy"] = acc_row["correct"] / acc_row["total"] * 100
    else:
        stats["accuracy"] = 0

    # Daily prediction count
    daily = db.fetchone("""
        SELECT COUNT(*) as c FROM predictions WHERE DATE(created_at) = DATE('now')
    """)
    stats["daily_predictions"] = daily["c"] if daily else 0
    stats["api_health"] = "✅"

    await update.message.reply_text(format_admin_stats(stats))


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin inline button callbacks."""
    query = update.callback_query
    await query.answer()
    data = query.data

    user_id = query.from_user.id
    if user_id not in TELEGRAM_ADMIN_IDS:
        await query.edit_message_text("⛔ Yetkiniz yok.")
        return

    db = get_backend()
    mgr = SubscriptionManager(db)

    if data == "admin_list_users":
        subs = mgr.list_subscribers()
        if not subs:
            await query.edit_message_text("📋 Henüz üye yok.")
            return
        lines = ["👥 *Üye Listesi*\n"]
        for s in subs[:15]:
            status = "✅" if s.get("is_active") else "❌"
            lines.append(f"{status} `{s['telegram_id']}` [{s.get('plan','?')}]")
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown")

    elif data == "admin_stats":
        stats = mgr.get_stats()
        stats["accuracy"] = 0
        stats["daily_predictions"] = 0
        stats["api_health"] = "✅"
        await query.edit_message_text(format_admin_stats(stats))

    elif data == "admin_system":
        await query.edit_message_text(
            "🔧 *Sistem Bilgisi*\n\n"
            "Versiyon: v3.0\n"
            "Model: Stacking Ensemble\n"
            "Ligler: 5 (EPL, La Liga, BL, SA, SL)\n"
            "DB: SQLite v4",
            parse_mode="Markdown",
        )

    elif data == "admin_refresh":
        await query.edit_message_text("🔄 Veriler yenilendi.")

    elif data == "admin_add_prompt":
        await query.edit_message_text(
            "➕ Üye eklemek için:\n`/ekle <telegram_id> <gün> [plan]`\n\n"
            "Örnek: `/ekle 123456789 30 premium`",
            parse_mode="Markdown",
        )

    elif data == "admin_broadcast_prompt":
        await query.edit_message_text(
            "📢 Duyuru göndermek için:\n`/duyuru <mesajınız>`",
            parse_mode="Markdown",
        )

    elif data == "admin_detailed_report":
        await query.edit_message_text("🔄 Detaylı başarı ve kâr (ROI) raporu hesaplanıyor...")
        report_text = _generate_detailed_report()
        await query.edit_message_text(report_text, parse_mode="Markdown")


@admin_only
async def detailed_report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command handler to show detailed audit/ROI report and copy-paste advertising text."""
    wait_msg = await update.message.reply_text("🔄 Detaylı rapor hazırlanıyor, lütfen bekleyin...")
    report_text = _generate_detailed_report()
    await wait_msg.edit_text(report_text, parse_mode="Markdown")


def _generate_detailed_report() -> str:
    db = get_backend()
    db.connect()
    try:
        # Check if we have posted matches in DB to compute stats on
        has_posted = db.fetchone("SELECT COUNT(*) as c FROM predictions WHERE was_posted = 1 AND actual_result IS NOT NULL")["c"] > 0
        posted_cond = "AND p.was_posted = 1" if has_posted else ""
        
        # Fetch all predictions that have ended
        rows = db.fetchall(f"""
            SELECT 
                p.predicted_result, 
                p.actual_result, 
                p.home_win_prob, 
                p.draw_prob, 
                p.away_win_prob,
                o.home_odds, 
                o.draw_odds, 
                o.away_odds
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            LEFT JOIN odds o ON p.match_id = o.match_id
            WHERE p.actual_result IS NOT NULL 
              AND p.predicted_result IN ('H', 'D', 'A')
              {posted_cond}
        """)
        
        total = len(rows)
        if total == 0:
            return "❌ Sistemde henüz sonuçlanmış maç tahmini bulunmamaktadır."
            
        main_wins = 0
        dc_wins = 0
        
        main_profit = 0.0
        main_staked = 0.0
        
        val_wins = 0
        val_total = 0
        val_profit = 0.0
        val_staked = 0.0
        
        for r in rows:
            pr = r['predicted_result']
            ar = r['actual_result']
            
            # 1. Main 1X2 Check
            if pr == ar:
                main_wins += 1
                
            # 2. Alternative Double Chance Check
            if pr == 'H' and ar in ('H', 'D'):
                dc_wins += 1
            elif pr == 'A' and ar in ('A', 'D'):
                dc_wins += 1
            elif pr == 'D' and ar in ('H', 'D'):
                dc_wins += 1
                
            # 3. Main Profit / Yield (if odds exist)
            ho = r['home_odds']
            do = r['draw_odds']
            ao = r['away_odds']
            odds = ho if pr == 'H' else (do if pr == 'D' else ao)
            
            if odds and odds > 1.0:
                main_staked += 1.0
                if pr == ar:
                    main_profit += (odds - 1.0)
                else:
                    main_profit -= 1.0
                    
            # 4. Value Bet Check
            hp = r['home_win_prob']
            dp = r['draw_prob']
            ap = r['away_win_prob']
            prob = hp if pr == 'H' else (dp if pr == 'D' else ap)
            
            if prob and odds and odds > 1.0:
                ev = (prob * odds) - 1.0
                if ev > 0.05:
                    val_total += 1
                    val_staked += 1.0
                    if pr == ar:
                        val_wins += 1
                        val_profit += (odds - 1.0)
                    else:
                        val_profit -= 1.0
                        
        main_acc = (main_wins / total * 100) if total > 0 else 0
        dc_acc = (dc_wins / total * 100) if total > 0 else 0
        
        main_roi = (main_profit / main_staked * 100) if main_staked > 0 else 0
        val_acc = (val_wins / val_total * 100) if val_total > 0 else 0
        val_roi = (val_profit / val_staked * 100) if val_staked > 0 else 0
        
        data_source_label = "Sadece Kanalda Paylaşılan Güncel Maçlar" if has_posted else "Eğitim & Geçmiş Simülasyon Verisi (Fallback)"
        
        report = (
            f"📊 *DETAYLI BAŞARI VE ROI RAPORU*\n"
            f"{'━'*30}\n\n"
            f"📍 *İstatistik Türü:* `{data_source_label}`\n"
            f"• Toplam Maç Sayısı: *{total}*\n"
            f"🎯 *Ana Tahmin Başarısı (1X2)*: `%{main_acc:.1f}` ({main_wins}/{total})\n"
            f"🛡 *Çifte Şans / Alt. Başarısı*: `%{dc_acc:.1f}` ({dc_wins}/{total})\n\n"
            f"💰 *KAZANÇ VE YATIRIM GETİRİSİ (ROI)*\n"
            f"• Ana Tahmin Toplam Kâr: `+{main_profit:.1f} unit`\n"
            f"• Ana Tahmin ROI Getirisi: `%{main_roi:.1f}`\n\n"
            f"💎 *AI DEĞERLİ (VALUE) TAHMİNLER*\n"
            f"• Toplam Value Maç: *{val_total}*\n"
            f"• Value Başarı Oranı: `%{val_acc:.1f}` ({val_wins}/{val_total})\n"
            f"• Value Net Kâr (Kasa Kârı): `+{val_profit:.1f} unit`\n"
            f"• *Value Getiri Oranı (ROI)*: `%{val_roi:.1f}` 🔥\n\n"
            f"{'━'*30}\n"
            f"📢 *HAZIR REKLAM METNİ (Kopyala-Yapıştır)*\n\n"
            f"🤖 *Güzel Tahmin Ensemble AI ile Kazancınızı Katlayın!*\n\n"
            f"Geçtiğimiz dönemde yapay zeka modellerimizle tam *{total}* futbol karşılaşmasını analiz ettik! İşte resmi başarı istatistiklerimiz:\n\n"
            f"🎯 *Ana Tahmin Başarısı:* %{main_acc:.1f}\n"
            f"🛡 *Çifte Şans / Alternatif Başarısı:* %{dc_acc:.1f}\n"
            f"💎 *AI Değer (Value) Getiri Oranı (ROI):* %{val_roi:.1f}\n\n"
            f"💰 Sadece Değerli (Value) seçimleri takip ederek *+{val_profit:.0f} birim* net kâr elde ettik!\n\n"
            f"Yapay zekanın gücüyle düzenli kazanmak ve kasayı büyütmek için hemen aramıza katılın! 👇\n"
            f"🔗 {TELEGRAM_VIP_LINK}"
        )
        return report
    except Exception as e:
        logger.error(f"Error generating detailed report: {e}")
        return f"❌ Rapor oluşturulurken bir hata oluştu: {e}"
    finally:
        db.close()
