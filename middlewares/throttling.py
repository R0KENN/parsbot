import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, CallbackQuery

from config import THROTTLE_RATE


class ThrottlingMiddleware(BaseMiddleware):
    """
    Глушит слишком частые действия одного пользователя.
    Защита от двойных кликов по кнопкам и спама командами,
    чтобы не словить flood-бан от Telegram.
    """

    def __init__(self, rate: float = THROTTLE_RATE):
        self.rate = rate
        self._last: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        now = time.monotonic()
        last = self._last.get(user.id, 0.0)

        if now - last < self.rate:
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer("⏳ Не так быстро…", show_alert=False)
                except Exception:
                    pass
            return None

        self._last[user.id] = now
        return await handler(event, data)
