"""Interactive coupon builder with inline keyboards."""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config.leagues import ACTIVE_LEAGUES, LEAGUE_EMOJI
from app.bot.admin import premium_only
from app.bot.formatters import format_coupon, confidence_emoji, result_emoji
from src.db.base import get_backend

logger = logging.getLogger(__name__)

SELECT_LEAGUE, SELECT_MATCH, SELECT_BET_TYPE, CONFIRM_PICK, REVIEW_COUPON = range(5)


@premium_only
async def start_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry: show league selection buttons."""
    context.user_data["coupon_picks"] = []

    buttons = []
    row = []
    for code, league in ACTIVE_LEAGUES.items():
        emoji = LEAGUE_EMOJI.get(code, "⚽")
        row.append(InlineKeyboardButton(
            f"{emoji} {league.name}", callback_data=f"league_{code}"
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton("🎯 Sistem Önerisi", callback_data="league_auto"),
    ])

    reply_markup = InlineKeyboardMarkup(buttons)

    if update.message:
        await update.message.reply_text(
            "📋 *Kupon Oluşturucu*\nBir lig seçin:",
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )
    else:
        await update.callback_query.edit_message_text(
            "📋 *Kupon Oluşturucu*\nBir lig seçin:",
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )
    return SELECT_LEAGUE


async def league_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show matches for selected league."""
    query = update.callback_query
    await query.answer()

    league_code = query.data.replace("league_", "")
    context.user_data["selected_league"] = league_code

    if league_code == "auto":
        return await _auto_coupon(update, context)

    db = get_backend()

    # Get today/tomorrow matches
    matches = db.fetchall("""
        SELECT m.id, t1.name as home_team, t2.name as away_team, m.date,
               p.confidence_score, p.predicted_result
        FROM matches m
        JOIN teams t1 ON m.home_team_id = t1.id
        JOIN teams t2 ON m.away_team_id = t2.id
        LEFT JOIN predictions p ON p.match_id = m.id
        WHERE m.league_code = ?
        AND DATE(m.date) >= DATE('now') AND DATE(m.date) <= DATE('now', '+2 days')
        AND m.ft_result IS NULL
        ORDER BY m.date, p.confidence_score DESC
    """, (league_code,))

    if not matches:
        await query.edit_message_text(
            f"📋 Bu lig için yaklaşan maç bulunamadı.\n"
            "Başka bir lig seçmek için /kupon yazın."
        )
        return ConversationHandler.END

    buttons = []
    for m in matches[:8]:
        conf = m.get("confidence_score", 0) or 0
        ce = confidence_emoji(conf)
        btn_text = f"{m['home_team'][:12]} vs {m['away_team'][:12]} {ce}%{conf}"
        buttons.append([InlineKeyboardButton(
            btn_text, callback_data=f"match_{m['id']}"
        )])

    buttons.append([InlineKeyboardButton("⬅️ Geri", callback_data="coupon_back_leagues")])

    league_name = ACTIVE_LEAGUES.get(league_code, type("", (), {"name": league_code})).name
    await query.edit_message_text(
        f"⚽ *{league_name}* — Maç seçin:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )
    return SELECT_MATCH


async def match_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bet types for selected match."""
    query = update.callback_query
    await query.answer()

    match_id = int(query.data.replace("match_", ""))
    context.user_data["selected_match"] = match_id

    db = get_backend()
    match = db.fetchone("""
        SELECT m.*, t1.name as home_team, t2.name as away_team,
               p.home_win_prob, p.draw_prob, p.away_win_prob, p.confidence_score
        FROM matches m
        JOIN teams t1 ON m.home_team_id = t1.id
        JOIN teams t2 ON m.away_team_id = t2.id
        LEFT JOIN predictions p ON p.match_id = m.id
        WHERE m.id = ?
    """, (match_id,))

    if not match:
        await query.edit_message_text("❌ Maç bulunamadı.")
        return ConversationHandler.END

    context.user_data["match_info"] = dict(match)

    h_prob = match.get("home_win_prob", 0.33) or 0.33
    d_prob = match.get("draw_prob", 0.33) or 0.33
    a_prob = match.get("away_win_prob", 0.33) or 0.33
    conf = match.get("confidence_score", 0) or 0

    buttons = [
        [
            InlineKeyboardButton(f"1️⃣ 1X2", callback_data="bet_1x2"),
            InlineKeyboardButton(f"📊 Ü/A 2.5", callback_data="bet_ou25"),
        ],
        [
            InlineKeyboardButton(f"⚽ KG Var/Yok", callback_data="bet_btts"),
            InlineKeyboardButton(f"🔄 Çifte Şans", callback_data="bet_dc"),
        ],
        [InlineKeyboardButton("⬅️ Geri", callback_data="coupon_back_matches")],
    ]

    info_text = (
        f"⚽ *{match['home_team']} vs {match['away_team']}*\n"
        f"📅 {str(match.get('date', ''))[:10]}\n\n"
        f"📊 H: %{h_prob*100:.0f} | D: %{d_prob*100:.0f} | A: %{a_prob*100:.0f}\n"
        f"{confidence_emoji(conf)} Güven: %{conf}\n\n"
        f"Bahis türünü seçin:"
    )

    await query.edit_message_text(
        info_text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )
    return SELECT_BET_TYPE


async def bet_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show options for selected bet type."""
    query = update.callback_query
    await query.answer()

    bet_type = query.data.replace("bet_", "")
    context.user_data["selected_bet_type"] = bet_type
    match_info = context.user_data.get("match_info", {})

    h_prob = match_info.get("home_win_prob", 0.33) or 0.33
    d_prob = match_info.get("draw_prob", 0.33) or 0.33
    a_prob = match_info.get("away_win_prob", 0.33) or 0.33

    buttons = []
    if bet_type == "1x2":
        best = max([("H", h_prob), ("D", d_prob), ("A", a_prob)], key=lambda x: x[1])
        buttons = [
            [InlineKeyboardButton(
                f"{'✅ ' if best[0]=='H' else ''}🏠 Ev ({1/max(h_prob,0.01):.2f} | %{h_prob*100:.0f})",
                callback_data=f"pick_1x2_H"
            )],
            [InlineKeyboardButton(
                f"{'✅ ' if best[0]=='D' else ''}🤝 Bere ({1/max(d_prob,0.01):.2f} | %{d_prob*100:.0f})",
                callback_data=f"pick_1x2_D"
            )],
            [InlineKeyboardButton(
                f"{'✅ ' if best[0]=='A' else ''}✈️ Dep ({1/max(a_prob,0.01):.2f} | %{a_prob*100:.0f})",
                callback_data=f"pick_1x2_A"
            )],
        ]
    elif bet_type == "ou25":
        over_odds = 1 / 0.55  # placeholder
        under_odds = 1 / 0.45
        buttons = [
            [InlineKeyboardButton(f"📈 Üst 2.5 ({over_odds:.2f})", callback_data="pick_ou25_over")],
            [InlineKeyboardButton(f"📉 Alt 2.5 ({under_odds:.2f})", callback_data="pick_ou25_under")],
        ]
    elif bet_type == "btts":
        buttons = [
            [InlineKeyboardButton("⚽ KG Var", callback_data="pick_btts_yes")],
            [InlineKeyboardButton("🚫 KG Yok", callback_data="pick_btts_no")],
        ]
    elif bet_type == "dc":
        buttons = [
            [InlineKeyboardButton(f"1X (%{(h_prob+d_prob)*100:.0f})", callback_data="pick_dc_1x")],
            [InlineKeyboardButton(f"12 (%{(h_prob+a_prob)*100:.0f})", callback_data="pick_dc_12")],
            [InlineKeyboardButton(f"X2 (%{(d_prob+a_prob)*100:.0f})", callback_data="pick_dc_x2")],
        ]

    buttons.append([InlineKeyboardButton("⬅️ Geri", callback_data="coupon_back_bets")])

    await query.edit_message_text(
        f"🎰 *Seçiminizi yapın:*",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )
    return CONFIRM_PICK


async def pick_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add pick to coupon and show summary."""
    query = update.callback_query
    await query.answer()

    data = query.data.replace("pick_", "")
    match_info = context.user_data.get("match_info", {})

    # Parse pick
    parts = data.split("_")
    bet_type = parts[0]
    selection = parts[1] if len(parts) > 1 else ""

    bet_labels = {
        "1x2_H": "Ev Sahibi (1)", "1x2_D": "Berabere (X)", "1x2_A": "Deplasman (2)",
        "ou25_over": "Üst 2.5", "ou25_under": "Alt 2.5",
        "btts_yes": "KG Var", "btts_no": "KG Yok",
        "dc_1x": "1X", "dc_12": "12", "dc_x2": "X2",
    }

    pick = {
        "match_id": context.user_data.get("selected_match"),
        "home_team": match_info.get("home_team", "?"),
        "away_team": match_info.get("away_team", "?"),
        "bet_type": bet_type,
        "bet_label": bet_labels.get(data, data),
        "odds": 1.50,  # Will be populated from odds table
        "confidence": match_info.get("confidence_score", 0) or 50,
    }

    picks = context.user_data.get("coupon_picks", [])
    picks.append(pick)
    context.user_data["coupon_picks"] = picks

    total_odds = 1.0
    for p in picks:
        total_odds *= p.get("odds", 1.5)

    summary = f"✅ *Eklendi!*\n\n📋 Kuponunuz ({len(picks)} maç)\nToplam Oran: {total_odds:.2f}\n"
    for i, p in enumerate(picks, 1):
        summary += f"\n{i}. {p['home_team']} vs {p['away_team']}: {p['bet_label']}"

    buttons = [
        [
            InlineKeyboardButton("➕ Maç Ekle", callback_data="coupon_add_more"),
            InlineKeyboardButton("📋 Kuponu Gör", callback_data="coupon_review"),
        ],
        [
            InlineKeyboardButton("🗑️ Sıfırla", callback_data="coupon_reset"),
            InlineKeyboardButton("✅ Onayla", callback_data="coupon_confirm"),
        ],
    ]

    await query.edit_message_text(
        summary,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )
    return REVIEW_COUPON


async def review_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show final coupon or handle review actions."""
    query = update.callback_query
    await query.answer()
    data = query.data

    picks = context.user_data.get("coupon_picks", [])

    if data == "coupon_confirm":
        if not picks:
            await query.edit_message_text("❌ Kupon boş!")
            return ConversationHandler.END

        total_odds = 1.0
        for p in picks:
            total_odds *= p.get("odds", 1.5)

        coupon_text = format_coupon(picks, "custom", total_odds)

        # Save to DB
        import json
        db = get_backend()
        tg_id = query.from_user.id
        try:
            db.execute("""
                INSERT INTO user_coupons (telegram_id, picks_json, total_odds, strategy)
                VALUES (?, ?, ?, 'custom')
            """, (tg_id, json.dumps(picks, default=str), total_odds))
        except Exception as e:
            logger.error(f"Failed to save coupon: {e}")

        await query.edit_message_text(coupon_text)
        context.user_data.clear()
        return ConversationHandler.END

    elif data == "coupon_add_more":
        return await start_coupon(update, context)

    elif data == "coupon_reset":
        context.user_data["coupon_picks"] = []
        await query.edit_message_text("🗑️ Kupon sıfırlandı.")
        return await start_coupon(update, context)

    elif data == "coupon_review":
        if not picks:
            await query.edit_message_text("📋 Kupon boş. /kupon ile başlayın.")
            return ConversationHandler.END

        total_odds = 1.0
        for p in picks:
            total_odds *= p.get("odds", 1.5)

        coupon_text = format_coupon(picks, "custom", total_odds)
        buttons = [
            [
                InlineKeyboardButton("✅ Onayla", callback_data="coupon_confirm"),
                InlineKeyboardButton("➕ Ekle", callback_data="coupon_add_more"),
            ],
            [InlineKeyboardButton("🗑️ İptal", callback_data="coupon_reset")],
        ]
        await query.edit_message_text(
            coupon_text,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return REVIEW_COUPON


async def coupon_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle navigation callbacks."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "coupon_back_leagues":
        return await start_coupon(update, context)
    elif data.startswith("coupon_back"):
        return await start_coupon(update, context)

    return ConversationHandler.END


async def _auto_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate system-recommended coupon."""
    query = update.callback_query
    db = get_backend()

    # Get highest confidence predictions
    predictions = db.fetchall("""
        SELECT p.*, m.date, t1.name as home_team, t2.name as away_team
        FROM predictions p
        JOIN matches m ON p.match_id = m.id
        JOIN teams t1 ON m.home_team_id = t1.id
        JOIN teams t2 ON m.away_team_id = t2.id
        WHERE DATE(m.date) >= DATE('now') AND m.ft_result IS NULL
        AND p.confidence_score >= 65
        ORDER BY p.confidence_score DESC
        LIMIT 5
    """)

    if not predictions:
        await query.edit_message_text("🎯 Yeterli güvenilir tahmin bulunamadı.")
        return ConversationHandler.END

    picks = []
    for pred in predictions:
        result_labels = {"H": "Ev Sahibi (1)", "D": "Berabere (X)", "A": "Deplasman (2)"}
        picks.append({
            "home_team": pred["home_team"],
            "away_team": pred["away_team"],
            "bet_type": "1x2",
            "bet_label": result_labels.get(pred.get("predicted_result", "H"), "?"),
            "odds": 1.50,
            "confidence": pred.get("confidence_score", 0),
        })

    total_odds = 1.0
    for p in picks:
        total_odds *= p["odds"]

    coupon_text = format_coupon(picks, "banko", total_odds)

    buttons = [
        [
            InlineKeyboardButton("✅ Kaydet", callback_data="coupon_confirm"),
            InlineKeyboardButton("✏️ Düzenle", callback_data="coupon_add_more"),
        ],
    ]

    context.user_data["coupon_picks"] = picks
    await query.edit_message_text(
        f"🎯 *Sistem Önerisi*\n\n{coupon_text}",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )
    return REVIEW_COUPON
