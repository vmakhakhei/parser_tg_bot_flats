"""
Парсер для baranovichi.etagi.com
"""
import re
import sys
import os
from typing import List, Optional
from bs4 import BeautifulSoup

# Добавляем родительскую директорию в path для импорта
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.base import BaseScraper, Listing

# Импортируем error_logger если доступен
try:
    from error_logger import log_error, log_warning, log_info
except ImportError:
    def log_error(source, message, exception=None):
        print(f"[ERROR] [{source}] {message}: {exception}")
    def log_warning(source, message):
        print(f"[WARN] [{source}] {message}")
    def log_info(source, message):
        print(f"[INFO] [{source}] {message}")


class EtagiScraper(BaseScraper):
    """Парсер объявлений с baranovichi.etagi.com через API"""
    
    SOURCE_NAME = "etagi"
    BASE_URL = "https://baranovichi.etagi.com"
    API_URL = "https://baranovichi.etagi.com/rest/etagi.flats"
    CITY_ID = 3192  # ID Барановичей в системе Etagi
    
    async def fetch_listings(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
    ) -> List[Listing]:
        """Получает список объявлений через API"""
        
        # Формируем запрос к API
        import urllib.parse
        import json
        
        # Фильтр для получения квартир
        filter_data = [
            "and",
            [
                ["in|=", "f.city_id", [self.CITY_ID]],
                ["=", "class", "flats"],
                ["in", "status", ["active"]]
            ]
        ]
        
        params = {
            "protName": "flats",
            "filter": json.dumps(filter_data),
            "orderId": "default",
            "limit": "50",
            "as": "f",
            "lang": "ru",
        }
        
        url = f"{self.API_URL}?{urllib.parse.urlencode(params)}"
        log_info("etagi", f"Запрос API: {url[:100]}...")
        
        json_data = await self._fetch_json(url)
        if not json_data:
            log_warning("etagi", "API не вернул данные")
            return []
        
        return self._parse_api_response(json_data, min_rooms, max_rooms, min_price, max_price)
    
    def _parse_api_response(
        self, 
        data: dict,
        min_rooms: int,
        max_rooms: int,
        min_price: int,
        max_price: int
    ) -> List[Listing]:
        """Парсит ответ API"""
        listings = []
        
        # API возвращает данные в поле "data" или напрямую как список
        items = data.get("data", data) if isinstance(data, dict) else data
        
        if not isinstance(items, list):
            items = [items] if items else []
        
        log_info("etagi", f"API вернул {len(items)} записей")
        
        for item in items:
            listing = self._parse_api_item(item)
            if listing:
                if self._matches_filters(listing, min_rooms, max_rooms, min_price, max_price):
                    listings.append(listing)
        
        log_info("etagi", f"После фильтрации: {len(listings)} объявлений")
        return listings
    
    def _parse_api_item(self, item: dict) -> Optional[Listing]:
        """Парсит одну запись из API"""
        try:
            # ID объявления
            obj_id = item.get("id") or item.get("object_id")
            if not obj_id:
                return None
            
            listing_id = f"etagi_{obj_id}"
            
            # URL
            url = f"{self.BASE_URL}/realty/{obj_id}/"
            
            # Комнаты
            rooms = int(item.get("rooms", 0) or item.get("rooms_count", 0) or 0)
            if rooms == 0 and item.get("is_studio"):
                rooms = 1
            
            # Площадь
            area = float(item.get("area", 0) or item.get("area_total", 0) or 0)
            
            # Цена в BYN
            price_byn = int(item.get("price", 0) or 0)
            
            # Цена за м²
            price_per_sqm = int(item.get("price_m2", 0) or 0)
            
            # Этаж
            floor_num = item.get("floor", "")
            floor_total = item.get("floors", "") or item.get("floors_total", "")
            floor = f"{floor_num}/{floor_total}" if floor_num and floor_total else str(floor_num) if floor_num else ""
            
            # Адрес
            street = item.get("street", "") or item.get("address", "")
            house = item.get("house", "") or item.get("house_num", "")
            address = f"{street} {house}, Барановичи".strip(", ")
            if not street:
                address = "Барановичи"
            
            # Пропускаем если цена = 0
            if price_byn == 0:
                return None
            
            # Конвертация BYN -> USD (курс ~2.95)
            price_usd = int(price_byn / 2.95) if price_byn else 0
            
            # Формируем заголовок
            title = f"{rooms}-комн., {area} м²" if rooms and area else "Квартира"
            
            # Фото
            photos = []
            if item.get("images"):
                for img in item["images"][:3]:
                    if isinstance(img, dict):
                        photos.append(img.get("src", ""))
                    elif isinstance(img, str):
                        photos.append(img)
            
            return Listing(
                id=listing_id,
                source="Etagi.com",
                title=title,
                price=price_byn,
                price_formatted=f"{price_byn:,} BYN (≈${price_usd:,})".replace(",", " "),
                rooms=rooms,
                area=area,
                floor=floor,
                address=address,
                photos=photos,
                url=url,
                currency="BYN",
                price_usd=price_usd,
                price_byn=price_byn,
                price_per_sqm=price_per_sqm,
                price_per_sqm_formatted=f"{price_per_sqm:,} BYN/м²".replace(",", " ") if price_per_sqm else "",
            )
            
        except Exception as e:
            log_error("etagi", f"Ошибка парсинга записи API", e)
            return None
    
    def _matches_filters(
        self, 
        listing: Listing, 
        min_rooms: int,
        max_rooms: int,
        min_price: int,
        max_price: int
    ) -> bool:
        """Проверяет соответствие фильтрам"""
        # Фильтры в USD, конвертируем цену Etagi из BYN в USD для сравнения
        price_usd = listing.price_usd if listing.price_usd else int(listing.price / 2.95)
        
        if listing.rooms > 0 and (listing.rooms < min_rooms or listing.rooms > max_rooms):
            return False
        if price_usd > 0 and (price_usd < min_price or price_usd > max_price):
            return False
        return True
