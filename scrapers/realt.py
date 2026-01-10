"""
Парсер для Realt.by
"""
import re
from typing import List, Optional
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Listing


class RealtByScraper(BaseScraper):
    """Парсер объявлений с Realt.by"""
    
    SOURCE_NAME = "realt.by"
    BASE_URL = "https://realt.by"
    
    def _build_search_url(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
    ) -> str:
        """Строит URL для поиска"""
        # URL для продажи квартир в Барановичах
        url = f"{self.BASE_URL}/sale/flats/?search=eJwrSCzIyEktyq4wTzYy0jVMMje0MDWq0DXIMTU0MDJMLstMSi8GAKP0C9g%3D"
        
        # Добавляем фильтр по комнатам
        rooms_filter = "&".join([f"room_{r}=1" for r in range(min_rooms, max_rooms + 1)])
        if rooms_filter:
            url += f"&{rooms_filter}"
        
        # Фильтр по цене
        if min_price > 0:
            url += f"&price_min={min_price}"
        if max_price < 100000:
            url += f"&price_max={max_price}"
            
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
        # Прямая ссылка на Барановичи
        url = f"{self.BASE_URL}/sale/flats/baranovichi/"
        
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
        cards = soup.find_all('div', class_=re.compile(r'listing-item|teaser|card'))
        
        for card in cards:
            listing = self._parse_card(card)
            if listing and self._matches_filters(listing, min_rooms, max_rooms, min_price, max_price):
                listings.append(listing)
        
        return listings
    
    def _parse_card(self, card) -> Optional[Listing]:
        """Парсит карточку объявления"""
        try:
            # Ссылка и ID
            link_elem = card.find('a', href=re.compile(r'/sale/flats/object/'))
            if not link_elem:
                link_elem = card.find('a', href=True)
            
            if not link_elem:
                return None
                
            url = link_elem.get('href', '')
            if not url.startswith('http'):
                url = f"{self.BASE_URL}{url}"
            
            # Генерируем ID из URL
            listing_id = Listing.generate_id("realt", url)
            
            # Заголовок
            title_elem = card.find(['h2', 'h3', 'span'], class_=re.compile(r'title|name'))
            title = title_elem.get_text(strip=True) if title_elem else "Квартира"
            
            # Цена
            price = 0
            price_elem = card.find(class_=re.compile(r'price|cost'))
            if price_elem:
                price_text = price_elem.get_text(strip=True)
                price = self._parse_price(price_text)
            
            # Комнаты
            rooms = 0
            rooms_match = re.search(r'(\d+)\s*-?\s*комн', title, re.I)
            if rooms_match:
                rooms = int(rooms_match.group(1))
            
            # Площадь
            area = 0.0
            area_match = re.search(r'(\d+[.,]?\d*)\s*м', title + card.get_text())
            if area_match:
                area = float(area_match.group(1).replace(',', '.'))
            
            # Адрес
            address_elem = card.find(class_=re.compile(r'address|location|geo'))
            address = address_elem.get_text(strip=True) if address_elem else "Барановичи"
            
            # Фото
            photos = []
            img_elem = card.find('img', src=True)
            if img_elem:
                img_src = img_elem.get('src') or img_elem.get('data-src')
                if img_src:
                    if not img_src.startswith('http'):
                        img_src = f"{self.BASE_URL}{img_src}"
                    photos.append(img_src)
            
            return Listing(
                id=listing_id,
                source="Realt.by",
                title=title,
                price=price,
                price_formatted=f"${price:,}".replace(",", " ") if price else "Цена не указана",
                rooms=rooms,
                area=area,
                address=address,
                photos=photos,
                url=url,
            )
            
        except Exception as e:
            print(f"[Realt.by] Ошибка парсинга карточки: {e}")
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

