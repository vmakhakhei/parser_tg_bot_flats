"""
Парсер для re.kufar.by (новый домен Kufar Недвижимость)
"""
import re
from typing import List, Optional
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Listing


class KufarScraper(BaseScraper):
    """Парсер объявлений с re.kufar.by"""
    
    SOURCE_NAME = "kufar"
    BASE_URL = "https://re.kufar.by"
    
    def _build_search_url(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
    ) -> str:
        """Строит URL для поиска квартир"""
        # Базовый URL для квартир в Барановичах
        url = f"{self.BASE_URL}/l/baranovichi/kupit/kvartiru"
        
        # Параметры
        params = []
        
        # Количество комнат
        rooms = []
        for r in range(min_rooms, min(max_rooms + 1, 5)):
            rooms.append(str(r))
        if max_rooms >= 5:
            rooms.append("5_and_more")
        if rooms:
            params.append(f"rooms={','.join(rooms)}")
        
        # Цена (в долларах)
        if min_price > 0:
            params.append(f"price_usd_from={min_price}")
        if max_price < 100000:
            params.append(f"price_usd_to={max_price}")
        
        # Сортировка по дате
        params.append("sort=date_desc")
        
        if params:
            url += "?" + "&".join(params)
        
        return url
    
    async def fetch_listings(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
    ) -> List[Listing]:
        """Получает список объявлений"""
        url = self._build_search_url(city, min_rooms, max_rooms, min_price, max_price)
        
        html = await self._fetch_html(url)
        if not html:
            return []
        
        return self._parse_html(html, min_rooms, max_rooms, min_price, max_price)
    
    def _parse_html(
        self, 
        html: str,
        min_rooms: int,
        max_rooms: int,
        min_price: int,
        max_price: int
    ) -> List[Listing]:
        """Парсит HTML страницу"""
        soup = BeautifulSoup(html, 'lxml')
        listings = []
        
        # Ищем все ссылки на объявления
        links = soup.find_all('a', href=re.compile(r'/vi/[^/]+/kupit/kvartiru/\d+'))
        
        seen_ids = set()
        for link in links:
            listing = self._parse_listing_link(link)
            if listing and listing.id not in seen_ids:
                if self._matches_filters(listing, min_rooms, max_rooms, min_price, max_price):
                    listings.append(listing)
                    seen_ids.add(listing.id)
        
        return listings
    
    def _parse_listing_link(self, link) -> Optional[Listing]:
        """Парсит ссылку на объявление"""
        try:
            url = link.get('href', '')
            if not url:
                return None
            
            if not url.startswith('http'):
                url = f"{self.BASE_URL}{url}"
            
            # ID из URL
            id_match = re.search(r'/(\d+)', url)
            if not id_match:
                return None
            listing_id = f"kufar_{id_match.group(1)}"
            
            # Текст объявления
            text = link.get_text(separator=' ', strip=True)
            
            # Цена
            price = 0
            price_match = re.search(r'([\d\s]+)\s*\$', text)
            if price_match:
                price = self._parse_price(price_match.group(1))
            
            # Комнаты и площадь
            rooms = 0
            area = 0.0
            
            # Ищем паттерн "X комн., XX м²"
            rooms_area_match = re.search(r'(\d+)\s*комн[.,]\s*([\d.,]+)\s*м', text)
            if rooms_area_match:
                rooms = int(rooms_area_match.group(1))
                area = float(rooms_area_match.group(2).replace(',', '.'))
            else:
                # Пробуем найти отдельно
                rooms_match = re.search(r'(\d+)\s*комн', text)
                if rooms_match:
                    rooms = int(rooms_match.group(1))
                area_match = re.search(r'([\d.,]+)\s*м[²2]?', text)
                if area_match:
                    area = float(area_match.group(1).replace(',', '.'))
            
            # Этаж
            floor = ""
            floor_match = re.search(r'этаж\s*(\d+)\s*из\s*(\d+)', text, re.I)
            if floor_match:
                floor = f"{floor_match.group(1)}/{floor_match.group(2)}"
            
            # Адрес
            address = "Барановичи"
            # Обычно адрес идёт после площади
            addr_match = re.search(r'(?:м²|м2)\s+(.+?)(?:Барановичи|Брест|$)', text)
            if addr_match:
                address = addr_match.group(1).strip()
                if not address:
                    address = "Барановичи"
            
            # Фото
            photos = []
            img = link.find('img', src=True)
            if img:
                img_src = img.get('src') or img.get('data-src')
                if img_src and 'placeholder' not in img_src.lower():
                    if img_src.startswith('//'):
                        img_src = f"https:{img_src}"
                    elif not img_src.startswith('http'):
                        img_src = f"{self.BASE_URL}{img_src}"
                    photos.append(img_src)
            
            title = f"{rooms}-комн., {area} м²" if rooms and area else "Квартира"
            
            return Listing(
                id=listing_id,
                source="Kufar.by",
                title=title,
                price=price,
                price_formatted=f"${price:,}".replace(",", " ") if price else "Цена не указана",
                rooms=rooms,
                area=area,
                floor=floor,
                address=address,
                photos=photos,
                url=url,
            )
            
        except Exception as e:
            print(f"[Kufar] Ошибка парсинга: {e}")
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
