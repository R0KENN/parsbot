from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from keyboards.inline import (
    main_menu, sites_list, back_button, site_menu, confirm_delete,
    sort_choice, period_choice, limit_choice, limit_choice_for_site,
    hours_choice_for_site
)
from handlers.commands import AddSite
from services import storage, reddit
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


async def _finish_add(call, state):
    """Сохраняет сайт из данных FSM и заводит расписание."""
    data = await state.get_data()
    url = data["url"]
    hours = data["hours"]
    sort = data.get("sort", "new")
    period = data.get("period", "day")
    limit = data.get("limit", 200)

    site_id = storage.add_site(
        call.from_user.id, url, hours, sort, period, limit)
    schedule_site(call.bot, call.from_user.id, site_id, hours)
    await state.clear()

    limit_txt = "все" if limit == 0 else str(limit)
    if reddit.is_reddit_url(url):
        extra = f"\nЛента: {sort}" + (f" ({period})" if sort == "top" else "")
    else:
        extra = ""
    await call.message.edit_text(
        f"✅ Добавлено!\nПроверка каждые {hours} ч.\n"
        f"Медиа за раз: {limit_txt}{extra}",
        reply_markup=main_menu())


@router.callback_query(F.data.startswith("hours_"))
async def set_hours(call: CallbackQuery, state: FSMContext):
    hours = int(call.data.split("_")[1])
    data = await state.get_data()
    url = data.get("url")
    if not url:
        await call.answer("Сначала добавь ссылку", show_alert=True)
        return
    await state.update_data(hours=hours)

    # После интервала спрашиваем, сколько медиа присылать за раз
    await state.set_state(AddSite.waiting_limit)
    await call.message.edit_text(
        "🔢 Сколько новых медиа присылать за одну проверку?\n"
        "«Все» — пришлёт всё новое по очереди.",
        reply_markup=limit_choice())
    await call.answer()


@router.callback_query(F.data.startswith("limit_"))
async def set_limit(call: CallbackQuery, state: FSMContext):
    limit = int(call.data.split("_")[1])   # 0 = все
    await state.update_data(limit=limit)
    data = await state.get_data()
    url = data.get("url")

    # Для Reddit спрашиваем тип ленты, для обычных сайтов — сразу сохраняем
    if reddit.is_reddit_url(url):
        await state.set_state(AddSite.waiting_sort)
        await call.message.edit_text(
            "📊 Какую ленту отслеживать?", reply_markup=sort_choice())
    else:
        await _finish_add(call, state)
    await call.answer()


@router.callback_query(F.data.startswith("sort_"))
async def set_sort(call: CallbackQuery, state: FSMContext):
    sort = call.data.split("_")[1]   # new / hot / top
    await state.update_data(sort=sort)

    if sort == "top":
        await state.set_state(AddSite.waiting_period)
        await call.message.edit_text(
            "📅 За какой период брать топ?", reply_markup=period_choice())
    else:
        await _finish_add(call, state)
    await call.answer()


@router.callback_query(F.data.startswith("period_"))
async def set_period(call: CallbackQuery, state: FSMContext):
    period = call.data.split("_")[1]  # hour/day/week/month/year/all
    await state.update_data(period=period)
    await _finish_add(call, state)
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

    limit = site.get("limit", 200)
    limit_txt = "все" if limit == 0 else str(limit)
    info = (
        f"⚙️ Настройки:\n\n"
        f"🔗 {site['url']}\n"
        f"⏱ Проверка каждые {site['hours']} ч\n"
        f"🔢 Медиа за раз: {limit_txt}\n"
        f"📦 Скачано медиа: {len(site['seen'])}"
    )
    if reddit.is_reddit_url(site["url"]):
        sort = site.get("sort", "new")
        info += f"\n📊 Лента: {sort}"
        if sort == "top":
            info += f" ({site.get('period', 'day')})"

    await call.message.edit_text(info, reply_markup=site_menu(site_id))
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

# Открыть выбор нового лимита
@router.callback_query(F.data.startswith("setlimit_"))
async def set_limit_menu(call: CallbackQuery):
    site_id = call.data.split("_", 1)[1]
    site = storage.get_site(call.from_user.id, site_id)
    if site is None:
        await call.answer("Сайт не найден", show_alert=True)
        return
    await call.message.edit_text(
        "🔢 Сколько новых медиа присылать за одну проверку?\n"
        "«Все» — пришлёт всё новое по очереди.",
        reply_markup=limit_choice_for_site(site_id),
    )
    await call.answer()


# Сохранить новый лимит
@router.callback_query(F.data.startswith("chlimit_"))
async def change_limit(call: CallbackQuery):
    # формат: chlimit_<site_id>_<число>
    _, site_id, value = call.data.split("_", 2)
    limit = int(value)
    ok = storage.update_limit(call.from_user.id, site_id, limit)
    if not ok:
        await call.answer("Сайт не найден", show_alert=True)
        return

    site = storage.get_site(call.from_user.id, site_id)
    limit_txt = "все" if limit == 0 else str(limit)

    info = (
        f"⚙️ Настройки:\n\n"
        f"🔗 {site['url']}\n"
        f"⏱ Проверка каждые {site['hours']} ч\n"
        f"🔢 Медиа за раз: {limit_txt}\n"
        f"📦 Скачано медиа: {len(site['seen'])}"
    )
    if reddit.is_reddit_url(site["url"]):
        sort = site.get("sort", "new")
        info += f"\n📊 Лента: {sort}"
        if sort == "top":
            info += f" ({site.get('period', 'day')})"

    await call.message.edit_text(info, reply_markup=site_menu(site_id))
    await call.answer(f"Лимит изменён: {limit_txt}")

# Открыть выбор нового интервала
@router.callback_query(F.data.startswith("sethours_"))
async def set_hours_menu(call: CallbackQuery):
    site_id = call.data.split("_", 1)[1]
    site = storage.get_site(call.from_user.id, site_id)
    if site is None:
        await call.answer("Сайт не найден", show_alert=True)
        return
    await call.message.edit_text(
        "⏱ Как часто проверять этот сайт?",
        reply_markup=hours_choice_for_site(site_id),
    )
    await call.answer()


# Сохранить новый интервал и пересоздать задачу планировщика
@router.callback_query(F.data.startswith("chhours_"))
async def change_hours(call: CallbackQuery):
    # формат: chhours_<site_id>_<часы>
    _, site_id, value = call.data.split("_", 2)
    hours = int(value)
    ok = storage.update_hours(call.from_user.id, site_id, hours)
    if not ok:
        await call.answer("Сайт не найден", show_alert=True)
        return

    # пересоздаём задачу с новым интервалом (replace_existing=True внутри)
    schedule_site(call.bot, call.from_user.id, site_id, hours)

    site = storage.get_site(call.from_user.id, site_id)
    limit = site.get("limit", 200)
    limit_txt = "все" if limit == 0 else str(limit)

    info = (
        f"⚙️ Настройки:\n\n"
        f"🔗 {site['url']}\n"
        f"⏱ Проверка каждые {site['hours']} ч\n"
        f"🔢 Медиа за раз: {limit_txt}\n"
        f"📦 Скачано медиа: {len(site['seen'])}"
    )
    if reddit.is_reddit_url(site["url"]):
        sort = site.get("sort", "new")
        info += f"\n📊 Лента: {sort}"
        if sort == "top":
            info += f" ({site.get('period', 'day')})"

    await call.message.edit_text(info, reply_markup=site_menu(site_id))
    await call.answer(f"Интервал изменён: {hours} ч")

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
