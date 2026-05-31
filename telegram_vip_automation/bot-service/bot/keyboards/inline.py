from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import List, Dict, Any


def get_start_keyboard() -> InlineKeyboardMarkup:
    """Returns the main starting keyboard with growth features (Trial, Referral)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ VIP Paketleri Listele", callback_data="list_packages")
    builder.button(text="🎁 3 Günlük Deneme Başlat", callback_data="claim_trial")
    builder.button(text="👥 Davet Et & Kazan", callback_data="referral_menu")
    builder.button(text="👤 Üyelik Durumunu Sorgula", callback_data="check_subscription")
    builder.button(text="💬 Canlı Destek / Yardım", callback_data="support_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_packages_keyboard(packages: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """Returns inline buttons for choosing a specific VIP package."""
    builder = InlineKeyboardBuilder()
    for pkg in packages:
        # Dynamic callback data like select_package:<package_id>
        builder.button(
            text=f"📦 {pkg['name']} - {pkg['price']} TRY",
            callback_data=f"select_package:{pkg['id']}"
        )
    builder.button(text="🔙 Ana Menü", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_payment_keyboard(payment_id: int, provider_tx_id: str) -> InlineKeyboardMarkup:
    """Returns keyboard with payment, coupon code entry, and cancel buttons."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="💳 Şimdi Öde (Simülasyon)",
        callback_data=f"simulate_pay:{provider_tx_id}"
    )
    builder.button(
        text="🎫 Kupon Uygula",
        callback_data=f"apply_coupon:{payment_id}:{provider_tx_id}"
    )
    builder.button(text="❌ Ödemeyi İptal Et", callback_data="cancel_payment")
    builder.adjust(1)
    return builder.as_markup()


def get_back_to_main_keyboard() -> InlineKeyboardMarkup:
    """Return a simple button to go back to main menu."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Ana Menü", callback_data="main_menu")
    return builder.as_markup()
