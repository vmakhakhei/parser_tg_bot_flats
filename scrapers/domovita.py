"""
Парсер для Domovita.by
"""
import re
from typing import List, Optional
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Listing


class DomovitaScraper(BaseScraper):
    """Парсер объявлений с Domovita.by"""
    
    SOURCE_NAME = "domovita"
    BASE_URL = "https://domovita.by"
    
    async def fetch_listings(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
    ) -> List[Listing]:
        """Получает список объявлений"""
        # URL для продажи квартир в Барановичах
        url = f"{self.BASE_URL}/prodazha/kvartiry/baranovichi"
        
        # Добавляем параметры фильтрации
        params = []
        if min_rooms > 1:
            params.append(f"rooms_from={min_rooms}")
        if max_rooms < 4:
            params.append(f"rooms_to={max_rooms}")
        if min_price > 0:
            params.append(f"price_from={min_price}")
        if max_price < 100000:
            params.append(f"price_to={max_price}")
        
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
        cards = soup.find_all('div', class_=re.compile(r'object-card|listing-card|item'))
        if not cards:
            cards = soup.find_all('article')
        if not cards:
            cards = soup.find_all('a', href=re.compile(r'/prodazha/kvartiry/'))
        
        for card in cards[:20]:  # Ограничиваем количество
            listing = self._parse_card(card)
            if listing and self._matches_filters(listing, min_rooms, max_rooms, min_price, max_price):
                listings.append(listing)
        
        return listings
    
    def _parse_card(self, card) -> Optional[Listing]:
        """Парсит карточку объявления"""
        try:
            # Ссылка
            link_elem = card if card.name == 'a' else card.find('a', href=True)
            if not link_elem:
                return None
                
            url = link_elem.get('href', '')
            if not url or 'prodazha' not in url:
                return None
            if not url.startswith('http'):
                url = f"{self.BASE_URL}{url}"
            
            listing_id = Listing.generate_id("domovita", url)
            
            # Заголовок и информация
            text_content = card.get_text(separator=' ', strip=True)
            
            # Комнаты
            rooms = 0
            rooms_match = re.search(r'(\d+)\s*-?\s*комн', text_content, re.I)
            if rooms_match:
                rooms = int(rooms_match.group(1))
            
            # Площадь
            area = 0.0
            area_match = re.search(r'(\d+[.,]?\d*)\s*м[²2]?', text_content)
            if area_match:
                area = float(area_match.group(1).replace(',', '.'))
            
            # Цена
            price = 0
            price_match = re.search(r'([\d\s]+)\s*\$', text_content)
            if price_match:
                price = self._parse_price(price_match.group(1))
            else:
                price_match = re.search(r'\$\s*([\d\s]+)', text_content)
                if price_match:
                    price = self._parse_price(price_match.group(1))
            
            # Адрес
            address = "Барановичи"
            address_elem = card.find(class_=re.compile(r'address|location'))
            if address_elem:
                address = address_elem.get_text(strip=True)
            
            # Фото
            photos = []
            img_elem = card.find('img', src=True)
            if img_elem:
                img_src = img_elem.get('src') or img_elem.get('data-src')
                if img_src and not 'placeholder' in img_src:
                    if not img_src.startswith('http'):
                        img_src = f"{self.BASE_URL}{img_src}"
                    photos.append(img_src)
            
            title = f"{rooms}-комн. квартира, {area} м²" if rooms and area else "Квартира"
            
            # Валидация через DTO перед созданием полного Listing
            dto = self.validate_listing_data(
                title=title,
                price=price,
                url=url,
                location=address,
                source="Domovita.by"
            )
            
            if not dto:
                log_warning("domovita", f"Объявление не прошло валидацию DTO: title='{title[:50]}...', price={price}, url={url[:50]}...")
                return None
            
            return Listing(
                id=listing_id,
                source=dto.source,
                title=dto.title,
                price=dto.price,
                price_formatted=f"${price:,}".replace(",", " ") if price else "Цена не указана",
                rooms=rooms,
                area=area,
                address=dto.location,
                photos=photos,
                url=dto.url,
            )
            
        except Exception as e:
            print(f"[Domovita] Ошибка парсинга: {e}")
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

