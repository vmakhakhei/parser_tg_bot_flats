"""
Агрегатор всех парсеров - собирает объявления со всех источников
"""
import asyncio
from typing import List, Dict, Any, Optional
from scrapers.base import Listing
from scrapers.kufar import KufarScraper
from scrapers.realt import RealtByScraper
from scrapers.domovita import DomovitaScraper
from scrapers.onliner import OnlinerRealtScraper
from scrapers.gohome import GoHomeScraper
from scrapers.hata import HataScraper
from scrapers.etagi import EtagiScraper


class ListingsAggregator:
    """Агрегатор объявлений со всех сайтов"""
    
    # Все доступные парсеры
    SCRAPERS = {
        "kufar": KufarScraper,
        "realt": RealtByScraper,
        "domovita": DomovitaScraper,
        "onliner": OnlinerRealtScraper,
        "gohome": GoHomeScraper,
        "hata": HataScraper,
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
        
        # Выполняем все запросы параллельно
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Обрабатываем результаты
        for source_name, result in zip(source_names, results):
            if isinstance(result, Exception):
                print(f"[{source_name}] Ошибка: {result}")
            elif isinstance(result, list):
                all_listings.extend(result)
                print(f"[{source_name}] Найдено: {len(result)} объявлений")
        
        # Удаляем дубликаты по ID
        unique_listings = self._remove_duplicates(all_listings)
        
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
            print(f"[{scraper.SOURCE_NAME}] Ошибка получения данных: {e}")
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

