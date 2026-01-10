"""
Парсер для Hata.by (baranovichi.hata.by)
"""
import re
from typing import List, Optional
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Listing


class HataScraper(BaseScraper):
    """Парсер объявлений с baranovichi.hata.by"""
    
    SOURCE_NAME = "hata"
    BASE_URL = "https://baranovichi.hata.by"
    
    async def fetch_listings(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
    ) -> List[Listing]:
        """Получает список объявлений"""
        
        # URL для продажи квартир
        url = f"{self.BASE_URL}/sale-flat/"
        
        # Параметры
        params = []
        
        # Комнаты
        if min_rooms > 1:
            params.append(f"rooms_min={min_rooms}")
        if max_rooms < 4:
            params.append(f"rooms_max={max_rooms}")
        
        # Цена
        if min_price > 0:
            params.append(f"price_min={min_price}")
        if max_price < 100000:
            params.append(f"price_max={max_price}")
        
        params.append("currency=2")  # USD
        
        if params:
            url += "?" + "&".join(params)
        
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
        
        # Ищем карточки объявлений
        cards = soup.find_all('div', class_=re.compile(r'object|item|card|listing'))
        if not cards:
            cards = soup.find_all('tr', class_=re.compile(r'item|row'))
        if not cards:
            cards = soup.find_all('a', href=re.compile(r'/sale-flat/\d+|/flat/\d+'))
        
        seen_ids = set()
        for card in cards[:30]:
            listing = self._parse_card(card)
            if listing and listing.id not in seen_ids:
                if self._matches_filters(listing, min_rooms, max_rooms, min_price, max_price):
                    listings.append(listing)
                    seen_ids.add(listing.id)
        
        return listings
    
    def _parse_card(self, card) -> Optional[Listing]:
        """Парсит карточку объявления"""
        try:
            # Ссылка
            if card.name == 'a':
                link_elem = card
            else:
                link_elem = card.find('a', href=True)
            
            if not link_elem:
                return None
            
            url = link_elem.get('href', '')
            if not url:
                return None
            if not url.startswith('http'):
                url = f"{self.BASE_URL}{url}"
            
            # ID из URL
            id_match = re.search(r'/(\d+)', url)
            listing_id = f"hata_{id_match.group(1)}" if id_match else Listing.generate_id("hata", url)
            
            # Текст для анализа
            text = card.get_text(separator=' ', strip=True)
            
            # Комнаты
            rooms = 0
            rooms_match = re.search(r'(\d+)\s*-?\s*комн', text, re.I)
            if rooms_match:
                rooms = int(rooms_match.group(1))
            
            # Площадь
            area = 0.0
            area_match = re.search(r'(\d+[.,]?\d*)\s*м[²2]?', text)
            if area_match:
                area = float(area_match.group(1).replace(',', '.'))
            
            # Цена
            price = 0
            price_patterns = [
                r'([\d\s]+)\s*\$',
                r'\$\s*([\d\s]+)',
                r'([\d\s]+)\s*у\.?\s*е\.?',
                r'([\d\s]+)\s*USD',
            ]
            for pattern in price_patterns:
                price_match = re.search(pattern, text, re.I)
                if price_match:
                    price = self._parse_price(price_match.group(1))
                    break
            
            # Адрес
            address = "Барановичи"
            address_elem = card.find(class_=re.compile(r'address|street|location'))
            if address_elem:
                address = address_elem.get_text(strip=True)
            
            # Этаж
            floor = ""
            floor_match = re.search(r'(\d+)\s*/\s*(\d+)\s*эт', text, re.I)
            if floor_match:
                floor = f"{floor_match.group(1)}/{floor_match.group(2)} этаж"
            
            # Фото
            photos = []
            for img in card.find_all('img', src=True)[:3]:
                img_src = img.get('src') or img.get('data-src')
                if img_src and 'no-photo' not in img_src and 'placeholder' not in img_src:
                    if not img_src.startswith('http'):
                        img_src = f"{self.BASE_URL}{img_src}"
                    photos.append(img_src)
            
            title = f"{rooms}-комн., {area} м²" if rooms else "Квартира"
            
            return Listing(
                id=listing_id,
                source="Hata.by",
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
            print(f"[Hata] Ошибка парсинга: {e}")
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

