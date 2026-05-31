from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import List, Dict, Any


def get_start_keyboard() -> InlineKeyboardMarkup:
    """Returns the main starting keyboard."""
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ VIP Paketleri Listele", callback_data="list_packages")
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
    """Returns keyboard with payment link / simulation confirmation button."""
    builder = InlineKeyboardBuilder()
    # Adding a mock payment execution simulation button so the user can easily pay
    builder.button(
        text="💳 Şimdi Öde (Simülasyon)",
        callback_data=f"simulate_pay:{provider_tx_id}"
    )
    builder.button(text="❌ Ödemeyi İptal Et", callback_data="cancel_payment")
    builder.adjust(1)
    return builder.as_markup()


def get_back_to_main_keyboard() -> InlineKeyboardMarkup:
    """Return a simple button to go back to main menu."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Ana Menü", callback_data="main_menu")
    return builder.as_markup()
