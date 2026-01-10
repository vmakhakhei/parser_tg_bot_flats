"""
Парсер для Etagi (baranovichi.etagi.com)
"""
import re
from typing import List, Optional
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Listing


class EtagiScraper(BaseScraper):
    """Парсер объявлений с baranovichi.etagi.com"""
    
    SOURCE_NAME = "etagi"
    BASE_URL = "https://baranovichi.etagi.com"
    
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
        url = f"{self.BASE_URL}/realty/"
        
        # Параметры
        params = []
        
        # Комнаты
        rooms_list = [str(r) for r in range(min_rooms, max_rooms + 1)]
        if rooms_list:
            params.append(f"rooms={','.join(rooms_list)}")
        
        # Цена (Etagi использует рубли, нужно конвертировать)
        # Примерный курс: 1 USD = 3.2 BYN = 90 RUB
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
        
        # Ищем карточки объявлений (Etagi использует разные классы)
        cards = soup.find_all('div', class_=re.compile(r'card|object|listing|item'))
        if not cards:
            cards = soup.find_all('article')
        if not cards:
            cards = soup.find_all('a', href=re.compile(r'/realty/\d+'))
        
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
            
            # ID
            id_match = re.search(r'/(\d+)', url)
            listing_id = f"etagi_{id_match.group(1)}" if id_match else Listing.generate_id("etagi", url)
            
            # Текст
            text = card.get_text(separator=' ', strip=True)
            
            # Комнаты
            rooms = 0
            patterns = [
                r'(\d+)\s*-?\s*комн',
                r'(\d+)\s*к\.',
                r'(\d+)\s*комн',
            ]
            for pattern in patterns:
                rooms_match = re.search(pattern, text, re.I)
                if rooms_match:
                    rooms = int(rooms_match.group(1))
                    break
            
            # Площадь
            area = 0.0
            area_match = re.search(r'(\d+[.,]?\d*)\s*м[²2]?', text)
            if area_match:
                area = float(area_match.group(1).replace(',', '.'))
            
            # Цена (Etagi может показывать в разных валютах)
            price = 0
            # Сначала ищем в долларах
            usd_match = re.search(r'([\d\s]+)\s*\$|USD\s*([\d\s]+)', text)
            if usd_match:
                price_str = usd_match.group(1) or usd_match.group(2)
                price = self._parse_price(price_str)
            else:
                # Ищем в рублях и конвертируем
                rub_match = re.search(r'([\d\s]+)\s*(?:руб|₽|RUB)', text, re.I)
                if rub_match:
                    rub_price = self._parse_price(rub_match.group(1))
                    price = rub_price // 90  # Примерная конвертация
                else:
                    # Ищем в белорусских рублях
                    byn_match = re.search(r'([\d\s]+)\s*(?:BYN|бел\.?\s*руб)', text, re.I)
                    if byn_match:
                        byn_price = self._parse_price(byn_match.group(1))
                        price = byn_price // 3  # Примерная конвертация
            
            # Адрес
            address = "Барановичи"
            address_elem = card.find(class_=re.compile(r'address|location|geo'))
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
                img_src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                if img_src and 'placeholder' not in img_src.lower() and 'no-' not in img_src.lower():
                    if img_src.startswith('//'):
                        img_src = f"https:{img_src}"
                    elif not img_src.startswith('http'):
                        img_src = f"{self.BASE_URL}{img_src}"
                    photos.append(img_src)
            
            title = f"{rooms}-комн. квартира, {area} м²" if rooms else "Квартира"
            
            return Listing(
                id=listing_id,
                source="Etagi.com",
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
            print(f"[Etagi] Ошибка парсинга: {e}")
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

