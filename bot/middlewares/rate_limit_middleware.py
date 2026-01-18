"""
Middleware для rate limiting запросов от пользователей

Защищает от блокировок Telegram API:
- Ограничение количества запросов на пользователя
- Защита от слишком частых команд
"""

import time
from typing import Any, Awaitable, Callable, Dict
from collections import defaultdict
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from error_logger import log_warning

# Настройки rate limiting
MAX_REQUESTS_PER_MINUTE = 10  # Максимум запросов в минуту на пользователя
MAX_REQUESTS_PER_HOUR = 50  # Максимум запросов в час на пользователя
COMMAND_COOLDOWN = 2  # Минимальная задержка между командами в секундах

# Хранилище запросов пользователей
_user_requests: Dict[int, list] = defaultdict(list)  # {user_id: [timestamps]}
_user_last_command: Dict[int, float] = {}  # {user_id: last_command_timestamp}


class RateLimitMiddleware(BaseMiddleware):
    """Middleware для ограничения частоты запросов от пользователей"""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Получаем user_id из события
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None

        if not user_id:
            # Если не удалось определить пользователя, пропускаем
            return await handler(event, data)

        current_time = time.time()

        # Проверка cooldown между командами
        if user_id in _user_last_command:
            time_since_last = current_time - _user_last_command[user_id]
            if time_since_last < COMMAND_COOLDOWN:
                wait_time = COMMAND_COOLDOWN - time_since_last
                log_warning(
                    "rate_limit",
                    f"Пользователь {user_id} слишком часто отправляет команды. Ожидание {wait_time:.1f} сек",
                )

                # Отвечаем пользователю о необходимости подождать
                if isinstance(event, Message):
                    try:
                        await event.answer(
                            f"⏳ Пожалуйста, подождите {wait_time:.0f} секунд перед следующей командой.",
                            show_alert=False,
                        )
                    except Exception:
                        pass  # Игнорируем ошибки отправки
                elif isinstance(event, CallbackQuery):
                    try:
                        await event.answer(f"⏳ Подождите {wait_time:.0f} сек", show_alert=False)
                    except Exception:
                        pass

                return  # Блокируем обработку

        # Очистка старых запросов (старше часа)
        one_hour_ago = current_time - 3600
        _user_requests[user_id] = [ts for ts in _user_requests[user_id] if ts > one_hour_ago]

        # Проверка лимита в час
        requests_last_hour = len(_user_requests[user_id])
        if requests_last_hour >= MAX_REQUESTS_PER_HOUR:
            log_warning(
                "rate_limit",
                f"Пользователь {user_id} превысил лимит запросов в час ({requests_last_hour}/{MAX_REQUESTS_PER_HOUR})",
            )

            if isinstance(event, Message):
                try:
                    await event.answer(
                        "⚠️ Вы превысили лимит запросов. Попробуйте позже.", show_alert=False
                    )
                except Exception:
                    pass
            elif isinstance(event, CallbackQuery):
                try:
                    await event.answer("⚠️ Лимит запросов превышен", show_alert=True)
                except Exception:
                    pass

            return  # Блокируем обработку

        # Проверка лимита в минуту
        one_minute_ago = current_time - 60
        requests_last_minute = len([ts for ts in _user_requests[user_id] if ts > one_minute_ago])

        if requests_last_minute >= MAX_REQUESTS_PER_MINUTE:
            log_warning(
                "rate_limit",
                f"Пользователь {user_id} превысил лимит запросов в минуту ({requests_last_minute}/{MAX_REQUESTS_PER_MINUTE})",
            )

            if isinstance(event, Message):
                try:
                    await event.answer(
                        "⏳ Слишком много запросов. Подождите минуту.", show_alert=False
                    )
                except Exception:
                    pass
            elif isinstance(event, CallbackQuery):
                try:
                    await event.answer("⏳ Подождите минуту", show_alert=False)
                except Exception:
                    pass

            return  # Блокируем обработку

        # Регистрируем запрос
        _user_requests[user_id].append(current_time)
        _user_last_command[user_id] = current_time

        # Вызываем обработчик
        return await handler(event, data)
