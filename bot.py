"""
Telegram бот для мониторинга объявлений о квартирах
"""
import asyncio
import logging
from typing import List, Dict, Any

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, InputMediaPhoto, CallbackQuery
from aiogram.filters import Command, CommandStart
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import BOT_TOKEN, CHANNEL_ID, MAX_PHOTOS
from database import (
    init_database, 
    get_filters, 
    update_filters, 
    is_listing_sent, 
    mark_listing_sent,
    get_sent_listings_count
)
from scrapers.aggregator import ListingsAggregator
from scrapers.base import Listing

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Роутер для обработки команд
router = Router()

# Список источников по умолчанию
# Временно оставляем только работающие парсеры
DEFAULT_SOURCES = ["kufar", "hata"]


def format_listing_message(listing: Listing) -> str:
    """Форматирует сообщение об объявлении"""
    rooms_text = f"{listing.rooms}-комн." if listing.rooms > 0 else ""
    area_text = f"{listing.area} м²" if listing.area > 0 else ""
    
    # Формируем заголовок
    title_parts = [p for p in [rooms_text, area_text] if p]
    title = " • ".join(title_parts) if title_parts else listing.title
    
    message = f"""🏠 <b>{title}</b>

💰 <b>Цена:</b> {listing.price_formatted}
🚪 <b>Комнат:</b> {listing.rooms}
📐 <b>Площадь:</b> {listing.area} м²
📍 <b>Адрес:</b> {listing.address}
🌐 <b>Источник:</b> {listing.source}

🔗 <a href="{listing.url}">Открыть объявление</a>
"""
    return message


async def send_listing_to_channel(bot: Bot, listing: Listing) -> bool:
    """Отправляет объявление в канал"""
    try:
        message_text = format_listing_message(listing)
        photos = listing.photos
        
        if photos:
            # Отправляем медиагруппу с фотографиями
            media_group = []
            for i, photo_url in enumerate(photos[:MAX_PHOTOS]):
                if i == 0:
                    # Первое фото с подписью
                    media_group.append(
                        InputMediaPhoto(
                            media=photo_url,
                            caption=message_text,
                            parse_mode=ParseMode.HTML
                        )
                    )
                else:
                    media_group.append(InputMediaPhoto(media=photo_url))
            
            await bot.send_media_group(
                chat_id=CHANNEL_ID,
                media=media_group
            )
        else:
            # Без фотографий - просто текст
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=message_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
        
        # Отмечаем как отправленное
        await mark_listing_sent(listing.to_dict())
        logger.info(f"Отправлено объявление: {listing.id} ({listing.source})")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка отправки объявления {listing.id}: {e}")
        return False


async def check_new_listings(bot: Bot):
    """Проверяет новые объявления и отправляет их в канал"""
    logger.info("=" * 50)
    logger.info("Проверка новых объявлений со всех источников...")
    
    filters = await get_filters()
    
    if not filters.get("is_active", True):
        logger.info("Мониторинг отключен")
        return
    
    # Используем агрегатор для получения со всех сайтов
    aggregator = ListingsAggregator(enabled_sources=DEFAULT_SOURCES)
    
    listings = await aggregator.fetch_all_listings(
        city=filters.get("city", "барановичи"),
        min_rooms=filters.get("min_rooms", 1),
        max_rooms=filters.get("max_rooms", 4),
        min_price=filters.get("min_price", 0),
        max_price=filters.get("max_price", 100000),
    )
    
    logger.info(f"Всего найдено объявлений: {len(listings)}")
    
    new_count = 0
    for listing in listings:
        # Проверяем, не отправляли ли уже
        if not await is_listing_sent(listing.id):
            if await send_listing_to_channel(bot, listing):
                new_count += 1
                # Задержка между сообщениями чтобы не получить бан
                await asyncio.sleep(3)
    
    if new_count > 0:
        logger.info(f"✅ Отправлено новых объявлений: {new_count}")
    else:
        logger.info("Новых объявлений нет")
    
    logger.info("=" * 50)


# ============ КОМАНДЫ БОТА ============

@router.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    sources_list = ", ".join(DEFAULT_SOURCES)
    await message.answer(
        "🏠 <b>Бот мониторинга квартир</b>\n\n"
        "Этот бот отслеживает новые объявления о продаже квартир в Барановичах.\n\n"
        f"📡 <b>Источники:</b> {sources_list}\n\n"
        "📋 <b>Доступные команды:</b>\n"
        "/filters - Посмотреть текущие фильтры\n"
        "/setrooms 1 3 - Установить количество комнат (мин макс)\n"
        "/setprice 0 50000 - Установить цену в $ (мин макс)\n"
        "/start_monitoring - Включить мониторинг\n"
        "/stop_monitoring - Выключить мониторинг\n"
        "/check - Проверить новые объявления сейчас\n"
        "/sources - Список источников\n"
        "/stats - Статистика\n"
        "/help - Помощь",
        parse_mode=ParseMode.HTML
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help"""
    await message.answer(
        "📚 <b>Справка по командам</b>\n\n"
        "<b>Настройка фильтров:</b>\n"
        "• /setrooms 1 3 - квартиры от 1 до 3 комнат\n"
        "• /setprice 10000 50000 - цена от $10000 до $50000\n"
        "• /setcity барановичи - установить город\n\n"
        "<b>Управление:</b>\n"
        "• /start_monitoring - включить автоматический мониторинг\n"
        "• /stop_monitoring - выключить мониторинг\n"
        "• /check - проверить объявления прямо сейчас\n\n"
        "<b>Информация:</b>\n"
        "• /filters - текущие настройки фильтров\n"
        "• /sources - список источников данных\n"
        "• /stats - статистика отправленных объявлений\n\n"
        "❗ Бот отправляет уведомления в канал, ID которого указан в настройках.",
        parse_mode=ParseMode.HTML
    )


@router.message(Command("sources"))
async def cmd_sources(message: Message):
    """Показывает список источников"""
    sources_info = """
📡 <b>Источники объявлений:</b>

🔹 <b>Kufar.by</b> - крупнейшая доска объявлений Беларуси
🔹 <b>Realt.by</b> - портал недвижимости
🔹 <b>Domovita.by</b> - недвижимость Беларуси
🔹 <b>Onliner.by</b> - популярный портал
🔹 <b>GoHome.by</b> - недвижимость
🔹 <b>Hata.by</b> - региональные объявления
🔹 <b>Etagi.com</b> - агентство недвижимости

Бот проверяет все источники каждые 10 минут и отправляет только новые уникальные объявления.
"""
    await message.answer(sources_info, parse_mode=ParseMode.HTML)


@router.message(Command("filters"))
async def cmd_filters(message: Message):
    """Показывает текущие фильтры"""
    filters = await get_filters()
    
    status = "✅ Активен" if filters.get("is_active", True) else "❌ Отключен"
    
    await message.answer(
        f"⚙️ <b>Текущие фильтры</b>\n\n"
        f"📍 <b>Город:</b> {filters.get('city', 'барановичи').title()}\n"
        f"🚪 <b>Комнат:</b> от {filters.get('min_rooms', 1)} до {filters.get('max_rooms', 4)}\n"
        f"💰 <b>Цена:</b> ${filters.get('min_price', 0):,} - ${filters.get('max_price', 100000):,}\n\n"
        f"📡 <b>Статус:</b> {status}",
        parse_mode=ParseMode.HTML
    )


@router.message(Command("setrooms"))
async def cmd_set_rooms(message: Message):
    """Установка фильтра по количеству комнат"""
    try:
        args = message.text.split()[1:]
        if len(args) < 2:
            await message.answer(
                "⚠️ Используйте: /setrooms <мин> <макс>\n"
                "Пример: /setrooms 1 3"
            )
            return
        
        min_rooms = int(args[0])
        max_rooms = int(args[1])
        
        if min_rooms < 1 or max_rooms > 5 or min_rooms > max_rooms:
            await message.answer("⚠️ Неверные значения. Комнат может быть от 1 до 5.")
            return
        
        await update_filters(min_rooms=min_rooms, max_rooms=max_rooms)
        await message.answer(
            f"✅ Фильтр обновлен!\n"
            f"Комнат: от {min_rooms} до {max_rooms}"
        )
        
    except (ValueError, IndexError):
        await message.answer(
            "⚠️ Используйте: /setrooms <мин> <макс>\n"
            "Пример: /setrooms 1 3"
        )


@router.message(Command("setprice"))
async def cmd_set_price(message: Message):
    """Установка фильтра по цене"""
    try:
        args = message.text.split()[1:]
        if len(args) < 2:
            await message.answer(
                "⚠️ Используйте: /setprice <мин> <макс>\n"
                "Пример: /setprice 10000 50000"
            )
            return
        
        min_price = int(args[0])
        max_price = int(args[1])
        
        if min_price < 0 or max_price > 1000000 or min_price > max_price:
            await message.answer("⚠️ Неверные значения цены.")
            return
        
        await update_filters(min_price=min_price, max_price=max_price)
        await message.answer(
            f"✅ Фильтр обновлен!\n"
            f"Цена: ${min_price:,} - ${max_price:,}"
        )
        
    except (ValueError, IndexError):
        await message.answer(
            "⚠️ Используйте: /setprice <мин> <макс>\n"
            "Пример: /setprice 10000 50000"
        )


@router.message(Command("setcity"))
async def cmd_set_city(message: Message):
    """Установка города"""
    try:
        args = message.text.split()[1:]
        if not args:
            await message.answer(
                "⚠️ Используйте: /setcity <город>\n"
                "Пример: /setcity барановичи\n\n"
                "Поддерживаемые города: барановичи"
            )
            return
        
        city = args[0].lower()
        await update_filters(city=city)
        await message.answer(f"✅ Город установлен: {city.title()}")
        
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}")


@router.message(Command("start_monitoring"))
async def cmd_start_monitoring(message: Message):
    """Включение мониторинга"""
    await update_filters(is_active=True)
    await message.answer("✅ Мониторинг включен!")


@router.message(Command("stop_monitoring"))
async def cmd_stop_monitoring(message: Message):
    """Выключение мониторинга"""
    await update_filters(is_active=False)
    await message.answer("❌ Мониторинг отключен.")


@router.message(Command("check"))
async def cmd_check(message: Message):
    """Ручная проверка объявлений"""
    await message.answer("🔍 Проверяю новые объявления со всех источников...\nЭто может занять 30-60 секунд.")
    await check_new_listings(message.bot)
    await message.answer("✅ Проверка завершена!")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Статистика"""
    count = await get_sent_listings_count()
    filters = await get_filters()
    status = "✅ Активен" if filters.get("is_active", True) else "❌ Отключен"
    
    await message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"📨 Отправлено объявлений: {count}\n"
        f"📡 Статус мониторинга: {status}\n"
        f"🌐 Источников: {len(DEFAULT_SOURCES)}",
        parse_mode=ParseMode.HTML
    )


async def create_bot() -> tuple[Bot, Dispatcher]:
    """Создает и настраивает бота"""
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не установлен! Проверьте файл .env")
    
    bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
    
    dp = Dispatcher()
    dp.include_router(router)
    
    # Инициализация базы данных
    await init_database()
    
    return bot, dp
