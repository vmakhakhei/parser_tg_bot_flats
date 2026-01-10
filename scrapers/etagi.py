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
        
        url = f"{self.BASE_URL}/realty/"
        
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
        seen_ids = set()
        
        # Ищем все ссылки на объявления формата /realty/XXXXXXXX/
        for link in soup.find_all('a', href=re.compile(r'/realty/\d+/')):
            href = link.get('href', '')
            
            # Извлекаем ID объявления
            id_match = re.search(r'/realty/(\d+)/', href)
            if not id_match:
                continue
            
            object_id = id_match.group(1)
            listing_id = f"etagi_{object_id}"
            
            # Пропускаем дубликаты
            if listing_id in seen_ids:
                continue
            seen_ids.add(listing_id)
            
            # Ищем текст объявления
            link_text = link.get_text(separator=' ', strip=True)
            
            # Пропускаем если это не карточка объявления
            if 'комн' not in link_text.lower() and 'студия' not in link_text.lower():
                continue
            
            # Парсим данные
            listing = self._parse_listing_from_text(link_text, listing_id, href)
            if listing:
                if self._matches_filters(listing, min_rooms, max_rooms, min_price, max_price):
                    listings.append(listing)
        
        log_info("etagi", f"Найдено: {len(listings)} объявлений")
        return listings
    
    def _parse_listing_from_text(self, text: str, listing_id: str, href: str) -> Optional[Listing]:
        """Парсит объявление из текста"""
        try:
            # URL
            url = href if href.startswith('http') else f"{self.BASE_URL}{href}"
            
            # Комнаты
            rooms = 0
            rooms_match = re.search(r'(\d+)-комн', text, re.I)
            if rooms_match:
                rooms = int(rooms_match.group(1))
            elif 'студия' in text.lower():
                rooms = 1
            
            # Площадь - ищем формат "XX.X м2"
            area = 0.0
            area_match = re.search(r'(\d+(?:[.,]\d+)?)\s*м[²2]', text)
            if area_match:
                area = float(area_match.group(1).replace(',', '.'))
            
            # Цена в BYN - ищем формат "XXX XXX BYN"
            price_byn = 0
            price_byn_match = re.search(r'(\d[\d\s]*\d)\s*BYN', text)
            if price_byn_match:
                price_str = price_byn_match.group(1).replace(' ', '').replace('\xa0', '')
                try:
                    price_byn = int(price_str)
                except:
                    pass
            
            # Цена за м² в BYN
            price_per_sqm = 0
            price_per_sqm_match = re.search(r'(\d[\d\s]*\d)\s*BYN\s*/\s*м[²2]', text)
            if price_per_sqm_match:
                price_str = price_per_sqm_match.group(1).replace(' ', '').replace('\xa0', '')
                try:
                    price_per_sqm = int(price_str)
                except:
                    pass
            
            # Этаж - ищем формат "X/Y эт"
            floor = ""
            floor_match = re.search(r'(\d+)/(\d+)\s*эт', text)
            if floor_match:
                floor = f"{floor_match.group(1)}/{floor_match.group(2)}"
            
            # Адрес - ищем "ул. XXX" или всё что после этажа
            address = "Барановичи"
            addr_match = re.search(r'(ул\.\s*[^\d]+?)(?:\d|Показать|$)', text)
            if addr_match:
                address = addr_match.group(1).strip() + ", Барановичи"
            
            # Состояние квартиры
            condition = ""
            condition_patterns = [
                'Обычное состояние', 'Косметический ремонт', 'Евроремонт',
                'Требует ремонта', 'Улучшенная черновая', 'Без отделки'
            ]
            for pattern in condition_patterns:
                if pattern.lower() in text.lower():
                    condition = pattern
                    break
            
            # Формируем заголовок
            title = f"{rooms}-комн., {area} м²" if rooms and area else "Квартира"
            if condition:
                title += f" ({condition})"
            
            # Пропускаем если цена = 0
            if price_byn == 0:
                return None
            
            # Конвертация примерная BYN -> USD (курс ~2.95)
            price_usd = int(price_byn / 2.95) if price_byn else 0
            
            # Формируем цену
            price_formatted = f"{price_byn:,} BYN".replace(",", " ")
            if price_usd:
                price_formatted += f" (≈${price_usd:,})".replace(",", " ")
            
            return Listing(
                id=listing_id,
                source="Etagi.com",
                title=title,
                price=price_byn,  # Основная цена в BYN
                price_formatted=price_formatted,
                rooms=rooms,
                area=area,
                floor=floor,
                address=address,
                photos=[],  # Фото не парсим (требуется отдельный запрос)
                url=url,
                currency="BYN",
                price_usd=price_usd,
                price_byn=price_byn,
                price_per_sqm=price_per_sqm,
                price_per_sqm_formatted=f"{price_per_sqm:,} BYN/м²".replace(",", " ") if price_per_sqm else "",
            )
            
        except Exception as e:
            log_error("etagi", f"Ошибка парсинга объявления", e)
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
