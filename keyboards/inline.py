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
    for site in sites:
        short = site["url"][:30] + ("…" if len(site["url"]) > 30 else "")
        kb.button(text=f"{short} ({site['hours']}ч)",
                  callback_data=f"open_{site['id']}")
    kb.button(text="⬅️ Назад", callback_data="back_main")
    kb.adjust(1)
    return kb.as_markup()


def site_menu(site_id: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Проверить сейчас", callback_data=f"checkone_{site_id}")
    kb.button(text="⏱ Изменить интервал", callback_data=f"sethours_{site_id}")
    kb.button(text="🔢 Изменить лимит", callback_data=f"setlimit_{site_id}")
    kb.button(text="🗑 Удалить", callback_data=f"askdel_{site_id}")
    kb.button(text="⬅️ К списку", callback_data="list_sites")
    kb.adjust(1)
    return kb.as_markup()


def confirm_delete(site_id: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, удалить", callback_data=f"del_{site_id}")
    kb.button(text="❌ Отмена", callback_data=f"open_{site_id}")
    kb.adjust(2)
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

def sort_choice() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🆕 New", callback_data="sort_new")
    kb.button(text="🔥 Hot", callback_data="sort_hot")
    kb.button(text="🏆 Top", callback_data="sort_top")
    kb.button(text="⬅️ Отмена", callback_data="back_main")
    kb.adjust(3)
    return kb.as_markup()


def period_choice() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="За час", callback_data="period_hour")
    kb.button(text="За день", callback_data="period_day")
    kb.button(text="За неделю", callback_data="period_week")
    kb.button(text="За месяц", callback_data="period_month")
    kb.button(text="За год", callback_data="period_year")
    kb.button(text="За всё время", callback_data="period_all")
    kb.button(text="⬅️ Отмена", callback_data="back_main")
    kb.adjust(2)
    return kb.as_markup()

def limit_choice() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for n in (10, 25, 50, 100, 200):
        kb.button(text=f"{n}", callback_data=f"limit_{n}")
    kb.button(text="♾ Все", callback_data="limit_0")
    kb.button(text="⬅️ Отмена", callback_data="back_main")
    kb.adjust(3)
    return kb.as_markup()

def limit_choice_for_site(site_id: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for n in (10, 25, 50, 100, 200):
        kb.button(text=f"{n}", callback_data=f"chlimit_{site_id}_{n}")
    kb.button(text="♾ Все", callback_data=f"chlimit_{site_id}_0")
    kb.button(text="⬅️ Назад", callback_data=f"open_{site_id}")
    kb.adjust(3)
    return kb.as_markup()

def hours_choice_for_site(site_id: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for h in (1, 3, 6, 12, 24):
        kb.button(text=f"{h} ч", callback_data=f"chhours_{site_id}_{h}")
    kb.button(text="⬅️ Назад", callback_data=f"open_{site_id}")
    kb.adjust(3)
    return kb.as_markup()
