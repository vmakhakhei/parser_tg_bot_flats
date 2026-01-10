"""
Telegram бот для мониторинга объявлений о квартирах
"""
import asyncio
import logging
import aiosqlite
from typing import List, Dict, Any, Optional

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, InputMediaPhoto, CallbackQuery
from aiogram.filters import Command, CommandStart
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import BOT_TOKEN, MAX_PHOTOS, DATABASE_PATH
from database import (
    init_database, 
    get_filters, 
    update_filters, 
    is_listing_sent,
    is_duplicate_content,
    mark_listing_sent,
    get_sent_listings_count,
    get_duplicates_stats,
    get_recent_listings,
    get_user_filters,
    set_user_filters,
    is_listing_sent_to_user,
    mark_listing_sent_to_user,
    get_active_users
)
from scrapers.aggregator import ListingsAggregator
from scrapers.base import Listing
from error_logger import error_logger, log_error, log_warning, log_info

# ИИ-оценщик (опционально)
try:
    from ai_valuator import valuate_listing
    AI_VALUATOR_AVAILABLE = True
except ImportError:
    AI_VALUATOR_AVAILABLE = False
    valuate_listing = None

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
# etagi - Etagi.com HTML парсинг (30 объявлений)
DEFAULT_SOURCES = ["kufar", "hata", "etagi"]


def format_listing_message(listing: Listing, ai_valuation: Optional[Dict[str, Any]] = None) -> str:
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
    
    # ИИ-оценка (если доступна)
    if ai_valuation:
        fair_price = ai_valuation.get("fair_price_usd", 0)
        is_overpriced = ai_valuation.get("is_overpriced", False)
        assessment = ai_valuation.get("assessment", "")
        
        if fair_price > 0:
            price_status = "🔴 Завышена" if is_overpriced else "🟢 Справедлива"
            price_emoji = "🔴" if is_overpriced else "🟢"
            lines.append("")
            lines.append(f"🤖 <b>ИИ-оценка:</b> ${fair_price:,} {price_status}".replace(",", " "))
            if assessment:
                lines.append(f"💡 <i>{assessment}</i>")
            lines.append("")
    
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


async def send_listing_to_user(bot: Bot, user_id: int, listing: Listing) -> bool:
    """Отправляет объявление пользователю"""
    try:
        # Пытаемся получить ИИ-оценку (если доступна)
        ai_valuation = None
        if AI_VALUATOR_AVAILABLE and valuate_listing:
            try:
                # Таймаут для ИИ-оценки (максимум 5 секунд)
                ai_valuation = await asyncio.wait_for(valuate_listing(listing), timeout=5.0)
                if ai_valuation:
                    log_info("ai", f"ИИ-оценка получена для {listing.id}: ${ai_valuation.get('fair_price_usd', 0):,}")
            except asyncio.TimeoutError:
                log_warning("ai", f"Таймаут ИИ-оценки для {listing.id}")
            except Exception as e:
                log_warning("ai", f"Ошибка ИИ-оценки для {listing.id}: {e}")
        
        message_text = format_listing_message(listing, ai_valuation)
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
                chat_id=user_id,
                media=media_group
            )
        else:
            # Без фотографий - просто текст
            await bot.send_message(
                chat_id=user_id,
                text=message_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
        
        # Отмечаем как отправленное пользователю и глобально
        await mark_listing_sent_to_user(user_id, listing.id)
        await mark_listing_sent(listing.to_dict())  # Глобальная дедупликация
        logger.info(f"Отправлено пользователю {user_id}: {listing.id} ({listing.source})")
        return True
        
    except Exception as e:
        error_logger.log_error("bot", f"Ошибка отправки объявления {listing.id} пользователю {user_id}", e)
        return False


async def check_new_listings(bot: Bot):
    """Проверяет новые объявления и отправляет их активным пользователям"""
    logger.info("=" * 50)
    logger.info("Проверка новых объявлений со всех источников...")
    
    # Получаем список активных пользователей
    active_users = await get_active_users()
    
    if not active_users:
        logger.info("Нет активных пользователей")
        return
    
    logger.info(f"Активных пользователей: {len(active_users)}")
    
    # Получаем все объявления со всех источников (без фильтров)
    aggregator = ListingsAggregator(enabled_sources=DEFAULT_SOURCES)
    
    all_listings = await aggregator.fetch_all_listings(
        city="барановичи",
        min_rooms=1,
        max_rooms=5,
        min_price=0,
        max_price=1000000,  # Широкий диапазон для всех пользователей
    )
    
    logger.info(f"Всего найдено объявлений: {len(all_listings)}")
    
    total_sent = 0
    
    # Для каждого пользователя проверяем объявления по его фильтрам
    for user_id in active_users:
        user_filters = await get_user_filters(user_id)
        if not user_filters or not user_filters.get("is_active"):
            continue
        
        user_new_count = 0
        
        for listing in all_listings:
            # Проверяем соответствие фильтрам пользователя
            if not _matches_user_filters(listing, user_filters):
                continue
            
            # Проверяем, не отправляли ли уже этому пользователю
            if await is_listing_sent_to_user(user_id, listing.id):
                continue
            
            # Проверяем глобальную дедупликацию по контенту
            dup_check = await is_duplicate_content(
                rooms=listing.rooms,
                area=listing.area,
                address=listing.address,
                price=listing.price
            )
            
            if dup_check["is_duplicate"]:
                log_info("dedup", 
                    f"Дубликат для пользователя {user_id}: {listing.source} ID={listing.id}"
                )
                continue
            
            # Отправляем объявление пользователю
            if await send_listing_to_user(bot, user_id, listing):
                user_new_count += 1
                total_sent += 1
                # Задержка между сообщениями чтобы не получить бан
                await asyncio.sleep(2)
        
        if user_new_count > 0:
            logger.info(f"Пользователю {user_id} отправлено: {user_new_count} объявлений")
    
    if total_sent > 0:
        logger.info(f"✅ Всего отправлено новых объявлений: {total_sent}")
    else:
        logger.info("Новых объявлений нет")
    
    logger.info("=" * 50)


def _matches_user_filters(listing: Listing, filters: Dict[str, Any]) -> bool:
    """Проверяет соответствие объявления фильтрам пользователя"""
    # Комнаты
    if listing.rooms > 0:
        min_rooms = filters.get("min_rooms", 1)
        max_rooms = filters.get("max_rooms", 4)
        if listing.rooms < min_rooms or listing.rooms > max_rooms:
            return False
    
    # Цена (конвертируем в USD если нужно)
    price = listing.price
    if listing.price_usd:
        price = listing.price_usd
    elif listing.price_byn and not listing.price_usd:
        # Конвертируем BYN в USD примерно (курс ~2.95)
        price = int(listing.price_byn / 2.95)
    
    # Проверяем цену только если она указана
    if price > 0:
        min_price = filters.get("min_price", 0)
        max_price = filters.get("max_price", 1000000)  # Используем значение из фильтров или максимум
        if price < min_price or price > max_price:
            log_info("filter", f"Не прошёл фильтр: {listing.rooms}к, ${price} (диапазон: ${min_price}-${max_price})")
            return False
    
    return True


# ============ КОМАНДЫ БОТА ============

@router.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start - запрашивает фильтры если их нет"""
    user_id = message.from_user.id
    
    # Проверяем, есть ли у пользователя фильтры
    user_filters = await get_user_filters(user_id)
    
    if not user_filters:
        # Первый запуск - запрашиваем фильтры
        builder = InlineKeyboardBuilder()
        builder.button(text="🚪 Настроить фильтры", callback_data="setup_filters")
        
        await message.answer(
            "👋 <b>Добро пожаловать!</b>\n\n"
            "Я помогу вам найти квартиру в Барановичах.\n\n"
            "📋 <b>Как это работает:</b>\n"
            "1️⃣ Настройте фильтры (комнаты, цена)\n"
            "2️⃣ Я найду подходящие объявления\n"
            "3️⃣ Автоматически буду присылать новые объявления\n\n"
            "Нажмите кнопку ниже, чтобы начать:",
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup()
        )
    else:
        # Фильтры уже установлены - показываем их и предлагаем изменить
        status = "✅ Активен" if user_filters.get("is_active") else "❌ Отключен"
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🔍 Проверить сейчас", callback_data="check_now")
        builder.button(text="⚙️ Изменить фильтры", callback_data="setup_filters")
        builder.row()
        builder.button(text="📊 Статистика", callback_data="show_stats")
        
        await message.answer(
            f"🏠 <b>Ваши фильтры</b>\n\n"
            f"🚪 <b>Комнат:</b> от {user_filters.get('min_rooms', 1)} до {user_filters.get('max_rooms', 4)}\n"
            f"💰 <b>Цена:</b> ${user_filters.get('min_price', 0):,} - ${user_filters.get('max_price', 100000):,}\n\n"
            f"📡 <b>Статус:</b> {status}\n\n"
            f"Я проверяю новые объявления каждые 10 минут и присылаю только те, что подходят под ваши фильтры.",
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup()
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
        ("Etagi.com", "etagi", "агентство недвижимости"),
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

@router.callback_query(F.data == "setup_filters")
async def cb_setup_filters(callback: CallbackQuery):
    """Настройка фильтров для пользователя"""
    user_id = callback.from_user.id
    user_filters = await get_user_filters(user_id)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🚪 Комнаты", callback_data="user_filter_rooms")
    builder.button(text="💰 Цена", callback_data="user_filter_price")
    builder.row()
    builder.button(text="✅ Готово", callback_data="user_filters_done")
    
    # Показываем текущие значения если они есть
    if user_filters:
        rooms_text = f"{user_filters.get('min_rooms', 1)}-{user_filters.get('max_rooms', 4)}"
        price_text = f"${user_filters.get('min_price', 0):,} - ${user_filters.get('max_price', 100000):,}".replace(",", " ")
        current_info = f"\n\n<b>Текущие настройки:</b>\n🚪 Комнаты: {rooms_text}\n💰 Цена: {price_text}"
    else:
        current_info = ""
    
    await callback.message.edit_text(
        "⚙️ <b>Настройка фильтров</b>\n\n"
        "Выберите параметры поиска:\n\n"
        "🚪 <b>Комнаты</b> — диапазон комнат (1-2, 2-3, 3-4, 4+)\n"
        "💰 <b>Цена</b> — цена от и до в USD\n\n"
        "После настройки я найду подходящие объявления и буду присылать новые автоматически."
        + current_info,
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data == "user_filters_done")
async def cb_filters_done(callback: CallbackQuery):
    """Завершение настройки фильтров и отправка результатов"""
    user_id = callback.from_user.id
    
    # Сразу отвечаем на callback чтобы избежать timeout
    await callback.answer("Ищу объявления...")
    
    user_filters = await get_user_filters(user_id)
    if not user_filters:
        # Устанавливаем дефолтные фильтры если их нет
        await set_user_filters(user_id)
        user_filters = await get_user_filters(user_id)
    
    status_msg = await callback.message.answer(
        "🔍 <b>Ищу подходящие объявления...</b>\n\n"
        "Это может занять несколько секунд.",
        parse_mode=ParseMode.HTML
    )
    
    # Ищем объявления по фильтрам пользователя
    aggregator = ListingsAggregator(enabled_sources=DEFAULT_SOURCES)
    
    listings = await aggregator.fetch_all_listings(
        city=user_filters.get("city", "барановичи"),
        min_rooms=user_filters.get("min_rooms", 1),
        max_rooms=user_filters.get("max_rooms", 4),
        min_price=user_filters.get("min_price", 0),
        max_price=user_filters.get("max_price", 100000),
    )
    
    # Фильтруем по фильтрам пользователя
    filtered_listings = []
    for l in listings:
        if _matches_user_filters(l, user_filters):
            if not await is_listing_sent_to_user(user_id, l.id):
                filtered_listings.append(l)
    
    # Отправляем результаты
    if filtered_listings:
        await status_msg.edit_text(
            f"✅ <b>Найдено {len(filtered_listings)} объявлений</b>\n\n"
            f"Отправляю результаты...",
            parse_mode=ParseMode.HTML
        )
        
        sent_count = 0
        for listing in filtered_listings[:20]:  # Максимум 20 за раз
            if await send_listing_to_user(callback.bot, user_id, listing):
                sent_count += 1
                await asyncio.sleep(2)
        
        await status_msg.edit_text(
            f"✅ <b>Готово!</b>\n\n"
            f"Отправлено {sent_count} объявлений.\n\n"
            f"Я буду автоматически присылать новые объявления каждые 10 минут, которые подходят под ваши фильтры.\n\n"
            f"Используйте /start чтобы изменить настройки.",
            parse_mode=ParseMode.HTML
        )
    else:
        await status_msg.edit_text(
            "😔 <b>Объявлений не найдено</b>\n\n"
            "Попробуйте изменить фильтры:\n"
            "• Расширьте диапазон цен\n"
            "• Измените количество комнат\n\n"
            "Используйте /start для изменения настроек.",
            parse_mode=ParseMode.HTML
        )


@router.callback_query(F.data == "check_now")
async def cb_check_now(callback: CallbackQuery):
    """Принудительная проверка объявлений для пользователя"""
    user_id = callback.from_user.id
    
    # Сразу отвечаем на callback
    await callback.answer("Проверяю...")
    
    user_filters = await get_user_filters(user_id)
    if not user_filters:
        await callback.message.answer("Сначала настройте фильтры через /start")
        return
    
    status_msg = await callback.message.answer(
        "🔍 <b>Проверяю новые объявления...</b>",
        parse_mode=ParseMode.HTML
    )
    
    # Ищем новые объявления
    aggregator = ListingsAggregator(enabled_sources=DEFAULT_SOURCES)
    
    all_listings = await aggregator.fetch_all_listings(
        city="барановичи",
        min_rooms=1,
        max_rooms=5,
        min_price=0,
        max_price=1000000,
    )
    
    new_listings = []
    for listing in all_listings:
        if _matches_user_filters(listing, user_filters):
            if not await is_listing_sent_to_user(user_id, listing.id):
                dup_check = await is_duplicate_content(
                    listing.rooms, listing.area, listing.address, listing.price
                )
                if not dup_check["is_duplicate"]:
                    new_listings.append(listing)
    
    if new_listings:
        await status_msg.edit_text(
            f"✅ <b>Найдено {len(new_listings)} новых объявлений</b>\n\nОтправляю...",
            parse_mode=ParseMode.HTML
        )
        
        sent_count = 0
        for listing in new_listings[:20]:
            if await send_listing_to_user(callback.bot, user_id, listing):
                sent_count += 1
                await asyncio.sleep(2)
        
        await status_msg.edit_text(
            f"✅ <b>Отправлено {sent_count} новых объявлений</b>",
            parse_mode=ParseMode.HTML
        )
    else:
        await status_msg.edit_text(
            "📭 <b>Новых объявлений нет</b>\n\n"
            "Все подходящие объявления уже были отправлены ранее.",
            parse_mode=ParseMode.HTML
        )


@router.callback_query(F.data == "show_stats")
async def cb_show_stats(callback: CallbackQuery):
    """Показывает статистику для пользователя"""
    user_id = callback.from_user.id
    
    user_filters = await get_user_filters(user_id)
    if not user_filters:
        await callback.answer("Сначала настройте фильтры", show_alert=True)
        return
    
    # Подсчитываем отправленные объявления пользователю
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM user_sent_listings WHERE user_id = ?",
            (user_id,)
        )
        sent_count = (await cursor.fetchone())[0]
    
    await callback.message.answer(
        f"📊 <b>Ваша статистика</b>\n\n"
        f"📨 Получено объявлений: {sent_count}\n"
        f"🚪 Комнат: {user_filters.get('min_rooms', 1)}-{user_filters.get('max_rooms', 4)}\n"
        f"💰 Цена: ${user_filters.get('min_price', 0):,} - ${user_filters.get('max_price', 100000):,}\n\n"
        f"📡 Статус: {'✅ Активен' if user_filters.get('is_active') else '❌ Отключен'}",
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@router.callback_query(F.data == "user_filter_rooms")
async def cb_user_filter_rooms(callback: CallbackQuery):
    """Показывает кнопки выбора комнат для пользователя"""
    builder = InlineKeyboardBuilder()
    
    # Кнопки для выбора диапазонов комнат
    builder.button(text="1-2 комнаты", callback_data="user_rooms_1_2")
    builder.button(text="2-3 комнаты", callback_data="user_rooms_2_3")
    builder.button(text="3-4 комнаты", callback_data="user_rooms_3_4")
    builder.button(text="4+ комнат", callback_data="user_rooms_4_5")
    builder.row()
    builder.button(text="Все (1-5)", callback_data="user_rooms_1_5")
    builder.button(text="Назад", callback_data="setup_filters")
    
    await callback.message.edit_text(
        "🚪 <b>Выберите диапазон комнат:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("user_rooms_"))
async def cb_user_set_rooms(callback: CallbackQuery):
    """Устанавливает количество комнат для пользователя"""
    user_id = callback.from_user.id
    parts = callback.data.split("_")
    min_rooms = int(parts[2])
    max_rooms = int(parts[3])
    
    user_filters = await get_user_filters(user_id)
    await set_user_filters(
        user_id,
        city=user_filters.get("city", "барановичи") if user_filters else "барановичи",
        min_rooms=min_rooms,
        max_rooms=max_rooms,
        min_price=user_filters.get("min_price", 0) if user_filters else 0,
        max_price=user_filters.get("max_price", 100000) if user_filters else 100000,
        is_active=True
    )
    
    await callback.answer(f"✅ Комнаты: {min_rooms}-{max_rooms}")
    await cb_setup_filters(callback)


@router.callback_query(F.data == "user_filter_price")
async def cb_user_filter_price(callback: CallbackQuery):
    """Показывает меню настройки цены (в 2 шага)"""
    user_id = callback.from_user.id
    user_filters = await get_user_filters(user_id)
    
    current_min = user_filters.get("min_price", 0) if user_filters else 0
    current_max = user_filters.get("max_price", 100000) if user_filters else 100000
    
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Цена ОТ", callback_data="user_price_min")
    builder.button(text="💰 Цена ДО", callback_data="user_price_max")
    builder.row()
    builder.button(text="✅ Готово", callback_data="setup_filters")
    builder.button(text="🔄 Сбросить", callback_data="user_price_reset")
    
    await callback.message.edit_text(
        f"💰 <b>Настройка цены (USD)</b>\n\n"
        f"Текущие значения:\n"
        f"• Цена ОТ: ${current_min:,}\n"
        f"• Цена ДО: ${current_max:,}\n\n"
        f"Нажмите кнопку для изменения или введите значение вручную:\n"
        f"<code>/pricefrom 20000</code> — цена от $20,000\n"
        f"<code>/priceto 50000</code> — цена до $50,000",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data == "user_price_min")
async def cb_user_price_min(callback: CallbackQuery):
    """Запрашивает минимальную цену"""
    await callback.message.edit_text(
        "💰 <b>Введите минимальную цену (USD)</b>\n\n"
        "Например:\n"
        "• <code>0</code> — без ограничения снизу\n"
        "• <code>20000</code> — от $20,000\n"
        "• <code>30000</code> — от $30,000\n\n"
        "Или используйте команду:\n"
        "<code>/pricefrom 20000</code>",
        parse_mode=ParseMode.HTML
    )
    await callback.answer("Введите цену от или используйте /pricefrom")


@router.callback_query(F.data == "user_price_max")
async def cb_user_price_max(callback: CallbackQuery):
    """Запрашивает максимальную цену"""
    await callback.message.edit_text(
        "💰 <b>Введите максимальную цену (USD)</b>\n\n"
        "Например:\n"
        "• <code>50000</code> — до $50,000\n"
        "• <code>80000</code> — до $80,000\n"
        "• <code>1000000</code> — без ограничения сверху\n\n"
        "Или используйте команду:\n"
        "<code>/priceto 50000</code>",
        parse_mode=ParseMode.HTML
    )
    await callback.answer("Введите цену до или используйте /priceto")


@router.callback_query(F.data == "user_price_reset")
async def cb_user_price_reset(callback: CallbackQuery):
    """Сбрасывает фильтр цены"""
    user_id = callback.from_user.id
    user_filters = await get_user_filters(user_id)
    
    await set_user_filters(
        user_id,
        city=user_filters.get("city", "барановичи") if user_filters else "барановичи",
        min_rooms=user_filters.get("min_rooms", 1) if user_filters else 1,
        max_rooms=user_filters.get("max_rooms", 4) if user_filters else 4,
        min_price=0,
        max_price=1000000,
        is_active=True
    )
    
    await callback.answer("✅ Цена сброшена: $0 - $1,000,000")
    await cb_user_filter_price(callback)




@router.callback_query(F.data == "filter_rooms")
async def cb_filter_rooms(callback: CallbackQuery):
    """Показывает кнопки выбора комнат (старая версия для обратной совместимости)"""
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
    """Установка фильтра по количеству комнат для пользователя"""
    user_id = message.from_user.id
    try:
        args = message.text.split()[1:]
        
        if len(args) == 0:
            await message.answer(
                "🚪 <b>Настройка фильтра комнат</b>\n\n"
                "Используйте диапазоны:\n"
                "• <code>/setrooms 1 2</code> — 1-2 комнаты\n"
                "• <code>/setrooms 2 3</code> — 2-3 комнаты\n"
                "• <code>/setrooms 3 4</code> — 3-4 комнаты\n"
                "• <code>/setrooms 4 5</code> — 4+ комнат\n\n"
                "Или нажмите /start для интерактивного выбора",
                parse_mode=ParseMode.HTML
            )
            return
        
        if len(args) == 1:
            rooms = int(args[0])
            if rooms < 1 or rooms > 5:
                await message.answer("⚠️ Комнат может быть от 1 до 5.")
                return
            min_rooms = max_rooms = rooms
        else:
            min_rooms = int(args[0])
            max_rooms = int(args[1])
        
        if min_rooms < 1 or max_rooms > 5 or min_rooms > max_rooms:
            await message.answer("⚠️ Неверные значения. Комнат может быть от 1 до 5.")
            return
        
        user_filters = await get_user_filters(user_id)
        await set_user_filters(
            user_id,
            city=user_filters.get("city", "барановичи") if user_filters else "барановичи",
            min_rooms=min_rooms,
            max_rooms=max_rooms,
            min_price=user_filters.get("min_price", 0) if user_filters else 0,
            max_price=user_filters.get("max_price", 1000000) if user_filters else 1000000,
            is_active=True
        )
        
        if min_rooms == max_rooms:
            await message.answer(f"✅ Фильтр обновлен!\nТолько {min_rooms}-комнатные квартиры")
        else:
            await message.answer(f"✅ Фильтр обновлен!\nКомнат: {min_rooms}-{max_rooms}")
        
    except ValueError:
        await message.answer(
            "⚠️ Неверный формат!\n\n"
            "Примеры:\n"
            "/setrooms 1 2 — 1-2 комнаты\n"
            "/setrooms 2 3 — 2-3 комнаты"
        )


@router.message(Command("pricefrom"))
async def cmd_price_from(message: Message):
    """Установка минимальной цены для пользователя"""
    user_id = message.from_user.id
    try:
        args = message.text.split()[1:]
        if not args:
            await message.answer(
                "💰 <b>Установка минимальной цены</b>\n\n"
                "Используйте:\n"
                "• <code>/pricefrom 20000</code> — цена от $20,000\n"
                "• <code>/pricefrom 0</code> — без ограничения снизу",
                parse_mode=ParseMode.HTML
            )
            return
        
        min_price = int(args[0])
        if min_price < 0 or min_price > 1000000:
            await message.answer("⚠️ Неверное значение (0 - 1,000,000)")
            return
        
        user_filters = await get_user_filters(user_id)
        if not user_filters:
            await set_user_filters(user_id, min_price=min_price)
        else:
            await set_user_filters(
                user_id,
                city=user_filters.get("city", "барановичи"),
                min_rooms=user_filters.get("min_rooms", 1),
                max_rooms=user_filters.get("max_rooms", 4),
                min_price=min_price,
                max_price=user_filters.get("max_price", 1000000),
                is_active=True
            )
        
        await message.answer(f"✅ Минимальная цена установлена: ${min_price:,}")
        
    except ValueError:
        await message.answer("⚠️ Неверный формат! Используйте: /pricefrom 20000")


@router.message(Command("priceto"))
async def cmd_price_to(message: Message):
    """Установка максимальной цены для пользователя"""
    user_id = message.from_user.id
    try:
        args = message.text.split()[1:]
        if not args:
            await message.answer(
                "💰 <b>Установка максимальной цены</b>\n\n"
                "Используйте:\n"
                "• <code>/priceto 50000</code> — цена до $50,000\n"
                "• <code>/priceto 1000000</code> — без ограничения сверху",
                parse_mode=ParseMode.HTML
            )
            return
        
        max_price = int(args[0])
        if max_price < 0 or max_price > 1000000:
            await message.answer("⚠️ Неверное значение (0 - 1,000,000)")
            return
        
        user_filters = await get_user_filters(user_id)
        if not user_filters:
            await set_user_filters(user_id, max_price=max_price)
        else:
            await set_user_filters(
                user_id,
                city=user_filters.get("city", "барановичи"),
                min_rooms=user_filters.get("min_rooms", 1),
                max_rooms=user_filters.get("max_rooms", 4),
                min_price=user_filters.get("min_price", 0),
                max_price=max_price,
                is_active=True
            )
        
        await message.answer(f"✅ Максимальная цена установлена: ${max_price:,}")
        
    except ValueError:
        await message.answer("⚠️ Неверный формат! Используйте: /priceto 50000")


@router.message(Command("setprice"))
async def cmd_set_price(message: Message):
    """Установка фильтра по цене (старая команда для обратной совместимости)"""
    user_id = message.from_user.id
    try:
        args = message.text.split()[1:]
        
        if len(args) == 0:
            await message.answer(
                "💰 <b>Настройка фильтра цены</b>\n\n"
                "Используйте:\n"
                "• <code>/setprice 50000</code> — до $50,000\n"
                "• <code>/setprice 20000 50000</code> — от $20k до $50k\n\n"
                "Или по отдельности:\n"
                "• <code>/pricefrom 20000</code> — цена от\n"
                "• <code>/priceto 50000</code> — цена до",
                parse_mode=ParseMode.HTML
            )
            return
        
        if len(args) == 1:
            max_price = int(args[0])
            min_price = 0
        else:
            min_price = int(args[0])
            max_price = int(args[1])
        
        if min_price < 0 or max_price > 1000000 or min_price > max_price:
            await message.answer("⚠️ Неверные значения цены (0 - 1,000,000).")
            return
        
        user_filters = await get_user_filters(user_id)
        await set_user_filters(
            user_id,
            city=user_filters.get("city", "барановичи") if user_filters else "барановичи",
            min_rooms=user_filters.get("min_rooms", 1) if user_filters else 1,
            max_rooms=user_filters.get("max_rooms", 4) if user_filters else 4,
            min_price=min_price,
            max_price=max_price,
            is_active=True
        )
        
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


@router.message(Command("testai"))
async def cmd_test_ai(message: Message):
    """Тестирует ИИ-оценщик на примере объявления"""
    if not AI_VALUATOR_AVAILABLE or not valuate_listing:
        await message.answer(
            "❌ <b>ИИ-оценщик не настроен</b>\n\n"
            "Для активации:\n"
            "1. Получи API ключ Groq: https://console.groq.com/keys\n"
            "2. Добавь переменную GROQ_API_KEY в Railway\n\n"
            "Подробнее: см. AI_SETUP.md",
            parse_mode=ParseMode.HTML
        )
        return
    
    await message.answer("🤖 Тестирую ИИ-оценщик...")
    
    # Создаем тестовое объявление
    from scrapers.base import Listing
    test_listing = Listing(
        id="test_123",
        source="Test",
        title="2-комн. квартира",
        price=35000,
        price_formatted="$35,000",
        rooms=2,
        area=50.0,
        address="ул. Советская, Барановичи",
        url="https://example.com",
        floor="3/5",
        year_built="2010",
        currency="USD",
        price_usd=35000,
        price_byn=0,
        price_per_sqm=700,
        price_per_sqm_formatted="700 $/м²"
    )
    
    try:
        ai_valuation = await asyncio.wait_for(valuate_listing(test_listing), timeout=10.0)
        
        if ai_valuation:
            fair_price = ai_valuation.get("fair_price_usd", 0)
            is_overpriced = ai_valuation.get("is_overpriced", False)
            assessment = ai_valuation.get("assessment", "")
            
            status = "🔴 Завышена" if is_overpriced else "🟢 Справедлива"
            
            await message.answer(
                f"✅ <b>ИИ-оценщик работает!</b>\n\n"
                f"📊 <b>Тестовое объявление:</b>\n"
                f"2-комн., 50 м², $35,000\n\n"
                f"🤖 <b>ИИ-оценка:</b>\n"
                f"Справедливая цена: ${fair_price:,}\n"
                f"Статус: {status}\n\n"
                f"💡 <i>{assessment}</i>",
                parse_mode=ParseMode.HTML
            )
        else:
            await message.answer(
                "⚠️ <b>ИИ-оценщик не вернул результат</b>\n\n"
                "Проверь:\n"
                "• Правильность API ключа\n"
                "• Логи ошибок: /logs",
                parse_mode=ParseMode.HTML
            )
    except asyncio.TimeoutError:
        await message.answer(
            "⏱ <b>Таймаут запроса</b>\n\n"
            "ИИ-оценщик не ответил за 10 секунд.\n"
            "Проверь подключение к интернету.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(
            f"❌ <b>Ошибка тестирования</b>\n\n"
            f"Детали: {str(e)}\n\n"
            f"Проверь логи: /logs",
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
