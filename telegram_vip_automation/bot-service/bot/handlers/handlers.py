import logging
from aiogram import Router, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime

from bot.client.api_client import api_client
from bot.keyboards.inline import (
    get_start_keyboard,
    get_packages_keyboard,
    get_payment_keyboard,
    get_back_to_main_keyboard
)

logger = logging.getLogger(__name__)
router = Router()

class CouponStates(StatesGroup):
    waiting_for_coupon_code = State()

START_TEXT = (
    "👋 **Güzel Tahmin VIP Otomasyon Botuna Hoş Geldiniz!**\n\n"
    "Bu bot aracılığıyla VIP grubumuza üye olabilir, ödemelerinizi güvenli şekilde gerçekleştirebilir "
    "ve abonelik sürenizi sorgulayabilirsiniz.\n\n"
    "Lütfen yapmak istediğiniz işlemi aşağıdaki menüden seçin:"
)


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    """Handler for /start command."""
    args = command.args
    if args:
        # Check if it looks like a referral or affiliate code
        if args.startswith("VIP") or len(args) >= 6:
            try:
                # Log referral click via API
                await api_client.log_referral_click(
                    telegram_id=message.from_user.id,
                    code=args
                )
                logger.info(f"Logged referral click for user {message.from_user.id} with code {args}")
            except Exception as e:
                logger.error(f"Failed to log referral click: {e}")

    await message.answer(
        text=START_TEXT,
        parse_mode="Markdown",
        reply_markup=get_start_keyboard()
    )


@router.callback_query(F.data == "main_menu")
async def show_main_menu(callback: CallbackQuery):
    """Returns to main menu."""
    await callback.message.edit_text(
        text=START_TEXT,
        parse_mode="Markdown",
        reply_markup=get_start_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "list_packages")
async def list_packages(callback: CallbackQuery):
    """Lists active packages fetched from API Service."""
    packages = await api_client.get_packages()
    
    if not packages:
        await callback.message.edit_text(
            text="⚠️ Şu anda aktif bir VIP paket bulunamadı. Lütfen daha sonra tekrar deneyin.",
            reply_markup=get_back_to_main_keyboard()
        )
        await callback.answer()
        return
        
    await callback.message.edit_text(
        text="📦 **Aktif VIP Üyelik Paketlerimiz:**\n\nLütfen satın almak istediğiniz paketi seçin:",
        parse_mode="Markdown",
        reply_markup=get_packages_keyboard(packages)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("select_package:"))
async def process_package_selection(callback: CallbackQuery):
    """Processes chosen package and initiates payment simulation via API Service."""
    package_id = int(callback.data.split(":")[1])
    
    try:
        payment = await api_client.create_payment(
            telegram_id=callback.from_user.id,
            package_id=package_id,
            username=callback.from_user.username or "",
            first_name=callback.from_user.first_name or "",
            last_name=callback.from_user.last_name or ""
        )
        
        if not payment:
            raise ValueError("API Service ödeme kaydı oluşturamadı.")
        
        # Display the package choice
        pay_text = (
            f"💳 **Ödeme İşlemi Başlatıldı**\n\n"
            f"🔹 **Süre:** {payment.get('currency', 'TRY')} bazlı işlem\n"
            f"🔹 **Tutar:** {payment.get('amount')} TRY\n"
            f"🔹 **İşlem Kodu:** `{payment.get('provider_tx_id')}`\n\n"
            f"Simülasyon ortamında testi tamamlamak için aşağıdaki ödeme butonuna tıklayabilirsiniz."
        )
        
        await callback.message.edit_text(
            text=pay_text,
            parse_mode="Markdown",
            reply_markup=get_payment_keyboard(payment.get("id"), payment.get("provider_tx_id"))
        )
        
    except Exception as e:
        logger.error(f"Failed to initiate package selection: {e}")
        await callback.message.edit_text(
            text=f"❌ Hata: Ödeme işlemi başlatılamadı ({str(e)})",
            reply_markup=get_back_to_main_keyboard()
        )
    await callback.answer()


@router.callback_query(F.data.startswith("simulate_pay:"))
async def process_payment_simulation(callback: CallbackQuery):
    """Simulates payment confirmation and presents channel invite link from API."""
    provider_tx_id = callback.data.split(":")[1]
    
    await callback.message.edit_text(
        text="🔄 Ödemeniz doğrulanıyor, lütfen bekleyin...",
        reply_markup=None
    )
    
    try:
        result = await api_client.simulate_payment_webhook(provider_tx_id)
        if not result or result.get("status") != "success":
            raise ValueError(result.get("detail") if result else "Geçersiz yanıt")
            
        sub = result.get("subscription", {})
        end_date_str = sub.get("end_date", "")
        # Parse datetime for friendly display
        try:
            dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            formatted_date = dt.strftime('%d.%m.%Y %H:%M')
        except Exception:
            formatted_date = end_date_str

        success_text = (
            f"✅ **Ödeme Başarıyla Onaylandı!**\n\n"
            f"Aboneliğiniz aktif hale getirilmiştir.\n"
            f"📅 **Bitiş Tarihi:** {formatted_date}\n\n"
            f"VIP Kanalımıza katılmak için aşağıdaki tek kullanımlık linki kullanabilirsiniz. "
            f"Bu link 24 saat içinde geçersiz olacaktır:\n\n"
            f"🔗 {result.get('invite_link')}"
        )
        
        await callback.message.edit_text(
            text=success_text,
            parse_mode="Markdown",
            reply_markup=get_back_to_main_keyboard()
        )
    except Exception as e:
        logger.error(f"Payment simulation failed: {e}")
        await callback.message.edit_text(
            text=f"❌ Ödeme onaylanırken bir hata oluştu: {str(e)}",
            reply_markup=get_back_to_main_keyboard()
        )
    await callback.answer()


@router.callback_query(F.data == "cancel_payment")
async def cancel_payment(callback: CallbackQuery):
    """Cancels current pending payment operation."""
    await callback.message.edit_text(
        text="❌ Ödeme işlemi iptal edildi.",
        reply_markup=get_back_to_main_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "check_subscription")
async def check_subscription(callback: CallbackQuery):
    """Checks the subscription details of the user via API Service."""
    try:
        status_res = await api_client.get_subscription_status(callback.from_user.id)
        
        if not status_res or not status_res.get("has_active_subscription"):
            await callback.message.edit_text(
                text="⚠️ Aktif bir VIP aboneliğiniz bulunmamaktadır.",
                reply_markup=get_back_to_main_keyboard()
            )
            await callback.answer()
            return
            
        sub = status_res.get("subscription", {})
        try:
            start_dt = datetime.fromisoformat(sub.get("start_date", "").replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(sub.get("end_date", "").replace("Z", "+00:00"))
            start_str = start_dt.strftime('%d.%m.%Y %H:%M')
            end_str = end_dt.strftime('%d.%m.%Y %H:%M')
        except Exception:
            start_str = sub.get("start_date")
            end_str = sub.get("end_date")
            
        sub_text = (
            f"👤 **VIP Abonelik Bilgileriniz:**\n\n"
            f"🔹 **Durum:** Aktif ✅\n"
            f"🔹 **Paket:** {sub.get('package_name', 'Özel Paket')}\n"
            f"📅 **Başlangıç:** {start_str}\n"
            f"📅 **Bitiş:** {end_str}\n\n"
            f"Herhangi bir sorun yaşarsanız destek ekibimizle iletişime geçebilirsiniz."
        )
        
        await callback.message.edit_text(
            text=sub_text,
            parse_mode="Markdown",
            reply_markup=get_back_to_main_keyboard()
        )
    except Exception as e:
        logger.error(f"Subscription query failed: {e}")
        await callback.message.edit_text(
            text="❌ Abonelik bilgileri sorgulanırken bir hata oluştu.",
            reply_markup=get_back_to_main_keyboard()
        )
    await callback.answer()


@router.callback_query(F.data == "support_menu")
async def show_support_menu(callback: CallbackQuery):
    """Shows direct support contact menu."""
    support_text = (
        "💬 **Canlı Destek & Yardım**\n\n"
        "Ödeme süreçleri, üyelik yenileme veya VIP kanal erişimiyle alakalı sorularınız için "
        "destek yöneticimizle iletişime geçebilirsiniz:\n\n"
        "👉 @admin_kullanici_adi\n\n"
        "Geri dönüşler en kısa sürede sağlanacaktır."
    )
    await callback.message.edit_text(
        text=support_text,
        parse_mode="Markdown",
        reply_markup=get_back_to_main_keyboard()
    )
    await callback.answer()


# ── REFERRAL SYSTEM HANDLERS ────────────────────────────────────────────────

async def handle_referral_menu(user_id: int, bot_username: str) -> str:
    """Helper to fetch and format referral program information."""
    try:
        res = await api_client.get_referral_code(user_id)
        if not res or res.get("status") != "success":
            return "❌ Davet et & kazan kodu alınamadı. Lütfen daha sonra tekrar deneyin."
        
        code = res.get("code")
        ref_link = f"https://t.me/{bot_username}?start={code}"
        
        text = (
            "👥 **Davet Et & Kazan Programı!**\n\n"
            "Arkadaşlarınızı VIP grubumuza davet edin, birlikte kazanın!\n\n"
            "🎁 **Davet Ödülü:**\n"
            "Davet ettiğiniz arkadaşınız ilk VIP paket ödemesini gerçekleştirdiğinde size **7 gün ücretsiz VIP üyelik** tanımlanır! "
            "Aktif bir üyeliğiniz varsa süreniz otomatik olarak 7 gün uzatılır.\n\n"
            "🔗 **Sizin Özel Davet Linkiniz:**\n"
            f"`{ref_link}`\n\n"
            "Yukarıdaki linke tıklayarak kopyalayabilir ve arkadaşlarınızla paylaşabilirsiniz!"
        )
        return text
    except Exception as e:
        logger.error(f"Referral menu helper error: {e}")
        return "❌ Bir hata oluştu, davet kodu bilgisi alınamadı."


@router.message(Command("ref"))
async def cmd_ref(message: Message):
    """Handler for /ref command."""
    bot_info = await message.bot.get_me()
    text = await handle_referral_menu(message.from_user.id, bot_info.username)
    await message.answer(
        text=text,
        parse_mode="Markdown",
        reply_markup=get_back_to_main_keyboard()
    )


@router.callback_query(F.data == "referral_menu")
async def referral_menu_callback(callback: CallbackQuery):
    """Callback handler for referral menu button."""
    bot_info = await callback.bot.get_me()
    text = await handle_referral_menu(callback.from_user.id, bot_info.username)
    await callback.message.edit_text(
        text=text,
        parse_mode="Markdown",
        reply_markup=get_back_to_main_keyboard()
    )
    await callback.answer()


# ── FREE TRIAL HANDLERS ───────────────────────────────────────────────────

async def process_trial_claim(telegram_id: int, message: Message):
    """Helper to claim trial, update status, and present single-use invite link."""
    waiting_msg = await message.answer("🔄 Deneme süreniz oluşturuluyor, lütfen bekleyin...")
    try:
        res = await api_client.claim_free_trial(telegram_id)
        if not res or res.get("status") != "success":
            await waiting_msg.edit_text(
                text=(
                    "⚠️ **Deneme Süresi Başlatılamadı**\n\n"
                    "Ücretsiz deneme hakkınızı (3 gün) daha önce kullanmış olabilirsiniz, "
                    "veya zaten aktif bir VIP aboneliğiniz bulunuyor olabilir.\n\n"
                    "Sorularınız için Canlı Destek ekibimizle iletişime geçebilirsiniz."
                ),
                reply_markup=get_back_to_main_keyboard()
            )
            return
        
        invite_link = res.get("invite_link")
        await waiting_msg.edit_text(
            text=(
                "🎁 **3 Günlük Deneme VIP Üyeliğiniz Aktifleştirildi!**\n\n"
                "VIP kanalımıza katılmak için aşağıdaki tek kullanımlık linki kullanabilirsiniz. "
                "Bu link 24 saat içinde geçersiz olacaktır:\n\n"
                f"🔗 {invite_link}"
            ),
            parse_mode="Markdown",
            reply_markup=get_back_to_main_keyboard()
        )
    except Exception as e:
        logger.error(f"Error processing trial claim: {e}")
        await waiting_msg.edit_text(
            text="❌ Deneme üyeliği başlatılırken teknik bir hata oluştu.",
            reply_markup=get_back_to_main_keyboard()
        )


@router.message(Command("trial"))
async def cmd_trial(message: Message):
    """Handler for /trial command."""
    await process_trial_claim(message.from_user.id, message)


@router.callback_query(F.data == "claim_trial")
async def claim_trial_callback(callback: CallbackQuery):
    """Callback handler for free trial button."""
    await process_trial_claim(callback.from_user.id, callback.message)
    await callback.answer()


# ── COUPON HANDLERS ───────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("apply_coupon:"))
async def process_apply_coupon_callback(callback: CallbackQuery, state: FSMContext):
    """Puts the user into FSM state to accept coupon code text."""
    parts = callback.data.split(":")
    payment_id = int(parts[1])
    provider_tx_id = parts[2]
    
    await state.update_data(payment_id=payment_id, provider_tx_id=provider_tx_id)
    await state.set_state(CouponStates.waiting_for_coupon_code)
    
    await callback.message.answer(
        text="🎫 Lütfen uygulamak istediğiniz indirim veya deneme kupon kodunu yazın (Örn: RENEW15):",
        reply_markup=get_back_to_main_keyboard()
    )
    await callback.answer()


@router.message(CouponStates.waiting_for_coupon_code)
async def process_coupon_code_message(message: Message, state: FSMContext):
    """Processes coupon code text input, validates it, and updates payment."""
    coupon_code = message.text.strip().upper()
    state_data = await state.get_data()
    payment_id = state_data.get("payment_id")
    provider_tx_id = state_data.get("provider_tx_id")
    
    await state.clear()
    waiting_msg = await message.answer("🔄 Kupon kodu doğrulanıyor...")
    
    try:
        res = await api_client.validate_coupon(
            code=coupon_code,
            telegram_id=message.from_user.id,
            payment_id=payment_id
        )
        
        if not res or res.get("status") != "success":
            await waiting_msg.edit_text(
                text="❌ Geçersiz, süresi dolmuş veya limitleri aşılmış bir kupon kodu girdiniz.",
                reply_markup=get_back_to_main_keyboard()
            )
            return
            
        coupon_data = res.get("coupon", {})
        discounted_amount = res.get("discounted_amount")
        
        coupon_val = coupon_data.get("value")
        coupon_type = coupon_data.get("coupon_type")
        
        discount_text = f"%{coupon_val}" if coupon_type == "percentage" else f"{coupon_val} TRY"
        if coupon_type == "free_trial":
            discount_text = "3 Gün Ücretsiz"
            
        success_text = (
            f"✅ **Kupon Başarıyla Uygulandı!**\n\n"
            f"🔹 **Uygulanan Kupon:** `{coupon_code}` ({discount_text} indirim)\n"
            f"🔹 **Yeni Ödenecek Tutar:** {discounted_amount} TRY\n"
            f"🔹 **İşlem Kodu:** `{provider_tx_id}`\n\n"
            f"İşlemi tamamlamak için aşağıdaki ödeme butonuna tıklayabilirsiniz."
        )
        
        await waiting_msg.edit_text(
            text=success_text,
            parse_mode="Markdown",
            reply_markup=get_payment_keyboard(payment_id, provider_tx_id)
        )
    except Exception as e:
        logger.error(f"Coupon validation error: {e}")
        await waiting_msg.edit_text(
            text="❌ Kupon kodu doğrulanırken bir hata oluştu.",
            reply_markup=get_back_to_main_keyboard()
        )
