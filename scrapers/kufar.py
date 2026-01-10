"""
Парсер для re.kufar.by через официальный API
"""
import re
import json
import sys
import os
from typing import List, Optional

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


class KufarScraper(BaseScraper):
    """Парсер объявлений с Kufar через API"""
    
    SOURCE_NAME = "kufar"
    BASE_URL = "https://re.kufar.by"
    API_URL = "https://api.kufar.by/search-api/v2/search/rendered-paginated"
    
    async def fetch_listings(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
    ) -> List[Listing]:
        """Получает список объявлений через API"""
        
        # Базовые параметры запроса (работающий формат из браузера)
        # size=30 берем из пагинации сайта
        url = (
            f"{self.API_URL}"
            f"?cat=1010"  # Категория: квартиры
            f"&cur=USD"
            f"&gtsy=country-belarus~province-brestskaja_oblast~locality-baranovichi"
            f"&typ=sell"  # Продажа
            f"&sort=lst.d"  # Сортировка по дате (новые первые)
            f"&size=30"  # Количество объявлений на странице
        )
        
        log_info("kufar", f"Запрос API: {url}")
        
        # Получаем JSON
        json_data = await self._fetch_json(url)
        if not json_data:
            return []
        
        return self._parse_api_response(json_data, min_rooms, max_rooms, min_price, max_price)
    
    async def _fetch_json(self, url: str) -> Optional[dict]:
        """Получает JSON от API"""
        import aiohttp
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://re.kufar.by/",
            "Origin": "https://re.kufar.by",
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=30) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        log_warning("kufar", f"API ответил кодом {response.status}")
                        return None
        except Exception as e:
            log_error("kufar", f"Ошибка API запроса", e)
            return None
    
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
        
        # Данные в ads
        ads = data.get("ads", [])
        
        for ad in ads[:30]:  # Берём первые 30
            listing = self._parse_ad(ad)
            if listing:
                if self._matches_filters(listing, min_rooms, max_rooms, min_price, max_price):
                    listings.append(listing)
        
        log_info("kufar", f"Найдено: {len(listings)} объявлений")
        return listings
    
    def _parse_ad(self, ad: dict) -> Optional[Listing]:
        """Парсит одно объявление из API"""
        try:
            # ID
            ad_id = ad.get("ad_id")
            if not ad_id:
                return None
            listing_id = f"kufar_{ad_id}"
            
            # URL
            ad_link = ad.get("ad_link", "")
            if ad_link and not ad_link.startswith("http"):
                ad_link = f"{self.BASE_URL}{ad_link}"
            
            # Получаем параметры объявления
            params = {}
            for param in ad.get("ad_parameters", []):
                params[param.get("p")] = param.get("v")
                params[f"{param.get('p')}_text"] = param.get("vl")
            
            # Комнаты
            rooms = 0
            rooms_val = params.get("rooms", "")
            if rooms_val:
                try:
                    rooms = int(rooms_val)
                except:
                    if "5" in str(rooms_val):
                        rooms = 5
            
            # Площадь
            area = 0.0
            area_val = params.get("size", "")
            if area_val:
                try:
                    area = float(str(area_val).replace(",", "."))
                except:
                    pass
            
            # Этаж
            floor = ""
            floor_val = params.get("floor", "")
            floors_val = params.get("re_number_floors", "")
            # floor может быть списком [1] или числом
            if isinstance(floor_val, list) and floor_val:
                floor_val = floor_val[0]
            if floor_val and floors_val:
                floor = f"{floor_val}/{floors_val}"
            elif floor_val:
                floor = str(floor_val)
            
            # Цена
            price = 0
            price_usd = 0
            price_byn = 0
            currency = "USD"
            
            # Цена в USD (в центах)
            raw_price_usd = ad.get("price_usd", "")
            if raw_price_usd:
                try:
                    price_usd = int(raw_price_usd) // 100
                    price = price_usd
                except:
                    pass
            
            # Цена в BYN (в копейках)
            raw_price_byn = ad.get("price_byn", "")
            if raw_price_byn:
                try:
                    price_byn = int(raw_price_byn) // 100
                    # Если нет USD цены, используем BYN
                    if not price_usd:
                        price = price_byn
                        currency = "BYN"
                except:
                    pass
            
            # Если нет цены, пробуем другие варианты
            if not price:
                for param in ad.get("ad_parameters", []):
                    if "price" in param.get("p", "").lower():
                        try:
                            price = int(param.get("v", 0))
                        except:
                            pass
            
            # Адрес
            address = "Барановичи"
            
            # Из account_parameters
            for acc_param in ad.get("account_parameters", []):
                if acc_param.get("p") == "address":
                    address = acc_param.get("v", "Барановичи")
                    break
            
            # Или из параметров
            if address == "Барановичи":
                street = params.get("street_text", "") or params.get("street", "")
                house = params.get("house", "")
                if street:
                    address = street
                    if house:
                        address += f", {house}"
                    address += ", Барановичи"
            
            # Фото - пока отключены из-за проблем с URL
            # Kufar использует CDN с токенами, поэтому напрямую ссылки не работают
            # TODO: исследовать правильный формат URL для фото
            photos = []
            # images = ad.get("images", [])
            # for img in images[:3]:
            #     path = img.get("path", "")
            #     if path:
            #         photo_url = f"https://rms.kufar.by/v1/{path}"
            #         photos.append(photo_url)
            
            # Формируем заголовок
            title = f"{rooms}-комн., {area} м²" if rooms and area else "Квартира"
            
            # Форматирование цены в зависимости от валюты
            if currency == "USD":
                price_formatted = f"${price:,}".replace(",", " ") if price else "Цена не указана"
            else:
                price_formatted = f"{price:,} BYN".replace(",", " ") if price else "Цена не указана"
            
            # Добавляем цену в другой валюте если есть
            if price_usd and price_byn:
                if currency == "USD":
                    price_formatted += f" ({price_byn:,} BYN)".replace(",", " ")
                else:
                    price_formatted += f" (${price_usd:,})".replace(",", " ")
            
            return Listing(
                id=listing_id,
                source="Kufar.by",
                title=title,
                price=price,
                price_formatted=price_formatted,
                rooms=rooms if rooms else 0,
                area=area if area else 0.0,
                floor=floor,
                address=address,
                photos=photos,
                url=ad_link,
                currency=currency,
                price_usd=price_usd,
                price_byn=price_byn,
            )
            
        except Exception as e:
            log_error("kufar", f"Ошибка парсинга объявления", e)
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
        if listing.rooms > 0 and (listing.rooms < min_rooms or listing.rooms > max_rooms):
            return False
        if listing.price > 0 and (listing.price < min_price or listing.price > max_price):
            return False
        return True
