from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить сайт", callback_data="add_site")
    kb.button(text="📋 Мои сайты", callback_data="list_sites")
    kb.button(text="🔄 Проверить сейчас", callback_data="check_now")
    kb.adjust(1)
    return kb.as_markup()


def sites_list(sites: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for i, site in enumerate(sites):
        short = site["url"][:30] + ("…" if len(site["url"]) > 30 else "")
        kb.button(text=f"🗑 {short} ({site['hours']}ч)",
                  callback_data=f"del_{i}")
    kb.button(text="⬅️ Назад", callback_data="back_main")
    kb.adjust(1)
    return kb.as_markup()


def hours_choice() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for h in (1, 3, 6, 12, 24):
        kb.button(text=f"{h} ч", callback_data=f"hours_{h}")
    kb.button(text="⬅️ Отмена", callback_data="back_main")
    kb.adjust(3)
    return kb.as_markup()


def back_button() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data="back_main")
    return kb.as_markup()
