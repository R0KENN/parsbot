from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from keyboards.inline import (
    main_menu, sites_list, back_button, site_menu, confirm_delete
)
from handlers.commands import AddSite
from services import storage
from services.scheduler import schedule_site, run_site_check, unschedule_site

router = Router()


@router.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Главное меню:", reply_markup=main_menu())
    await call.answer()


@router.callback_query(F.data == "add_site")
async def add_site(call: CallbackQuery, state: FSMContext):
    await state.set_state(AddSite.waiting_url)
    await call.message.edit_text(
        "🔗 Пришли ссылку на страницу с медиа:", reply_markup=back_button())
    await call.answer()


@router.callback_query(F.data.startswith("hours_"))
async def set_hours(call: CallbackQuery, state: FSMContext):
    hours = int(call.data.split("_")[1])
    data = await state.get_data()
    url = data.get("url")
    if not url:
        await call.answer("Сначала добавь ссылку", show_alert=True)
        return

    site_id = storage.add_site(call.from_user.id, url, hours)
    schedule_site(call.bot, call.from_user.id, site_id, hours)

    await state.clear()
    await call.message.edit_text(
        f"✅ Сайт добавлен!\nПроверка каждые {hours} ч.",
        reply_markup=main_menu())
    await call.answer()


@router.callback_query(F.data == "list_sites")
async def list_sites_cb(call: CallbackQuery):
    sites = storage.list_sites(call.from_user.id)
    if not sites:
        await call.message.edit_text("📭 Список пуст.", reply_markup=main_menu())
    else:
        await call.message.edit_text(
            "📋 Твои сайты (нажми для настроек):",
            reply_markup=sites_list(sites))
    await call.answer()


# Открыть карточку одного сайта
@router.callback_query(F.data.startswith("open_"))
async def open_site(call: CallbackQuery):
    site_id = call.data.split("_", 1)[1]
    site = storage.get_site(call.from_user.id, site_id)
    if site is None:
        await call.answer("Сайт не найден", show_alert=True)
        return
    await call.message.edit_text(
        f"⚙️ Настройки сайта:\n\n"
        f"🔗 {site['url']}\n"
        f"⏱ Проверка каждые {site['hours']} ч\n"
        f"📦 Скачано медиа: {len(site['seen'])}",
        reply_markup=site_menu(site_id),
    )
    await call.answer()


# Проверить один конкретный сайт
@router.callback_query(F.data.startswith("checkone_"))
async def check_one(call: CallbackQuery):
    site_id = call.data.split("_", 1)[1]
    site = storage.get_site(call.from_user.id, site_id)
    if site is None:
        await call.answer("Сайт не найден", show_alert=True)
        return
    await call.answer("🔄 Проверяю этот сайт…")
    await run_site_check(call.bot, call.from_user.id, site_id)


# Спросить подтверждение удаления
@router.callback_query(F.data.startswith("askdel_"))
async def ask_delete(call: CallbackQuery):
    site_id = call.data.split("_", 1)[1]
    site = storage.get_site(call.from_user.id, site_id)
    if site is None:
        await call.answer("Сайт не найден", show_alert=True)
        return
    await call.message.edit_text(
        f"❓ Удалить этот сайт?\n\n{site['url']}",
        reply_markup=confirm_delete(site_id),
    )
    await call.answer()


# Подтверждённое удаление
@router.callback_query(F.data.startswith("del_"))
async def delete_site(call: CallbackQuery):
    site_id = call.data.split("_", 1)[1]
    storage.remove_site(call.from_user.id, site_id)
    unschedule_site(call.from_user.id, site_id)
    sites = storage.list_sites(call.from_user.id)
    if sites:
        await call.message.edit_text(
            "📋 Твои сайты:", reply_markup=sites_list(sites))
    else:
        await call.message.edit_text("📭 Список пуст.", reply_markup=main_menu())
    await call.answer("Удалено")


@router.callback_query(F.data == "check_now")
async def check_now(call: CallbackQuery):
    sites = storage.list_sites(call.from_user.id)
    if not sites:
        await call.answer("Сначала добавь сайт", show_alert=True)
        return
    await call.answer("🔄 Проверяю...")
    # копируем id заранее: список может измениться во время длинной проверки
    for site_id in [s["id"] for s in sites]:
        await run_site_check(call.bot, call.from_user.id, site_id)
