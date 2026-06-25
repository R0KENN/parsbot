from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from keyboards.inline import main_menu, hours_choice

router = Router()


class AddSite(StatesGroup):
    waiting_url = State()
    waiting_hours = State()
    waiting_sort = State()
    waiting_period = State()


@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я скачиваю медиа с сайтов по расписанию.\n\n"
        "Добавь сайт, выбери интервал — и я буду присылать новые фото и видео.",
        reply_markup=main_menu(),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer("Главное меню:", reply_markup=main_menu())


# Шаг 1: пользователь прислал ссылку
@router.message(StateFilter(AddSite.waiting_url))
async def receive_url(message: Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith(("http://", "https://")):
        await message.answer("❌ Это не похоже на ссылку. Пришли URL целиком.")
        return
    await state.update_data(url=url)
    await state.set_state(AddSite.waiting_hours)
    await message.answer("⏱ Как часто проверять сайт?",
                         reply_markup=hours_choice())
