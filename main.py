"""
from error_logger import log_info, log_error
from error_logger import log_info, log_warning, log_error
from database import ensure_turso_tables_exist
from error_logger import log_info, log_warning
from ai_valuator import get_valuator
from error_logger import log_error
from database import update_cached_listings_daily_turso
from database import get_active_users
from database_turso import get_user_filters_turso, has_valid_user_filters

Главный файл запуска бота мониторинга квартир

Entrypoint для приложения:
1. Загружает конфигурацию
2. Инициализирует базу данных
3. Запускает Telegram-бот
"""
# ВАЖНО: Импортируем error_logger ПЕРВЫМ, чтобы настроить логирование
# Это гарантирует, что логи идут в stdout/stderr для Railway
import error_logger

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from bot.app import create_bot
from bot.services.search_service import check_new_listings
from config import CHECK_INTERVAL, BOT_TOKEN, USE_TURSO_CACHE
from database import init_database, clear_old_listings


def setup_logging():
    """
    Настройка логирования
    
    Примечание: Основное логирование настраивается в error_logger.py при импорте
    Здесь просто получаем логгер для main.py
    """
    # error_logger уже импортирован в начале файла и настроил логирование
    # Все логи идут в stdout/stderr (для Railway) и опционально в файл
    
    # Создаем отдельный логгер для main.py
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    
    # НЕ добавляем дополнительные handlers здесь, т.к. это может конфликтовать
    # Все логи уже настроены в error_logger.py
    
    return logger


def load_config():
    """Загружает и проверяет конфигурацию"""
    
    # Конфигурация загружается автоматически при импорте config
    # Проверяем наличие обязательных параметров
    if not BOT_TOKEN:
        log_error("main", "❌ BOT_TOKEN не установлен!")
        log_error("main", "Создайте файл .env и укажите BOT_TOKEN")
        log_error("main", "Получить токен можно у @BotFather в Telegram")
        return False
    
    log_info("main", "✅ Конфигурация загружена")
    return True


async def initialize_database():
    """Инициализирует базу данных"""
    
    # Инициализация основной базы данных
    try:
        await init_database()
        log_info("main", "✅ База данных инициализирована")
    except Exception as e:
        log_error("main", "Ошибка инициализации базы данных", e)
        raise
    
    # Инициализация Turso (если включено)
    if USE_TURSO_CACHE:
        try:
            await ensure_turso_tables_exist()
            log_info("main", "✅ Turso кэш инициализирован")
        except Exception as e:
            log_warning("main", f"⚠️ Не удалось инициализировать Turso: {e}")
            log_warning("main", "💡 Проверьте переменные окружения TURSO_DB_URL и TURSO_AUTH_TOKEN")
    
    return True


def check_ai_valuator():
    """Проверяет доступность ИИ-оценщика"""
    
    try:
        valuator = get_valuator()
        if valuator:
            provider_name = valuator.provider.upper()
            if provider_name == "GEMINI":
                log_info("main", f"🤖 ИИ-оценщик настроен: {provider_name} (с анализом фото)")
            else:
                log_info("main", f"🤖 ИИ-оценщик настроен: {provider_name} (без анализа фото)")
        else:
            log_info("main", "⚠️ ИИ-оценщик не настроен (GEMINI_API_KEY не указан)")
            log_info("main", "💡 Рекомендуется использовать Gemini для анализа фотографий квартир")
    except Exception as e:
        log_warning("main", f"⚠️ ИИ-оценщик недоступен: {e}")


logger = setup_logging()


async def run_search_once(bot):
    """Одноразовая проверка объявлений (запускается при старте)"""
    
    log_info("main", "🔍 Запуск первоначальной проверки объявлений...")
    try:
        await check_new_listings(bot)
    except Exception as e:
        log_error("main", "Ошибка при первоначальной проверке", e)


async def scheduled_check(bot):
    """Запланированная проверка объявлений (для периодических запусков)"""
    
    log_info("scheduler", f"Запуск плановой проверки: {datetime.now()}")
    try:
        await check_new_listings(bot)
    except Exception as e:
        log_error("scheduler", "Ошибка при плановой проверке", e)


async def cleanup_old_records():
    """Очистка старых записей"""
    
    log_info("scheduler", "Очистка старых записей...")
    try:
        await clear_old_listings(days=30)
    except Exception as e:
        log_error("scheduler", "Ошибка при очистке старых записей", e)


async def main():
    """Главная функция запуска - entrypoint приложения"""
    
    logger.info("=" * 50)
    logger.info("🏠 Запуск бота мониторинга квартир")
    logger.info("=" * 50)
    
    # Шаг 1: Загрузка конфигурации
    logger.info("📋 Шаг 1: Загрузка конфигурации...")
    if not load_config():
        logger.error("❌ Не удалось загрузить конфигурацию")
        sys.exit(1)
    
    # Шаг 2: Инициализация базы данных
    logger.info("💾 Шаг 2: Инициализация базы данных...")
    await initialize_database()
    
    # Шаг 3: Проверка ИИ-оценщика (опционально)
    logger.info("🤖 Шаг 3: Проверка ИИ-оценщика...")
    check_ai_valuator()
    
    # Шаг 4: Создание и запуск Telegram-бота
    logger.info("🤖 Шаг 4: Создание Telegram-бота...")
    bot, dp = await create_bot()
    logger.info("✅ Бот создан")
    
    # Настройка планировщика
    scheduler = AsyncIOScheduler()
    
    # Проверка объявлений каждые N минут (30 минут по умолчанию)
    # Задержка первого запуска на 2 минуты, чтобы пользователь успел нажать /start
    scheduler.add_job(
        scheduled_check,
        trigger=IntervalTrigger(
            minutes=CHECK_INTERVAL,
            start_date=datetime.now(timezone.utc) + timedelta(minutes=2)
        ),
        args=[bot],
        id='check_listings',
        name='Проверка новых объявлений',
        replace_existing=True
    )
    
    # Очистка старых записей раз в день
    scheduler.add_job(
        cleanup_old_records,
        trigger=IntervalTrigger(days=1),
        id='cleanup',
        name='Очистка старых записей',
        replace_existing=True
    )
    
    # Ежедневное обновление кэша Turso (если включено)
    if USE_TURSO_CACHE:
        async def update_turso_cache():
            """Обновление кэша Turso"""
            
            try:
                await update_cached_listings_daily_turso()
            except Exception as e:
                log_error("scheduler", "Ошибка обновления кэша Turso", e)
        
        scheduler.add_job(
            update_turso_cache,
            trigger=IntervalTrigger(days=1),
            id='update_turso_cache',
            name='Обновление кэша Turso',
            replace_existing=True
        )
        logger.info("✅ Задача ежедневного обновления кэша Turso добавлена")
    
    scheduler.start()
    interval_hours = CHECK_INTERVAL / 60
    logger.info(f"✅ Планировщик запущен (интервал: {interval_hours:.1f} часов)")
    
    # Первая проверка при запуске ТОЛЬКО если есть активные пользователи с валидными фильтрами
    # Это предотвращает запуск поиска до того, как пользователь нажмет /start
    async def check_and_run_search():
        
        active_users = await get_active_users()
        if not active_users:
            logger.info("[startup] Нет активных пользователей, пропускаю initial search")
            return
        
        # Проверяем, что у всех активных пользователей есть валидные фильтры
        users_without_filters = []
        for user_id in active_users:
            filters = await get_user_filters_turso(user_id)
            if not has_valid_user_filters(filters):
                users_without_filters.append(user_id)
        
        if users_without_filters:
            logger.error(
                f"[startup] ❌ Active users without valid filters: {users_without_filters}. "
                f"Skipping initial search."
            )
            return
        
        logger.info(f"[startup] Найдено {len(active_users)} активных пользователей с фильтрами")
        # ❌ ВРЕМЕННО КОММЕНТИРУЕМ initial search
        # Пока не убедимся, что:
        # - /start работает
        # - users и user_filters консистентны
        # await run_search_once(bot)
    
    asyncio.create_task(check_and_run_search())
    
    # Запуск бота
    logger.info("🚀 Бот запущен и готов к работе!")
    logger.info("📱 Бот работает в режиме личных сообщений")
    
    try:
        # Пробуем запустить polling с обработкой конфликтов
        retry_count = 0
        max_retries = 3
        
        while retry_count < max_retries:
            try:
                await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
                break  # Успешный запуск
            except Exception as e:
                error_msg = str(e).lower()
                if "conflict" in error_msg or "getupdates" in error_msg:
                    retry_count += 1
                    if retry_count < max_retries:
                        wait_time = retry_count * 5
                        logger.warning(f"⚠️ Конфликт с другим экземпляром бота. Ожидание {wait_time} секунд перед повтором...")
                        logger.warning("💡 Убедитесь, что только один экземпляр бота запущен!")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error("❌ Не удалось запустить бота из-за конфликта с другим экземпляром")
                        logger.error("🔧 Решение:")
                        logger.error("   1. Остановите все другие экземпляры бота")
                        logger.error("   2. Проверьте, не запущен ли бот на другом сервере")
                        logger.error("   3. Используйте скрипт: ./stop_bot.sh")
                        raise
                else:
                    # Другая ошибка - пробрасываем дальше
                    raise
    finally:
        scheduler.shutdown()
        if bot.session:
            try:
                await bot.session.close()
            except Exception as e:
                logger.warning(f"Ошибка при закрытии сессии: {e}")
        logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    """Entrypoint приложения"""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Получен сигнал остановки")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)

