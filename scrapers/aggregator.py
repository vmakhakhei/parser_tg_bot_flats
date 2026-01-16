"""
Агрегатор всех парсеров - собирает объявления со всех источников
"""
import asyncio
import sys
import os
import time
import json
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

# Вспомогательная функция для debug логирования
def _write_debug_log(data):
    """Записывает debug лог в файл"""
    try:
        import os
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_path = os.path.join(base_dir, ".cursor", "debug.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")
    except Exception as e:
        try:
            log_error("aggregator", f"Debug log error: {e}")
        except:
            pass


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
        
        Returns:
            Объединенный список объявлений со всех сайтов
        """
        all_listings = []
        tasks = []
        source_names = []
        
        # Создаем задачи для каждого парсера
        for source_name in self.enabled_sources:
            if source_name in self.SCRAPERS:
                scraper_class = self.SCRAPERS[source_name]
                task = self._fetch_from_source(
                    scraper_class(),
                    city, min_rooms, max_rooms, min_price, max_price
                )
                tasks.append(task)
                source_names.append(source_name)
        
        # #region agent log
        _write_debug_log({
            "sessionId": "test-session",
            "runId": "run1",
            "hypothesisId": "C",
            "location": "aggregator.py:88",
            "message": "Aggregator fetch start",
            "data": {"city": city, "sources": source_names, "filters": {"min_rooms": min_rooms, "max_rooms": max_rooms, "min_price": min_price, "max_price": max_price}},
            "timestamp": int(time.time() * 1000)
        })
        # #endregion
        
        # Выполняем все запросы параллельно
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Обрабатываем результаты (парсеры сами логируют количество)
        source_stats = {}
        for source_name, result in zip(source_names, results):
            if isinstance(result, Exception):
                log_error(source_name, f"Ошибка парсинга", result)
                source_stats[source_name] = {"error": str(result), "count": 0}
            elif isinstance(result, list):
                all_listings.extend(result)
                source_stats[source_name] = {"count": len(result), "error": None}
        
        # #region agent log
        _write_debug_log({
            "sessionId": "test-session",
            "runId": "run1",
            "hypothesisId": "C",
            "location": "aggregator.py:105",
            "message": "Aggregator source results",
            "data": {"city": city, "source_stats": source_stats, "total_before_dedup": len(all_listings)},
            "timestamp": int(time.time() * 1000)
        })
        # #endregion
        
        # Удаляем дубликаты по ID
        unique_listings = self._remove_duplicates(all_listings)
        
        # #region agent log
        _write_debug_log({
            "sessionId": "test-session",
            "runId": "run1",
            "hypothesisId": "C",
            "location": "aggregator.py:115",
            "message": "Aggregator deduplication",
            "data": {"city": city, "before_dedup": len(all_listings), "after_dedup": len(unique_listings), "duplicates_removed": len(all_listings) - len(unique_listings)},
            "timestamp": int(time.time() * 1000)
        })
        # #endregion
        
        # Сортируем по дате (новые первые) - у нас нет даты, сортируем по цене
        unique_listings.sort(key=lambda x: x.price if x.price > 0 else 999999999)
        
        return unique_listings
    
    async def _fetch_from_source(
        self,
        scraper,
        city: str,
        min_rooms: int,
        max_rooms: int,
        min_price: int,
        max_price: int,
    ) -> List[Listing]:
        """Получает объявления из одного источника"""
        try:
            async with scraper:
                listings = await scraper.fetch_listings(
                    city=city,
                    min_rooms=min_rooms,
                    max_rooms=max_rooms,
                    min_price=min_price,
                    max_price=max_price,
                )
                return listings
        except Exception as e:
            log_error(scraper.SOURCE_NAME, f"Ошибка получения данных", e)
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

