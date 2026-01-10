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
    is_duplicate_content,
    mark_listing_sent,
    get_sent_listings_count,
    get_duplicates_stats,
    get_recent_listings
)
from scrapers.aggregator import ListingsAggregator
from scrapers.base import Listing
from error_logger import error_logger, log_error, log_warning, log_info

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Роутер для обработки команд
router = Router()

# Список источников по умолчанию (работающие парсеры)
# kufar - Kufar.by API (30 объявлений)
# hata - Hata.by HTML парсинг (3-5 объявлений)
# etagi - Etagi.com (временно отключен - API требует авторизацию)
DEFAULT_SOURCES = ["kufar", "hata"]


def format_listing_message(listing: Listing) -> str:
    """Форматирует сообщение об объявлении"""
    rooms_text = f"{listing.rooms}-комн." if listing.rooms > 0 else ""
    area_text = f"{listing.area} м²" if listing.area > 0 else ""
    
    # Формируем заголовок
    title_parts = [p for p in [rooms_text, area_text] if p]
    title = " • ".join(title_parts) if title_parts else listing.title
    
    # Строим сообщение
    lines = [f"🏠 <b>{title}</b>", ""]
    
    # Цена
    lines.append(f"💰 <b>Цена:</b> {listing.price_formatted}")
    
    # Цена за м² (вычисляется автоматически в Listing.__post_init__)
    if listing.price_per_sqm_formatted:
        lines.append(f"📊 <b>Цена/м²:</b> {listing.price_per_sqm_formatted}")
    
    # Основная информация
    lines.append(f"🚪 <b>Комнат:</b> {listing.rooms}")
    lines.append(f"📐 <b>Площадь:</b> {listing.area} м²")
    
    # Этаж
    if listing.floor:
        lines.append(f"🏢 <b>Этаж:</b> {listing.floor}")
    
    # Год постройки
    if listing.year_built:
        lines.append(f"📅 <b>Год:</b> {listing.year_built}")
    
    lines.append(f"📍 <b>Адрес:</b> {listing.address}")
    lines.append(f"🌐 <b>Источник:</b> {listing.source}")
    lines.append("")
    lines.append(f"🔗 <a href=\"{listing.url}\">Открыть объявление</a>")
    
    return "\n".join(lines)


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
        error_logger.log_error("bot", f"Ошибка отправки объявления {listing.id}", e)
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
    skipped_by_id = 0
    skipped_by_content = 0
    
    for listing in listings:
        # 1. Проверяем по ID (точное совпадение)
        if await is_listing_sent(listing.id):
            skipped_by_id += 1
            continue
        
        # 2. Проверяем по контенту (дубликаты с разных сайтов)
        dup_check = await is_duplicate_content(
            rooms=listing.rooms,
            area=listing.area,
            address=listing.address,
            price=listing.price
        )
        
        if dup_check["is_duplicate"]:
            skipped_by_content += 1
            log_info("dedup", 
                f"Дубликат: {listing.source} ID={listing.id} "
                f"похож на {dup_check['original_source']} ID={dup_check['original_id']}"
            )
            continue
        
        # Отправляем новое объявление
        if await send_listing_to_channel(bot, listing):
            new_count += 1
            # Задержка между сообщениями чтобы не получить бан
            await asyncio.sleep(3)
    
    if new_count > 0:
        logger.info(f"✅ Отправлено новых объявлений: {new_count}")
    else:
        logger.info("Новых объявлений нет")
    
    if skipped_by_id > 0 or skipped_by_content > 0:
        logger.info(f"⏭️ Пропущено: {skipped_by_id} по ID, {skipped_by_content} дубликатов по контенту")
    
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
        "📋 <b>Основные команды:</b>\n"
        "/filters - 🎛 Настройка фильтров (с кнопками!)\n"
        "/check - 🔍 Проверить объявления сейчас\n"
        "/stats - 📊 Статистика\n\n"
        "⚙️ <b>Быстрые фильтры:</b>\n"
        "/setrooms 2 - Только 2-комнатные\n"
        "/setrooms 1 3 - От 1 до 3 комнат\n"
        "/setprice 50000 - До $50,000\n"
        "/setprice 20000 40000 - $20k-$40k\n"
        "/resetfilters - Сбросить все фильтры\n\n"
        "/help - Полная справка",
        parse_mode=ParseMode.HTML
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help"""
    await message.answer(
        "📚 <b>Полная справка по командам</b>\n\n"
        "🎛 <b>Настройка фильтров:</b>\n"
        "• /filters - интерактивное меню с кнопками\n\n"
        "• /setrooms 2 - только 2-комнатные\n"
        "• /setrooms 1 3 - от 1 до 3 комнат\n\n"
        "• /setprice 50000 - до $50,000\n"
        "• /setprice 20000 50000 - от $20k до $50k\n\n"
        "• /resetfilters - сбросить все фильтры\n"
        "• /setcity барановичи - изменить город\n\n"
        "⚡ <b>Управление:</b>\n"
        "• /start_monitoring - включить авто-мониторинг\n"
        "• /stop_monitoring - выключить мониторинг\n"
        "• /check - проверить объявления сейчас\n\n"
        "📊 <b>Информация:</b>\n"
        "• /stats - статистика\n"
        "• /sources - список источников\n"
        "• /duplicates - статистика дубликатов\n"
        "• /recent - последние 10 объявлений\n\n"
        "🔧 <b>Отладка:</b>\n"
        "• /errors - последние ошибки\n"
        "• /logs - все логи\n"
        "• /clearerrors - очистить логи",
        parse_mode=ParseMode.HTML
    )


@router.message(Command("sources"))
async def cmd_sources(message: Message):
    """Показывает список источников"""
    active_sources = DEFAULT_SOURCES
    
    sources = [
        ("Kufar.by", "kufar", "крупнейшая доска объявлений Беларуси"),
        ("Hata.by", "hata", "региональные объявления"),
        ("Etagi.com", "etagi", "агентство (API требует авторизацию)"),
        ("Realt.by", "realt", "портал недвижимости (SPA - не поддерживается)"),
        ("Domovita.by", "domovita", "недвижимость (нет данных для Барановичей)"),
        ("Onliner.by", "onliner", "популярный портал (не поддерживается)"),
        ("GoHome.by", "gohome", "недвижимость (сервер недоступен)"),
    ]
    
    lines = ["📡 <b>Источники объявлений:</b>", ""]
    
    for name, key, desc in sources:
        if key in active_sources:
            lines.append(f"✅ <b>{name}</b> — {desc}")
        else:
            lines.append(f"❌ <s>{name}</s> — {desc}")
    
    lines.append("")
    lines.append(f"📊 <b>Активных источников:</b> {len(active_sources)}")
    lines.append("🔄 Проверка каждые 10 минут")
    
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(Command("filters"))
async def cmd_filters(message: Message):
    """Показывает текущие фильтры с кнопками настройки"""
    filters = await get_filters()
    
    status = "✅ Активен" if filters.get("is_active", True) else "❌ Отключен"
    
    # Создаем inline кнопки
    builder = InlineKeyboardBuilder()
    builder.button(text="🚪 Комнаты", callback_data="filter_rooms")
    builder.button(text="💰 Цена", callback_data="filter_price")
    builder.button(text="🔄 Сбросить", callback_data="filter_reset")
    builder.adjust(2, 1)
    
    await message.answer(
        f"⚙️ <b>Текущие фильтры</b>\n\n"
        f"📍 <b>Город:</b> {filters.get('city', 'барановичи').title()}\n"
        f"🚪 <b>Комнат:</b> от {filters.get('min_rooms', 1)} до {filters.get('max_rooms', 4)}\n"
        f"💰 <b>Цена:</b> ${filters.get('min_price', 0):,} - ${filters.get('max_price', 100000):,}\n\n"
        f"📡 <b>Статус:</b> {status}\n\n"
        f"<i>Нажмите кнопку для изменения или используйте команды:</i>\n"
        f"/setrooms, /setprice, /resetfilters",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )


# ============ INLINE КНОПКИ ДЛЯ ФИЛЬТРОВ ============

@router.callback_query(F.data == "filter_rooms")
async def cb_filter_rooms(callback: CallbackQuery):
    """Показывает кнопки выбора комнат"""
    builder = InlineKeyboardBuilder()
    
    # Кнопки для выбора количества комнат
    builder.button(text="1 комната", callback_data="rooms_1_1")
    builder.button(text="2 комнаты", callback_data="rooms_2_2")
    builder.button(text="3 комнаты", callback_data="rooms_3_3")
    builder.button(text="1-2 комн.", callback_data="rooms_1_2")
    builder.button(text="2-3 комн.", callback_data="rooms_2_3")
    builder.button(text="1-3 комн.", callback_data="rooms_1_3")
    builder.button(text="1-4 комн.", callback_data="rooms_1_4")
    builder.button(text="🔙 Назад", callback_data="filter_back")
    builder.adjust(3, 3, 1, 1)
    
    await callback.message.edit_text(
        "🚪 <b>Выберите количество комнат:</b>\n\n"
        "<i>Нажмите на кнопку или введите команду:</i>\n"
        "<code>/setrooms 2</code> - только 2-комнатные\n"
        "<code>/setrooms 1 3</code> - от 1 до 3 комнат",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rooms_"))
async def cb_set_rooms(callback: CallbackQuery):
    """Устанавливает количество комнат"""
    parts = callback.data.split("_")
    min_rooms = int(parts[1])
    max_rooms = int(parts[2])
    
    await update_filters(min_rooms=min_rooms, max_rooms=max_rooms)
    
    if min_rooms == max_rooms:
        text = f"✅ Установлено: только {min_rooms}-комнатные"
    else:
        text = f"✅ Установлено: {min_rooms}-{max_rooms} комнаты"
    
    await callback.message.edit_text(text)
    await callback.answer(text)


@router.callback_query(F.data == "filter_price")
async def cb_filter_price(callback: CallbackQuery):
    """Показывает кнопки выбора цены"""
    builder = InlineKeyboardBuilder()
    
    # Популярные диапазоны цен
    builder.button(text="до $30,000", callback_data="price_0_30000")
    builder.button(text="до $40,000", callback_data="price_0_40000")
    builder.button(text="до $50,000", callback_data="price_0_50000")
    builder.button(text="$20k-$40k", callback_data="price_20000_40000")
    builder.button(text="$30k-$50k", callback_data="price_30000_50000")
    builder.button(text="$40k-$60k", callback_data="price_40000_60000")
    builder.button(text="$50k-$80k", callback_data="price_50000_80000")
    builder.button(text="Любая цена", callback_data="price_0_500000")
    builder.button(text="🔙 Назад", callback_data="filter_back")
    builder.adjust(3, 2, 2, 1, 1)
    
    await callback.message.edit_text(
        "💰 <b>Выберите диапазон цены:</b>\n\n"
        "<i>Нажмите на кнопку или введите команду:</i>\n"
        "<code>/setprice 50000</code> - до $50,000\n"
        "<code>/setprice 20000 40000</code> - от $20,000 до $40,000",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("price_"))
async def cb_set_price(callback: CallbackQuery):
    """Устанавливает диапазон цены"""
    parts = callback.data.split("_")
    min_price = int(parts[1])
    max_price = int(parts[2])
    
    await update_filters(min_price=min_price, max_price=max_price)
    
    if min_price == 0:
        text = f"✅ Установлено: до ${max_price:,}"
    else:
        text = f"✅ Установлено: ${min_price:,} - ${max_price:,}"
    
    await callback.message.edit_text(text)
    await callback.answer(text)


@router.callback_query(F.data == "filter_reset")
async def cb_filter_reset(callback: CallbackQuery):
    """Сбрасывает фильтры до значений по умолчанию"""
    await update_filters(
        min_rooms=1,
        max_rooms=4,
        min_price=0,
        max_price=100000
    )
    await callback.message.edit_text(
        "🔄 Фильтры сброшены!\n\n"
        "Комнат: 1-4\n"
        "Цена: до $100,000"
    )
    await callback.answer("Фильтры сброшены!")


@router.callback_query(F.data == "filter_back")
async def cb_filter_back(callback: CallbackQuery):
    """Возврат к главному меню фильтров"""
    filters = await get_filters()
    status = "✅ Активен" if filters.get("is_active", True) else "❌ Отключен"
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🚪 Комнаты", callback_data="filter_rooms")
    builder.button(text="💰 Цена", callback_data="filter_price")
    builder.button(text="🔄 Сбросить", callback_data="filter_reset")
    builder.adjust(2, 1)
    
    await callback.message.edit_text(
        f"⚙️ <b>Текущие фильтры</b>\n\n"
        f"📍 <b>Город:</b> {filters.get('city', 'барановичи').title()}\n"
        f"🚪 <b>Комнат:</b> от {filters.get('min_rooms', 1)} до {filters.get('max_rooms', 4)}\n"
        f"💰 <b>Цена:</b> ${filters.get('min_price', 0):,} - ${filters.get('max_price', 100000):,}\n\n"
        f"📡 <b>Статус:</b> {status}",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.message(Command("setrooms"))
async def cmd_set_rooms(message: Message):
    """Установка фильтра по количеству комнат
    
    Примеры:
    /setrooms 2 - только 2-комнатные
    /setrooms 1 3 - от 1 до 3 комнат
    """
    try:
        args = message.text.split()[1:]
        
        if len(args) == 0:
            # Показываем помощь
            await message.answer(
                "🚪 <b>Настройка фильтра комнат</b>\n\n"
                "Используйте:\n"
                "• <code>/setrooms 2</code> — только 2-комнатные\n"
                "• <code>/setrooms 1 3</code> — от 1 до 3 комнат\n"
                "• <code>/setrooms 1 4</code> — любые (1-4 комнаты)\n\n"
                "Или нажмите /filters для интерактивного выбора",
                parse_mode=ParseMode.HTML
            )
            return
        
        if len(args) == 1:
            # Один параметр — точное количество комнат
            rooms = int(args[0])
            if rooms < 1 or rooms > 5:
                await message.answer("⚠️ Комнат может быть от 1 до 5.")
                return
            min_rooms = max_rooms = rooms
        else:
            # Два параметра — диапазон
            min_rooms = int(args[0])
            max_rooms = int(args[1])
        
        if min_rooms < 1 or max_rooms > 5 or min_rooms > max_rooms:
            await message.answer("⚠️ Неверные значения. Комнат может быть от 1 до 5.")
            return
        
        await update_filters(min_rooms=min_rooms, max_rooms=max_rooms)
        
        if min_rooms == max_rooms:
            await message.answer(f"✅ Фильтр обновлен!\nТолько {min_rooms}-комнатные квартиры")
        else:
            await message.answer(f"✅ Фильтр обновлен!\nКомнат: от {min_rooms} до {max_rooms}")
        
    except ValueError:
        await message.answer(
            "⚠️ Неверный формат!\n\n"
            "Примеры:\n"
            "/setrooms 2 — только 2-комнатные\n"
            "/setrooms 1 3 — от 1 до 3 комнат"
        )


@router.message(Command("setprice"))
async def cmd_set_price(message: Message):
    """Установка фильтра по цене
    
    Примеры:
    /setprice 50000 - до $50,000
    /setprice 20000 50000 - от $20,000 до $50,000
    """
    try:
        args = message.text.split()[1:]
        
        if len(args) == 0:
            # Показываем помощь
            await message.answer(
                "💰 <b>Настройка фильтра цены</b>\n\n"
                "Используйте:\n"
                "• <code>/setprice 50000</code> — до $50,000\n"
                "• <code>/setprice 20000 50000</code> — от $20k до $50k\n"
                "• <code>/setprice 0 100000</code> — любая цена\n\n"
                "Или нажмите /filters для интерактивного выбора",
                parse_mode=ParseMode.HTML
            )
            return
        
        if len(args) == 1:
            # Один параметр — максимальная цена (от 0)
            max_price = int(args[0])
            min_price = 0
        else:
            # Два параметра — диапазон
            min_price = int(args[0])
            max_price = int(args[1])
        
        if min_price < 0 or max_price > 1000000 or min_price > max_price:
            await message.answer("⚠️ Неверные значения цены (0 - 1,000,000).")
            return
        
        await update_filters(min_price=min_price, max_price=max_price)
        
        if min_price == 0:
            await message.answer(f"✅ Фильтр обновлен!\nЦена: до ${max_price:,}")
        else:
            await message.answer(f"✅ Фильтр обновлен!\nЦена: ${min_price:,} - ${max_price:,}")
        
    except ValueError:
        await message.answer(
            "⚠️ Неверный формат!\n\n"
            "Примеры:\n"
            "/setprice 50000 — до $50,000\n"
            "/setprice 20000 50000 — от $20k до $50k"
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


@router.message(Command("resetfilters"))
async def cmd_reset_filters(message: Message):
    """Сброс фильтров до значений по умолчанию"""
    await update_filters(
        min_rooms=1,
        max_rooms=4,
        min_price=0,
        max_price=100000
    )
    await message.answer(
        "🔄 <b>Фильтры сброшены!</b>\n\n"
        "Комнат: 1-4\n"
        "Цена: до $100,000",
        parse_mode=ParseMode.HTML
    )


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
    error_stats = error_logger.get_stats()
    dup_stats = await get_duplicates_stats()
    
    await message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"📨 Отправлено объявлений: {count}\n"
        f"📡 Статус мониторинга: {status}\n"
        f"🌐 Источников: {len(DEFAULT_SOURCES)}\n\n"
        f"🔍 <b>Дедупликация:</b>\n"
        f"  • Уникальных объявлений: {dup_stats.get('unique_content', 0)}\n"
        f"  • Групп дубликатов: {dup_stats.get('duplicate_groups', 0)}\n\n"
        f"⚠️ <b>Ошибки:</b> {error_stats['total_errors']}\n"
        f"⚡ <b>Предупреждения:</b> {error_stats['total_warnings']}",
        parse_mode=ParseMode.HTML
    )


@router.message(Command("duplicates"))
async def cmd_duplicates(message: Message):
    """Показывает статистику по дубликатам"""
    stats = await get_duplicates_stats()
    
    lines = ["🔍 <b>Статистика дубликатов</b>", ""]
    lines.append(f"📨 Всего отправлено: {stats.get('total_sent', 0)}")
    lines.append(f"🆔 Уникальных по контенту: {stats.get('unique_content', 0)}")
    lines.append(f"👯 Групп дубликатов: {stats.get('duplicate_groups', 0)}")
    lines.append("")
    
    # По источникам
    if stats.get("by_source"):
        lines.append("<b>По источникам:</b>")
        for source, count in stats["by_source"].items():
            lines.append(f"  • {source or 'неизвестно'}: {count}")
        lines.append("")
    
    # Детали дубликатов
    if stats.get("duplicate_details"):
        lines.append("<b>Примеры дубликатов:</b>")
        for dup in stats["duplicate_details"][:5]:
            lines.append(f"  • Хеш {dup['hash'][:8]}...: {dup['count']} шт ({dup['sources']})")
    
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(Command("recent"))
async def cmd_recent(message: Message):
    """Показывает последние отправленные объявления"""
    listings = await get_recent_listings(10)
    
    if not listings:
        await message.answer("📭 Еще нет отправленных объявлений.")
        return
    
    lines = ["📋 <b>Последние 10 отправленных объявлений:</b>", ""]
    
    for i, l in enumerate(listings, 1):
        source = l.get("source", "?")
        rooms = l.get("rooms", "?")
        area = l.get("area", "?")
        price = l.get("price", 0)
        sent = l.get("sent_at", "")[:16] if l.get("sent_at") else "?"
        
        lines.append(f"{i}. [{source}] {rooms}к, {area}м², {price:,}".replace(",", " "))
        lines.append(f"   🕐 {sent}")
    
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(Command("errors"))
async def cmd_errors(message: Message):
    """Показывает последние ошибки"""
    errors = error_logger.get_errors(limit=15)
    
    if not errors:
        await message.answer("✅ Ошибок нет! Все работает отлично.")
        return
    
    # Формируем сообщение
    text = "🚨 <b>Последние ошибки:</b>\n\n"
    
    for i, err in enumerate(reversed(errors), 1):
        timestamp = err.get("timestamp", "")
        source = err.get("source", "unknown")
        msg = err.get("message", "")
        exc = err.get("exception", "")
        
        text += f"<b>{i}.</b> [{source}] {timestamp}\n"
        text += f"   📝 {msg[:100]}\n"
        if exc:
            text += f"   ⚠️ <code>{exc[:150]}</code>\n"
        text += "\n"
    
    # Telegram ограничивает длину сообщения
    if len(text) > 4000:
        text = text[:4000] + "\n\n... (сокращено)"
    
    await message.answer(text, parse_mode=ParseMode.HTML)


@router.message(Command("warnings"))
async def cmd_warnings(message: Message):
    """Показывает последние предупреждения"""
    warnings = error_logger.get_warnings(limit=10)
    
    if not warnings:
        await message.answer("✅ Предупреждений нет!")
        return
    
    text = "⚡ <b>Последние предупреждения:</b>\n\n"
    
    for i, warn in enumerate(reversed(warnings), 1):
        timestamp = warn.get("timestamp", "")
        source = warn.get("source", "unknown")
        msg = warn.get("message", "")
        
        text += f"<b>{i}.</b> [{source}] {timestamp}\n"
        text += f"   📝 {msg[:100]}\n\n"
    
    await message.answer(text, parse_mode=ParseMode.HTML)


@router.message(Command("clearerrors"))
async def cmd_clear_errors(message: Message):
    """Очищает все логи ошибок"""
    error_logger.clear()
    await message.answer("🗑 Все логи ошибок очищены!")


@router.message(Command("logs"))
async def cmd_logs(message: Message):
    """Показывает все последние логи"""
    logs = error_logger.get_all_logs(limit=20)
    stats = error_logger.get_stats()
    
    if not logs:
        await message.answer("📋 Логов пока нет.")
        return
    
    text = f"📋 <b>Последние логи</b>\n"
    text += f"Ошибок: {stats['total_errors']} | Предупреждений: {stats['total_warnings']}\n\n"
    
    # По источникам
    if stats['errors_by_source']:
        text += "<b>Ошибки по источникам:</b>\n"
        for source, count in stats['errors_by_source'].items():
            text += f"  • {source}: {count}\n"
        text += "\n"
    
    text += "<b>Последние записи:</b>\n\n"
    
    for log in logs[:15]:
        timestamp = log.get("timestamp", "")[-8:]  # Только время
        source = log.get("source", "?")
        msg = log.get("message", "")[:60]
        log_type = "🔴" if log.get("type") == "error" else "🟡"
        
        text += f"{log_type} <code>{timestamp}</code> [{source}]\n   {msg}\n"
    
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    
    await message.answer(text, parse_mode=ParseMode.HTML)


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
