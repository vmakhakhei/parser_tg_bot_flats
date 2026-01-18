"""
Middleware для защиты от spam-команд

Защищает от:
- Повторяющихся одинаковых команд
- Слишком частых callback-запросов
- Массовых запросов от одного пользователя
"""

import time
from typing import Any, Awaitable, Callable, Dict
from collections import defaultdict, deque

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from error_logger import log_warning

# Настройки защиты от спама
SAME_COMMAND_COOLDOWN = 5  # Секунд между одинаковыми командами
MAX_CALLBACKS_PER_MINUTE = 20  # Максимум callback-запросов в минуту
SPAM_THRESHOLD = 5  # Количество одинаковых команд подряд для блокировки

# Хранилище последних команд пользователей
_user_last_commands: Dict[int, deque] = defaultdict(lambda: deque(maxlen=SPAM_THRESHOLD + 1))
_user_last_command_time: Dict[int, Dict[str, float]] = defaultdict(
    dict
)  # {user_id: {command: timestamp}}
_user_callbacks_count: Dict[int, list] = defaultdict(list)  # {user_id: [timestamps]}


class SpamProtectionMiddleware(BaseMiddleware):
    """Middleware для защиты от spam-команд"""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Получаем user_id и команду из события
        user_id = None
        command = None

        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            if event.text:
                # Извлекаем команду из текста
                command = event.text.split()[0] if event.text else None
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
            command = event.data if event.data else None

        if not user_id:
            # Если не удалось определить пользователя, пропускаем
            return await handler(event, data)

        current_time = time.time()

        # Защита от повторяющихся одинаковых команд
        if command:
            last_time = _user_last_command_time[user_id].get(command, 0)
            time_since_last = current_time - last_time

            if time_since_last < SAME_COMMAND_COOLDOWN:
                log_warning(
                    "spam_protection",
                    f"Пользователь {user_id} повторяет команду '{command}' слишком часто",
                )

                if isinstance(event, Message):
                    try:
                        await event.answer(
                            f"⏳ Пожалуйста, не повторяйте команду так часто. Подождите {SAME_COMMAND_COOLDOWN - int(time_since_last)} секунд.",
                            show_alert=False,
                        )
                    except Exception:
                        pass
                elif isinstance(event, CallbackQuery):
                    try:
                        await event.answer("⏳ Подождите", show_alert=False)
                    except Exception:
                        pass

                return  # Блокируем обработку

            _user_last_command_time[user_id][command] = current_time

        # Защита от массовых callback-запросов
        if isinstance(event, CallbackQuery):
            # Очистка старых callback-запросов (старше минуты)
            one_minute_ago = current_time - 60
            _user_callbacks_count[user_id] = [
                ts for ts in _user_callbacks_count[user_id] if ts > one_minute_ago
            ]

            # Проверка лимита callback-запросов
            callbacks_count = len(_user_callbacks_count[user_id])
            if callbacks_count >= MAX_CALLBACKS_PER_MINUTE:
                log_warning(
                    "spam_protection",
                    f"Пользователь {user_id} превысил лимит callback-запросов ({callbacks_count}/{MAX_CALLBACKS_PER_MINUTE})",
                )

                try:
                    await event.answer(
                        "⚠️ Слишком много запросов. Подождите минуту.", show_alert=True
                    )
                except Exception:
                    pass

                return  # Блокируем обработку

            # Регистрируем callback-запрос
            _user_callbacks_count[user_id].append(current_time)

        # Защита от повторяющихся одинаковых команд подряд
        if command:
            _user_last_commands[user_id].append(command)

            # Проверяем, не повторяется ли команда слишком часто
            if len(_user_last_commands[user_id]) >= SPAM_THRESHOLD:
                last_commands = list(_user_last_commands[user_id])
                if len(set(last_commands[-SPAM_THRESHOLD:])) == 1:
                    # Все последние команды одинаковые
                    log_warning(
                        "spam_protection",
                        f"Пользователь {user_id} отправляет одинаковые команды подряд: {command}",
                    )

                    if isinstance(event, Message):
                        try:
                            await event.answer(
                                "⚠️ Обнаружена подозрительная активность. Пожалуйста, используйте команды разумно.",
                                show_alert=False,
                            )
                        except Exception:
                            pass
                    elif isinstance(event, CallbackQuery):
                        try:
                            await event.answer("⚠️ Подозрительная активность", show_alert=True)
                        except Exception:
                            pass

                    return  # Блокируем обработку

        # Вызываем обработчик
        return await handler(event, data)
