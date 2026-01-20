"""
Агрегатор всех парсеров - собирает объявления со всех источников

Особенности:
- Каждый scraper обернут в try/except
- При падении одного scraper остальные продолжают работать
- Детальное логирование ошибок с указанием имени scraper
"""
import asyncio
import sys
import os
import time
import json
import aiohttp
from typing import List, Dict, Any, Optional
from datetime import datetime
from collections import defaultdict

# Добавляем родительскую директорию в path для импорта
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.base import Listing
from scrapers.kufar import KufarScraper
from scrapers.realt import RealtByScraper
from scrapers.domovita import DomovitaScraper
from scrapers.onliner import OnlinerRealtScraper
from scrapers.gohome import GoHomeScraper
from scrapers.etagi import EtagiScraper

# Импортируем error_logger если доступен
try:
    from error_logger import log_error, log_warning, log_info
except ImportError:
    # Fallback если модуль недоступен
    def log_error(source, message, exception=None):
        print(f"[ERROR] [{source}] {message}: {exception}")
    def log_warning(source, message):
        print(f"[WARN] [{source}] {message}")
    def log_info(source, message):
        print(f"[INFO] [{source}] {message}")

class ListingsAggregator:
    """Агрегатор объявлений со всех сайтов"""
    
    # Все доступные парсеры
    SCRAPERS = {
        "kufar": KufarScraper,
        "realt": RealtByScraper,
        "domovita": DomovitaScraper,
        "onliner": OnlinerRealtScraper,
        "gohome": GoHomeScraper,
        "etagi": EtagiScraper,
    }
    
    def __init__(self, enabled_sources: Optional[List[str]] = None):
        """
        Инициализация агрегатора
        
        Args:
            enabled_sources: Список включенных источников. 
                            Если None - используются все.
        """
        if enabled_sources:
            self.enabled_sources = [s.lower() for s in enabled_sources]
        else:
            self.enabled_sources = list(self.SCRAPERS.keys())
    
    async def fetch_all_listings(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
    ) -> tuple[List[Listing], List[Dict[str, Any]]]:
        """
        Получает объявления со всех включенных источников
        
        Каждый scraper обернут в try/except, при падении одного
        остальные продолжают работать.
        
        Returns:
            Объединенный список объявлений со всех сайтов
        """
        all_listings = []
        tasks = []
        source_names = []
        
        log_info("aggregator", f"Начинаю парсинг с {len(self.enabled_sources)} источников: {', '.join(self.enabled_sources)}")
        
        # Создаем задачи для каждого парсера
        for source_name in self.enabled_sources:
            if source_name in self.SCRAPERS:
                try:
                    scraper_class = self.SCRAPERS[source_name]
                    # Создаем экземпляр scraper'а с защитой от ошибок инициализации
                    try:
                        scraper_instance = scraper_class()
                    except Exception as e:
                        log_error("aggregator", f"Ошибка создания экземпляра scraper '{source_name}'", e)
                        continue
                    
                    # Создаем задачу с защитой от падений
                    task = self._fetch_from_source(
                        scraper_instance,
                        source_name,
                        city, min_rooms, max_rooms, min_price, max_price
                    )
                    tasks.append(task)
                    source_names.append(source_name)
                except Exception as e:
                    log_error("aggregator", f"Ошибка подготовки scraper '{source_name}'", e)
                    continue
        
        if not tasks:
            log_warning("aggregator", "Не удалось создать ни одной задачи для парсинга")
            return []
        
        # Выполняем все запросы параллельно с защитой от исключений
        # return_exceptions=True гарантирует, что при падении одного scraper'а остальные продолжат работу
        log_info("aggregator", f"Запускаю {len(tasks)} задач параллельно...")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Обрабатываем результаты с детальным логированием
        source_stats = {}
        successful_sources = 0
        failed_sources = 0
        
        for source_name, result in zip(source_names, results):
            if isinstance(result, Exception):
                # Ошибка уже залогирована в _fetch_from_source, но логируем здесь тоже для статистики
                log_error("aggregator", f"Scraper '{source_name}' завершился с ошибкой: {type(result).__name__}", result)
                source_stats[source_name] = {"error": str(result), "count": 0}
                failed_sources += 1
            elif result is None:
                # КРИТИЧНО: None считается ошибкой
                log_error("aggregator", f"Scraper '{source_name}' вернул None - это ошибка!")
                source_stats[source_name] = {"error": "Вернул None", "count": 0}
                failed_sources += 1
            elif isinstance(result, list):
                count = len(result)
                all_listings.extend(result)
                source_stats[source_name] = {"count": count, "error": None}
                successful_sources += 1
                log_info("aggregator", f"✅ Scraper '{source_name}': получено {count} объявлений")
            else:
                log_error("aggregator", f"Scraper '{source_name}': неожиданный тип результата: {type(result)}, значение: {result}")
                source_stats[source_name] = {"error": f"Неожиданный тип результата: {type(result)}", "count": 0}
                failed_sources += 1
        
        # Логируем итоговую статистику
        log_info("aggregator", f"Парсинг завершен: успешно {successful_sources}/{len(source_names)}, ошибок {failed_sources}")
        log_info("aggregator", f"📊 Всего объявлений до дедупликации: {len(all_listings)}")
        
        # Удаляем дубликаты по ID
        unique_listings = self._remove_duplicates(all_listings)
        duplicates_removed = len(all_listings) - len(unique_listings)
        if duplicates_removed > 0:
            log_info("aggregator", f"Удалено {duplicates_removed} дубликатов по ID")
        
        # Дедупликация по signature (адрес + vendor + цена + фото)
        if unique_listings:
            try:
                from scrapers.aggregator_utils import dedupe_by_signature
                before_signature = len(unique_listings)
                unique_listings = dedupe_by_signature(unique_listings)
                signature_removed = before_signature - len(unique_listings)
                if signature_removed > 0:
                    log_info("aggregator", f"[AGGREGATOR] удалено {signature_removed} дубликатов по signature (из {before_signature})")
            except ImportError as e:
                log_warning("aggregator", f"Не удалось импортировать dedupe_by_signature: {e}")
            except Exception as e:
                log_error("aggregator", f"Ошибка при дедупликации по signature: {e}")
        
        # КРИТИЧНО: Сохраняем все объявления в таблицу apartments одной транзакцией
        # Это гарантирует, что данные реально попадают в БД, а не только существуют в памяти
        if unique_listings:
            try:
                from database_turso import sync_apartments_batch
                
                # Сохраняем все объявления одной транзакцией
                inserted_ids = await sync_apartments_batch(unique_listings)
                
                if inserted_ids:
                    # Фильтруем только реально вставленные объявления
                    new_listings = [
                        listing for listing in unique_listings
                        if str(listing.id) in inserted_ids
                    ]
                    
                    # Запускаем уведомления в фоне, не блокируя парсинг остальных источников
                    from bot.services.notification_service import notify_users_about_new_apartments_summary
                    asyncio.create_task(
                        notify_users_about_new_apartments_summary(new_listings)
                    )
                    
                    log_info("aggregator", f"[AGGREGATOR] отправлено в notify: {len(new_listings)}")
                else:
                    log_info("aggregator", "[AGGREGATOR] новых объявлений нет")
            except ImportError as e:
                log_error("aggregator", f"Не удалось импортировать sync_apartments_batch: {e}")
            except Exception as e:
                log_error("aggregator", f"Критическая ошибка при сохранении в apartments: {e}")
        
        # Сортируем по дате (новые первые) - у нас нет даты, сортируем по цене
        unique_listings.sort(key=lambda x: x.price if x.price > 0 else 999999999)
        
        log_info("aggregator", f"Итого уникальных объявлений: {len(unique_listings)}")
        
        # КРИТИЧНО: явно возвращаем список
        if not isinstance(unique_listings, list):
            log_error("aggregator", f"ОШИБКА: _remove_duplicates вернул не список: {type(unique_listings)}")
            return []
        
        # Возвращаем unique_listings для обратной совместимости
        # new_apartments доступны через атрибут или можно добавить отдельный метод
        return unique_listings
    
    async def _fetch_from_source(
        self,
        scraper,
        source_name: str,
        city: str,
        min_rooms: int,
        max_rooms: int,
        min_price: int,
        max_price: int,
    ) -> List[Listing]:
        """
        Получает объявления из одного источника
        
        Обернут в try/except для защиты от падений.
        При ошибке возвращает пустой список, остальные scraper'ы продолжают работу.
        
        Args:
            scraper: Экземпляр scraper'а
            source_name: Имя источника (для логирования)
            city: Город для поиска
            min_rooms: Минимальное количество комнат
            max_rooms: Максимальное количество комнат
            min_price: Минимальная цена
            max_price: Максимальная цена
        
        Returns:
            Список объявлений или пустой список при ошибке
        """
        scraper_name = getattr(scraper, 'SOURCE_NAME', source_name)
        
        try:
            log_info("aggregator", f"🔄 Запускаю scraper '{scraper_name}' для города '{city}'...")
            
            # Инициализация scraper'а (context manager)
            try:
                async with scraper:
                    # Получение объявлений
                    listings = await scraper.fetch_listings(
                        city=city,
                        min_rooms=min_rooms,
                        max_rooms=max_rooms,
                        min_price=min_price,
                        max_price=max_price,
                    )
                    
                    # Проверяем результат - КРИТИЧНО: должен быть список, не None
                    if listings is None:
                        log_error("aggregator", f"Scraper '{scraper_name}' вернул None вместо списка - это ошибка!")
                        return []
                    
                    if not isinstance(listings, list):
                        log_error("aggregator", f"Scraper '{scraper_name}' вернул не список: {type(listings)}, значение: {listings}")
                        return []
                    
                    # Логируем результат с деталями
                    count = len(listings)
                    log_info("aggregator", f"✅ Scraper '{scraper_name}': получено {count} объявлений")
                    
                    # Проверяем, что все элементы - это Listing объекты
                    if count > 0:
                        first_item = listings[0]
                        if not isinstance(first_item, Listing):
                            log_warning("aggregator", f"Scraper '{scraper_name}': первый элемент не Listing, а {type(first_item)}")
                    
                    return listings
                    
            except asyncio.TimeoutError as e:
                log_error("aggregator", f"Scraper '{scraper_name}': таймаут при получении данных", e)
                return []
            except aiohttp.ClientError as e:
                log_error("aggregator", f"Scraper '{scraper_name}': ошибка HTTP-запроса", e)
                return []
            except Exception as e:
                log_error("aggregator", f"Scraper '{scraper_name}': ошибка при работе с context manager", e)
                return []
                
        except Exception as e:
            # Защита от любых других ошибок (инициализация, импорт и т.д.)
            log_error("aggregator", f"Scraper '{scraper_name}': критическая ошибка", e)
            return []
    
    def _remove_duplicates(self, listings: List[Listing]) -> List[Listing]:
        """Удаляет дубликаты объявлений"""
        seen_ids = set()
        unique = []
        
        for listing in listings:
            if listing.id not in seen_ids:
                seen_ids.add(listing.id)
                unique.append(listing)
        
        return unique
    
    @classmethod
    def get_available_sources(cls) -> List[str]:
        """Возвращает список доступных источников"""
        return list(cls.SCRAPERS.keys())


async def apartment_dict_to_listing(apartment_dict: Dict[str, Any]) -> Optional[Listing]:
    """
    Конвертирует словарь из таблицы apartments в объект Listing
    
    Args:
        apartment_dict: Словарь с данными из таблицы apartments
    
    Returns:
        Объект Listing или None при ошибке
    """
    try:
        # Определяем цену и валюту
        price_usd = apartment_dict.get("price_usd") or 0
        price_byn = apartment_dict.get("price_byn") or 0
        currency = apartment_dict.get("currency", "USD")
        
        # Выбираем основную цену
        if currency == "USD":
            price = price_usd
        elif currency == "BYN":
            price = price_byn
        else:
            price = price_usd if price_usd > 0 else price_byn
        
        # Форматируем цену
        if currency == "USD":
            price_formatted = f"${price:,}".replace(",", " ") if price > 0 else "Цена не указана"
        else:
            price_formatted = f"{price:,} BYN".replace(",", " ") if price > 0 else "Цена не указана"
        
        # Добавляем цену в другой валюте если есть
        if price_usd and price_byn:
            if currency == "USD":
                price_formatted += f" ({price_byn:,} BYN)".replace(",", " ")
            else:
                price_formatted += f" (${price_usd:,})".replace(",", " ")
        
        # Получаем photos (может быть список или JSON строка)
        photos = apartment_dict.get("photos", [])
        if isinstance(photos, str):
            try:
                photos = json.loads(photos) if photos else []
            except:
                photos = []
        if not isinstance(photos, list):
            photos = []
        
        # Формируем title если его нет
        title = apartment_dict.get("title", "")
        if not title:
            rooms = apartment_dict.get("rooms", 0)
            area = apartment_dict.get("total_area", 0.0)
            if rooms and area:
                title = f"{rooms}-комн., {area} м²"
            else:
                title = "Квартира"
        
        listing = Listing(
            id=apartment_dict.get("ad_id", ""),
            source=apartment_dict.get("source", "unknown"),
            title=title,
            price=price,
            price_formatted=price_formatted,
            rooms=apartment_dict.get("rooms", 0),
            area=apartment_dict.get("total_area", 0.0),
            address=apartment_dict.get("address", ""),
            url=apartment_dict.get("url", ""),
            photos=photos,
            floor=apartment_dict.get("floor", ""),
            description=apartment_dict.get("description", ""),
            currency=currency,
            price_usd=price_usd,
            price_byn=price_byn,
            year_built=apartment_dict.get("year_built", ""),
            created_at=apartment_dict.get("created_at", ""),
            is_company=apartment_dict.get("is_company"),
            balcony=apartment_dict.get("balcony", ""),
            bathroom=apartment_dict.get("bathroom", ""),
            total_floors=apartment_dict.get("total_floors", ""),
            house_type=apartment_dict.get("house_type", ""),
            renovation_state=apartment_dict.get("renovation_state", ""),
            kitchen_area=apartment_dict.get("kitchen_area", 0.0),
            living_area=apartment_dict.get("living_area", 0.0),
        )
        
        # Добавляем raw_json как атрибут для извлечения координат
        if "raw_json" in apartment_dict:
            listing.raw_json = apartment_dict["raw_json"]
        
        # Добавляем city как атрибут если есть
        if "city" in apartment_dict:
            listing.city = apartment_dict["city"]
        
        return listing
    except Exception as e:
        log_error("aggregator", f"Ошибка конвертации apartment_dict в Listing: {e}")
        return None


# Порог объединения по координатам (в метрах)
GEO_THRESHOLD_METERS = 80


def _extract_coords_from_listing(listing: Listing) -> tuple[Optional[float], Optional[float]]:
    """
    Извлекает координаты из объявления.
    
    Пытается получить координаты из:
    1. Атрибутов listing.lat и listing.lon (если есть)
    2. raw_json (если есть и содержит coordinates)
    
    Args:
        listing: Объявление
        
    Returns:
        Кортеж (lat, lon) или (None, None) если координаты не найдены
    """
    # Проверяем атрибуты объекта
    lat = getattr(listing, "lat", None)
    lon = getattr(listing, "lon", None)
    
    if lat is not None and lon is not None:
        return (lat, lon)
    
    # Пытаемся извлечь из raw_json (если есть)
    raw_json = getattr(listing, "raw_json", None)
    if raw_json:
        try:
            import json
            if isinstance(raw_json, str):
                data = json.loads(raw_json)
            else:
                data = raw_json
            
            # Kufar API хранит координаты как [lon, lat]
            coords = data.get("coordinates")
            if coords and isinstance(coords, list) and len(coords) >= 2:
                lon, lat = coords[0], coords[1]
                return (lat, lon)
        except Exception:
            pass
    
    return (None, None)


def _extract_city_from_listing(listing: Listing) -> str:
    """
    Извлекает город из объявления.
    
    Пытается получить город из:
    1. Атрибута listing.city (если есть)
    2. Адреса через _extract_city_from_address
    
    Args:
        listing: Объявление
    
    Returns:
        Название города в нижнем регистре
    """
    city = getattr(listing, "city", None)
    if city:
        return str(city).strip().lower()
    
    # Извлекаем из адреса
    from database_turso import _extract_city_from_address
    return _extract_city_from_address(listing.address or "").lower()


def extract_vendor_from_listing(listing: Listing) -> Optional[str]:
    """
    Извлекает vendor (agency или seller) из объявления.
    
    Пытается получить vendor из:
    1. Атрибута listing.vendor (если есть)
    2. raw_json (agency или seller)
    
    Args:
        listing: Объявление
    
    Returns:
        Название vendor или None если не найдено
    """
    # Проверяем атрибут listing.vendor
    vendor = getattr(listing, "vendor", None)
    if vendor:
        return str(vendor).strip()
    
    # Извлекаем из raw_json
    raw_json = getattr(listing, "raw_json", None)
    if raw_json:
        try:
            if isinstance(raw_json, dict):
                vendor = raw_json.get("agency") or raw_json.get("seller")
            elif isinstance(raw_json, str):
                data = json.loads(raw_json)
                vendor = data.get("agency") or data.get("seller")
            else:
                vendor = None
            
            if vendor:
                return str(vendor).strip()
        except Exception:
            pass
    
    return None


def make_group_key(listing: Listing) -> tuple:
    """
    Создает ключ группировки для объявления.
    
    Приоритет:
    1. Если есть номер дома -> (house_key, city, street, house) или (house_vendor_key, city, street, house, vendor)
    2. Если есть координаты -> (coords_key, city, street, rounded_lat, rounded_lon) или (coords_vendor_key, city, street, lat, lon, vendor)
    3. Иначе -> (street_key, city, street)
    
    Если GROUP_BY_VENDOR_FOR_ADDRESS=True и есть vendor, добавляется vendor в ключ.
    
    Args:
        listing: Объявление
        
    Returns:
        Кортеж-ключ для группировки
    """
    from utils.address_utils import split_address
    from config import GROUP_BY_VENDOR_FOR_ADDRESS
    
    addr = split_address(listing.address or "")
    city = _extract_city_from_listing(listing)
    street = addr["street"]
    house = addr["house"]
    vendor = extract_vendor_from_listing(listing) if GROUP_BY_VENDOR_FOR_ADDRESS else None
    
    if house:
        if GROUP_BY_VENDOR_FOR_ADDRESS and vendor:
            return ("house_vendor_key", city, street, house, vendor)
        return ("house_key", city, street, house)
    
    # Проверяем координаты
    lat, lon = _extract_coords_from_listing(listing)
    if lat is not None and lon is not None:
        # Используем округленные координаты как начальный bucket
        rounded_lat = round(lat, 4)
        rounded_lon = round(lon, 4)
        if GROUP_BY_VENDOR_FOR_ADDRESS and vendor:
            return ("coords_vendor_key", city, street, rounded_lat, rounded_lon, vendor)
        return ("coords_key", city, street, rounded_lat, rounded_lon)
    
    return ("street_key", city, street)


def group_similar_listings(listings: List[Listing]) -> List[List[Listing]]:
    """
    Группирует объявления по адресу с поддержкой гео-кластеризации.
    
    Объявления с разным количеством комнат в одном доме объединяются в одну группу.
    
    Использует многоуровневую стратегию:
    1. Первичная группировка по ключу (дом/координаты/улица) - БЕЗ учета количества комнат
    2. Гео-кластеризация для объявлений с координатами (distance-based)
    
    Args:
        listings: Список объявлений для группировки
        
    Returns:
        Список групп объявлений (каждая группа - список объявлений)
    """
    from utils.geo import haversine_m
    
    # 1) Первичная группировка по ключу
    buckets = defaultdict(list)
    for l in listings:
        key = make_group_key(l)
        buckets[key].append(l)
    
    # 2) Внутри каждого coords_key делаем точное гео-кластерирование (distance-based)
    final_groups = []
    for key, bucket in buckets.items():
        tag = key[0]
        if tag != "coords_key" or len(bucket) <= 1:
            final_groups.append(bucket)
            continue
        
        # Агломеративное объединение по расстоянию (O(n^2) в bucket'е, bucket обычно мал)
        used = [False] * len(bucket)
        for i, a in enumerate(bucket):
            if used[i]:
                continue
            group = [a]
            used[i] = True
            
            lat_a, lon_a = _extract_coords_from_listing(a)
            if lat_a is None or lon_a is None:
                continue
            
            for j in range(i+1, len(bucket)):
                if used[j]:
                    continue
                b = bucket[j]
                lat_b, lon_b = _extract_coords_from_listing(b)
                if lat_b is None or lon_b is None:
                    continue
                
                d = haversine_m(lat_a, lon_a, lat_b, lon_b)
                if d <= GEO_THRESHOLD_METERS:
                    group.append(b)
                    used[j] = True
            
            final_groups.append(group)
    
    return final_groups


async def notify_users_about_new_apartments(new_listings: List[Listing]) -> None:
    """
    Отправляет уведомления пользователям о новых объявлениях
    
    Эта функция вызывается асинхронно в фоне и не блокирует парсинг остальных источников.
    Работает только с переданными новыми объявлениями (уже реально вставленными в БД).
    Применяет фильтры пользователей и проверяет sent_ads для финальной защиты от дублей.
    
    Args:
        new_listings: Список Listing объектов - реально новых объявлений (уже в БД)
    """
    if not new_listings:
        log_info("aggregator", "[NOTIFY] нет новых объявлений для уведомлений")
        return
    
    try:
        # Импортируем необходимые функции
        from database import get_active_users, get_user_filters
        from bot.services.search_service import _process_user_listings_normal_mode, validate_user_filters, matches_user_filters
        from bot.services.ai_service import check_new_listings_ai_mode
        from database import is_ad_sent_to_user
        from aiogram import Bot
        from config import BOT_TOKEN as TELEGRAM_BOT_TOKEN
        
        if not TELEGRAM_BOT_TOKEN:
            log_warning("aggregator", "[NOTIFY] TELEGRAM_BOT_TOKEN не настроен, уведомления отключены")
            return
        
        log_info("aggregator", f"[NOTIFY] начинаю обработку {len(new_listings)} новых объявлений")
        
        # Получаем активных пользователей
        active_users = await get_active_users()
        if not active_users:
            log_info("aggregator", "[NOTIFY] нет активных пользователей")
            return
        
        log_info("aggregator", f"[NOTIFY] найдено {len(active_users)} активных пользователей")
        
        # Создаем бот
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        try:
            total_sent = 0
            
            # Для каждого пользователя проверяем объявления по его фильтрам
            for user_id in active_users:
                try:
                    user_filters = await get_user_filters(user_id)
                    if not user_filters:
                        continue
                    
                    # Проверяем валидность фильтров
                    is_valid, error_msg = validate_user_filters(user_filters)
                    if not is_valid:
                        continue
                    
                    # Применяем фильтры пользователя к новым объявлениям
                    filtered_listings = []
                    for listing in new_listings:
                        # Проверяем, не отправляли ли уже это объявление пользователю (sent_ads - финальная защита)
                        if await is_ad_sent_to_user(user_id, listing.id):
                            continue
                        
                        # Проверяем соответствие фильтрам пользователя
                        if matches_user_filters(listing, user_filters, user_id=user_id, log_details=False):
                            filtered_listings.append(listing)
                    
                    if not filtered_listings:
                        continue
                    
                    # Группируем объявления по адресу и количеству комнат
                    groups = group_similar_listings(filtered_listings)
                    
                    # Отправляем объявления пользователю в зависимости от режима
                    if user_filters.get("ai_mode"):
                        # В ИИ-режиме группировка не применяется, отправляем как раньше
                        await check_new_listings_ai_mode(bot, user_id, user_filters, filtered_listings)
                    else:
                        # В обычном режиме применяем группировку
                        from bot.services.notification_service import send_listing_to_user, send_grouped_listings_to_user
                        
                        user_sent = 0
                        for group in groups:
                            if len(group) == 1:
                                # Одно объявление - отправляем как обычно
                                result = await send_listing_to_user(bot, user_id, group[0], use_ai_valuation=False)
                                if result:
                                    user_sent += 1
                            else:
                                # Несколько объявлений - отправляем группированное сообщение
                                result = await send_grouped_listings_to_user(bot, user_id, group)
                                if result:
                                    user_sent += len(group)
                        
                        total_sent += user_sent
                        
                except Exception as e:
                    log_error("aggregator", f"[NOTIFY] ошибка обработки пользователя {user_id}: {e}")
                    continue
            
            log_info("aggregator", f"[NOTIFY] обработка завершена, отправлено {total_sent} объявлений")
            
        finally:
            await bot.session.close()
        
    except ImportError as e:
        log_error("aggregator", f"[NOTIFY] не удалось импортировать необходимые модули: {e}")
    except Exception as e:
        log_error("aggregator", f"[NOTIFY] ошибка при отправке уведомлений: {e}")
        import traceback
        traceback.print_exc()


async def test_aggregator():
    """Тестирование агрегатора"""
    print("🔍 Тестирование агрегатора объявлений...\n")
    
    aggregator = ListingsAggregator()
    
    listings = await aggregator.fetch_all_listings(
        city="барановичи",
        min_rooms=1,
        max_rooms=3,
        min_price=0,
        max_price=50000,
    )
    
    print(f"\n{'='*50}")
    print(f"📊 Всего найдено уникальных объявлений: {len(listings)}")
    print(f"{'='*50}\n")
    
    # Показываем первые 5 объявлений
    for i, listing in enumerate(listings[:5], 1):
        print(f"--- Объявление {i} ---")
        print(f"🏷️  Источник: {listing.source}")
        print(f"🏠 {listing.title}")
        print(f"💰 Цена: {listing.price_formatted}")
        print(f"🚪 Комнат: {listing.rooms}")
        print(f"📐 Площадь: {listing.area} м²")
        print(f"📍 Адрес: {listing.address}")
        print(f"🔗 URL: {listing.url}")
        print(f"📸 Фото: {len(listing.photos)} шт.")
        print()
    
    # Статистика по источникам
    print("📈 Статистика по источникам:")
    from collections import Counter
    sources = Counter(l.source for l in listings)
    for source, count in sources.most_common():
        print(f"  • {source}: {count}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Тестирование агрегатора объявлений")
    parser.add_argument("--city", type=str, default="барановичи", help="Город для поиска")
    parser.add_argument("--min-rooms", type=int, default=1, help="Минимальное количество комнат")
    parser.add_argument("--max-rooms", type=int, default=4, help="Максимальное количество комнат")
    parser.add_argument("--min-price", type=int, default=0, help="Минимальная цена")
    parser.add_argument("--max-price", type=int, default=100000, help="Максимальная цена")
    parser.add_argument("--max-pages", type=int, default=None, help="Максимальное количество страниц (для скрейперов, поддерживающих этот параметр)")
    
    args = parser.parse_args()
    
    async def run_aggregator():
        """Запуск агрегатора с параметрами командной строки"""
        print("🔍 Тестирование агрегатора объявлений...\n")
        print(f"Параметры:")
        print(f"  Город: {args.city}")
        print(f"  Комнаты: {args.min_rooms}-{args.max_rooms}")
        print(f"  Цена: ${args.min_price:,}-${args.max_price:,}".replace(",", " "))
        if args.max_pages:
            print(f"  Макс. страниц: {args.max_pages}")
        print()
        
        aggregator = ListingsAggregator()
        
        listings = await aggregator.fetch_all_listings(
            city=args.city,
            min_rooms=args.min_rooms,
            max_rooms=args.max_rooms,
            min_price=args.min_price,
            max_price=args.max_price,
        )
        
        print(f"\n{'='*50}")
        print(f"📊 Всего найдено уникальных объявлений: {len(listings)}")
        print(f"{'='*50}\n")
        
        # Показываем первые 5 объявлений
        for i, listing in enumerate(listings[:5], 1):
            print(f"--- Объявление {i} ---")
            print(f"🏷️  Источник: {listing.source}")
            print(f"🏠 {listing.title}")
            print(f"💰 Цена: {listing.price_formatted}")
            print(f"🚪 Комнат: {listing.rooms}")
            print(f"📐 Площадь: {listing.area} м²")
            print(f"📍 Адрес: {listing.address}")
            print(f"🔗 URL: {listing.url}")
            print(f"📸 Фото: {len(listing.photos)} шт.")
            print()
        
        # Статистика по источникам
        print("📈 Статистика по источникам:")
        from collections import Counter
        sources = Counter(l.source for l in listings)
        for source, count in sources.most_common():
            print(f"  • {source}: {count}")
    
    asyncio.run(run_aggregator())

