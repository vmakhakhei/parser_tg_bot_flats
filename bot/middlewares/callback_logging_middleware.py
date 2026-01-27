"""
Middleware для логирования callback-нажатий
"""
import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, CallbackQuery

logger = logging.getLogger("bot_callbacks")


class CallbackLoggingMiddleware(BaseMiddleware):
    """Middleware для логирования всех callback-нажатий"""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Логируем только callback-запросы
        if isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
            callback_data = event.data if hasattr(event, 'data') else None
            
            logger.info(
                f"[CB_CLICK] user={user_id} data={callback_data}"
            )
        
        # Продолжаем обработку
        return await handler(event, data)
