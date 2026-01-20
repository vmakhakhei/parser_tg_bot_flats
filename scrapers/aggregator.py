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
    ) -> List[Listing]:
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
            log_info("aggregator", f"Удалено {duplicates_removed} дубликатов")
        
        # КРИТИЧНО: Сохраняем каждое объявление в таблицу apartments
        # Это гарантирует, что данные реально попадают в БД, а не только существуют в памяти
        if unique_listings:
            try:
                from database_turso import sync_apartment_from_listing
                
                saved_count = 0
                for listing in unique_listings:
                    try:
                        # Сохраняем объявление в apartments
                        success = await sync_apartment_from_listing(listing, raw_json="{}")
                        if success:
                            saved_count += 1
                            # Контрольный лог в aggregator
                            log_info("aggregator", f"[AGGREGATOR] persisted ad_id={listing.id} source={listing.source}")
                        else:
                            log_warning("aggregator", f"[AGGREGATOR] failed to persist ad_id={listing.id} source={listing.source}")
                    except Exception as e:
                        log_error("aggregator", f"Ошибка сохранения объявления {listing.id} в apartments", e)
                        continue
                
                log_info("aggregator", f"💾 Сохранено {saved_count} из {len(unique_listings)} объявлений в таблицу apartments")
            except ImportError as e:
                log_error("aggregator", f"Не удалось импортировать sync_apartment_from_listing: {e}")
            except Exception as e:
                log_error("aggregator", f"Критическая ошибка при сохранении в apartments: {e}")
        
        # Сортируем по дате (новые первые) - у нас нет даты, сортируем по цене
        unique_listings.sort(key=lambda x: x.price if x.price > 0 else 999999999)
        
        log_info("aggregator", f"Итого уникальных объявлений: {len(unique_listings)}")
        
        # КРИТИЧНО: явно возвращаем список
        if not isinstance(unique_listings, list):
            log_error("aggregator", f"ОШИБКА: _remove_duplicates вернул не список: {type(unique_listings)}")
            return []
        
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
    asyncio.run(test_aggregator())

