"""
Инициализация и настройка Telegram бота
"""

import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, ADMIN_TELEGRAM_IDS
from database import init_database

# Импортируем handlers
from bot.handlers import help, search, start, debug, admin, filters_quick, actions

# Импортируем middleware
from bot.middlewares.error_middleware import ErrorMiddleware
from bot.middlewares.rate_limit_middleware import RateLimitMiddleware
from bot.middlewares.spam_protection_middleware import SpamProtectionMiddleware
from bot.middlewares.callback_logging_middleware import CallbackLoggingMiddleware

logger = logging.getLogger(__name__)


async def create_bot() -> tuple[Bot, Dispatcher]:
    """Создает и настраивает бота"""
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не установлен! Проверьте файл .env")

    # ВАЖНО: Не логируем токен бота нигде в коде
    # BOT_TOKEN используется только для создания Bot объекта
    bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)

    # Создаем FSM storage для состояний
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Регистрируем middleware в правильном порядке:
    # 1. Rate limiting (первым, чтобы блокировать слишком частые запросы)
    # 2. Spam protection (вторым, для защиты от спама)
    # 3. Callback logging (для логирования всех callback-нажатий)
    # 4. Error handling (последним, для логирования ошибок)

    # Rate limiting
    dp.message.middleware(RateLimitMiddleware())
    dp.callback_query.middleware(RateLimitMiddleware())

    # Spam protection
    dp.message.middleware(SpamProtectionMiddleware())
    dp.callback_query.middleware(SpamProtectionMiddleware())

    # Callback logging
    dp.callback_query.middleware(CallbackLoggingMiddleware())

    # Error handling
    dp.message.middleware(ErrorMiddleware())
    dp.callback_query.middleware(ErrorMiddleware())

    # Регистрируем routers из новой структуры
    dp.include_router(start.router)
    dp.include_router(search.router)
    dp.include_router(help.router)
    dp.include_router(debug.router)
    dp.include_router(admin.router)
    dp.include_router(filters_quick.router)
    dp.include_router(actions.router)

    # Старый router из bot.py удален для устранения дублирования хэндлеров
    # Все обработчики должны быть перенесены в соответствующие модули в bot/handlers/

    # Инициализация базы данных
    await init_database()
    
    # Логируем загруженных администраторов
    logger.info(
        "[admin] loaded admin ids: %s",
        ADMIN_TELEGRAM_IDS
    )

    return bot, dp
