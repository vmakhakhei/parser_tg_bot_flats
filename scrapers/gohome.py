"""
Парсер для GoHome.by
"""
import re
from typing import List, Optional
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Listing


class GoHomeScraper(BaseScraper):
    """Парсер объявлений с gohome.by"""
    
    SOURCE_NAME = "gohome"
    BASE_URL = "https://gohome.by"
    
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
        url = f"{self.BASE_URL}/sale/flat/baranovichi"
        
        # Параметры фильтрации
        params = []
        
        # Комнаты
        rooms_param = ",".join(str(r) for r in range(min_rooms, max_rooms + 1))
        if rooms_param:
            params.append(f"rooms={rooms_param}")
        
        # Цена
        if min_price > 0:
            params.append(f"price_from={min_price}")
        if max_price < 100000:
            params.append(f"price_to={max_price}")
        
        params.append("currency=usd")
        
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
        cards = soup.find_all('div', class_=re.compile(r'object|listing|card|item'))
        if not cards:
            cards = soup.find_all('article')
        if not cards:
            # Ищем ссылки на объявления
            cards = soup.find_all('a', href=re.compile(r'/sale/flat/\d+'))
        
        seen_urls = set()
        for card in cards[:30]:
            listing = self._parse_card(card)
            if listing and listing.url not in seen_urls:
                if self._matches_filters(listing, min_rooms, max_rooms, min_price, max_price):
                    listings.append(listing)
                    seen_urls.add(listing.url)
        
        return listings
    
    def _parse_card(self, card) -> Optional[Listing]:
        """Парсит карточку объявления"""
        try:
            # Ссылка
            if card.name == 'a':
                link_elem = card
            else:
                link_elem = card.find('a', href=re.compile(r'/sale/|/flat/'))
            
            if not link_elem:
                return None
            
            url = link_elem.get('href', '')
            if not url:
                return None
            if not url.startswith('http'):
                url = f"{self.BASE_URL}{url}"
            
            listing_id = Listing.generate_id("gohome", url)
            
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
            
            # Цена (ищем в долларах)
            price = 0
            price_patterns = [
                r'([\d\s]+)\s*\$',
                r'\$\s*([\d\s]+)',
                r'([\d\s]+)\s*USD',
            ]
            for pattern in price_patterns:
                price_match = re.search(pattern, text, re.I)
                if price_match:
                    price = self._parse_price(price_match.group(1))
                    break
            
            # Адрес
            address = "Барановичи"
            address_elem = card.find(class_=re.compile(r'address|location|street'))
            if address_elem:
                address = address_elem.get_text(strip=True)
            else:
                # Пробуем найти адрес в тексте
                addr_match = re.search(r'ул\.\s*[^,\d]+', text)
                if addr_match:
                    address = addr_match.group(0)
            
            # Фото
            photos = []
            for img in card.find_all('img', src=True)[:3]:
                img_src = img.get('src') or img.get('data-src') or img.get('data-lazy')
                if img_src and 'placeholder' not in img_src.lower():
                    if not img_src.startswith('http'):
                        img_src = f"{self.BASE_URL}{img_src}"
                    photos.append(img_src)
            
            title = f"{rooms}-комн. квартира, {area} м²" if rooms else "Квартира"
            
            # Валидация через DTO перед созданием полного Listing
            dto = self.validate_listing_data(
                title=title,
                price=price,
                url=url,
                location=address,
                source="GoHome.by"
            )
            
            if not dto:
                log_warning("gohome", f"Объявление не прошло валидацию DTO: title='{title[:50]}...', price={price}, url={url[:50]}...")
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
            print(f"[GoHome] Ошибка парсинга: {e}")
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

