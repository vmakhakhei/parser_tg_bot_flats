"""
Telegram бот для мониторинга объявлений о квартирах
"""
import asyncio
import logging
import aiosqlite
import json
import base64
import time
from typing import List, Dict, Any, Optional

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, InputMediaPhoto, CallbackQuery
from aiogram.filters import Command, CommandStart
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, MAX_PHOTOS, DATABASE_PATH
from database import (
    init_database, 
    is_listing_sent,
    is_duplicate_content,
    mark_listing_sent,
    get_user_filters,
    set_user_filters,
    is_listing_sent_to_user,
    mark_listing_sent_to_user,
    get_active_users,
    save_ai_selected_listings,
    get_ai_selected_listings,
    is_listing_ai_valuated,
    mark_listing_ai_valuated,
    get_listing_by_id,
)
from scrapers.aggregator import ListingsAggregator
from scrapers.base import Listing
from error_logger import error_logger, log_error, log_warning, log_info

# ИИ-оценщик (опционально)
try:
    from ai_valuator import valuate_listing, select_best_listings
    AI_VALUATOR_AVAILABLE = True
except ImportError:
    AI_VALUATOR_AVAILABLE = False
    valuate_listing = None
    select_best_listings = None

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Роутер для обработки команд
router = Router()


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ СИНХРОНИЗАЦИИ С TURSO ==========

async def sync_user_filters_to_turso(
    user_id: int,
    city: str = "барановичи",
    min_rooms: int = 1,
    max_rooms: int = 4,
    min_price: int = 0,
    max_price: int = 100000,
    is_active: bool = True,
    ai_mode: bool = False,
    seller_type: Optional[str] = None
) -> bool:
    """
    Синхронизирует фильтры пользователя в Turso
    Конвертирует формат из старой БД (min_rooms/max_rooms) в новый формат (rooms как список)
    """
    try:
        from database import set_user_filters_turso
        
        # Конвертируем min_rooms/max_rooms в список комнат
        rooms = list(range(min_rooms, max_rooms + 1)) if min_rooms > 0 and max_rooms > 0 else None
        
        return await set_user_filters_turso(
            telegram_id=user_id,
            min_price=min_price,
            max_price=max_price if max_price < 1000000 else None,
            rooms=rooms,
            region=city,
            active=is_active,
            ai_mode=ai_mode,
            seller_type=seller_type
        )
    except Exception as e:
        logger.warning(f"Не удалось синхронизировать фильтры в Turso: {e}")
        return False


async def get_user_filters_unified(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Получает фильтры пользователя из Turso или старой БД
    Приоритет: Turso > старая БД
    """
    # Сначала пробуем Turso
    try:
        from database import get_user_filters_turso
        turso_filters = await get_user_filters_turso(user_id)
        if turso_filters:
            # Конвертируем формат для совместимости
            rooms = turso_filters.get("rooms", [])
            if rooms and len(rooms) > 0:
                turso_filters["min_rooms"] = min(rooms)
                turso_filters["max_rooms"] = max(rooms)
            else:
                turso_filters["min_rooms"] = 1
                turso_filters["max_rooms"] = 4
            turso_filters["is_active"] = turso_filters.get("active", True)
            turso_filters["city"] = turso_filters.get("region", "барановичи")
            return turso_filters
    except Exception as e:
        logger.warning(f"Не удалось получить фильтры из Turso: {e}")
    
    # Если не получилось из Turso, используем старую БД
    return await get_user_filters(user_id)

# FSM состояния для ввода цены
class PriceStates(StatesGroup):
    waiting_for_min_price = State()
    waiting_for_max_price = State()

# FSM состояния для ввода города
class CityStates(StatesGroup):
    waiting_for_city = State()

# FSM состояния для пошаговой настройки фильтров
class SetupStates(StatesGroup):
    waiting_for_city = State()
    waiting_for_rooms = State()
    waiting_for_price_min = State()
    waiting_for_price_max = State()
    waiting_for_seller = State()
    waiting_for_mode = State()

# Список областных центров и крупных городов Беларуси
BELARUS_CITIES = [
    # Областные центры
    ("Минск", "минск"),
    ("Гомель", "гомель"),
    ("Могилёв", "могилёв"),
    ("Витебск", "витебск"),
    ("Гродно", "гродно"),
    ("Брест", "брест"),
    # Крупные города
    ("Барановичи", "барановичи"),
    ("Бобруйск", "бобруйск"),
    ("Пинск", "пинск"),
    ("Орша", "орша"),
    ("Мозырь", "мозырь"),
    ("Солигорск", "солигорск"),
    ("Новополоцк", "новополоцк"),
    ("Лида", "лида"),
    ("Полоцк", "полоцк"),
    ("Молодечно", "молодечно"),
    ("Борисов", "борисов"),
    ("Жлобин", "жлобин"),
    ("Слуцк", "слуцк"),
    ("Кобрин", "кобрин"),
]

def normalize_city_name(city: str) -> str:
    """Нормализует название города для сравнения"""
    return city.lower().strip().replace("ё", "е").replace("й", "и")

def validate_city(city: str) -> tuple[bool, Optional[str]]:
    """
    Валидирует название города.
    Возвращает (is_valid, normalized_city_name)
    """
    normalized = normalize_city_name(city)
    
    # Проверяем точное совпадение
    for display_name, normalized_name in BELARUS_CITIES:
        if normalized == normalized_name:
            return True, normalized_name
    
    # Проверяем частичное совпадение (для опечаток)
    for display_name, normalized_name in BELARUS_CITIES:
        if normalized_name.startswith(normalized) or normalized.startswith(normalized_name):
            if len(normalized) >= 3:  # Минимум 3 символа для частичного совпадения
                return True, normalized_name
    
    # Если не найдено, разрешаем ввод вручную (но предупреждаем)
    if len(normalized) >= 2:  # Минимум 2 символа
        return True, normalized
    
    return False, None

# Список источников по умолчанию (работающие парсеры)
# kufar - Kufar.by API (30 объявлений)
# etagi - Etagi.com HTML парсинг
# etagi - Etagi.com HTML парсинг (30 объявлений)
DEFAULT_SOURCES = ["kufar", "etagi"]


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
        renovation_state = ai_valuation.get("renovation_state", "")
        recommendations = ai_valuation.get("recommendations", "")
        value_score = ai_valuation.get("value_score", 0)
        
        if fair_price > 0:
            price_status = "🔴 Завышена" if is_overpriced else "🟢 Справедлива"
            lines.append("")
            lines.append(f"🤖 <b>ИИ-оценка:</b> ${fair_price:,} {price_status}".replace(",", " "))
            
            # Оценка соотношения цена/качество
            if value_score > 0:
                score_emoji = "⭐" * min(value_score, 5)  # До 5 звезд
                lines.append(f"⭐ <b>Оценка:</b> {value_score}/10 {score_emoji}")
            
            # Состояние ремонта
            if renovation_state:
                renovation_emoji = {
                    "отличное": "✨",
                    "хорошее": "✅",
                    "среднее": "⚪",
                    "требует ремонта": "⚠️",
                    "плохое": "❌"
                }.get(renovation_state.lower(), "📋")
                lines.append(f"{renovation_emoji} <b>Ремонт:</b> {renovation_state}")
            
            # Детальная оценка
            if assessment:
                lines.append(f"💡 <i>{assessment}</i>")
            
            # Рекомендации
            if recommendations:
                lines.append("")
                lines.append(f"📋 <b>Рекомендации:</b>")
                lines.append(f"<i>{recommendations}</i>")
            
            lines.append("")
    
    # Цена за м² (вычисляется автоматически в Listing.__post_init__)
    if listing.price_per_sqm_formatted:
        lines.append(f"📊 <b>Цена/м²:</b> {listing.price_per_sqm_formatted}")
    
    # Основная информация
    lines.append(f"🚪 <b>Комнат:</b> {listing.rooms}")
    lines.append(f"📐 <b>Площадь:</b> {listing.area} м²")
    
    # Жилая площадь (если отличается от общей)
    if listing.living_area > 0 and listing.living_area != listing.area:
        lines.append(f"🛋️ <b>Жилая площадь:</b> {listing.living_area} м²")
    
    # Площадь кухни
    if listing.kitchen_area > 0:
        lines.append(f"🍳 <b>Кухня:</b> {listing.kitchen_area} м²")
    
    # Этаж
    if listing.floor:
        lines.append(f"🏢 <b>Этаж:</b> {listing.floor}")
    elif listing.total_floors:
        # Если есть только этажность без этажа
        lines.append(f"🏢 <b>Этажность:</b> {listing.total_floors} этажей")
    
    # Год постройки
    if listing.year_built:
        lines.append(f"📅 <b>Год:</b> {listing.year_built}")
    
    # Тип дома
    if listing.house_type:
        lines.append(f"🏗️ <b>Тип дома:</b> {listing.house_type}")
    
    # Балкон/лоджия
    if listing.balcony:
        balcony_emoji = "✅" if listing.balcony.lower() in ["есть", "да", "1"] else "❌"
        lines.append(f"{balcony_emoji} <b>Балкон:</b> {listing.balcony}")
    
    # Санузел
    if listing.bathroom:
        lines.append(f"🚿 <b>Санузел:</b> {listing.bathroom}")
    
    # Состояние ремонта
    if listing.renovation_state:
        renovation_emoji = {
            "отличное": "✨",
            "хорошее": "✅",
            "среднее": "⚪",
            "требует ремонта": "⚠️",
            "плохое": "❌",
            "вторичное": "📋"
        }.get(listing.renovation_state.lower(), "📋")
        lines.append(f"{renovation_emoji} <b>Ремонт:</b> {listing.renovation_state}")
    
    # Тип продавца
    if listing.is_company is not None:
        seller_type = "🏢 Агентство" if listing.is_company else "👤 Собственник"
        lines.append(f"{seller_type}")
    
    # Дата создания объявления
    if listing.created_at:
        # Форматируем дату для вывода
        try:
            from datetime import datetime
            date_obj = datetime.strptime(listing.created_at, "%Y-%m-%d")
            today = datetime.now()
            days_diff = (today - date_obj).days
            
            if days_diff == 0:
                date_display = "сегодня"
            elif days_diff == 1:
                date_display = "вчера"
            elif days_diff < 7:
                date_display = f"{days_diff} дн. назад"
            else:
                date_display = date_obj.strftime("%d.%m.%Y")
        except:
            date_display = listing.created_at
        
        lines.append(f"📆 <b>Опубликовано:</b> {date_display}")
    
    # Описание (первые 300 символов)
    if listing.description:
        description_text = listing.description.strip()
        if len(description_text) > 300:
            description_text = description_text[:300] + "..."
        lines.append("")
        lines.append(f"📝 <b>Описание:</b>")
        lines.append(f"<i>{description_text}</i>")
    
    lines.append("")
    lines.append(f"📍 <b>Адрес:</b> {listing.address}")
    lines.append(f"🌐 <b>Источник:</b> {listing.source}")
    lines.append("")
    lines.append(f"🔗 <a href=\"{listing.url}\">Открыть объявление</a>")
    
    return "\n".join(lines)


async def send_listing_to_user(bot: Bot, user_id: int, listing: Listing, use_ai_valuation: bool = False) -> bool:
    """Отправляет объявление пользователю
    
    Args:
        bot: Экземпляр бота
        user_id: ID пользователя
        listing: Объявление для отправки
        use_ai_valuation: Если True, будет выполнена ИИ-оценка (по умолчанию False - без оценки)
    """
    try:
        # ИИ-оценка выполняется ТОЛЬКО если явно запрошена
        ai_valuation = None
        if use_ai_valuation and AI_VALUATOR_AVAILABLE and valuate_listing:
            try:
                # Задержка между запросами к ИИ (чтобы не превысить rate limit)
                # Groq: 30 запросов/минуту = ~2 секунды между запросами
                await asyncio.sleep(2)
                
                # Таймаут для ИИ-оценки (максимум 20 секунд - включает инспекцию страницы)
                ai_valuation = await asyncio.wait_for(valuate_listing(listing), timeout=20.0)
                if ai_valuation:
                    log_info("ai", f"ИИ-оценка получена для {listing.id}: ${ai_valuation.get('fair_price_usd', 0):,}")
            except asyncio.TimeoutError:
                log_warning("ai", f"Таймаут ИИ-оценки для {listing.id}")
            except Exception as e:
                log_warning("ai", f"Ошибка ИИ-оценки для {listing.id}: {e}")
        
        message_text = format_listing_message(listing, ai_valuation)
        photos = listing.photos
        
        # Создаем кнопку "ИИ Оценка квартиры" если ИИ доступен, оценка не была выполнена и объявление еще не оценено
        reply_markup = None
        if not use_ai_valuation and AI_VALUATOR_AVAILABLE and valuate_listing:
            # Проверяем, было ли объявление уже оценено через ИИ
            if not await is_listing_ai_valuated(user_id, listing.id):
                # Используем только listing_id в callback_data (Telegram ограничивает до 64 байт)
                builder = InlineKeyboardBuilder()
                builder.button(text="🤖 ИИ Оценка квартиры", callback_data=f"ai_val_{listing.id}")
                builder.adjust(1)
                reply_markup = builder.as_markup()
        
        if photos:
            # Отправляем медиагруппу с фотографиями
            media_group = []
            for i, photo_url in enumerate(photos[:MAX_PHOTOS]):
                if i == 0:
                    # Первое фото с подписью и кнопкой
                    media_group.append(
                        InputMediaPhoto(
                            media=photo_url,
                            caption=message_text,
                            parse_mode=ParseMode.HTML
                        )
                    )
                else:
                    media_group.append(InputMediaPhoto(media=photo_url))
            
            # Отправляем медиагруппу
            await bot.send_media_group(
                chat_id=user_id,
                media=media_group
            )
            
            # Если есть кнопка ИИ-оценки, отправляем её отдельным сообщением после медиагруппы
            # (Telegram не поддерживает кнопки в медиагруппе напрямую)
            if reply_markup:
                await bot.send_message(
                    chat_id=user_id,
                    text="🤖 <b>Хотите получить ИИ-оценку этой квартиры?</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
            )
        else:
            # Без фотографий - просто текст с кнопкой
            await bot.send_message(
                chat_id=user_id,
                text=message_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
                reply_markup=reply_markup
            )
        
        # Отмечаем как отправленное пользователю и глобально
        await mark_listing_sent_to_user(user_id, listing.id)
        await mark_listing_sent(listing.to_dict())  # Глобальная дедупликация
        logger.info(f"Отправлено пользователю {user_id}: {listing.id} ({listing.source})")
        return True
        
    except Exception as e:
        error_logger.log_error("bot", f"Ошибка отправки объявления {listing.id} пользователю {user_id}", e)
        return False


def _validate_user_filters(user_filters: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Проверяет валидность фильтров пользователя. Возвращает (is_valid, error_message)"""
    if not user_filters:
        return False, "Фильтры не настроены"
    
    if not user_filters.get("city"):
        return False, "Город не выбран"
    
    return True, None


async def check_new_listings(bot: Bot):
    """Проверяет новые объявления и отправляет их активным пользователям"""
    global _filter_log_counters
    
    logger.info("=" * 50)
    logger.info("Проверка новых объявлений со всех источников...")
    
    # Сбрасываем счетчики логирования для всех пользователей
    _filter_log_counters.clear()
    
    # Получаем список активных пользователей
    active_users = await get_active_users()
    
    if not active_users:
        logger.info("Нет активных пользователей")
        return
    
    logger.info(f"Активных пользователей: {len(active_users)}")
    
    total_sent = 0
    
    # Для каждого пользователя проверяем объявления по его фильтрам
    for user_id in active_users:
        user_filters = await get_user_filters(user_id)
        if not user_filters or not user_filters.get("is_active"):
            continue
        
        # Проверяем валидность фильтров
        is_valid, error_msg = _validate_user_filters(user_filters)
        if not is_valid:
            log_warning("bot", f"Пропускаю пользователя {user_id}: {error_msg}")
            continue
        
        # Сбрасываем счетчик логирования для этого пользователя
        _filter_log_counters[user_id] = {"filtered": 0, "passed": 0}
        log_info("filter", f"[user_{user_id}] 📋 Применяю фильтры: город={user_filters.get('city')}, комнаты={user_filters.get('min_rooms')}-{user_filters.get('max_rooms')}, цена=${user_filters.get('min_price'):,}-${user_filters.get('max_price'):,}, продавец={user_filters.get('seller_type') or 'Все'}, режим={'ИИ' if user_filters.get('ai_mode') else 'Обычный'}")
        
        # Получаем город пользователя
        user_city = user_filters.get("city")
        
        # ========== КЭШИРОВАНИЕ: Сначала проверяем кэш в Turso ==========
        from database import (
            create_or_update_user_turso,
            get_user_filters_turso,
            set_user_filters_turso,
            get_active_users_turso,
            sync_ads_from_kufar_turso,
            build_dynamic_query_turso,
            check_api_query_cache_turso,
            save_api_query_cache_turso,
            get_cached_listings_by_filters_turso, 
            cache_listings_batch_turso,
            cached_listing_to_listing_turso
        )
        from config import USE_TURSO_CACHE
        
        cached_listings = []
        if USE_TURSO_CACHE:
            try:
                cached_data = await get_cached_listings_by_filters_turso(
                    city=user_city,
                    min_rooms=user_filters.get("min_rooms", 1),
                    max_rooms=user_filters.get("max_rooms", 5),
                    min_price=user_filters.get("min_price", 0),
                    max_price=user_filters.get("max_price", 1000000),
                    limit=200  # Берем больше, чтобы было из чего выбирать
                )
                
                # Конвертируем из словарей в объекты Listing
                for cached_dict in cached_data:
                    try:
                        listing = cached_listing_to_listing_turso(cached_dict)
                        if listing:
                            cached_listings.append(listing)
                    except Exception as e:
                        logger.warning(f"Ошибка конвертации объявления из кэша: {e}")
                        continue
                
                logger.info(f"📦 Найдено {len(cached_listings)} объявлений в кэше для пользователя {user_id}")
            except Exception as e:
                logger.warning(f"Ошибка получения данных из кэша, используем парсинг: {e}")
        
        # Получаем объявления для города пользователя
        # Парсим сайты только если кэша нет или мало объявлений
        all_listings = []
        if len(cached_listings) < 10:  # Если в кэше меньше 10 объявлений - парсим
            logger.info(f"🔍 В кэше мало объявлений ({len(cached_listings)}), парсим сайты...")
            aggregator = ListingsAggregator(enabled_sources=DEFAULT_SOURCES)
            
            parsed_listings = await aggregator.fetch_all_listings(
                city=user_city,
                min_rooms=user_filters.get("min_rooms", 1),
                max_rooms=user_filters.get("max_rooms", 5),
                min_price=user_filters.get("min_price", 0),
                max_price=user_filters.get("max_price", 1000000),
            )
            
            # Сохраняем все найденные объявления в кэш
            if USE_TURSO_CACHE and parsed_listings:
                try:
                    saved_count = await cache_listings_batch_turso(parsed_listings)
                    logger.info(f"💾 Сохранено {saved_count} объявлений в кэш")
                except Exception as e:
                    logger.warning(f"Ошибка сохранения в кэш: {e}")
            
            # Объединяем кэш и новые объявления (убираем дубликаты по ID)
            existing_ids = {l.id for l in cached_listings}
            new_listings = [l for l in parsed_listings if l.id not in existing_ids]
            all_listings = cached_listings + new_listings
        else:
            # Используем только кэш
            logger.info(f"✅ Используем кэш ({len(cached_listings)} объявлений), парсинг не требуется")
            all_listings = cached_listings
    
        logger.info(f"Для пользователя {user_id} (город: {user_city}) найдено объявлений: {len(all_listings)}")
        
        # Проверяем режим работы пользователя
        if user_filters.get("ai_mode"):
            # ИИ-режим: передаем все объявления в функцию ИИ-режима
            await check_new_listings_ai_mode(bot, user_id, user_filters, all_listings)
        else:
            # Обычный режим: отправляем все подходящие объявления (ИИ-оценка только по запросу через кнопку)
            user_new_count = 0
            
            for listing in all_listings:
                # Проверяем соответствие фильтрам пользователя
                if not _matches_user_filters(listing, user_filters, user_id=user_id, log_details=True):
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
                
                # Отправляем объявление пользователю БЕЗ ИИ-оценки (обычный режим)
                if await send_listing_to_user(bot, user_id, listing, use_ai_valuation=False):
                    user_new_count += 1
                    total_sent += 1
                    # Задержка между сообщениями чтобы не получить бан
                    await asyncio.sleep(1)
            
            if user_new_count > 0:
                logger.info(f"Пользователю {user_id} отправлено: {user_new_count} объявлений")
    
    if total_sent > 0:
        logger.info(f"✅ Всего отправлено новых объявлений: {total_sent}")
    else:
        logger.info("Новых объявлений нет")
    
    logger.info("=" * 50)


async def check_new_listings_ai_mode(
    bot: Bot, 
    user_id: int, 
    user_filters: Dict[str, Any], 
    all_listings: List[Listing],
    status_msg: Optional[Message] = None
):
    """ИИ-режим: собирает все подходящие объявления, отправляет ИИ для выбора лучших"""
    global _filter_log_counters
    
    logger.info(f"🤖 ИИ-режим для пользователя {user_id}")
    
    # Сбрасываем счетчик логирования для этого пользователя
    _filter_log_counters[user_id] = {"filtered": 0, "passed": 0}
    
    # Логируем фильтры пользователя
    log_info("filter", f"[user_{user_id}] 📋 Применяю фильтры: город={user_filters.get('city')}, комнаты={user_filters.get('min_rooms')}-{user_filters.get('max_rooms')}, цена=${user_filters.get('min_price'):,}-${user_filters.get('max_price'):,}, продавец={user_filters.get('seller_type') or 'Все'}")
    
    # Собираем ВСЕ подходящие объявления (без дедупликации)
    # ВАЖНО: НЕ проверяем is_listing_sent_to_user - берем ВСЕ подходящие объявления
    # ВАЖНО: НЕ проверяем is_duplicate_content - для ИИ-анализа нужны ВСЕ объявления, включая дубликаты
    candidate_listings = []
    filtered_out = 0
    
    for listing in all_listings:
        # Проверяем соответствие фильтрам пользователя
        if not _matches_user_filters(listing, user_filters, user_id=user_id, log_details=True):
            filtered_out += 1
            continue
        
        # Добавляем ВСЕ подходящие объявления, включая уже отправленные и дубликаты
        # ИИ должен проанализировать все варианты, чтобы выбрать лучшие
        candidate_listings.append(listing)
    
    seller_type = user_filters.get("seller_type")
    seller_filter_text = f", фильтр продавца: {seller_type if seller_type else 'Все'}"
    counter = _filter_log_counters.get(user_id, {"filtered": 0, "passed": 0})
    logger.info(f"ИИ-режим: всего {len(all_listings)}, отфильтровано {filtered_out}, кандидатов для анализа {len(candidate_listings)}{seller_filter_text}")
    logger.info(f"[user_{user_id}] 📊 Статистика фильтрации: отфильтровано {counter['filtered']} (логировано), прошло {counter['passed']} (логировано)")
    
    if not candidate_listings:
        logger.info(f"Пользователю {user_id} нет новых объявлений для ИИ-анализа")
        return
    
    logger.info(f"Найдено {len(candidate_listings)} кандидатов для ИИ-анализа")
    
    # Получаем предыдущие выбранные ИИ варианты для сравнения
    previous_selected = await get_ai_selected_listings(user_id)
    has_previous_selections = len(previous_selected) > 0
    
    # Если есть предыдущие выборы ИИ, оцениваем новые объявления и сравниваем
    if has_previous_selections and AI_VALUATOR_AVAILABLE and valuate_listing:
        logger.info(f"Найдено {len(previous_selected)} предыдущих выборов ИИ, оцениваю новые объявления...")
        await evaluate_and_compare_new_listings(bot, user_id, candidate_listings, previous_selected, user_filters)
        return
    
    # Отправляем уведомление пользователю о начале анализа (сохраняем для редактирования)
    status_msg = None
    try:
        # Рассчитываем примерное количество батчей для оценки времени
        total_candidates = len(candidate_listings)
        if total_candidates <= 15:
            estimated_batches_round1 = 1
        else:
            estimated_batches_round1 = (total_candidates + 11) // 12  # Округляем вверх
        
        # Рассчитываем примерное время обработки
        # Инспекция: ~7 секунд (20 объявлений параллельно)
        # Первый раунд батчей: (batches - 1) * 15 сек (задержка между батчами) + batches * 3 сек (обработка батча)
        # Дополнительные раунды: если получилось больше 12 вариантов, делаем еще раунды
        # Финальное сравнение: ~20 секунд
        inspection_time = 7
        batch_delay = 15  # Задержка между батчами
        batch_processing_time = 3  # Время обработки одного батча
        final_comparison_time = 20
        
        # Время первого раунда батчей
        if estimated_batches_round1 == 1:
            round1_time = batch_processing_time
        else:
            round1_time = (estimated_batches_round1 - 1) * batch_delay + estimated_batches_round1 * batch_processing_time
        
        # Оцениваем количество дополнительных раундов
        # Из каждого батча берем 2 варианта, поэтому максимум вариантов после первого раунда = batches * 2
        max_results_after_round1 = estimated_batches_round1 * 2
        
        # Если получилось больше 12, нужен второй раунд
        additional_rounds_time = 0
        if max_results_after_round1 > 12:
            # Второй раунд: разбиваем на батчи по 12
            estimated_batches_round2 = (max_results_after_round1 + 11) // 12
            if estimated_batches_round2 == 1:
                round2_time = batch_processing_time
            else:
                round2_time = (estimated_batches_round2 - 1) * batch_delay + estimated_batches_round2 * batch_processing_time
            additional_rounds_time = round2_time
            
            # Если и после второго раунда больше 12, нужен третий раунд (редко, но возможно)
            max_results_after_round2 = estimated_batches_round2 * 2
            if max_results_after_round2 > 12:
                estimated_batches_round3 = (max_results_after_round2 + 11) // 12
                if estimated_batches_round3 == 1:
                    round3_time = batch_processing_time
                else:
                    round3_time = (estimated_batches_round3 - 1) * batch_delay + estimated_batches_round3 * batch_processing_time
                additional_rounds_time += round3_time
        
        estimated_time_seconds = inspection_time + round1_time + additional_rounds_time + final_comparison_time
        estimated_time_minutes = estimated_time_seconds // 60
        estimated_time_secs = estimated_time_seconds % 60
        
        if estimated_time_minutes > 0:
            time_text = f"~{estimated_time_minutes} мин {estimated_time_secs} сек"
        else:
            time_text = f"~{estimated_time_seconds} сек"
        
        status_msg = await bot.send_message(
            user_id,
            f"🤖 <b>ИИ-анализ запущен</b>\n\n"
            f"📊 Найдено: {len(candidate_listings)} объявлений\n"
            f"📦 Будет обработано: {estimated_batches_round1} батч(ей) в первом раунде\n"
            f"⏱ Примерное время: {time_text}\n\n"
            f"⏳ Анализирую и выбираю лучшие варианты...",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        log_warning("ai_mode", f"Не удалось отправить уведомление пользователю {user_id}: {e}")
    
    # Засекаем время начала анализа
    start_time = time.time()
    
    # Отправляем все объявления в ИИ для выбора лучших
    if AI_VALUATOR_AVAILABLE and select_best_listings:
        try:
            best_with_reasons = await select_best_listings(
                candidate_listings, 
                user_filters,
                max_results=5  # Запрашиваем 5 вариантов
            )
            
            # Рассчитываем фактическое время обработки
            elapsed_time = time.time() - start_time
            elapsed_minutes = int(elapsed_time // 60)
            elapsed_seconds = int(elapsed_time % 60)
            
            if elapsed_minutes > 0:
                elapsed_text = f"{elapsed_minutes} мин {elapsed_seconds} сек"
            else:
                elapsed_text = f"{elapsed_seconds} сек"
            
            if best_with_reasons and len(best_with_reasons) > 0:
                logger.info(f"ИИ выбрал {len(best_with_reasons)} лучших вариантов для пользователя {user_id}")
                
                # Формируем сообщения с результатами (разбиваем на части если слишком длинные)
                TELEGRAM_MAX_LENGTH = 4000  # Оставляем запас от 4096
                
                # Заголовок
                header_text = f"✅ <b>ИИ выбрал {len(best_with_reasons)} лучших вариантов</b>\n\n"
                
                # Проверяем, есть ли analysis_summary в первом элементе (передаём через reason или отдельно)
                # Пока используем стандартный текст
                header_text += f"Из {len(candidate_listings)} объявлений проанализированы все по ссылкам и отобраны лучшие по соотношению цена-качество.\n"
                header_text += f"⏱ Время обработки: {elapsed_text}\n\n"
                
                # Формируем части сообщений
                messages_parts = []
                current_message = header_text
                
                for i, item in enumerate(best_with_reasons, 1):
                    listing = item.get("listing")
                    reason = item.get("reason", "Хорошее соотношение цена-качество")
                    
                    if not listing:
                        logger.warning(f"Пропускаю элемент {i}: нет listing")
                        continue
                    
                    rooms_text = f"{listing.rooms}-комн." if listing.rooms > 0 else "?"
                    area_text = f"{listing.area} м²" if listing.area > 0 else "?"
                    
                    # Рассчитываем цену за м² для сравнения
                    price_per_sqm = ""
                    if listing.area > 0 and listing.price > 0:
                        price_per_sqm_usd = int(listing.price / listing.area)
                        price_per_sqm = f" (${price_per_sqm_usd}/м²)"
                    
                    # Год постройки (если есть)
                    year_info = ""
                    if listing.year_built:
                        year_info = f", {listing.year_built}г"
                    
                    # Формируем текст для варианта
                    variant_text = f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    variant_text += f"<b>{i}. {rooms_text}, {area_text}{year_info}</b>\n"
                    variant_text += f"💰 {listing.price_formatted}{price_per_sqm}\n"
                    variant_text += f"📍 {listing.address}\n"
                    variant_text += f"🔗 <a href=\"{listing.url}\">Открыть объявление</a>\n\n"
                    
                    # Ограничиваем длину обоснования (максимум 500 символов)
                    if len(reason) > 500:
                        reason = reason[:497] + "..."
                    
                    variant_text += f"<b>📋 Обоснование:</b>\n{reason}\n\n"
                    
                    # Проверяем, поместится ли вариант в текущее сообщение
                    if len(current_message) + len(variant_text) > TELEGRAM_MAX_LENGTH:
                        # Сохраняем текущее сообщение и начинаем новое
                        messages_parts.append(current_message)
                        current_message = f"<b>Продолжение ({i}/{len(best_with_reasons)}):</b>\n\n{variant_text}"
                    else:
                        current_message += variant_text
                
                # Добавляем последнее сообщение
                if current_message.strip() != header_text.strip():
                    messages_parts.append(current_message)
                
                # Отправляем сообщения
                try:
                    if status_msg:
                        # Первое сообщение редактируем статус
                        if messages_parts:
                            await status_msg.edit_text(
                                messages_parts[0],
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=False
                            )
                            # Остальные отправляем отдельными сообщениями
                            for msg_part in messages_parts[1:]:
                                await bot.send_message(
                                    user_id,
                                    msg_part,
                                    parse_mode=ParseMode.HTML,
                                    disable_web_page_preview=False
                                )
                    else:
                        # Отправляем все сообщения отдельно
                        for msg_part in messages_parts:
                            await bot.send_message(
                                user_id,
                                msg_part,
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=False
                            )
                except Exception as e:
                    log_error("ai_mode", f"Ошибка редактирования/отправки результатов пользователю {user_id}", e)
                    # Fallback: отправляем сокращенную версию
                    try:
                        short_text = f"✅ <b>ИИ выбрал {len(best_with_reasons)} лучших вариантов</b>\n\n"
                        for i, item in enumerate(best_with_reasons[:3], 1):  # Только первые 3
                            listing = item.get("listing")
                            if listing:
                                rooms_text = f"{listing.rooms}-комн." if listing.rooms > 0 else "?"
                                area_text = f"{listing.area} м²" if listing.area > 0 else "?"
                                short_text += f"{i}. {rooms_text}, {area_text} - {listing.price_formatted}\n"
                                short_text += f"🔗 <a href=\"{listing.url}\">Открыть</a>\n\n"
                        await bot.send_message(
                            user_id,
                            short_text,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=False
                        )
                    except Exception:
                        pass
                
                # НЕ отправляем объявления отдельно - только одно сообщение с оценкой ИИ
                logger.info(f"Отправлено сообщение с {len(best_with_reasons)} рекомендациями пользователю {user_id}")
                
                # Сохраняем выбранные варианты для будущего сравнения
                await save_ai_selected_listings(user_id, best_with_reasons)
                
                # Показываем финальное меню действий после ИИ-анализа
                await show_actions_menu(bot, user_id, len(best_with_reasons), "ИИ-режим")
                
            else:
                logger.warning(f"ИИ не выбрал ни одного варианта для пользователя {user_id}")
                # ИИ не выбрал ни одного варианта - показываем сообщение с предложением изменить фильтры
                await show_no_listings_message(bot, user_id, status_msg)
        except Exception as e:
            log_error("ai_mode", f"Ошибка ИИ-анализа для пользователя {user_id}", e)
            # В ИИ-режиме НЕ отправляем объявления отдельно, только сообщение об ошибке
            try:
                if status_msg:
                    await status_msg.edit_text(
                        f"⚠️ <b>Ошибка ИИ-анализа</b>\n\n"
                        f"Произошла ошибка при анализе объявлений. Попробуйте повторить позже.",
                        parse_mode=ParseMode.HTML
                    )
                else:
                    await bot.send_message(
                        user_id,
                        f"⚠️ <b>Ошибка ИИ-анализа</b>\n\n"
                        f"Произошла ошибка при анализе объявлений. Попробуйте повторить позже.",
                        parse_mode=ParseMode.HTML
                    )
            except Exception:
                pass
    else:
        logger.warning("ИИ-оценщик недоступен")
        # В ИИ-режиме НЕ отправляем объявления отдельно, только сообщение
        try:
            if status_msg:
                await status_msg.edit_text(
                    f"⚠️ <b>ИИ-оценщик недоступен</b>\n\n"
                    f"ИИ-режим временно недоступен. Переключитесь на обычный режим в настройках.",
                    parse_mode=ParseMode.HTML
                )
            else:
                await bot.send_message(
                    user_id,
                    f"⚠️ <b>ИИ-оценщик недоступен</b>\n\n"
                    f"ИИ-режим временно недоступен. Переключитесь на обычный режим в настройках.",
                    parse_mode=ParseMode.HTML
                )
        except Exception:
            pass


async def evaluate_and_compare_new_listings(
    bot: Bot,
    user_id: int,
    new_listings: List[Listing],
    previous_selected: List[Dict[str, Any]],
    user_filters: Dict[str, Any]
):
    """Оценивает новые объявления через ИИ и сравнивает с предыдущими выбранными вариантами"""
    logger.info(f"Оцениваю {len(new_listings)} новых объявлений и сравниваю с {len(previous_selected)} предыдущими")
    
    # Отправляем уведомление пользователю
    status_msg = None
    try:
        status_msg = await bot.send_message(
            user_id,
            f"🔍 <b>Оценка новых объявлений</b>\n\n"
            f"Найдено {len(new_listings)} новых объявлений.\n"
            f"Оцениваю и сравниваю с предыдущими выбранными вариантами...",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        log_warning("ai_mode", f"Не удалось отправить уведомление пользователю {user_id}: {e}")
    
    # Оцениваем новые объявления через ИИ
    evaluated_listings = []
    for listing in new_listings[:10]:  # Ограничиваем до 10 для экономии API
        try:
            ai_valuation = await valuate_listing(listing)
            if ai_valuation:
                evaluated_listings.append({
                    "listing": listing,
                    "valuation": ai_valuation
                })
        except Exception as e:
            log_error("ai_mode", f"Ошибка оценки объявления {listing.id}", e)
    
    if not evaluated_listings:
        try:
            if status_msg:
                await status_msg.edit_text(
                    "⚠️ <b>Не удалось оценить новые объявления</b>\n\n"
                    "Попробуйте повторить позже.",
                    parse_mode=ParseMode.HTML
                )
        except Exception:
            pass
        return
    
    # Формируем сообщение с оценкой и сравнением
    results_text = f"📊 <b>Оценка новых объявлений</b>\n\n"
    results_text += f"Проанализировано {len(evaluated_listings)} новых объявлений.\n"
    results_text += f"Сравнение с {len(previous_selected)} предыдущими выбранными вариантами.\n\n"
    results_text += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # Сортируем по оценке (лучшие первыми)
    evaluated_listings.sort(
        key=lambda x: x["valuation"].get("value_score", 0),
        reverse=True
    )
    
    # Показываем топ-3 новых объявления с оценкой
    for i, item in enumerate(evaluated_listings[:3], 1):
        listing = item["listing"]
        valuation = item["valuation"]
        
        rooms_text = f"{listing.rooms}-комн." if listing.rooms > 0 else "?"
        area_text = f"{listing.area} м²" if listing.area > 0 else "?"
        
        price_per_sqm = ""
        if listing.area > 0 and listing.price > 0:
            price_per_sqm_usd = int(listing.price / listing.area)
            price_per_sqm = f" (${price_per_sqm_usd}/м²)"
        
        year_info = ""
        if listing.year_built:
            year_info = f", {listing.year_built}г"
        
        fair_price = valuation.get("fair_price_usd", 0)
        is_overpriced = valuation.get("is_overpriced", False)
        value_score = valuation.get("value_score", 0)
        assessment = valuation.get("assessment", "Оценка недоступна")
        
        results_text += f"<b>{i}. {rooms_text}, {area_text}{year_info}</b>\n"
        results_text += f"💰 {listing.price_formatted}{price_per_sqm}\n"
        results_text += f"📍 {listing.address}\n"
        results_text += f"🔗 <a href=\"{listing.url}\">Открыть объявление</a>\n\n"
        
        if fair_price > 0:
            price_diff = listing.price - fair_price
            price_diff_percent = int((price_diff / fair_price) * 100) if fair_price > 0 else 0
            results_text += f"💵 <b>Справедливая цена:</b> ${fair_price:,}\n"
            if is_overpriced:
                results_text += f"⚠️ <b>Завышена на:</b> ${abs(price_diff):,} ({abs(price_diff_percent)}%)\n"
            else:
                results_text += f"✅ <b>Цена справедлива</b>\n"
        
        results_text += f"⭐ <b>Оценка:</b> {value_score}/10\n"
        results_text += f"📋 <b>Анализ:</b> {assessment}\n\n"
        
        # Сравнение с предыдущими вариантами
        if previous_selected:
            results_text += f"📊 <b>Сравнение:</b> "
            if value_score >= 7:
                results_text += "Лучше большинства предыдущих вариантов\n"
            elif value_score >= 5:
                results_text += "Сопоставимо с предыдущими вариантами\n"
            else:
                results_text += "Хуже предыдущих вариантов\n"
        
        results_text += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # Отправляем сообщение
    try:
        if status_msg:
            await status_msg.edit_text(
                results_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
        else:
            await bot.send_message(
                user_id,
                results_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
    except Exception as e:
        log_error("ai_mode", f"Ошибка отправки оценки пользователю {user_id}", e)


# Счетчики для ограничения логирования (чтобы не засорять логи)
_filter_log_counters = {}  # {user_id: {"filtered": 0, "passed": 0}}
_MAX_FILTERED_LOGS = 20  # Максимум логов отфильтрованных объявлений
_MAX_PASSED_LOGS = 10   # Максимум логов прошедших объявлений

def _matches_user_filters(listing: Listing, filters: Dict[str, Any], user_id: Optional[int] = None, log_details: bool = True) -> bool:
    """Проверяет соответствие объявления фильтрам пользователя
    
    Args:
        listing: Объявление для проверки
        filters: Фильтры пользователя
        user_id: ID пользователя (для логирования)
        log_details: Логировать детали фильтрации (по умолчанию True)
    """
    global _filter_log_counters
    
    user_prefix = f"[user_{user_id}]" if user_id else "[filter]"
    
    # Инициализируем счетчик для пользователя
    if user_id and user_id not in _filter_log_counters:
        _filter_log_counters[user_id] = {"filtered": 0, "passed": 0}
    
    # Комнаты
    if listing.rooms > 0:
        min_rooms = filters.get("min_rooms", 1)
        max_rooms = filters.get("max_rooms", 4)
        if listing.rooms < min_rooms or listing.rooms > max_rooms:
            if log_details and user_id:
                counter = _filter_log_counters.get(user_id, {"filtered": 0, "passed": 0})
                if counter["filtered"] < _MAX_FILTERED_LOGS:
                    log_info("filter", f"{user_prefix} ❌ Отфильтровано по комнатам: {listing.id} ({listing.source}) - {listing.rooms}к (фильтр: {min_rooms}-{max_rooms}к), цена: {listing.price_formatted}, адрес: {listing.address}")
                    counter["filtered"] += 1
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
            if log_details and user_id:
                counter = _filter_log_counters.get(user_id, {"filtered": 0, "passed": 0})
                if counter["filtered"] < _MAX_FILTERED_LOGS:
                    log_info("filter", f"{user_prefix} ❌ Отфильтровано по цене: {listing.id} ({listing.source}) - {listing.rooms}к, ${price:,} (фильтр: ${min_price:,}-${max_price:,}), адрес: {listing.address}")
                    counter["filtered"] += 1
            return False
    
    # Фильтр по типу продавца (только для Kufar)
    seller_type = filters.get("seller_type")
    # Если фильтр не установлен (None) или "Все", то не применяем фильтр
    if seller_type and listing.is_company is not None:
        if seller_type == "owner" and listing.is_company:
            # Фильтр: только собственники, а объявление от агентства
            if log_details and user_id:
                counter = _filter_log_counters.get(user_id, {"filtered": 0, "passed": 0})
                if counter["filtered"] < _MAX_FILTERED_LOGS:
                    log_info("filter", f"{user_prefix} ❌ Отфильтровано по типу продавца: {listing.id} ({listing.source}) - агентство (фильтр: только собственники), {listing.rooms}к, {listing.price_formatted}")
                    counter["filtered"] += 1
            return False
        elif seller_type == "company" and not listing.is_company:
            # Фильтр: только агентства, а объявление от собственника (оставляем для совместимости)
            if log_details and user_id:
                counter = _filter_log_counters.get(user_id, {"filtered": 0, "passed": 0})
                if counter["filtered"] < _MAX_FILTERED_LOGS:
                    log_info("filter", f"{user_prefix} ❌ Отфильтровано по типу продавца: {listing.id} ({listing.source}) - собственник (фильтр: только агентства), {listing.rooms}к, {listing.price_formatted}")
                    counter["filtered"] += 1
            return False
    
    # Если прошли все фильтры - логируем успешное прохождение (только первые несколько для каждого пользователя)
    if log_details and user_id:
        counter = _filter_log_counters.get(user_id, {"filtered": 0, "passed": 0})
        if counter["passed"] < _MAX_PASSED_LOGS:
            log_info("filter", f"{user_prefix} ✅ Прошло фильтры: {listing.id} ({listing.source}) - {listing.rooms}к, {listing.price_formatted}, адрес: {listing.address}")
            counter["passed"] += 1
    
    return True


# ============ КОМАНДЫ БОТА ============

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start - пошаговая настройка фильтров"""
    user_id = message.from_user.id
    
    # Создаем/обновляем пользователя в Turso
    try:
        from database import create_or_update_user_turso
        await create_or_update_user_turso(
            user_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )
    except Exception as e:
        logger.warning(f"Не удалось создать пользователя в Turso: {e}")
    
    # Проверяем, есть ли у пользователя фильтры (из старой БД или Turso)
    user_filters = await get_user_filters(user_id)
    
    # Если фильтров нет в старой БД, проверяем Turso
    if not user_filters:
        try:
            from database import get_user_filters_turso
            user_filters = await get_user_filters_turso(user_id)
            # Конвертируем формат фильтров из Turso в формат старой БД для совместимости
            if user_filters:
                # Конвертируем rooms из списка в min_rooms/max_rooms
                rooms = user_filters.get("rooms", [])
                if rooms and len(rooms) > 0:
                    user_filters["min_rooms"] = min(rooms)
                    user_filters["max_rooms"] = max(rooms)
                else:
                    user_filters["min_rooms"] = 1
                    user_filters["max_rooms"] = 4
                user_filters["is_active"] = user_filters.get("active", True)
                user_filters["city"] = user_filters.get("region", "барановичи")
        except Exception as e:
            logger.warning(f"Не удалось получить фильтры из Turso: {e}")
    
    if not user_filters:
        # Первый запуск - начинаем пошаговую настройку
        await message.answer(
            "👋 <b>Добро пожаловать!</b>\n\n"
            "Я помогу вам найти квартиру.\n\n"
            "📋 <b>Давайте настроим фильтры пошагово:</b>\n"
            "1️⃣ Выберите город\n"
            "2️⃣ Выберите диапазон комнат\n"
            "3️⃣ Укажите диапазон цен\n"
            "4️⃣ Выберите тип продавца (Kufar)\n"
            "5️⃣ Выберите режим работы\n\n"
            "Начнем с выбора города:",
            parse_mode=ParseMode.HTML
        )
        
        # Показываем меню выбора города
        await show_city_selection_menu(message, state)
    else:
        # Фильтры уже установлены - показываем их и предлагаем изменить
        status = "✅ Активен" if user_filters.get("is_active") else "❌ Отключен"
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🔍 Проверить сейчас", callback_data="check_now")
        builder.button(text="🤖 ИИ-анализ", callback_data="check_now_ai")
        builder.button(text="⚙️ Изменить фильтры", callback_data="setup_filters")
        builder.button(text="📊 Статистика", callback_data="show_stats")
        
        # Принудительно размещаем по 1 кнопке в ряду
        builder.adjust(1)
        
        city_name = user_filters.get('city', 'барановичи').title()
        await message.answer(
            f"🏠 <b>Ваши фильтры</b>\n\n"
            f"📍 <b>Город:</b> {city_name}\n"
            f"🚪 <b>Комнат:</b> от {user_filters.get('min_rooms', 1)} до {user_filters.get('max_rooms', 4)}\n"
            f"💰 <b>Цена:</b> ${user_filters.get('min_price', 0):,} - ${user_filters.get('max_price', 100000):,}\n\n"
            f"📡 <b>Статус:</b> {status}\n\n"
            f"Я проверяю новые объявления каждые 12 часов и присылаю только те, что подходят под ваши фильтры.\n\n"
            f"💡 <i>Для ИИ-оценки конкретного объявления используйте кнопку \"🤖 ИИ Оценка квартиры\" под каждым объявлением.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup()
        )


async def show_city_selection_menu(message: Message, state: FSMContext):
    """Показывает меню выбора города для пошаговой настройки"""
    builder = InlineKeyboardBuilder()
    
    # Все кнопки на отдельных строках для лучшей читаемости
    builder.button(text="Минск", callback_data="setup_city_минск")
    builder.button(text="Брест", callback_data="setup_city_брест")
    builder.button(text="Гродно", callback_data="setup_city_гродно")
    builder.button(text="Витебск", callback_data="setup_city_витебск")
    builder.button(text="Гомель", callback_data="setup_city_гомель")
    builder.button(text="Могилёв", callback_data="setup_city_могилёв")
    builder.button(text="✏️ Ввести вручную", callback_data="setup_city_manual")
    
    # Принудительно размещаем по 1 кнопке в ряду
    builder.adjust(1)
    
    await message.answer(
        "📍 <b>Шаг 1 из 4: Выберите город</b>\n\n"
        "Выберите город из списка или введите название вручную.",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )
    await state.set_state(SetupStates.waiting_for_city)


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help"""
    await message.answer(
        "📚 <b>Справка по командам</b>\n\n"
        "🎛 <b>Настройка фильтров:</b>\n"
        "• /start - пошаговая настройка фильтров\n"
        "• /filters - показать текущие фильтры\n\n"
        "⚡ <b>Управление:</b>\n"
        "• /start_monitoring - включить авто-мониторинг\n"
        "• /stop_monitoring - выключить мониторинг\n"
        "• /check - проверить объявления сейчас\n\n"
        "📊 <b>Информация:</b>\n"
        "• /sources - список источников\n\n"
        "💡 <b>Как использовать:</b>\n"
        "1. Используйте /start для настройки фильтров\n"
        "2. Включите мониторинг командой /start_monitoring\n"
        "3. Бот будет автоматически присылать новые объявления\n"
        "4. Вы можете выбрать ИИ-режим для умного анализа объявлений",
        parse_mode=ParseMode.HTML
    )


@router.message(Command("sources"))
async def cmd_sources(message: Message):
    """Показывает список источников"""
    active_sources = DEFAULT_SOURCES
    
    sources = [
        ("Kufar.by", "kufar", "крупнейшая доска объявлений Беларуси"),
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
    lines.append("🔄 Проверка каждые 12 часов")
    
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(Command("filters"))
async def cmd_filters(message: Message):
    """Показывает текущие фильтры пользователя с кнопками настройки"""
    user_id = message.from_user.id
    user_filters = await get_user_filters(user_id)
    
    if not user_filters:
        await message.answer(
            "⚠️ Фильтры не настроены. Используйте /start для настройки.",
            parse_mode=ParseMode.HTML
        )
        return
    
    status = "✅ Активен" if user_filters.get("is_active", True) else "❌ Отключен"
    
    # Создаем inline кнопки
    builder = InlineKeyboardBuilder()
    builder.button(text="⚙️ Настроить фильтры", callback_data="setup_filters")
    
    # Принудительно размещаем по 1 кнопке в ряду
    builder.adjust(1)
    
    await message.answer(
        f"⚙️ <b>Ваши фильтры</b>\n\n"
        f"📍 <b>Город:</b> {user_filters.get('city', 'барановичи').title()}\n"
        f"🚪 <b>Комнат:</b> от {user_filters.get('min_rooms', 1)} до {user_filters.get('max_rooms', 4)}\n"
        f"💰 <b>Цена:</b> ${user_filters.get('min_price', 0):,} - ${user_filters.get('max_price', 100000):,}\n"
        f"🤖 <b>Режим:</b> {'ИИ-режим' if user_filters.get('ai_mode') else 'Обычный режим'}\n\n"
        f"📡 <b>Статус:</b> {status}\n\n"
        f"<i>Нажмите кнопку для изменения фильтров</i>",
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
    builder.button(text="📍 Город", callback_data="user_filter_city")
    builder.button(text="🚪 Комнаты", callback_data="user_filter_rooms")
    builder.button(text="💰 Цена", callback_data="user_filter_price")
    builder.button(text="👤 Тип продавца", callback_data="user_filter_seller")
    builder.button(text="✅ Готово", callback_data="user_filters_done")
    
    # Принудительно размещаем по 1 кнопке в ряду
    builder.adjust(1)
    
    # Показываем текущие значения если они есть
    if user_filters:
        city_text = user_filters.get('city', 'барановичи').title()
        rooms_text = f"{user_filters.get('min_rooms', 1)}-{user_filters.get('max_rooms', 4)}"
        price_text = f"${user_filters.get('min_price', 0):,} - ${user_filters.get('max_price', 100000):,}".replace(",", " ")
        seller_type = user_filters.get('seller_type')
        seller_text = "Все (Агентства + Собственники)" if not seller_type else "Только собственники"
        current_info = f"\n\n<b>Текущие настройки:</b>\n📍 Город: {city_text}\n🚪 Комнаты: {rooms_text}\n💰 Цена: {price_text}\n👤 Продавец: {seller_text}"
    else:
        current_info = ""
    
    await callback.message.edit_text(
        "⚙️ <b>Настройка фильтров</b>\n\n"
        "Выберите параметры поиска:\n\n"
        "📍 <b>Город</b> — выбор города для поиска\n"
        "🚪 <b>Комнаты</b> — диапазон комнат (1-2, 2-3, 3-4, 4+)\n"
        "💰 <b>Цена</b> — цена от и до в USD\n"
        "👤 <b>Тип продавца</b> — только собственники или агентства (Kufar)\n\n"
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
    
    user_filters = await get_user_filters(user_id)
    if not user_filters:
        await callback.answer("❌ Фильтры не настроены!", show_alert=True)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="⚙️ Настроить фильтры", callback_data="setup_filters")
        builder.adjust(1)
        
        await callback.message.edit_text(
            "❌ <b>Фильтры не настроены</b>\n\n"
            "Пожалуйста, настройте все фильтры перед поиском:\n"
            "• Город\n"
            "• Количество комнат\n"
            "• Диапазон цен\n\n"
            "Используйте кнопку ниже для настройки.",
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup()
        )
        return
    
    # Проверяем наличие обязательных фильтров
    if not user_filters.get("city"):
        await callback.answer("❌ Город не выбран!", show_alert=True)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="📍 Выбрать город", callback_data="user_filter_city")
        builder.button(text="🔙 Назад", callback_data="setup_filters")
        builder.adjust(1)
        
        await callback.message.edit_text(
            "❌ <b>Город не выбран</b>\n\n"
            "Пожалуйста, выберите город для поиска.",
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup()
        )
        return
    
    # Валидация диапазона цен (максимум $20,000)
    MAX_PRICE_RANGE = 20000
    min_price = user_filters.get("min_price", 0)
    max_price = user_filters.get("max_price", 100000)
    price_range = max_price - min_price
    
    if price_range > MAX_PRICE_RANGE:
        await callback.answer("❌ Слишком большой диапазон цен!", show_alert=True)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="💰 Изменить цену", callback_data="user_filter_price")
        builder.button(text="🔙 Назад", callback_data="setup_filters")
        builder.adjust(1)
        
        await callback.message.edit_text(
            f"❌ <b>Слишком большой диапазон цен!</b>\n\n"
            f"Ваш диапазон: ${min_price:,} - ${max_price:,} = <b>${price_range:,}</b>\n"
            f"Максимально допустимый: <b>${MAX_PRICE_RANGE:,}</b>\n\n"
            f"💡 Уменьшите разбежку для более точного поиска.\n"
            f"Например:\n"
            f"• ${min_price:,} - ${min_price + MAX_PRICE_RANGE:,}\n"
            f"• ${max_price - MAX_PRICE_RANGE:,} - ${max_price:,}",
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup()
        )
        return
    
    # Сразу отвечаем на callback чтобы избежать timeout
    await callback.answer("Ищу объявления...")
    
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
        if _matches_user_filters(l, user_filters, user_id=user_id, log_details=True):
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
            # Обычный режим - БЕЗ ИИ-оценки
            if await send_listing_to_user(callback.bot, user_id, listing, use_ai_valuation=False):
                sent_count += 1
                await asyncio.sleep(1)
        
        # Показываем меню ИИ-режима после отправки
        await show_actions_menu(callback.bot, user_id, sent_count, "ИИ-режим")
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
    """Принудительная проверка объявлений для пользователя - ВСЕГДА в обычном режиме"""
    user_id = callback.from_user.id
    
    # Сразу отвечаем на callback
    await callback.answer("Проверяю...")
    
    user_filters = await get_user_filters(user_id)
    if not user_filters:
        await callback.message.answer("Сначала настройте фильтры через /start")
        return
    
    # Кнопка "Проверить сейчас" ВСЕГДА работает в обычном режиме
    # ИИ-режим используется только для автоматических проверок каждые 12 часов
    logger.info(f"Пользователь {user_id}: ручная проверка - Обычный режим (все объявления)")

    status_msg = await callback.message.answer(
        "🔍 <b>Проверяю новые объявления...</b>",
        parse_mode=ParseMode.HTML
    )
    
    # Ищем новые объявления для города пользователя
    user_city = user_filters.get("city", "барановичи")
    aggregator = ListingsAggregator(enabled_sources=DEFAULT_SOURCES)
    
    # ИСПРАВЛЕНИЕ: Используем фильтры пользователя вместо широких диапазонов
    all_listings = await aggregator.fetch_all_listings(
        city=user_city,
        min_rooms=user_filters.get("min_rooms", 1),
        max_rooms=user_filters.get("max_rooms", 5),
        min_price=user_filters.get("min_price", 0),
        max_price=user_filters.get("max_price", 1000000),
    )
    
    new_listings = []
    for listing in all_listings:
        if _matches_user_filters(listing, user_filters, user_id=user_id, log_details=True):
            if not await is_listing_sent_to_user(user_id, listing.id):
                dup_check = await is_duplicate_content(
                    listing.rooms, listing.area, listing.address, listing.price
                )
                if not dup_check["is_duplicate"]:
                    new_listings.append(listing)
    
    if new_listings:
        # Показываем список всех найденных объявлений для выбора
        await show_listings_list(callback.bot, user_id, new_listings, status_msg)
    else:
        await status_msg.edit_text(
            "📭 <b>Новых объявлений нет</b>\n\n"
            "Все подходящие объявления уже были отправлены ранее.",
            parse_mode=ParseMode.HTML
        )
        # Показываем меню ИИ-режима даже если объявлений нет
        await show_actions_menu(callback.bot, user_id, 0, "ИИ-режим")


@router.callback_query(F.data == "check_now_from_ai")
async def cb_check_now_from_ai(callback: CallbackQuery):
    """Обычный парсер из меню ИИ-режима - сразу отправляет все объявления без показа списка"""
    user_id = callback.from_user.id
    
    await callback.answer("Отправляю все объявления...")
    
    user_filters = await get_user_filters(user_id)
    if not user_filters:
        await callback.message.answer("Сначала настройте фильтры через /start")
        return
    
    status_msg = await callback.message.answer(
        "🔍 <b>Проверяю объявления...</b>\n\n"
        "Отправляю все найденные объявления...",
        parse_mode=ParseMode.HTML
    )
    
    # Ищем все объявления для города пользователя с использованием фильтров пользователя
    user_city = user_filters.get("city", "барановичи")
    min_rooms = user_filters.get("min_rooms", 1)
    max_rooms = user_filters.get("max_rooms", 5)
    min_price = user_filters.get("min_price", 0)
    max_price = user_filters.get("max_price", 1000000)
    
    aggregator = ListingsAggregator(enabled_sources=DEFAULT_SOURCES)
    
    all_listings = await aggregator.fetch_all_listings(
        city=user_city,
        min_rooms=min_rooms,
        max_rooms=max_rooms,
        min_price=min_price,
        max_price=max_price,
    )
    
    new_listings = []
    for listing in all_listings:
        if _matches_user_filters(listing, user_filters, user_id=user_id, log_details=True):
            if not await is_listing_sent_to_user(user_id, listing.id):
                dup_check = await is_duplicate_content(
                    listing.rooms, listing.area, listing.address, listing.price
                )
                if not dup_check["is_duplicate"]:
                    new_listings.append(listing)
    
    if new_listings:
        try:
            await status_msg.edit_text(
                f"✅ <b>Найдено {len(new_listings)} объявлений</b>\n\nОтправляю...",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        
        sent_count = 0
        for listing in new_listings:
            if await send_listing_to_user(callback.bot, user_id, listing, use_ai_valuation=False):
                sent_count += 1
                await asyncio.sleep(1)
        
        # Показываем меню ИИ-режима после отправки
        await show_actions_menu(callback.bot, user_id, sent_count, "ИИ-режим")
    else:
        await status_msg.edit_text(
            "📭 <b>Новых объявлений нет</b>\n\n"
            "Все подходящие объявления уже были отправлены ранее.",
            parse_mode=ParseMode.HTML
        )
        # Показываем меню ИИ-режима даже если объявлений нет
        await show_actions_menu(callback.bot, user_id, 0, "ИИ-режим")


@router.callback_query(F.data == "check_now_ai")
async def cb_check_now_ai(callback: CallbackQuery):
    """ИИ-анализ: собирает все объявления и выбирает лучшие 3-5"""
    user_id = callback.from_user.id
    
    await callback.answer("Запускаю ИИ-анализ...")
    
    user_filters = await get_user_filters(user_id)
    if not user_filters:
        await callback.message.answer("Сначала настройте фильтры через /start")
        return
    
    # Получаем все объявления для города пользователя
    user_city = user_filters.get("city", "барановичи")
    aggregator = ListingsAggregator(enabled_sources=DEFAULT_SOURCES)
    
    status_msg = await callback.message.answer(
        "🤖 <b>ИИ-анализ запущен...</b>\n\n"
        "Собираю все объявления и анализирую лучшие варианты...",
        parse_mode=ParseMode.HTML
    )
    
    try:
        # Используем фильтры пользователя для получения объявлений
        min_rooms = user_filters.get("min_rooms", 1)
        max_rooms = user_filters.get("max_rooms", 5)
        min_price = user_filters.get("min_price", 0)
        max_price = user_filters.get("max_price", 1000000)
        
        all_listings = await aggregator.fetch_all_listings(
            city=user_city,
            min_rooms=min_rooms,
            max_rooms=max_rooms,
            min_price=min_price,
            max_price=max_price,
        )
        
        # Собираем ВСЕ подходящие объявления (включая уже отправленные), применяя фильтры пользователя
        # ВАЖНО: НЕ проверяем is_listing_sent_to_user - берем ВСЕ подходящие объявления
        # ВАЖНО: НЕ проверяем is_duplicate_content - для ИИ-анализа нужны ВСЕ объявления, включая дубликаты
        candidate_listings = []
        filtered_out_by_filters = 0
        
        # Сбрасываем счетчик логирования для этого пользователя
        _filter_log_counters[user_id] = {"filtered": 0, "passed": 0}
        log_info("filter", f"[user_{user_id}] 📋 Применяю фильтры: город={user_filters.get('city')}, комнаты={user_filters.get('min_rooms')}-{user_filters.get('max_rooms')}, цена=${user_filters.get('min_price'):,}-${user_filters.get('max_price'):,}, продавец={user_filters.get('seller_type') or 'Все'}")
        
        for listing in all_listings:
            # ВАЖНО: Всегда применяем фильтры пользователя (дополнительная проверка)
            if not _matches_user_filters(listing, user_filters, user_id=user_id, log_details=True):
                filtered_out_by_filters += 1
                continue
            
            # ВАЖНО: Добавляем ВСЕ подходящие объявления, включая уже отправленные и дубликаты
            # ИИ должен проанализировать все варианты, чтобы выбрать лучшие
            candidate_listings.append(listing)
        
        seller_type = user_filters.get("seller_type")
        seller_filter_text = f", фильтр продавца: {seller_type if seller_type else 'Все'}"
        counter = _filter_log_counters.get(user_id, {"filtered": 0, "passed": 0})
        logger.info(f"ИИ-анализ для пользователя {user_id}: всего объявлений {len(all_listings)}, "
                   f"отфильтровано по фильтрам {filtered_out_by_filters}, "
                   f"кандидатов для анализа {len(candidate_listings)}{seller_filter_text}")
        logger.info(f"[user_{user_id}] 📊 Статистика фильтрации: отфильтровано {counter['filtered']} (логировано), прошло {counter['passed']} (логировано)")
        
        if not candidate_listings:
            # Не найдено ни одного объявления для анализа - показываем сообщение с предложением изменить фильтры
            await show_no_listings_message(callback.bot, user_id, status_msg)
            return
        
        # Определяем количество лучших вариантов в зависимости от общего количества (от 1 до 5)
        total_count = len(candidate_listings)
        if total_count == 0:
            max_results = 0
        elif total_count == 1:
            max_results = 1
        elif total_count <= 3:
            max_results = total_count  # Если объявлений 2-3, выбираем все
        elif total_count <= 10:
            max_results = 3  # Если объявлений 4-10, выбираем 3 лучших
        else:
            max_results = 5  # Если объявлений больше 10, выбираем 5 лучших
        
        # Рассчитываем примерное количество батчей для оценки времени
        if total_count <= 15:
            estimated_batches_round1 = 1
        else:
            estimated_batches_round1 = (total_count + 11) // 12  # Округляем вверх
        
        # Рассчитываем примерное время обработки
        # Инспекция: ~7 секунд (20 объявлений параллельно)
        # Первый раунд батчей: (batches - 1) * 15 сек (задержка между батчами) + batches * 3 сек (обработка батча)
        # Дополнительные раунды: если получилось больше 12 вариантов, делаем еще раунды
        # Финальное сравнение: ~20 секунд
        inspection_time = 7
        batch_delay = 15  # Задержка между батчами
        batch_processing_time = 3  # Время обработки одного батча
        final_comparison_time = 20
        
        # Время первого раунда батчей
        if estimated_batches_round1 == 1:
            round1_time = batch_processing_time
        else:
            round1_time = (estimated_batches_round1 - 1) * batch_delay + estimated_batches_round1 * batch_processing_time
        
        # Оцениваем количество дополнительных раундов
        # Из каждого батча берем 2 варианта, поэтому максимум вариантов после первого раунда = batches * 2
        max_results_after_round1 = estimated_batches_round1 * 2
        
        # Если получилось больше 12, нужен второй раунд
        additional_rounds_time = 0
        if max_results_after_round1 > 12:
            # Второй раунд: разбиваем на батчи по 12
            estimated_batches_round2 = (max_results_after_round1 + 11) // 12
            if estimated_batches_round2 == 1:
                round2_time = batch_processing_time
            else:
                round2_time = (estimated_batches_round2 - 1) * batch_delay + estimated_batches_round2 * batch_processing_time
            additional_rounds_time = round2_time
            
            # Если и после второго раунда больше 12, нужен третий раунд (редко, но возможно)
            max_results_after_round2 = estimated_batches_round2 * 2
            if max_results_after_round2 > 12:
                estimated_batches_round3 = (max_results_after_round2 + 11) // 12
                if estimated_batches_round3 == 1:
                    round3_time = batch_processing_time
                else:
                    round3_time = (estimated_batches_round3 - 1) * batch_delay + estimated_batches_round3 * batch_processing_time
                additional_rounds_time += round3_time
        
        estimated_time_seconds = inspection_time + round1_time + additional_rounds_time + final_comparison_time
        estimated_time_minutes = estimated_time_seconds // 60
        estimated_time_secs = estimated_time_seconds % 60
        
        if estimated_time_minutes > 0:
            time_text = f"~{estimated_time_minutes} мин {estimated_time_secs} сек"
        else:
            time_text = f"~{estimated_time_seconds} сек"
        
        await status_msg.edit_text(
            f"🤖 <b>ИИ-анализ</b>\n\n"
            f"📊 Найдено: {total_count} объявлений\n"
            f"📦 Будет обработано: {estimated_batches_round1} батч(ей) в первом раунде\n"
            f"⏱ Примерное время: {time_text}\n\n"
            f"⏳ Анализирую и выбираю {max_results} лучших вариантов...",
            parse_mode=ParseMode.HTML
        )
        
        # Засекаем время начала анализа
        start_time = time.time()
        
        # Отправляем все объявления в ИИ для выбора лучших
        if not AI_VALUATOR_AVAILABLE:
            logger.warning(f"ИИ-оценщик недоступен для пользователя {user_id}")
            await status_msg.edit_text(
                "❌ <b>ИИ-оценщик недоступен</b>\n\n"
                "ИИ-анализ временно недоступен. Попробуйте позже.",
                parse_mode=ParseMode.HTML
            )
            await show_actions_menu(callback.bot, user_id, 0, "ИИ-режим")
            return
        
        if not select_best_listings:
            logger.warning(f"Функция select_best_listings недоступна для пользователя {user_id}")
            await status_msg.edit_text(
                "❌ <b>ИИ-оценщик недоступен</b>\n\n"
                "ИИ-анализ временно недоступен. Попробуйте позже.",
                parse_mode=ParseMode.HTML
            )
            await show_actions_menu(callback.bot, user_id, 0, "ИИ-режим")
            return
        
        logger.info(f"Запускаю ИИ-анализ для {len(candidate_listings)} объявлений, запрашиваю {max_results} лучших")
        
        try:
            best_with_reasons = await select_best_listings(
                candidate_listings,
                user_filters,
                max_results=max_results
            )
            
            # Рассчитываем фактическое время обработки
            elapsed_time = time.time() - start_time
            elapsed_minutes = int(elapsed_time // 60)
            elapsed_seconds = int(elapsed_time % 60)
            
            if elapsed_minutes > 0:
                elapsed_text = f"{elapsed_minutes} мин {elapsed_seconds} сек"
            else:
                elapsed_text = f"{elapsed_seconds} сек"
            
            actual_count = len(best_with_reasons) if best_with_reasons else 0
            logger.info(f"ИИ вернул {actual_count} вариантов (запрашивалось {max_results})")
            
            if best_with_reasons and len(best_with_reasons) > 0:
                logger.info(f"ИИ выбрал {len(best_with_reasons)} лучших вариантов для пользователя {user_id}")
                
                # Используем тот же формат сообщений, что и при первом запуске ИИ-мода
                TELEGRAM_MAX_LENGTH = 4000  # Оставляем запас от 4096
                
                # Заголовок
                header_text = f"✅ <b>ИИ выбрал {len(best_with_reasons)} лучших вариантов</b>\n\n"
                header_text += f"Из {total_count} объявлений проанализированы все по ссылкам и отобраны лучшие по соотношению цена-качество.\n"
                header_text += f"⏱ Время обработки: {elapsed_text}\n\n"
                
                # Формируем части сообщений
                messages_parts = []
                current_message = header_text
                
                for i, item in enumerate(best_with_reasons, 1):
                    listing = item.get("listing")
                    reason = item.get("reason", "Хорошее соотношение цена-качество")
                    
                    if not listing:
                        logger.warning(f"Пропускаю элемент {i}: нет listing")
                        continue
                    
                    rooms_text = f"{listing.rooms}-комн." if listing.rooms > 0 else "?"
                    area_text = f"{listing.area} м²" if listing.area > 0 else "?"
                    
                    # Рассчитываем цену за м² для сравнения
                    price_per_sqm = ""
                    if listing.area > 0 and listing.price > 0:
                        price_per_sqm_usd = int(listing.price / listing.area)
                        price_per_sqm = f" (${price_per_sqm_usd}/м²)"
                    
                    # Год постройки (если есть)
                    year_info = ""
                    if listing.year_built:
                        year_info = f", {listing.year_built}г"
                    
                    # Формируем текст для варианта
                    variant_text = f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    variant_text += f"<b>{i}. {rooms_text}, {area_text}{year_info}</b>\n"
                    variant_text += f"💰 {listing.price_formatted}{price_per_sqm}\n"
                    variant_text += f"📍 {listing.address}\n"
                    variant_text += f"🔗 <a href=\"{listing.url}\">Открыть объявление</a>\n\n"
                    
                    # Ограничиваем длину обоснования (максимум 500 символов)
                    if len(reason) > 500:
                        reason = reason[:497] + "..."
                    
                    variant_text += f"<b>📋 Обоснование:</b>\n{reason}\n\n"
                    
                    # Проверяем, поместится ли вариант в текущее сообщение
                    if len(current_message) + len(variant_text) > TELEGRAM_MAX_LENGTH:
                        # Сохраняем текущее сообщение и начинаем новое
                        messages_parts.append(current_message)
                        current_message = f"<b>Продолжение ({i}/{len(best_with_reasons)}):</b>\n\n{variant_text}"
                    else:
                        current_message += variant_text
                
                # Добавляем последнее сообщение
                if current_message.strip() != header_text.strip():
                    messages_parts.append(current_message)
                
                # Отправляем сообщения
                try:
                    # Первое сообщение редактируем статус
                    if messages_parts:
                        await status_msg.edit_text(
                            messages_parts[0],
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=False
                        )
                        # Остальные отправляем отдельными сообщениями
                        for msg_part in messages_parts[1:]:
                            await callback.bot.send_message(
                                user_id,
                                msg_part,
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=False
                            )
                except Exception as e:
                    log_error("ai_mode", f"Ошибка редактирования/отправки результатов пользователю {user_id}", e)
                    # Fallback: отправляем сокращенную версию
                    try:
                        short_text = f"✅ <b>ИИ выбрал {len(best_with_reasons)} лучших вариантов</b>\n\n"
                        for i, item in enumerate(best_with_reasons[:3], 1):  # Только первые 3
                            listing = item.get("listing")
                            if listing:
                                rooms_text = f"{listing.rooms}-комн." if listing.rooms > 0 else "?"
                                area_text = f"{listing.area} м²" if listing.area > 0 else "?"
                                short_text += f"{i}. {rooms_text}, {area_text} - {listing.price_formatted}\n"
                                short_text += f"🔗 <a href=\"{listing.url}\">Открыть</a>\n\n"
                        await callback.bot.send_message(
                            user_id,
                            short_text,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=False
                        )
                    except Exception:
                        pass
                
                # Сохраняем выбранные варианты для будущего сравнения
                await save_ai_selected_listings(user_id, best_with_reasons)
                
                # Показываем меню действий
                await show_actions_menu(callback.bot, user_id, len(best_with_reasons), "ИИ-режим")
            else:
                # ИИ не выбрал ни одного варианта - показываем сообщение с предложением изменить фильтры
                await show_no_listings_message(callback.bot, user_id, status_msg)
        except Exception as e:
            logger.error(f"Ошибка при вызове select_best_listings для пользователя {user_id}: {e}", exc_info=True)
            await status_msg.edit_text(
                "❌ <b>Ошибка ИИ-анализа</b>\n\n"
                "Произошла ошибка при попытке проанализировать объявления. Попробуйте позже.",
                parse_mode=ParseMode.HTML
            )
            await show_actions_menu(callback.bot, user_id, 0, "ИИ-режим")
    
    except Exception as e:
        logger.error(f"Ошибка ИИ-анализа для пользователя {user_id}: {e}", exc_info=True)
        await status_msg.edit_text(
            "❌ <b>Ошибка ИИ-анализа</b>\n\n"
            "Произошла ошибка при попытке проанализировать объявления. Попробуйте позже.",
            parse_mode=ParseMode.HTML
        )
        await show_actions_menu(callback.bot, user_id, 0, "ИИ-режим")


async def show_mode_selection_menu(message: Message, state: FSMContext):
    """Показывает меню выбора режима работы после настройки фильтров"""
    # Проверяем, что все предыдущие шаги пройдены
    data = await state.get_data()
    
    if not data.get("city"):
        await message.answer("❌ Ошибка: город не выбран. Начните настройку заново через /start")
        await state.clear()
        return
    
    if not data.get("min_rooms") or not data.get("max_rooms"):
        await message.answer("❌ Ошибка: количество комнат не выбрано. Начните настройку заново через /start")
        await state.clear()
        return
    
    if data.get("min_price") is None or data.get("max_price") is None:
        await message.answer("❌ Ошибка: цена не установлена. Начните настройку заново через /start")
        await state.clear()
        return
    
    builder = InlineKeyboardBuilder()
    
    builder.button(text="🔍 Обычный парсер", callback_data="setup_mode_normal")
    builder.button(text="🤖 ИИ-мод (лучшие варианты)", callback_data="setup_mode_ai")
    
    # Принудительно размещаем по 1 кнопке в ряду
    builder.adjust(1)
    
    await message.answer(
        "🎯 <b>Шаг 5 из 5: Выберите режим работы</b>\n\n"
        "<b>🔍 Обычный парсер</b>\n"
        "Бот будет присылать все найденные объявления, соответствующие вашим фильтрам.\n\n"
        "<b>🤖 ИИ-мод</b>\n"
        "ИИ проанализирует все объявления и выберет только лучшие варианты по соотношению цена-качество (3-5 вариантов).",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )
    await state.set_state(SetupStates.waiting_for_mode)


@router.callback_query(F.data.in_(["setup_mode_normal", "setup_mode_ai"]))
async def cb_setup_mode(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора режима работы"""
    user_id = callback.from_user.id
    ai_mode = callback.data == "setup_mode_ai"
    
    # Получаем данные из состояния
    data = await state.get_data()
    
    # Проверяем, что все обязательные поля заполнены
    city = data.get("city")
    min_rooms = data.get("min_rooms")
    max_rooms = data.get("max_rooms")
    min_price = data.get("min_price")
    max_price = data.get("max_price")
    seller_type = data.get("seller_type")  # Может быть None
    
    if not city:
        await callback.answer("❌ Город не выбран! Начните настройку заново через /start", show_alert=True)
        await state.clear()
        return
    
    if min_rooms is None or max_rooms is None:
        await callback.answer("❌ Количество комнат не выбрано! Начните настройку заново через /start", show_alert=True)
        await state.clear()
        return
    
    if min_price is None or max_price is None:
        await callback.answer("❌ Цена не установлена! Начните настройку заново через /start", show_alert=True)
        await state.clear()
        return
    
    await callback.answer()

    # Сохраняем фильтры с выбранным режимом
    await set_user_filters(
        telegram_id=user_id,
        city=city,
        min_rooms=min_rooms,
        max_rooms=max_rooms,
        min_price=min_price,
        max_price=max_price,
        is_active=True,
        ai_mode=ai_mode,
        seller_type=seller_type
    )
    
    await state.clear()
    
    # Отправляем сообщение о начале поиска
    mode_text = "ИИ-мод" if ai_mode else "Обычный парсер"
    status_msg = await callback.message.answer(
        f"✅ <b>Фильтры настроены!</b>\n\n"
        f"📍 Город: {city.title()}\n"
        f"🚪 Комнаты: {min_rooms}-{max_rooms}\n"
        f"💰 Цена: ${min_price:,} - ${max_price:,}\n"
        f"🤖 Режим: {mode_text}\n\n"
        f"🔍 Ищу подходящие объявления...",
        parse_mode=ParseMode.HTML
    )
        
    # Запускаем поиск
    await search_listings_after_setup(
        callback.bot,
        user_id,
        city,
        min_rooms,
        max_rooms,
        min_price,
        max_price,
        ai_mode,
        status_msg
    )


async def show_listings_list(bot: Bot, user_id: int, listings: List[Listing], status_msg: Message):
    """Показывает список всех найденных объявлений с краткой информацией"""
    if not listings:
        await status_msg.edit_text(
            "📭 <b>Объявлений не найдено</b>",
            parse_mode=ParseMode.HTML
        )
        await show_actions_menu(bot, user_id, 0, "ИИ-режим")
        return
    
    # Ограничиваем до 20 объявлений для удобства
    listings_to_show = listings[:20]
    
    # Формируем список объявлений
    listings_text = f"✅ <b>Найдено {len(listings)} объявлений</b>\n\n"
    listings_text += f"<b>Список всех вариантов:</b>\n\n"
    
    for i, listing in enumerate(listings_to_show, 1):
        rooms_text = f"{listing.rooms}-комн." if listing.rooms > 0 else "?"
        area_text = f"{listing.area} м²" if listing.area > 0 else "?"
        price_text = listing.price_formatted
        
        # Краткая информация
        listing_info = f"<b>{i}.</b> {rooms_text}, {area_text} - {price_text}\n"
        listing_info += f"📍 {listing.address[:50]}\n\n"
        
        # Если текст слишком длинный, обрезаем
        if len(listings_text) + len(listing_info) > 3500:
            listings_text += f"\n... и еще {len(listings) - i + 1} объявлений"
            break
        
        listings_text += listing_info
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📤 Отправить все", callback_data="send_all_listings")
    builder.button(text="❌ Отмена", callback_data="cancel_listings")
    
    # Принудительно размещаем по 1 кнопке в ряду
    builder.adjust(1)
    
    try:
        await status_msg.edit_text(
            listings_text,
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup()
        )
    except Exception as e:
        # Если сообщение слишком длинное, разбиваем на части
        log_warning("bot", f"Сообщение слишком длинное, отправляю сокращенную версию: {e}")
        short_text = f"✅ <b>Найдено {len(listings)} объявлений</b>\n\n"
        short_text += f"Показано первых {min(10, len(listings_to_show))} из {len(listings)} объявлений.\n\n"
        short_text += f"Нажмите 'Отправить все' чтобы получить все объявления."
        await status_msg.edit_text(
            short_text,
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup()
        )


async def show_actions_menu(bot: Bot, user_id: int, listings_count: int, mode: str = "Обычный режим"):
    """Показывает меню действий после отправки объявлений"""
    builder = InlineKeyboardBuilder()
    
    # Если это ИИ-режим, показываем меню выбора режима + сброс фильтров
    if mode == "ИИ-режим":
        builder.button(text="🔍 Обычный парсер", callback_data="check_now_from_ai")
        builder.button(text="🤖 ИИ-мод", callback_data="check_now_ai")
        builder.button(text="🔄 Сбросить фильтры и начать заново", callback_data="reset_filters")
    else:
        # Обычный режим - стандартное меню
        builder.button(text="🔍 Проверить сейчас", callback_data="check_now")
        builder.button(text="🤖 ИИ-анализ", callback_data="check_now_ai")
        builder.button(text="⚙️ Изменить фильтры", callback_data="setup_filters")
        builder.button(text="📊 Статистика", callback_data="show_stats")
    
    # Принудительно размещаем по 1 кнопке в ряду
    builder.adjust(1)
    
    mode_text = "ИИ-мод" if mode == "ИИ-режим" else "Обычный парсер"
    if listings_count > 0:
        if mode == "ИИ-режим":
            message_text = (
                f"✅ <b>ИИ выбрал {listings_count} лучших вариантов</b>\n\n"
                f"<b>Что дальше?</b>\n"
                f"• 🔍 Обычный парсер - получить все найденные объявления\n"
                f"• 🤖 ИИ-мод - снова выбрать лучшие варианты\n"
                f"• 🔄 Сбросить фильтры - начать настройку заново"
            )
        else:
            message_text = (
                f"✅ <b>Готово!</b>\n\n"
                f"📨 Отправлено объявлений: <b>{listings_count}</b>\n"
                f"🤖 Режим: <b>{mode_text}</b>\n\n"
                f"<b>Что дальше?</b>\n"
                f"• 🔍 Проверить сейчас - найти все новые объявления\n"
                f"• 🤖 ИИ-анализ - выбрать лучшие варианты\n"
                f"• ⚙️ Изменить фильтры - настроить поиск\n"
                f"• 📊 Статистика - посмотреть историю"
            )
    else:
        if mode == "ИИ-режим":
            message_text = (
                f"📭 <b>ИИ не нашел подходящих вариантов</b>\n\n"
                f"<b>Что дальше?</b>\n"
                f"• 🔍 Обычный парсер - получить все найденные объявления\n"
                f"• 🤖 ИИ-мод - попробовать снова\n"
                f"• 🔄 Сбросить фильтры - начать настройку заново"
            )
        else:
            message_text = (
                f"📭 <b>Новых объявлений нет</b>\n\n"
                f"Все подходящие объявления уже были отправлены ранее.\n\n"
                f"<b>Что дальше?</b>\n"
                f"• 🔍 Проверить сейчас - найти все новые объявления\n"
                f"• 🤖 ИИ-анализ - выбрать лучшие варианты\n"
                f"• ⚙️ Изменить фильтры - настроить поиск\n"
                f"• 📊 Статистика - посмотреть историю"
            )
    
    try:
        await bot.send_message(
            user_id,
            message_text,
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup()
        )
    except Exception as e:
        log_warning("bot", f"Не удалось отправить меню действий пользователю {user_id}: {e}")


@router.callback_query(F.data.startswith("ai_val_"))
async def cb_ai_valuate_listing(callback: CallbackQuery):
    """Обработчик кнопки 'ИИ Оценка квартиры' - оценивает конкретное объявление"""
    user_id = callback.from_user.id
    
    await callback.answer("Оцениваю квартиру...")
    
    # Получаем listing_id из callback_data (используем только ID, не весь JSON)
    listing_id = callback.data.replace("ai_val_", "")
    
    # Проверяем, было ли объявление уже оценено
    if await is_listing_ai_valuated(user_id, listing_id):
        await callback.message.answer(
            "ℹ️ <b>Объявление уже оценено</b>\n\n"
            "Это объявление уже было оценено через ИИ ранее.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Получаем данные объявления из базы или парсим заново
    listing_data = await get_listing_by_id(listing_id)
    
    if not listing_data:
        # Если нет в базе, получаем заново через агрегатор
        user_filters = await get_user_filters(user_id)
        if not user_filters:
            await callback.message.answer(
                "❌ <b>Ошибка</b>\n\nНе удалось получить данные объявления. Попробуйте позже.",
                parse_mode=ParseMode.HTML
            )
        return
    
        user_city = user_filters.get("city", "барановичи")
        aggregator = ListingsAggregator(enabled_sources=DEFAULT_SOURCES)
        all_listings = await aggregator.fetch_all_listings(
            city=user_city,
            min_rooms=1,
            max_rooms=5,
            min_price=0,
            max_price=1000000,
        )
        
        # Ищем объявление по ID
        listing = None
        for l in all_listings:
            if l.id == listing_id:
                listing = l
                break
        
        if not listing:
            await callback.message.answer(
                "❌ <b>Ошибка</b>\n\nОбъявление не найдено. Возможно, оно было удалено.",
                parse_mode=ParseMode.HTML
            )
            return
    else:
        # Создаем объект Listing из данных базы
        listing = Listing(
            id=listing_data["id"],
            source=listing_data["source"],
            title=listing_data.get("title", ""),
            price=listing_data.get("price", 0),
            price_formatted=f"${listing_data.get('price', 0):,}".replace(",", " "),
            rooms=listing_data.get("rooms", 0),
            area=listing_data.get("area", 0.0),
            address=listing_data.get("address", ""),
            url=listing_data.get("url", ""),
            description="",
            year_built="",
            created_at=""
        )
    
    # Отправляем уведомление о начале оценки
    status_msg = await callback.message.answer(
        "🤖 <b>ИИ-оценка запущена...</b>\n\n"
        f"Анализирую объявление: {listing.title[:50]}...\n"
        f"Это может занять до 30 секунд.",
        parse_mode=ParseMode.HTML
    )
    
    # Выполняем ИИ-оценку
    try:
        if not AI_VALUATOR_AVAILABLE or not valuate_listing:
            await status_msg.edit_text(
                "❌ <b>ИИ-оценщик недоступен</b>\n\n"
                "ИИ-оценка временно недоступна. Попробуйте позже.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Таймаут для ИИ-оценки (максимум 30 секунд - включает инспекцию страницы)
        ai_valuation = await asyncio.wait_for(valuate_listing(listing), timeout=30.0)
        
        if ai_valuation:
            # Форматируем результат оценки
            fair_price = ai_valuation.get("fair_price_usd", 0)
            is_overpriced = ai_valuation.get("is_overpriced", False)
            assessment = ai_valuation.get("assessment", "")
            renovation_state = ai_valuation.get("renovation_state", "")
            recommendations = ai_valuation.get("recommendations", "")
            value_score = ai_valuation.get("value_score", 0)
            
            # Формируем сообщение с оценкой
            evaluation_text = f"🤖 <b>ИИ-оценка квартиры</b>\n\n"
            evaluation_text += f"🏠 <b>{listing.title}</b>\n"
            evaluation_text += f"📍 {listing.address}\n"
            evaluation_text += f"💰 Текущая цена: {listing.price_formatted}\n\n"
            
            if fair_price > 0:
                price_status = "🔴 Завышена" if is_overpriced else "🟢 Справедлива"
                price_diff = listing.price - fair_price
                price_diff_percent = abs((price_diff / fair_price) * 100) if fair_price > 0 else 0
                
                evaluation_text += f"💵 <b>Справедливая цена:</b> ${fair_price:,} {price_status}\n".replace(",", " ")
                
                if price_diff != 0:
                    diff_text = f"${abs(price_diff):,}" if price_diff > 0 else f"-${abs(price_diff):,}"
                    evaluation_text += f"📊 Разница: {diff_text} ({price_diff_percent:.1f}%)\n\n"
                
                # Оценка соотношения цена/качество
                if value_score > 0:
                    score_emoji = "⭐" * min(value_score, 5)
                    evaluation_text += f"⭐ <b>Оценка:</b> {value_score}/10 {score_emoji}\n\n"
                
                # Состояние ремонта
                if renovation_state:
                    renovation_emoji = {
                        "отличное": "✨",
                        "хорошее": "✅",
                        "среднее": "⚪",
                        "требует ремонта": "⚠️",
                        "плохое": "❌"
                    }.get(renovation_state.lower(), "📋")
                    evaluation_text += f"{renovation_emoji} <b>Ремонт:</b> {renovation_state}\n\n"
                
                # Детальная оценка
                if assessment:
                    evaluation_text += f"💡 <b>Оценка:</b>\n<i>{assessment}</i>\n\n"
                
                # Рекомендации
                if recommendations:
                    evaluation_text += f"📋 <b>Рекомендации:</b>\n<i>{recommendations}</i>\n\n"
                
                evaluation_text += f"🔗 <a href=\"{listing.url}\">Открыть объявление</a>"
                
                # Отправляем оценку (разбиваем на части если слишком длинная)
                try:
                    await status_msg.edit_text(
                        evaluation_text,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False
                    )
                except Exception as e:
                    # Если сообщение слишком длинное, отправляем сокращенную версию
                    log_warning("ai_valuate", f"Сообщение слишком длинное, отправляю сокращенную версию: {e}")
                    short_text = f"🤖 <b>ИИ-оценка квартиры</b>\n\n"
                    short_text += f"💵 <b>Справедливая цена:</b> ${fair_price:,} {price_status}\n".replace(",", " ")
                    if value_score > 0:
                        short_text += f"⭐ <b>Оценка:</b> {value_score}/10\n"
                    if assessment:
                        short_text += f"\n💡 {assessment[:200]}...\n"
                    short_text += f"\n🔗 <a href=\"{listing.url}\">Открыть объявление</a>"
                    await status_msg.edit_text(
                        short_text,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False
                    )
        else:
            await status_msg.edit_text(
                "⚠️ <b>ИИ не смог оценить квартиру</b>\n\n"
                "Попробуйте позже или проверьте объявление вручную.",
                parse_mode=ParseMode.HTML
            )
        
    except asyncio.TimeoutError:
        await status_msg.edit_text(
            "⏱️ <b>Таймаут ИИ-оценки</b>\n\n"
            "Оценка заняла слишком много времени. Попробуйте позже.",
            parse_mode=ParseMode.HTML
        )
        log_warning("ai_valuate", f"Таймаут ИИ-оценки для {listing.id}")
    except Exception as e:
        log_error("ai_valuate", f"Ошибка ИИ-оценки для {listing.id}", e)
        await status_msg.edit_text(
            "❌ <b>Ошибка ИИ-оценки</b>\n\n"
            "Произошла ошибка при оценке квартиры. Попробуйте позже.",
            parse_mode=ParseMode.HTML
        )


@router.callback_query(F.data == "send_all_listings")
async def cb_send_all_listings(callback: CallbackQuery):
    """Отправляет все найденные объявления пользователю"""
    user_id = callback.from_user.id
    
    await callback.answer("Отправляю все объявления...")
    
    user_filters = await get_user_filters(user_id)
    if not user_filters:
        await callback.message.answer("Сначала настройте фильтры через /start")
        return
    
    # Получаем объявления заново
    user_city = user_filters.get("city", "барановичи")
    aggregator = ListingsAggregator(enabled_sources=DEFAULT_SOURCES)
    
    # ИСПРАВЛЕНИЕ: Используем фильтры пользователя вместо широких диапазонов
    all_listings = await aggregator.fetch_all_listings(
        city=user_city,
        min_rooms=user_filters.get("min_rooms", 1),
        max_rooms=user_filters.get("max_rooms", 5),
        min_price=user_filters.get("min_price", 0),
        max_price=user_filters.get("max_price", 1000000),
    )
        
    new_listings = []
    for listing in all_listings:
        if _matches_user_filters(listing, user_filters, user_id=user_id, log_details=True):
            if not await is_listing_sent_to_user(user_id, listing.id):
                dup_check = await is_duplicate_content(
                    listing.rooms, listing.area, listing.address, listing.price
                )
                if not dup_check["is_duplicate"]:
                    new_listings.append(listing)
    
    if new_listings:
        status_msg = await callback.message.answer(
            f"📤 <b>Отправляю {len(new_listings)} объявлений...</b>",
            parse_mode=ParseMode.HTML
        )
        
        sent_count = 0
        for listing in new_listings:
            # Обычный режим - БЕЗ ИИ-оценки
            if await send_listing_to_user(callback.bot, user_id, listing, use_ai_valuation=False):
                sent_count += 1
                await asyncio.sleep(1)
        
        # Показываем меню ИИ-режима после отправки (так как это было вызвано из меню после ИИ-мода)
        await show_actions_menu(callback.bot, user_id, sent_count, "ИИ-режим")
    else:
        await callback.message.answer(
            "📭 <b>Новых объявлений нет</b>",
            parse_mode=ParseMode.HTML
        )
        await show_actions_menu(callback.bot, user_id, 0, "ИИ-режим")


@router.callback_query(F.data == "cancel_listings")
async def cb_cancel_listings(callback: CallbackQuery):
    """Отменяет просмотр списка объявлений"""
    await callback.answer("Отменено")
    await show_actions_menu(callback.bot, callback.from_user.id, 0, "ИИ-режим")


@router.callback_query(F.data == "reset_filters")
async def cb_reset_filters(callback: CallbackQuery, state: FSMContext):
    """Сбрасывает фильтры и начинает настройку заново"""
    user_id = callback.from_user.id
    
    await callback.answer("Сбрасываю фильтры...")
    
    # Удаляем фильтры пользователя из базы данных
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("DELETE FROM user_filters WHERE user_id = ?", (user_id,))
        await db.commit()
    
    # Очищаем состояние FSM
    await state.clear()
    
    # Начинаем настройку заново
    await callback.message.answer(
        "🔄 <b>Фильтры сброшены</b>\n\n"
        "Начинаем настройку заново...",
        parse_mode=ParseMode.HTML
    )
    
    # Показываем меню выбора города
    await show_city_selection_menu(callback.message, state)


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
    
    # Все кнопки на отдельных строках для лучшей читаемости
    builder.button(text="1-2 комн.", callback_data="user_rooms_1_2")
    builder.button(text="2-3 комн.", callback_data="user_rooms_2_3")
    builder.button(text="3-4 комн.", callback_data="user_rooms_3_4")
    builder.button(text="4+ комн.", callback_data="user_rooms_4_5")
    builder.button(text="Все (1-5)", callback_data="user_rooms_1_5")
    builder.button(text="Назад", callback_data="setup_filters")
    
    # Принудительно размещаем по 1 кнопке в ряду
    builder.adjust(1)
    
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
        telegram_id=user_id,
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
    builder.button(text="💰 От", callback_data="user_price_min")
    builder.button(text="💰 До", callback_data="user_price_max")
    builder.button(text="✅ Готово", callback_data="setup_filters")
    builder.button(text="🔄 Сброс", callback_data="user_price_reset")
    
    # Принудительно размещаем по 1 кнопке в ряду
    builder.adjust(1)
    
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
async def cb_user_price_min(callback: CallbackQuery, state: FSMContext):
    """Запрашивает минимальную цену"""
    await callback.message.edit_text(
        "💰 <b>Введите минимальную цену (USD)</b>\n\n"
        "Просто введите число, например:\n"
        "• <code>0</code> — без ограничения снизу\n"
        "• <code>20000</code> — от $20,000\n"
        "• <code>30000</code> — от $30,000\n\n"
        "<i>Или используйте команду: /pricefrom 20000</i>",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(PriceStates.waiting_for_min_price)
    await callback.answer("Введите число или используйте /pricefrom")


@router.callback_query(F.data == "user_price_max")
async def cb_user_price_max(callback: CallbackQuery, state: FSMContext):
    """Запрашивает максимальную цену"""
    await callback.message.edit_text(
        "💰 <b>Введите максимальную цену (USD)</b>\n\n"
        "Просто введите число, например:\n"
        "• <code>50000</code> — до $50,000\n"
        "• <code>80000</code> — до $80,000\n"
        "• <code>1000000</code> — без ограничения сверху\n\n"
        "<i>Или используйте команду: /priceto 50000</i>",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(PriceStates.waiting_for_max_price)
    await callback.answer("Введите число или используйте /priceto")


@router.callback_query(F.data == "user_filter_seller")
async def cb_user_filter_seller(callback: CallbackQuery):
    """Показывает меню выбора типа продавца"""
    user_id = callback.from_user.id
    user_filters = await get_user_filters(user_id)
    current_seller_type = user_filters.get("seller_type") if user_filters else None
    
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Все (Агентства + Собственники)", callback_data="seller_all")
    builder.button(text="🏠 Только собственники", callback_data="seller_owner")
    builder.button(text="🔙 Назад", callback_data="setup_filters")
    
    builder.adjust(1)
    
    current_text = "Все (Агентства + Собственники)" if not current_seller_type else "Только собственники"
    
    await callback.message.edit_text(
        "👤 <b>Выберите тип продавца</b>\n\n"
        "Фильтр применяется только к объявлениям с Kufar.by:\n\n"
        "👤 <b>Все</b> — показывать все объявления (агентства + собственники)\n"
        "🏠 <b>Только собственники</b> — исключить объявления от агентств\n\n"
        f"Текущий выбор: <b>{current_text}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("seller_"))
async def cb_set_seller_type(callback: CallbackQuery):
    """Устанавливает тип продавца"""
    user_id = callback.from_user.id
    seller_data = callback.data.replace("seller_", "")
    
    user_filters = await get_user_filters(user_id)
    
    # Определяем значение для БД
    seller_type = None
    if seller_data == "owner":
        seller_type = "owner"
    # seller_data == "all" -> seller_type = None
    
    await set_user_filters(
        telegram_id=user_id,
        city=user_filters.get("city") if user_filters else "барановичи",
        min_rooms=user_filters.get("min_rooms") or 1 if user_filters else 1,
        max_rooms=user_filters.get("max_rooms") or 4 if user_filters else 4,
        min_price=user_filters.get("min_price") or 0 if user_filters else 0,
        max_price=user_filters.get("max_price") or 100000 if user_filters else 100000,
        seller_type=seller_type
    )
    
    seller_text = "Все" if not seller_type else ("Только собственники" if seller_type == "owner" else "Только агентства")
    await callback.answer(f"✅ Установлено: {seller_text}")
    await cb_setup_filters(callback)


@router.callback_query(F.data == "user_filter_city")
async def cb_user_filter_city(callback: CallbackQuery):
    """Показывает меню выбора города"""
    builder = InlineKeyboardBuilder()
    
    # Все кнопки на отдельных строках для лучшей читаемости
    builder.button(text="Минск", callback_data="city_минск")
    builder.button(text="Брест", callback_data="city_брест")
    builder.button(text="Гродно", callback_data="city_гродно")
    builder.button(text="Витебск", callback_data="city_витебск")
    builder.button(text="Гомель", callback_data="city_гомель")
    builder.button(text="Могилёв", callback_data="city_могилёв")
    builder.button(text="✏️ Ввести вручную", callback_data="city_manual")
    builder.button(text="🔙 Назад", callback_data="setup_filters")
    
    # Принудительно размещаем по 1 кнопке в ряду
    builder.adjust(1)
    
    await callback.message.edit_text(
        "📍 <b>Выберите город для поиска</b>\n\n"
        "Выберите город из списка или введите название вручную.\n\n"
        "<i>Если вашего города нет в списке, используйте кнопку \"Ввести вручную\"</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("setup_city_"))
async def cb_setup_city_step(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора города в пошаговой настройке"""
    city_data = callback.data.replace("setup_city_", "")
    
    if city_data == "manual":
        # Запрашиваем ввод города вручную
        await callback.message.edit_text(
            "✏️ <b>Введите название города</b>\n\n"
            "Просто напишите название города, например:\n"
            "• <code>минск</code>\n"
            "• <code>гомель</code>\n"
            "• <code>барановичи</code>",
            parse_mode=ParseMode.HTML
        )
        await state.set_state(SetupStates.waiting_for_city)
        await callback.answer("Введите название города")
        return
    
    # Сохраняем город в FSM
    await state.update_data(city=city_data)
    
    # Переходим к следующему шагу - выбор комнат
    await show_rooms_selection_menu(callback.message, state, city_data.title())
    await callback.answer()


@router.message(SetupStates.waiting_for_city)
async def process_setup_city_input(message: Message, state: FSMContext):
    """Обрабатывает ввод города в пошаговой настройке"""
    city_input = message.text.strip()
    
    # Валидируем город
    is_valid, normalized_city = validate_city(city_input)
    
    if not is_valid:
        await message.answer(
            "❌ <b>Неверный формат города</b>\n\n"
            "Пожалуйста, введите название города заново.\n"
            "Название должно содержать минимум 2 символа.\n\n"
            "<i>Примеры: минск, гомель, барановичи</i>",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Проверяем, есть ли город в списке известных
    display_name = normalized_city.title()
    for display, normalized in BELARUS_CITIES:
        if normalized == normalized_city:
            display_name = display
            break
    
    # Сохраняем город в FSM
    await state.update_data(city=normalized_city)
    
    # Переходим к следующему шагу - выбор комнат
    await show_rooms_selection_menu(message, state, display_name)


async def show_rooms_selection_menu(message: Message, state: FSMContext, city_name: str):
    """Показывает меню выбора комнат"""
    builder = InlineKeyboardBuilder()
    
    # Все кнопки на отдельных строках для лучшей читаемости
    builder.button(text="1-2 комн.", callback_data="setup_rooms_1_2")
    builder.button(text="2-3 комн.", callback_data="setup_rooms_2_3")
    builder.button(text="3-4 комн.", callback_data="setup_rooms_3_4")
    builder.button(text="4+ комн.", callback_data="setup_rooms_4_5")
    builder.button(text="Все (1-5)", callback_data="setup_rooms_1_5")
    
    # Принудительно размещаем по 1 кнопке в ряду
    builder.adjust(1)
    
    await message.answer(
        f"✅ Город выбран: <b>{city_name}</b>\n\n"
        f"🚪 <b>Шаг 2 из 4: Выберите диапазон комнат</b>\n\n"
        f"Выберите количество комнат:",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )
    await state.set_state(SetupStates.waiting_for_rooms)


@router.callback_query(F.data.startswith("setup_rooms_"))
async def cb_setup_rooms_step(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора комнат в пошаговой настройке"""
    # Проверяем, что город выбран
    data = await state.get_data()
    if not data.get("city"):
        await callback.answer("❌ Сначала выберите город!", show_alert=True)
        return
    
    rooms_data = callback.data.replace("setup_rooms_", "")
    parts = rooms_data.split("_")
    min_rooms = int(parts[0])
    max_rooms = int(parts[1])
    
    # Сохраняем комнаты в FSM
    await state.update_data(min_rooms=min_rooms, max_rooms=max_rooms)
    
    # Переходим к следующему шагу - выбор цены
    rooms_text = f"{min_rooms}-{max_rooms}" if min_rooms != max_rooms else f"{min_rooms}"
    await show_price_selection_menu(callback.message, state, rooms_text)
    await callback.answer()


async def show_price_selection_menu(message: Message, state: FSMContext, rooms_text: str):
    """Показывает запрос минимальной цены"""
    await message.answer(
        f"✅ Комнаты выбраны: <b>{rooms_text}</b>\n\n"
        f"💰 <b>Шаг 3 из 5: Укажите диапазон цен (USD)</b>\n\n"
        f"Введите минимальную цену (ОТ):\n\n"
        f"Просто напишите число, например:\n"
        f"• <code>0</code> — без ограничения снизу\n"
        f"• <code>20000</code> — от $20,000\n"
        f"• <code>30000</code> — от $30,000",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(SetupStates.waiting_for_price_min)




@router.message(SetupStates.waiting_for_price_min)
async def process_setup_price_min(message: Message, state: FSMContext):
    """Обрабатывает ввод минимальной цены"""
    # Проверяем, что город и комнаты выбраны
    data = await state.get_data()
    if not data.get("city"):
        await message.answer("❌ Ошибка: город не выбран. Начните настройку заново через /start")
        await state.clear()
        return
    if not data.get("min_rooms") or not data.get("max_rooms"):
        await message.answer("❌ Ошибка: количество комнат не выбрано. Начните настройку заново через /start")
        await state.clear()
        return
    
    try:
        price_text = message.text.strip().replace(" ", "").replace(",", "").replace("$", "")
        min_price = int(price_text)
        
        if min_price < 0:
            await message.answer("❌ Цена не может быть отрицательной. Попробуйте снова.")
            return
        
        await state.update_data(min_price=min_price)
        
        # Сразу запрашиваем максимальную цену
        await message.answer(
            f"✅ Минимальная цена установлена: <b>${min_price:,}</b>\n\n"
            f"💰 <b>Шаг 4 из 5: Введите максимальную цену (ДО):</b>\n\n"
            f"Просто напишите число, например:\n"
            f"• <code>50000</code> — до $50,000\n"
            f"• <code>80000</code> — до $80,000\n"
            f"• <code>100000</code> — до $100,000",
            parse_mode=ParseMode.HTML
        )
        await state.set_state(SetupStates.waiting_for_price_max)
        
    except ValueError:
        await message.answer(
            "❌ <b>Неверный формат</b>\n\n"
            "Введите число, например:\n"
            "• <code>0</code>\n"
            "• <code>20000</code>\n"
            "• <code>30000</code>",
            parse_mode=ParseMode.HTML
        )


@router.message(SetupStates.waiting_for_price_max)
async def process_setup_price_max(message: Message, state: FSMContext):
    """Обрабатывает ввод максимальной цены"""
    # Проверяем, что все предыдущие шаги пройдены
    data = await state.get_data()
    if not data.get("city"):
        await message.answer("❌ Ошибка: город не выбран. Начните настройку заново через /start")
        await state.clear()
        return
    if not data.get("min_rooms") or not data.get("max_rooms"):
        await message.answer("❌ Ошибка: количество комнат не выбрано. Начните настройку заново через /start")
        await state.clear()
        return
    if not data.get("min_price") and data.get("min_price") != 0:
        await message.answer("❌ Ошибка: минимальная цена не установлена. Начните настройку заново через /start")
        await state.clear()
        return
    
    try:
        price_text = message.text.strip().replace(" ", "").replace(",", "").replace("$", "")
        max_price = int(price_text)
        
        if max_price < 0:
            await message.answer("❌ Цена не может быть отрицательной. Попробуйте снова.")
            return
        
        # Получаем минимальную цену из состояния
        min_price = data.get("min_price", 0)
        
        if max_price < min_price:
            await message.answer(
                f"❌ Максимальная цена ({max_price:,}) не может быть меньше минимальной ({min_price:,}).\n"
                f"Попробуйте снова.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Валидация диапазона цен (максимум $20,000)
        MAX_PRICE_RANGE = 20000
        price_range = max_price - min_price
        if price_range > MAX_PRICE_RANGE:
            suggested_max = min_price + MAX_PRICE_RANGE
            await message.answer(
                f"❌ <b>Слишком большой диапазон цен!</b>\n\n"
                f"Ваш диапазон: ${min_price:,} - ${max_price:,} = <b>${price_range:,}</b>\n"
                f"Максимально допустимый: <b>${MAX_PRICE_RANGE:,}</b>\n\n"
                f"💡 Уменьшите разбежку.\n"
                f"Например: ${min_price:,} - ${suggested_max:,}\n\n"
                f"Введите новую максимальную цену:",
                parse_mode=ParseMode.HTML
            )
            return
        
        await state.update_data(max_price=max_price)
        
        # Показываем меню выбора типа продавца
        await show_seller_selection_menu(message, state)
        
    except ValueError:
        await message.answer(
            "❌ <b>Неверный формат</b>\n\n"
            "Введите число, например:\n"
            "• <code>50000</code>\n"
            "• <code>80000</code>\n"
            "• <code>100000</code>",
            parse_mode=ParseMode.HTML
        )


async def show_seller_selection_menu(message: Message, state: FSMContext):
    """Показывает меню выбора типа продавца"""
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Все (Агентства + Собственники)", callback_data="setup_seller_all")
    builder.button(text="🏠 Только собственники", callback_data="setup_seller_owner")
    
    builder.adjust(1)
    
    await message.answer(
        "✅ Цена установлена\n\n"
        "👤 <b>Шаг 4 из 5: Выберите тип продавца</b>\n\n"
        "Фильтр применяется только к объявлениям с Kufar.by:\n\n"
        "👤 <b>Все</b> — показывать все объявления (агентства + собственники)\n"
        "🏠 <b>Только собственники</b> — исключить объявления от агентств\n\n"
        "<i>Выберите вариант:</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )
    await state.set_state(SetupStates.waiting_for_seller)


@router.callback_query(F.data.startswith("setup_seller_"))
async def cb_setup_seller(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора типа продавца в пошаговой настройке"""
    seller_data = callback.data.replace("setup_seller_", "")
    
    # Определяем значение для БД
    seller_type = None
    if seller_data == "owner":
        seller_type = "owner"
    # seller_data == "all" -> seller_type = None
    
    # Сохраняем в FSM
    await state.update_data(seller_type=seller_type)
    
    seller_text = "Все (Агентства + Собственники)" if not seller_type else "Только собственники"
    await callback.answer(f"✅ Выбрано: {seller_text}")
    
    # Переходим к выбору режима
    await show_mode_selection_menu(callback.message, state)


async def show_no_listings_message(bot: Bot, user_id: int, status_msg: Optional[Message] = None):
    """Показывает сообщение об отсутствии объявлений с предложением обновить фильтры"""
    message_text = (
        "📭 <b>Объявлений не найдено</b>\n\n"
        "Не найдено объявлений, соответствующих вашим фильтрам.\n\n"
        "💡 <b>Попробуйте изменить фильтры:</b>\n"
        "• Расширьте диапазон цен\n"
        "• Измените количество комнат\n"
        "• Выберите другой город\n\n"
        "Используйте кнопку ниже для изменения фильтров."
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="⚙️ Изменить фильтры", callback_data="setup_filters")
    builder.adjust(1)
    
    try:
        if status_msg:
            await status_msg.edit_text(
                message_text,
                parse_mode=ParseMode.HTML,
                reply_markup=builder.as_markup()
            )
        else:
            await bot.send_message(
                user_id,
                message_text,
                parse_mode=ParseMode.HTML,
                reply_markup=builder.as_markup()
            )
    except Exception as e:
        # Если не удалось отредактировать, отправляем новое сообщение
        try:
            await bot.send_message(
                user_id,
                message_text,
                parse_mode=ParseMode.HTML,
                reply_markup=builder.as_markup()
            )
        except Exception:
            logger.error(f"Не удалось отправить сообщение об отсутствии объявлений пользователю {user_id}: {e}")


async def search_listings_after_setup(
    bot: Bot,
    user_id: int,
    city: str,
    min_rooms: int,
    max_rooms: int,
    min_price: int,
    max_price: int,
    ai_mode: bool,
    status_msg: Optional[Message] = None
):
    """Ищет объявления после завершения настройки"""
    try:
        # Создаем статус сообщение, если оно не передано
        if status_msg is None:
            status_msg = await bot.send_message(
                user_id,
                "🔍 <b>Ищу подходящие объявления...</b>",
                parse_mode=ParseMode.HTML
            )
        
        # Получаем объявления
        aggregator = ListingsAggregator(enabled_sources=DEFAULT_SOURCES)
        all_listings = await aggregator.fetch_all_listings(
            city=city,
            min_rooms=min_rooms,
            max_rooms=max_rooms,
            min_price=min_price,
            max_price=max_price,
        )
        
        # Проверяем, что вообще найдены какие-то объявления
        if not all_listings:
            await show_no_listings_message(bot, user_id, status_msg)
            await show_actions_menu(bot, user_id, 0, "ИИ-режим")
            return
        
        # Получаем полные фильтры пользователя из БД, чтобы включить seller_type
        user_filters_db = await get_user_filters(user_id)
        user_filters = {
            "city": city,
            "min_rooms": min_rooms,
            "max_rooms": max_rooms,
            "min_price": min_price,
            "max_price": max_price,
            "ai_mode": ai_mode,
            "is_active": True,
            "seller_type": user_filters_db.get("seller_type") if user_filters_db else None
        }
        
        if ai_mode:
            # ИИ-режим
            await check_new_listings_ai_mode(bot, user_id, user_filters, all_listings, status_msg)
        else:
            # Обычный режим
            new_listings = []
            filtered_out = 0
            already_sent = 0
            duplicates = 0
            
            # Сбрасываем счетчик логирования для этого пользователя
            _filter_log_counters[user_id] = {"filtered": 0, "passed": 0}
            log_info("filter", f"[user_{user_id}] 📋 Применяю фильтры: город={user_filters.get('city')}, комнаты={user_filters.get('min_rooms')}-{user_filters.get('max_rooms')}, цена=${user_filters.get('min_price'):,}-${user_filters.get('max_price'):,}, продавец={user_filters.get('seller_type') or 'Все'}")
            
            for listing in all_listings:
                if not _matches_user_filters(listing, user_filters, user_id=user_id, log_details=True):
                    filtered_out += 1
                    continue
                    
                if await is_listing_sent_to_user(user_id, listing.id):
                    already_sent += 1
                    continue
                    
                dup_check = await is_duplicate_content(
                    listing.rooms, listing.area, listing.address, listing.price
                )
                if dup_check["is_duplicate"]:
                    duplicates += 1
                    continue
                    
                new_listings.append(listing)
            
            logger.info(f"Обычный режим: всего {len(all_listings)}, отфильтровано {filtered_out}, уже отправлено {already_sent}, дубликатов {duplicates}, новых {len(new_listings)}")
            
            if new_listings:
                try:
                    await status_msg.edit_text(
                        f"✅ <b>Найдено {len(new_listings)} объявлений</b>\n\nОтправляю...",
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    # Если не удалось отредактировать, отправляем новое сообщение
                    await bot.send_message(
                        user_id,
                        f"✅ <b>Найдено {len(new_listings)} объявлений</b>\n\nОтправляю...",
                        parse_mode=ParseMode.HTML
                    )
                
                sent_count = 0
                for listing in new_listings:
                    # Обычный режим - БЕЗ ИИ-оценки
                    if await send_listing_to_user(bot, user_id, listing, use_ai_valuation=False):
                        sent_count += 1
                        await asyncio.sleep(1)  # Задержка между сообщениями
                
                # Показываем меню действий после отправки
                await show_actions_menu(bot, user_id, sent_count, "ИИ-режим")
            else:
                # Не найдено ни одного объявления - показываем сообщение с предложением изменить фильтры
                await show_no_listings_message(bot, user_id, status_msg)
    except Exception as e:
        logger.error(f"Ошибка при поиске объявлений: {e}")
        try:
            if status_msg:
                await status_msg.edit_text(
                    f"⚠️ <b>Ошибка при поиске объявлений</b>\n\n"
                    f"Попробуйте позже или измените фильтры через /start",
                    parse_mode=ParseMode.HTML
                )
            else:
                await bot.send_message(
                    user_id,
                    f"⚠️ <b>Ошибка при поиске объявлений</b>\n\n"
                    f"Попробуйте позже или измените фильтры через /start",
                    parse_mode=ParseMode.HTML
                )
        except Exception:
            # Если не удалось отправить сообщение об ошибке, просто логируем
            log_error("search", f"Не удалось отправить сообщение об ошибке пользователю {user_id}", e)
        finally:
            # После настройки фильтров всегда показываем меню ИИ-режима
            await show_actions_menu(bot, user_id, 0, "ИИ-режим")


@router.callback_query(F.data.startswith("city_"))
async def cb_user_set_city(callback: CallbackQuery, state: FSMContext):
    """Устанавливает город для пользователя"""
    user_id = callback.from_user.id
    city_data = callback.data.replace("city_", "")
    
    if city_data == "manual":
        # Запрашиваем ввод города вручную
        await callback.message.edit_text(
            "✏️ <b>Введите название города</b>\n\n"
            "Просто напишите название города, например:\n"
            "• <code>минск</code>\n"
            "• <code>гомель</code>\n"
            "• <code>барановичи</code>\n\n"
            "<i>Если город введен неправильно, я попрошу ввести еще раз.</i>",
            parse_mode=ParseMode.HTML
        )
        await state.set_state(CityStates.waiting_for_city)
        await callback.answer("Введите название города")
        return
    
    # Устанавливаем город из списка
    user_filters = await get_user_filters(user_id)
    if not user_filters:
        # Если фильтров нет, создаём новые только с городом
        await set_user_filters(
            telegram_id=user_id,
            city=city_data,
            min_rooms=1,  # Временные значения, пользователь должен настроить
            max_rooms=4,
            min_price=0,
            max_price=100000,
            is_active=False  # Не активен, пока не настроены все параметры
        )
    else:
        # Обновляем только город, остальные параметры оставляем как есть
        await set_user_filters(
            telegram_id=user_id,
            city=city_data,
            min_rooms=user_filters.get("min_rooms") or 1,
            max_rooms=user_filters.get("max_rooms") or 4,
            min_price=user_filters.get("min_price") or 0,
            max_price=user_filters.get("max_price") or 100000,
            is_active=user_filters.get("is_active", False)
        )
    
    await callback.answer(f"✅ Город установлен: {city_data.title()}")
    await cb_setup_filters(callback)


@router.message(CityStates.waiting_for_city)
async def process_city_input(message: Message, state: FSMContext):
    """Обрабатывает ввод города пользователем"""
    user_id = message.from_user.id
    city_input = message.text.strip()
    
    # Валидируем город
    is_valid, normalized_city = validate_city(city_input)
    
    if not is_valid:
        await message.answer(
            "❌ <b>Неверный формат города</b>\n\n"
            "Пожалуйста, введите название города заново.\n"
            "Название должно содержать минимум 2 символа.\n\n"
            "<i>Примеры: минск, гомель, барановичи</i>",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Проверяем, есть ли город в списке известных
    city_found = False
    display_name = normalized_city.title()
    for display, normalized in BELARUS_CITIES:
        if normalized == normalized_city:
            display_name = display
            city_found = True
            break
    
    # Если город не найден в списке, предупреждаем но разрешаем
    if not city_found:
        await message.answer(
            f"⚠️ <b>Город \"{city_input}\" не найден в списке известных городов.</b>\n\n"
            f"Я сохраню его как: <b>{normalized_city.title()}</b>\n\n"
            f"Если название введено неправильно, вы можете изменить его позже в настройках.",
            parse_mode=ParseMode.HTML
        )
    
    # Сохраняем город
    user_filters = await get_user_filters(user_id)
    if not user_filters:
        # Если фильтров нет, создаём новые только с городом
        await set_user_filters(
            telegram_id=user_id,
            city=normalized_city,
            min_rooms=1,  # Временные значения, пользователь должен настроить
            max_rooms=4,
            min_price=0,
            max_price=100000,
            is_active=False  # Не активен, пока не настроены все параметры
        )
    else:
        # Обновляем только город, остальные параметры оставляем как есть
        await set_user_filters(
            telegram_id=user_id,
            city=normalized_city,
            min_rooms=user_filters.get("min_rooms") or 1,
            max_rooms=user_filters.get("max_rooms") or 4,
            min_price=user_filters.get("min_price") or 0,
            max_price=user_filters.get("max_price") or 100000,
            is_active=user_filters.get("is_active", False)
        )
    
    await state.clear()
    
    # Показываем подтверждение и возвращаемся в меню
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Вернуться к настройкам", callback_data="setup_filters")
    
    # Принудительно размещаем по 1 кнопке в ряду
    builder.adjust(1)
    
    await message.answer(
        f"✅ <b>Город установлен: {display_name}</b>\n\n"
        f"Теперь поиск будет выполняться в городе <b>{display_name}</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data == "user_price_reset")
async def cb_user_price_reset(callback: CallbackQuery):
    """Сбрасывает фильтр цены"""
    user_id = callback.from_user.id
    user_filters = await get_user_filters(user_id)
    
    await set_user_filters(
        telegram_id=user_id,
        city=user_filters.get("city", "барановичи") if user_filters else "барановичи",
        min_rooms=user_filters.get("min_rooms", 1) if user_filters else 1,
        max_rooms=user_filters.get("max_rooms", 4) if user_filters else 4,
        min_price=0,
        max_price=1000000,
        is_active=True
    )
    
    await callback.answer("✅ Цена сброшена: $0 - $1,000,000")
    await cb_user_filter_price(callback)


# Обработчики ввода цены через FSM
@router.message(PriceStates.waiting_for_min_price)
async def process_min_price_input(message: Message, state: FSMContext):
    """Обрабатывает ввод минимальной цены"""
    user_id = message.from_user.id
    
    try:
        # Извлекаем число из текста (может быть с пробелами или запятыми)
        price_text = message.text.strip().replace(" ", "").replace(",", "").replace("$", "")
        min_price = int(price_text)
        
        if min_price < 0:
            await message.answer("❌ Цена не может быть отрицательной. Попробуйте снова.")
            return
        
        # Получаем текущие фильтры
        user_filters = await get_user_filters(user_id)
        
        # Обновляем минимальную цену
        await set_user_filters(
            telegram_id=user_id,
            city=user_filters.get("city", "барановичи") if user_filters else "барановичи",
            min_rooms=user_filters.get("min_rooms", 1) if user_filters else 1,
            max_rooms=user_filters.get("max_rooms", 4) if user_filters else 4,
            min_price=min_price,
            max_price=user_filters.get("max_price", 100000) if user_filters else 100000,
            is_active=True
        )
        
        await state.clear()
        await message.answer(
            f"✅ <b>Минимальная цена установлена: ${min_price:,}</b>\n\n"
            f"Теперь настройте максимальную цену или нажмите '✅ Готово'",
            parse_mode=ParseMode.HTML
        )
        
        # Возвращаемся в меню настройки цены
        user_filters = await get_user_filters(user_id)
        current_min = user_filters.get("min_price", 0)
        current_max = user_filters.get("max_price", 100000)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="💰 От", callback_data="user_price_min")
        builder.button(text="💰 До", callback_data="user_price_max")
        builder.button(text="✅ Готово", callback_data="setup_filters")
        builder.button(text="🔄 Сброс", callback_data="user_price_reset")
        
        # Принудительно размещаем по 1 кнопке в ряду
        builder.adjust(1)
        
        await message.answer(
            f"💰 <b>Настройка цены (USD)</b>\n\n"
            f"Текущие значения:\n"
            f"• Цена ОТ: ${current_min:,}\n"
            f"• Цена ДО: ${current_max:,}\n\n"
            f"Нажмите кнопку для изменения или введите значение вручную.",
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup()
        )
        
    except ValueError:
        await message.answer(
            "❌ <b>Неверный формат</b>\n\n"
            "Введите число, например:\n"
            "• <code>0</code>\n"
            "• <code>20000</code>\n"
            "• <code>30000</code>\n\n"
            "Или используйте команду: <code>/pricefrom 20000</code>",
            parse_mode=ParseMode.HTML
        )


@router.message(PriceStates.waiting_for_max_price)
async def process_max_price_input(message: Message, state: FSMContext):
    """Обрабатывает ввод максимальной цены"""
    user_id = message.from_user.id
    
    try:
        # Извлекаем число из текста (может быть с пробелами или запятыми)
        price_text = message.text.strip().replace(" ", "").replace(",", "").replace("$", "")
        max_price = int(price_text)
        
        if max_price < 0:
            await message.answer("❌ Цена не может быть отрицательной. Попробуйте снова.")
            return
        
        # Получаем текущие фильтры
        user_filters = await get_user_filters(user_id)
        current_min = user_filters.get("min_price", 0) if user_filters else 0
        
        if max_price < current_min:
            await message.answer(
                f"❌ Максимальная цена ({max_price:,}) не может быть меньше минимальной ({current_min:,}).\n"
                f"Попробуйте снова.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Обновляем максимальную цену
        await set_user_filters(
            telegram_id=user_id,
            city=user_filters.get("city", "барановичи") if user_filters else "барановичи",
            min_rooms=user_filters.get("min_rooms", 1) if user_filters else 1,
            max_rooms=user_filters.get("max_rooms", 4) if user_filters else 4,
            min_price=current_min,
            max_price=max_price,
            is_active=True
        )
        
        await state.clear()
        await message.answer(
            f"✅ <b>Максимальная цена установлена: ${max_price:,}</b>\n\n"
            f"Диапазон цен: ${current_min:,} - ${max_price:,}",
            parse_mode=ParseMode.HTML
        )
        
        # Возвращаемся в меню настройки цены
        user_filters = await get_user_filters(user_id)
        current_min = user_filters.get("min_price", 0)
        current_max = user_filters.get("max_price", 100000)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="💰 От", callback_data="user_price_min")
        builder.button(text="💰 До", callback_data="user_price_max")
        builder.button(text="✅ Готово", callback_data="setup_filters")
        builder.button(text="🔄 Сброс", callback_data="user_price_reset")
        
        # Принудительно размещаем по 1 кнопке в ряду
        builder.adjust(1)
        
        await message.answer(
            f"💰 <b>Настройка цены (USD)</b>\n\n"
            f"Текущие значения:\n"
            f"• Цена ОТ: ${current_min:,}\n"
            f"• Цена ДО: ${current_max:,}\n\n"
            f"Нажмите кнопку для изменения или введите значение вручную.",
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup()
        )
        
    except ValueError:
        await message.answer(
            "❌ <b>Неверный формат</b>\n\n"
            "Введите число, например:\n"
            "• <code>50000</code>\n"
            "• <code>80000</code>\n"
            "• <code>1000000</code>\n\n"
            "Или используйте команду: <code>/priceto 50000</code>",
            parse_mode=ParseMode.HTML
        )




@router.message(Command("start_monitoring"))
async def cmd_start_monitoring(message: Message):
    """Включение мониторинга для пользователя"""
    user_id = message.from_user.id
    user_filters = await get_user_filters(user_id)
    
    if not user_filters:
        await message.answer(
            "⚠️ Фильтры не настроены. Используйте /start для настройки.",
            parse_mode=ParseMode.HTML
        )
        return
    
    await set_user_filters(
        telegram_id=user_id,
        city=user_filters.get("city", "барановичи"),
        min_rooms=user_filters.get("min_rooms", 1),
        max_rooms=user_filters.get("max_rooms", 4),
        min_price=user_filters.get("min_price", 0),
        max_price=user_filters.get("max_price", 100000),
        is_active=True,
        ai_mode=user_filters.get("ai_mode", False),
        seller_type=user_filters.get("seller_type")
    )
    await message.answer("✅ Мониторинг включен!")


@router.message(Command("stop_monitoring"))
async def cmd_stop_monitoring(message: Message):
    """Выключение мониторинга для пользователя"""
    user_id = message.from_user.id
    user_filters = await get_user_filters(user_id)
    
    if not user_filters:
        await message.answer(
            "⚠️ Фильтры не настроены. Используйте /start для настройки.",
            parse_mode=ParseMode.HTML
        )
        return
    
    await set_user_filters(
        telegram_id=user_id,
        city=user_filters.get("city", "барановичи"),
        min_rooms=user_filters.get("min_rooms", 1),
        max_rooms=user_filters.get("max_rooms", 4),
        min_price=user_filters.get("min_price", 0),
        max_price=user_filters.get("max_price", 100000),
        is_active=False,
        ai_mode=user_filters.get("ai_mode", False),
        seller_type=user_filters.get("seller_type")
    )
    await message.answer("❌ Мониторинг отключен.")


@router.message(Command("check"))
async def cmd_check(message: Message):
    """Ручная проверка объявлений"""
    await message.answer("🔍 Проверяю новые объявления со всех источников...\nЭто может занять 30-60 секунд.")
    await check_new_listings(message.bot)
    await message.answer("✅ Проверка завершена!")


async def create_bot() -> tuple[Bot, Dispatcher]:
    """Создает и настраивает бота"""
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не установлен! Проверьте файл .env")
    
    bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
    
    # Создаем FSM storage для состояний
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)
    
    # Инициализация базы данных
    await init_database()
    
    return bot, dp
