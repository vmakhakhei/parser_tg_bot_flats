"""
Парсер для Realt.Onliner.by
"""
import re
from typing import List, Optional
from scrapers.base import BaseScraper, Listing


class OnlinerRealtScraper(BaseScraper):
    """Парсер объявлений с realt.onliner.by"""
    
    SOURCE_NAME = "onliner"
    BASE_URL = "https://r.onliner.by"
    API_URL = "https://r.onliner.by/sdapi/ak.api/search/apartments"
    
    async def fetch_listings(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
    ) -> List[Listing]:
        """Получает список объявлений через API"""
        
        # Onliner API для поиска квартир
        # Координаты Барановичей примерно: 53.13, 26.02
        params = {
            "bounds[lb][lat]": "53.05",
            "bounds[lb][long]": "25.90",
            "bounds[rt][lat]": "53.20",
            "bounds[rt][long]": "26.15",
            "price[min]": str(min_price) if min_price > 0 else "",
            "price[max]": str(max_price) if max_price < 100000 else "",
            "currency": "usd",
            "page": "1",
            "limit": "30",
        }
        
        # Добавляем фильтр по комнатам
        for r in range(min_rooms, min(max_rooms + 1, 5)):
            params[f"number_of_rooms[{r}]"] = "true"
        
        query = "&".join(f"{k}={v}" for k, v in params.items() if v)
        url = f"{self.API_URL}?{query}"
        
        data = await self._fetch_json(url)
        if not data:
            # Пробуем альтернативный URL
            return await self._fetch_via_html(city, min_rooms, max_rooms, min_price, max_price)
        
        return self._parse_api_response(data, min_rooms, max_rooms, min_price, max_price)
    
    async def _fetch_via_html(
        self,
        city: str,
        min_rooms: int,
        max_rooms: int,
        min_price: int,
        max_price: int,
    ) -> List[Listing]:
        """Запасной вариант через HTML"""
        from bs4 import BeautifulSoup
        
        url = f"{self.BASE_URL}/sale/apartments/baranovichi"
        html = await self._fetch_html(url)
        if not html:
            return []
        
        soup = BeautifulSoup(html, 'lxml')
        listings = []
        
        # Ищем объявления
        items = soup.find_all('div', class_=re.compile(r'classified'))
        
        for item in items[:20]:
            listing = self._parse_html_item(item)
            if listing and self._matches_filters(listing, min_rooms, max_rooms, min_price, max_price):
                listings.append(listing)
        
        return listings
    
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
        
        apartments = data.get("apartments", [])
        if not apartments:
            apartments = data.get("items", [])
        
        for apt in apartments:
            listing = self._parse_apartment(apt)
            if listing and self._matches_filters(listing, min_rooms, max_rooms, min_price, max_price):
                listings.append(listing)
        
        return listings
    
    def _parse_apartment(self, apt: dict) -> Optional[Listing]:
        """Парсит объявление из API"""
        try:
            apt_id = str(apt.get("id", ""))
            if not apt_id:
                return None
            
            # Цена
            price_data = apt.get("price", {})
            price = 0
            if isinstance(price_data, dict):
                price = int(price_data.get("converted", {}).get("USD", {}).get("amount", 0))
            elif isinstance(price_data, (int, float)):
                price = int(price_data)
            
            # Комнаты
            rooms = apt.get("number_of_rooms", 0)
            
            # Площадь
            area = apt.get("area", {})
            if isinstance(area, dict):
                area = float(area.get("total", 0))
            else:
                area = float(area) if area else 0.0
            
            # Адрес
            location = apt.get("location", {})
            address_parts = []
            if location.get("address"):
                address_parts.append(location["address"])
            if location.get("user_address"):
                address_parts.append(location["user_address"])
            address = ", ".join(address_parts) if address_parts else "Барановичи"
            
            # Год постройки из API
            year_built = ""
            # Пробуем разные варианты полей года в API Onliner
            year_val = apt.get("year", "") or apt.get("build_year", "") or apt.get("year_built", "")
            if year_val:
                try:
                    year_int = int(year_val)
                    if 1900 <= year_int <= 2025:
                        year_built = str(year_int)
                except:
                    pass
            
            # Если не нашли в основных полях, ищем в параметрах
            if not year_built:
                params = apt.get("params", [])
                for param in params:
                    if isinstance(param, dict):
                        param_name = param.get("name", "").lower()
                        if "год" in param_name or "year" in param_name or "постройки" in param_name:
                            param_value = param.get("value", "")
                            if param_value:
                                try:
                                    year_int = int(param_value)
                                    if 1900 <= year_int <= 2025:
                                        year_built = str(year_int)
                                        break
                                except:
                                    pass
            
            # Фото
            photos = []
            photo_data = apt.get("photo", [])
            if isinstance(photo_data, list):
                for p in photo_data[:3]:
                    if isinstance(p, str):
                        photos.append(p)
                    elif isinstance(p, dict):
                        photos.append(p.get("url", ""))
            elif isinstance(photo_data, str):
                photos.append(photo_data)
            
            # URL
            url = apt.get("url", f"{self.BASE_URL}/sale/apartments/{apt_id}")
            if not url.startswith("http"):
                url = f"https:{url}" if url.startswith("//") else f"{self.BASE_URL}{url}"
            
            return Listing(
                id=f"onliner_{apt_id}",
                source="Onliner.by",
                title=f"{rooms}-комн. квартира, {area} м²",
                price=price,
                price_formatted=f"${price:,}".replace(",", " ") if price else "Цена не указана",
                rooms=rooms,
                area=area,
                address=address,
                photos=photos,
                url=url,
                year_built=year_built,
            )
            
        except Exception as e:
            print(f"[Onliner] Ошибка парсинга: {e}")
            return None
    
    def _parse_html_item(self, item) -> Optional[Listing]:
        """Парсит HTML элемент объявления"""
        try:
            link = item.find('a', href=True)
            if not link:
                return None
            
            url = link.get('href', '')
            if not url.startswith('http'):
                url = f"{self.BASE_URL}{url}"
            
            listing_id = Listing.generate_id("onliner", url)
            text = item.get_text(separator=' ', strip=True)
            
            # Парсим данные
            rooms = 0
            rooms_match = re.search(r'(\d+)\s*-?\s*комн', text, re.I)
            if rooms_match:
                rooms = int(rooms_match.group(1))
            
            area = 0.0
            area_match = re.search(r'(\d+[.,]?\d*)\s*м', text)
            if area_match:
                area = float(area_match.group(1).replace(',', '.'))
            
            price = 0
            price_match = re.search(r'([\d\s]+)\s*\$', text)
            if price_match:
                price = self._parse_price(price_match.group(1))
            
            # Год постройки - улучшенный парсинг из HTML
            year_built = ""
            year_patterns = [
                r'год\s+постройки[:\s]+(\d{4})',
                r'построен\s+в\s+(\d{4})',
                r'(\d{4})\s+г\.',
                r'(\d{4})\s+год',
                r'год[:\s]+(\d{4})'
            ]
            for pattern in year_patterns:
                year_match = re.search(pattern, text, re.IGNORECASE)
                if year_match:
                    try:
                        year_int = int(year_match.group(1))
                        if 1900 <= year_int <= 2025:
                            year_built = str(year_int)
                            break
                    except:
                        pass
            
            # Фото
            photos = []
            img = item.find('img', src=True)
            if img:
                img_src = img.get('src') or img.get('data-src')
                if img_src:
                    photos.append(img_src)
            
            return Listing(
                id=listing_id,
                source="Onliner.by",
                title=f"{rooms}-комн., {area} м²",
                price=price,
                price_formatted=f"${price:,}".replace(",", " ") if price else "Цена не указана",
                rooms=rooms,
                area=area,
                address="Барановичи",
                photos=photos,
                url=url,
                year_built=year_built,
            )
            
        except Exception as e:
            print(f"[Onliner] Ошибка HTML парсинга: {e}")
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

