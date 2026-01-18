"""
Инициализация и настройка Telegram бота
"""

import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from database import init_database

# Импортируем handlers
from bot.handlers import help, search, start

# Импортируем middleware
from bot.middlewares.error_middleware import ErrorMiddleware
from bot.middlewares.rate_limit_middleware import RateLimitMiddleware
from bot.middlewares.spam_protection_middleware import SpamProtectionMiddleware

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
    # 3. Error handling (последним, для логирования ошибок)

    # Rate limiting
    dp.message.middleware(RateLimitMiddleware())
    dp.callback_query.middleware(RateLimitMiddleware())

    # Spam protection
    dp.message.middleware(SpamProtectionMiddleware())
    dp.callback_query.middleware(SpamProtectionMiddleware())

    # Error handling
    dp.message.middleware(ErrorMiddleware())
    dp.callback_query.middleware(ErrorMiddleware())

    # Регистрируем routers из новой структуры
    dp.include_router(start.router)
    dp.include_router(search.router)
    dp.include_router(help.router)

    # Временно импортируем старый router для сохранения функциональности
    # Постепенно перенесем все обработчики в новые модули
    # Импортируем router из старого bot.py (он находится в корне проекта)
    try:
        # Импортируем router из старого bot.py напрямую
        # Используем абсолютный импорт из корня проекта
        import importlib.util
        import sys
        import os

        # Получаем путь к старому bot.py
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bot_path = os.path.join(root_dir, "bot.py")

        if os.path.exists(bot_path):
            # Загружаем модуль динамически
            spec = importlib.util.spec_from_file_location("bot_legacy", bot_path)
            bot_legacy = importlib.util.module_from_spec(spec)
            sys.modules["bot_legacy"] = bot_legacy
            spec.loader.exec_module(bot_legacy)

            # Регистрируем старый router
            if hasattr(bot_legacy, "router"):
                dp.include_router(bot_legacy.router)
                logger.info("Старый router зарегистрирован для обратной совместимости")
    except Exception as e:
        logger.warning(f"Не удалось импортировать старый router: {e}")
        # Продолжаем работу без старого router - используем только новую структуру

    # Инициализация базы данных
    await init_database()

    return bot, dp
