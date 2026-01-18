"""
Главный файл запуска бота мониторинга квартир

Entrypoint для приложения:
1. Загружает конфигурацию
2. Инициализирует базу данных
3. Запускает Telegram-бот
"""
import asyncio
import logging
import sys
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from bot.app import create_bot
from bot.services.search_service import check_new_listings
from config import CHECK_INTERVAL, BOT_TOKEN, USE_TURSO_CACHE
from database import init_database, clear_old_listings


def setup_logging():
    """
    Настройка логирования
    
    Примечание: Основное логирование настраивается в error_logger.py
    Здесь настраиваем только дополнительный логгер для main.py
    """
    # Импортируем error_logger, чтобы он инициализировал систему логирования
    import error_logger
    
    # Создаем отдельный логгер для main.py
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    
    # Также сохраняем логи в bot.log для обратной совместимости
    # (основные логи идут в logs/app.log через error_logger)
    try:
        file_handler = logging.FileHandler('bot.log', encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception:
        # Если не удалось создать файл, продолжаем без него
        pass
    
    return logger


def load_config():
    """Загружает и проверяет конфигурацию"""
    from error_logger import log_info, log_error
    
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
    from error_logger import log_info, log_warning, log_error
    
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
            from database import ensure_turso_tables_exist
            await ensure_turso_tables_exist()
            log_info("main", "✅ Turso кэш инициализирован")
        except Exception as e:
            log_warning("main", f"⚠️ Не удалось инициализировать Turso: {e}")
            log_warning("main", "💡 Проверьте переменные окружения TURSO_DB_URL и TURSO_AUTH_TOKEN")
    
    return True


def check_ai_valuator():
    """Проверяет доступность ИИ-оценщика"""
    from error_logger import log_info, log_warning
    
    try:
        from ai_valuator import get_valuator
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


async def scheduled_check(bot):
    """Запланированная проверка объявлений"""
    from error_logger import log_info, log_error
    
    log_info("scheduler", f"Запуск плановой проверки: {datetime.now()}")
    try:
        await check_new_listings(bot)
    except Exception as e:
        log_error("scheduler", "Ошибка при плановой проверке", e)


async def cleanup_old_records():
    """Очистка старых записей"""
    from error_logger import log_info, log_error
    
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
    
    # Проверка объявлений каждые N минут (12 часов = 720 минут)
    scheduler.add_job(
        scheduled_check,
        trigger=IntervalTrigger(minutes=CHECK_INTERVAL),
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
            from error_logger import log_error
            
            try:
                from database import update_cached_listings_daily_turso
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
    
    # Первая проверка при запуске
    logger.info("🔍 Первоначальная проверка объявлений...")
    await check_new_listings(bot)
    
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

