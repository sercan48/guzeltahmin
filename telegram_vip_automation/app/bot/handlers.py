import logging
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from sqlalchemy.future import select

from app.db.session import async_session
from app.repositories.package import PackageRepository
from app.repositories.user import UserRepository
from app.repositories.subscription import SubscriptionRepository
from app.services.payment import PaymentService
from app.services.telegram import TelegramService
from app.keyboards.inline import (
    get_start_keyboard,
    get_packages_keyboard,
    get_payment_keyboard,
    get_back_to_main_keyboard
)

logger = logging.getLogger(__name__)
router = Router()

START_TEXT = (
    "👋 **Güzel Tahmin VIP Otomasyon Botuna Hoş Geldiniz!**\n\n"
    "Bu bot aracılığıyla VIP grubumuza üye olabilir, ödemelerinizi güvenli şekilde gerçekleştirebilir "
    "ve abonelik sürenizi sorgulayabilirsiniz.\n\n"
    "Lütfen yapmak istediğiniz işlemi aşağıdaki menüden seçin:"
)


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Handler for /start command."""
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
    """Lists active packages from the database."""
    async with async_session() as db:
        pkg_repo = PackageRepository(db)
        # Fetch all active packages
        packages = await pkg_repo.list_active()
        
        if not packages:
            await callback.message.edit_text(
                text="⚠️ Şu anda aktif bir VIP paket bulunamadı. Lütfen daha sonra tekrar deneyin.",
                reply_markup=get_back_to_main_keyboard()
            )
            await callback.answer()
            return
            
        pkg_list = [
            {
                "id": p.id,
                "name": p.name,
                "price": float(p.price),
                "duration_days": p.duration_days
            }
            for p in packages
        ]
        
        await callback.message.edit_text(
            text="📦 **Aktif VIP Üyelik Paketlerimiz:**\n\nLütfen satın almak istediğiniz paketi seçin:",
            parse_mode="Markdown",
            reply_markup=get_packages_keyboard(pkg_list)
        )
        await callback.answer()


@router.callback_query(F.data.startswith("select_package:"))
async def process_package_selection(callback: CallbackQuery):
    """Processes chosen package and initiates payment simulation."""
    package_id = int(callback.data.split(":")[1])
    
    async with async_session() as db:
        tg_service = TelegramService(bot=callback.bot)
        pay_service = PaymentService(db, tg_service)
        
        try:
            payment = await pay_service.create_mock_payment(
                telegram_id=callback.from_user.id,
                package_id=package_id,
                username=callback.from_user.username or "",
                first_name=callback.from_user.first_name or "",
                last_name=callback.from_user.last_name or ""
            )
            
            pkg_repo = PackageRepository(db)
            package = await pkg_repo.get(package_id)
            
            pay_text = (
                f"💳 **Ödeme İşlemi Başlatıldı**\n\n"
                f"🔹 **Seçilen Paket:** {package.name}\n"
                f"🔹 **Süre:** {package.duration_days} Gün\n"
                f"🔹 **Tutar:** {payment.amount} TRY\n"
                f"🔹 **İşlem Kodu:** `{payment.provider_tx_id}`\n\n"
                f"Simülasyon ortamında testi tamamlamak için aşağıdaki ödeme butonuna tıklayabilirsiniz."
            )
            
            await callback.message.edit_text(
                text=pay_text,
                parse_mode="Markdown",
                reply_markup=get_payment_keyboard(payment.id, payment.provider_tx_id)
            )
            
        except ValueError as ve:
            await callback.message.edit_text(
                text=f"❌ Hata: {str(ve)}",
                reply_markup=get_back_to_main_keyboard()
            )
        await callback.answer()


@router.callback_query(F.data.startswith("simulate_pay:"))
async def process_payment_simulation(callback: CallbackQuery):
    """Simulates payment confirmation and presents channel invite link."""
    provider_tx_id = callback.data.split(":")[1]
    
    # Send a typing status
    await callback.message.edit_text(
        text="🔄 Ödemeniz doğrulanıyor, lütfen bekleyin...",
        reply_markup=None
    )
    
    async with async_session() as db:
        tg_service = TelegramService(bot=callback.bot)
        pay_service = PaymentService(db, tg_service)
        
        try:
            payment, subscription, invite_link = await pay_service.confirm_payment(provider_tx_id)
            
            success_text = (
                f"✅ **Ödeme Başarıyla Onaylandı!**\n\n"
                f"Aboneliğiniz aktif hale getirilmiştir.\n"
                f"📅 **Bitiş Tarihi:** {subscription.end_date.strftime('%d.%m.%Y %H:%M')}\n\n"
                f"VIP Kanalımıza katılmak için aşağıdaki tek kullanımlık linki kullanabilirsiniz. "
                f"Bu link 24 saat içinde geçersiz olacaktır:\n\n"
                f"🔗 {invite_link}"
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
    """Checks the subscription details of the user."""
    async with async_session() as db:
        user_repo = UserRepository(db)
        sub_repo = SubscriptionRepository(db)
        
        user = await user_repo.get_by_telegram_id(callback.from_user.id)
        if not user:
            await callback.message.edit_text(
                text="⚠️ Sistemde kayıtlı üyeliğiniz bulunmamaktadır. Önce bir paket satın almalısınız.",
                reply_markup=get_back_to_main_keyboard()
            )
            await callback.answer()
            return
            
        sub = await sub_repo.get_active_by_user(user.id)
        if not sub or not sub.is_active:
            await callback.message.edit_text(
                text="⚠️ Aktif bir VIP aboneliğiniz bulunmamaktadır.",
                reply_markup=get_back_to_main_keyboard()
            )
            await callback.answer()
            return
            
        pkg_repo = PackageRepository(db)
        package = await pkg_repo.get(sub.package_id)
        
        sub_text = (
            f"👤 **VIP Abonelik Bilgileriniz:**\n\n"
            f"🔹 **Durum:** Aktif ✅\n"
            f"🔹 **Paket:** {package.name if package else 'Özel Paket'}\n"
            f"📅 **Başlangıç:** {sub.start_date.strftime('%d.%m.%Y %H:%M')}\n"
            f"📅 **Bitiş:** {sub.end_date.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"Herhangi bir sorun yaşarsanız destek ekibimizle iletişime geçebilirsiniz."
        )
        
        await callback.message.edit_text(
            text=sub_text,
            parse_mode="Markdown",
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
