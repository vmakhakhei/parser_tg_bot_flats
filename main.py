"""
Главный файл запуска бота мониторинга квартир
"""
import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from bot import create_bot, check_new_listings
from config import CHECK_INTERVAL, BOT_TOKEN
from database import init_database, clear_old_listings

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


async def scheduled_check(bot):
    """Запланированная проверка объявлений"""
    logger.info(f"Запуск плановой проверки: {datetime.now()}")
    try:
        await check_new_listings(bot)
    except Exception as e:
        logger.error(f"Ошибка при плановой проверке: {e}")


async def cleanup_old_records():
    """Очистка старых записей"""
    logger.info("Очистка старых записей...")
    await clear_old_listings(days=30)


async def main():
    """Главная функция запуска"""
    
    # Проверка конфигурации
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не установлен!")
        logger.error("Создайте файл .env и укажите BOT_TOKEN")
        logger.error("Получить токен можно у @BotFather в Telegram")
        return
    
    logger.info("=" * 50)
    logger.info("🏠 Запуск бота мониторинга квартир")
    logger.info("=" * 50)
    
    # Инициализация базы данных
    await init_database()
    logger.info("✅ База данных инициализирована")
    
    # Проверка ИИ-оценщика
    try:
        from ai_valuator import get_valuator
        valuator = get_valuator()
        if valuator:
            provider_name = valuator.provider.upper()
            if provider_name == "GEMINI":
                logger.info(f"🤖 ИИ-оценщик настроен: {provider_name} (с анализом фото)")
            else:
                logger.info(f"🤖 ИИ-оценщик настроен: {provider_name} (без анализа фото)")
        else:
            logger.info("⚠️ ИИ-оценщик не настроен (GEMINI_API_KEY не указан)")
            logger.info("💡 Рекомендуется использовать Gemini для анализа фотографий квартир")
    except Exception as e:
        logger.warning(f"⚠️ ИИ-оценщик недоступен: {e}")
    
    # Создание бота
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
        if bot.session and not bot.session.closed:
            await bot.session.close()
        logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")

