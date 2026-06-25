from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from keyboards.inline import main_menu, sites_list, back_button
from handlers.commands import AddSite
from services import storage
from services.scheduler import schedule_site, run_site_check

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

    storage.add_site(call.from_user.id, url, hours)
    index = len(storage.list_sites(call.from_user.id)) - 1
    schedule_site(call.bot, call.from_user.id, index, hours)

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
            "📋 Твои сайты (нажми, чтобы удалить):",
            reply_markup=sites_list(sites))
    await call.answer()


@router.callback_query(F.data.startswith("del_"))
async def delete_site(call: CallbackQuery):
    index = int(call.data.split("_")[1])
    storage.remove_site(call.from_user.id, index)
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
    for i in range(len(sites)):
        await run_site_check(call.bot, call.from_user.id, i)
