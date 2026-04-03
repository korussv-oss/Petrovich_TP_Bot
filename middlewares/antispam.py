"""
Защита от спама: ограничение частоты запросов по user_id (throttling).
Если пользователь шлёт сообщения/нажатия чаще заданного интервала — запрос не обрабатывается.
"""
import time
from typing import Callable, Dict, Any, Awaitable, Optional

from aiogram import BaseMiddleware
from aiogram.types import Update

# Интервал в секундах между запросами от одного пользователя
DEFAULT_COOLDOWN = 0.75

_CLEANUP_INTERVAL = 300.0  # чистим словарь каждые 5 минут


def _get_user_id(update: Update) -> Optional[int]:
    if update.message and update.message.from_user:
        return update.message.from_user.id
    if update.callback_query and update.callback_query.from_user:
        return update.callback_query.from_user.id
    if update.inline_query and update.inline_query.from_user:
        return update.inline_query.from_user.id
    if update.edited_message and update.edited_message.from_user:
        return update.edited_message.from_user.id
    return None


class AntispamMiddleware(BaseMiddleware):
    """Пропускает событие только если прошло не менее cooldown секунд с предыдущего от этого user_id."""

    def __init__(self, cooldown: float = DEFAULT_COOLDOWN):
        self.cooldown = cooldown
        self._throttle: Dict[int, float] = {}
        self._last_cleanup: float = time.monotonic()

    def _maybe_cleanup(self, now: float) -> None:
        """Периодически удаляет давно неактивных пользователей из словаря throttle.

        Без очистки словарь растёт бесконечно по мере появления новых уникальных user_id,
        что приводит к медленной утечке памяти за время работы бота.
        """
        if now - self._last_cleanup < _CLEANUP_INTERVAL:
            return
        cutoff = now - self.cooldown * 20
        self._throttle = {uid: ts for uid, ts in self._throttle.items() if ts > cutoff}
        self._last_cleanup = now

    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any],
    ) -> Any:
        user_id = _get_user_id(event)
        if user_id is None:
            return await handler(event, data)

        now = time.monotonic()
        self._maybe_cleanup(now)

        last = self._throttle.get(user_id, 0.0)
        if now - last < self.cooldown:
            # Слишком частый запрос — не вызываем handler
            if event.callback_query:
                await event.callback_query.answer(
                    "⏳ Слишком частые нажатия. Подождите немного.",
                    show_alert=False,
                )
            # Для сообщений не отвечаем, чтобы не поощрять спам
            return None

        self._throttle[user_id] = now
        return await handler(event, data)
