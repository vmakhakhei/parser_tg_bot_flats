"""
Сервис для поиска и фильтрации объявлений
"""

import logging
from typing import List, Dict, Any, Optional, Tuple

from scrapers.aggregator import ListingsAggregator
from scrapers.base import Listing
from database import (
    get_user_filters,
    get_active_users,
    is_listing_sent_to_user,
    is_duplicate_content,
)
from error_logger import log_info, log_warning
from config import DEFAULT_SOURCES, USE_TURSO_CACHE

logger = logging.getLogger(__name__)

# Счетчики для ограничения логирования
_filter_log_counters: Dict[int, Dict[str, int]] = {}
_MAX_FILTERED_LOGS = 20  # Максимум логов отфильтрованных объявлений
_MAX_PASSED_LOGS = 10  # Максимум логов прошедших объявлений


def validate_user_filters(user_filters: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Проверяет валидность фильтров пользователя.

    Returns:
        Кортеж (is_valid, error_message)
    """
    if not user_filters:
        return False, "Фильтры не настроены"

    if not user_filters.get("city"):
        return False, "Город не выбран"

    return True, None


def _log_filtered_listing(
    user_prefix: str,
    listing: Listing,
    reason: str,
    user_id: Optional[int],
) -> None:
    """Логирует отфильтрованное объявление."""
    if not user_id:
        return

    counter = _filter_log_counters.get(user_id, {"filtered": 0, "passed": 0})
    if counter["filtered"] < _MAX_FILTERED_LOGS:
        log_info(
            "filter",
            f"{user_prefix} ❌ Отфильтровано {reason}: {listing.id} "
            f"({listing.source}) - {listing.rooms}к, "
            f"{listing.price_formatted}, адрес: {listing.address}",
        )
        counter["filtered"] += 1


def _check_rooms_filter(
    listing: Listing,
    filters: Dict[str, Any],
    user_prefix: str,
    user_id: Optional[int],
    log_details: bool,
) -> bool:
    """Проверяет соответствие объявления фильтру по комнатам."""
    if listing.rooms <= 0:
        return True

    min_rooms = filters.get("min_rooms", 1)
    max_rooms = filters.get("max_rooms", 4)

    if listing.rooms < min_rooms or listing.rooms > max_rooms:
        if log_details:
            _log_filtered_listing(
                user_prefix,
                listing,
                f"по комнатам (фильтр: {min_rooms}-{max_rooms}к)",
                user_id,
            )
        return False

    return True


def _get_price_in_usd(listing: Listing) -> int:
    """Конвертирует цену объявления в USD."""
    if listing.price_usd:
        return listing.price_usd
    elif listing.price_byn and not listing.price_usd:
        # Конвертируем BYN в USD примерно (курс ~2.95)
        return int(listing.price_byn / 2.95)
    return listing.price


def _check_price_filter(
    listing: Listing,
    filters: Dict[str, Any],
    user_prefix: str,
    user_id: Optional[int],
    log_details: bool,
) -> bool:
    """Проверяет соответствие объявления фильтру по цене."""
    price = _get_price_in_usd(listing)

    if price <= 0:
        return True

    min_price = filters.get("min_price", 0)
    max_price = filters.get("max_price", 1000000)

    if price < min_price or price > max_price:
        if log_details:
            _log_filtered_listing(
                user_prefix,
                listing,
                f"по цене: ${price:,} (фильтр: ${min_price:,}-${max_price:,})",
                user_id,
            )
        return False

    return True


def _check_seller_type_filter(
    listing: Listing,
    filters: Dict[str, Any],
    user_prefix: str,
    user_id: Optional[int],
    log_details: bool,
) -> bool:
    """Проверяет соответствие объявления фильтру по типу продавца."""
    seller_type = filters.get("seller_type")

    if not seller_type or listing.is_company is None:
        return True

    if seller_type == "owner" and listing.is_company:
        if log_details:
            _log_filtered_listing(
                user_prefix,
                listing,
                "по типу продавца: агентство (фильтр: только собственники)",
                user_id,
            )
        return False

    if seller_type == "company" and not listing.is_company:
        if log_details:
            _log_filtered_listing(
                user_prefix,
                listing,
                "по типу продавца: собственник (фильтр: только агентства)",
                user_id,
            )
        return False

    return True


def _log_passed_listing(
    user_prefix: str,
    listing: Listing,
    user_id: Optional[int],
) -> None:
    """Логирует объявление, прошедшее все фильтры."""
    if not user_id:
        return

    counter = _filter_log_counters.get(user_id, {"filtered": 0, "passed": 0})
    if counter["passed"] < _MAX_PASSED_LOGS:
        log_info(
            "filter",
            f"{user_prefix} ✅ Прошло фильтры: {listing.id} "
            f"({listing.source}) - {listing.rooms}к, "
            f"{listing.price_formatted}, адрес: {listing.address}",
        )
        counter["passed"] += 1


def matches_user_filters(
    listing: Listing,
    filters: Dict[str, Any],
    user_id: Optional[int] = None,
    log_details: bool = True,
) -> bool:
    """Проверяет соответствие объявления фильтрам пользователя.

    Args:
        listing: Объявление для проверки
        filters: Фильтры пользователя
        user_id: ID пользователя (для логирования)
        log_details: Логировать детали фильтрации

    Returns:
        True если объявление соответствует фильтрам
    """
    user_prefix = f"[user_{user_id}]" if user_id else "[filter]"

    # Инициализируем счетчик для пользователя
    if user_id and user_id not in _filter_log_counters:
        _filter_log_counters[user_id] = {"filtered": 0, "passed": 0}

    # Проверяем фильтры по порядку
    if not _check_rooms_filter(listing, filters, user_prefix, user_id, log_details):
        return False

    if not _check_price_filter(listing, filters, user_prefix, user_id, log_details):
        return False

    if not _check_seller_type_filter(listing, filters, user_prefix, user_id, log_details):
        return False

    # Если прошли все фильтры - логируем успешное прохождение
    if log_details:
        _log_passed_listing(user_prefix, listing, user_id)

    return True


async def _get_cached_listings(
    user_id: int,
    user_city: str,
    user_filters: Dict[str, Any],
) -> List[Listing]:
    """Получает объявления из кэша Turso."""
    cached_listings: List[Listing] = []

    if not USE_TURSO_CACHE:
        return cached_listings

    try:
        from database import (
            get_cached_listings_by_filters_turso,
            cached_listing_to_listing_turso,
        )

        cached_data = await get_cached_listings_by_filters_turso(
            city=user_city,
            min_rooms=user_filters.get("min_rooms", 1),
            max_rooms=user_filters.get("max_rooms", 5),
            min_price=user_filters.get("min_price", 0),
            max_price=user_filters.get("max_price", 1000000),
            limit=200,
        )

        for cached_dict in cached_data:
            try:
                listing = cached_listing_to_listing_turso(cached_dict)
                if listing:
                    cached_listings.append(listing)
            except Exception as e:
                log_warning("search", f"Ошибка конвертации объявления из кэша: {e}")
                continue

        log_info(
            "search",
            f"📦 Найдено {len(cached_listings)} объявлений в кэше " f"для пользователя {user_id}",
        )
    except Exception as e:
        log_warning("search", f"Ошибка получения данных из кэша, используем парсинг: {e}")

    return cached_listings


async def _parse_and_cache_listings(
    user_city: str,
    user_filters: Dict[str, Any],
    cached_listings: List[Listing],
) -> List[Listing]:
    """Парсит сайты и сохраняет объявления в кэш."""
    log_info(
        "search",
        f"🔍 В кэше мало объявлений ({len(cached_listings)}), парсим сайты...",
    )

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
            from database import cache_listings_batch_turso

            saved_count = await cache_listings_batch_turso(parsed_listings)
            log_info("search", f"💾 Сохранено {saved_count} объявлений в кэш")
        except Exception as e:
            log_warning("search", f"Ошибка сохранения в кэш: {e}")

    # Объединяем кэш и новые объявления (убираем дубликаты по ID)
    existing_ids = {listing.id for listing in cached_listings}
    new_listings = [listing for listing in parsed_listings if listing.id not in existing_ids]

    return cached_listings + new_listings


async def fetch_listings_for_user(user_id: int, user_filters: Dict[str, Any]) -> List[Listing]:
    """Получает объявления для пользователя с учетом кэширования.

    Args:
        user_id: ID пользователя
        user_filters: Фильтры пользователя

    Returns:
        Список объявлений
    """
    user_city = user_filters.get("city")

    # Сбрасываем счетчик логирования для этого пользователя
    _filter_log_counters[user_id] = {"filtered": 0, "passed": 0}

    log_info(
        "filter",
        f"[user_{user_id}] 📋 Применяю фильтры: "
        f"город={user_filters.get('city')}, "
        f"комнаты={user_filters.get('min_rooms')}-{user_filters.get('max_rooms')}, "
        f"цена=${user_filters.get('min_price'):,}-${user_filters.get('max_price'):,}, "
        f"продавец={user_filters.get('seller_type') or 'Все'}, "
        f"режим={'ИИ' if user_filters.get('ai_mode') else 'Обычный'}",
    )

    # Получаем объявления из кэша
    cached_listings = await _get_cached_listings(user_id, user_city, user_filters)

    # Парсим сайты только если кэша нет или мало объявлений
    if len(cached_listings) < 10:
        all_listings = await _parse_and_cache_listings(user_city, user_filters, cached_listings)
    else:
        log_info(
            "search",
            f"✅ Используем кэш ({len(cached_listings)} объявлений), " "парсинг не требуется",
        )
        all_listings = cached_listings

    log_info(
        "search",
        f"Для пользователя {user_id} (город: {user_city}) "
        f"найдено объявлений: {len(all_listings)}",
    )

    return all_listings


def reset_filter_counters() -> None:
    """Сбрасывает счетчики логирования фильтрации."""
    global _filter_log_counters  # noqa: F824
    _filter_log_counters.clear()


async def _process_listing_for_user(
    bot: Any,
    user_id: int,
    listing: Listing,
    user_filters: Dict[str, Any],
) -> bool:
    """Обрабатывает одно объявление для пользователя в обычном режиме.

    Returns:
        True если объявление было отправлено
    """
    from bot.services.notification_service import send_listing_to_user

    # Проверяем соответствие фильтрам пользователя
    if not matches_user_filters(listing, user_filters, user_id=user_id, log_details=True):
        return False

    # Проверяем, не отправляли ли уже этому пользователю
    if await is_listing_sent_to_user(user_id, listing.id):
        return False

    # Проверяем глобальную дедупликацию по контенту
    dup_check = await is_duplicate_content(
        rooms=listing.rooms,
        area=listing.area,
        address=listing.address,
        price=listing.price,
    )

    if dup_check["is_duplicate"]:
        log_info(
            "dedup",
            f"Дубликат для пользователя {user_id}: " f"{listing.source} ID={listing.id}",
        )
        return False

    # Отправляем объявление пользователю БЕЗ ИИ-оценки (обычный режим)
    return await send_listing_to_user(bot, user_id, listing, use_ai_valuation=False)


async def _process_user_listings_normal_mode(
    bot: Any,
    user_id: int,
    all_listings: List[Listing],
    user_filters: Dict[str, Any],
) -> int:
    """Обрабатывает объявления для пользователя в обычном режиме.

    Returns:
        Количество отправленных объявлений
    """
    import asyncio

    user_new_count = 0

    for listing in all_listings:
        if await _process_listing_for_user(bot, user_id, listing, user_filters):
            user_new_count += 1
            # Задержка между сообщениями чтобы не получить бан
            await asyncio.sleep(2)

    if user_new_count > 0:
        log_info(
            "search",
            f"Пользователю {user_id} отправлено: {user_new_count} объявлений",
        )

    return user_new_count


async def check_new_listings(bot: Any) -> None:
    """Проверяет новые объявления и отправляет их активным пользователям."""
    from bot.services.ai_service import check_new_listings_ai_mode

    reset_filter_counters()

    log_info("search", "=" * 50)
    log_info("search", "Проверка новых объявлений со всех источников...")

    # Получаем список активных пользователей
    active_users = await get_active_users()

    if not active_users:
        log_info("search", "Нет активных пользователей")
        return

    log_info("search", f"Активных пользователей: {len(active_users)}")

    total_sent = 0

    # Для каждого пользователя проверяем объявления по его фильтрам
    for user_id in active_users:
        user_filters = await get_user_filters(user_id)
        if not user_filters or not user_filters.get("is_active"):
            continue

        # Проверяем валидность фильтров
        is_valid, error_msg = validate_user_filters(user_filters)
        if not is_valid:
            log_warning("bot", f"Пропускаю пользователя {user_id}: {error_msg}")
            continue

        # Получаем объявления для пользователя
        all_listings = await fetch_listings_for_user(user_id, user_filters)

        # Проверяем режим работы пользователя
        if user_filters.get("ai_mode"):
            # ИИ-режим: передаем все объявления в функцию ИИ-режима
            await check_new_listings_ai_mode(bot, user_id, user_filters, all_listings)
        else:
            # Обычный режим: отправляем все подходящие объявления
            user_new_count = await _process_user_listings_normal_mode(
                bot, user_id, all_listings, user_filters
            )
            total_sent += user_new_count

    if total_sent > 0:
        log_info("search", f"✅ Всего отправлено новых объявлений: {total_sent}")
    else:
        log_info("search", "Новых объявлений нет")

    log_info("search", "=" * 50)
