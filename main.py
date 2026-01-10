"""
Главный файл запуска бота мониторинга квартир
"""
import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from bot import create_bot, check_new_listings
from config import CHECK_INTERVAL, BOT_TOKEN, CHANNEL_ID
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
    
    if not CHANNEL_ID:
        logger.error("❌ CHANNEL_ID не установлен!")
        logger.error("Укажите ID канала в файле .env")
        return
    
    logger.info("=" * 50)
    logger.info("🏠 Запуск бота мониторинга квартир")
    logger.info("=" * 50)
    
    # Инициализация базы данных
    await init_database()
    logger.info("✅ База данных инициализирована")
    
    # Создание бота
    bot, dp = await create_bot()
    logger.info("✅ Бот создан")
    
    # Настройка планировщика
    scheduler = AsyncIOScheduler()
    
    # Проверка объявлений каждые N минут
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
    logger.info(f"✅ Планировщик запущен (интервал: {CHECK_INTERVAL} мин)")
    
    # Первая проверка при запуске
    logger.info("🔍 Первоначальная проверка объявлений...")
    await check_new_listings(bot)
    
    # Запуск бота
    logger.info("🚀 Бот запущен и готов к работе!")
    logger.info(f"📢 Канал для уведомлений: {CHANNEL_ID}")
    
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()
        await bot.session.close()
        logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")

