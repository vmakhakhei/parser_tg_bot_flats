"""
Middleware для обработки ошибок
"""

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from error_logger import error_logger

logger = logging.getLogger(__name__)


def sanitize_event_info(event: TelegramObject) -> str:
    """
    Извлекает безопасную информацию о событии для логирования

    Не логирует:
    - Текст сообщений пользователей
    - Персональные данные (username, first_name, last_name, phone)
    - Токены

    Логирует только:
    - Тип события
    - ID пользователя (безопасно)
    - ID чата (безопасно)
    """
    if isinstance(event, Message):
        user_id = event.from_user.id if event.from_user else None
        chat_id = event.chat.id if event.chat else None
        return f"Message from user_id={user_id}, chat_id={chat_id}"
    elif isinstance(event, CallbackQuery):
        user_id = event.from_user.id if event.from_user else None
        return f"CallbackQuery from user_id={user_id}"
    else:
        return f"{type(event).__name__}"


class ErrorMiddleware(BaseMiddleware):
    """Middleware для перехвата и логирования ошибок"""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception as e:
            # Фильтруем ожидаемые ошибки - не логируем как ошибки
            exception_type_name = type(e).__name__
            
            # TelegramForbiddenError - ожидаемая ошибка (пользователь заблокировал бота)
            if exception_type_name == "TelegramForbiddenError":
                event_info = sanitize_event_info(event)
                error_logger.log_info(
                    "middleware",
                    f"Пользователь заблокировал бота ({event_info})"
                )
                # Пробрасываем ошибку дальше, но не логируем как ошибку
                raise
            
            # Логируем только безопасную информацию о событии
            event_info = sanitize_event_info(event)
            error_logger.log_error(
                "middleware",
                f"Ошибка в обработчике ({event_info}): {type(e).__name__}",
                e,
                exc_info=True,
            )
            # Пробрасываем ошибку дальше, чтобы aiogram мог её обработать
            raise
