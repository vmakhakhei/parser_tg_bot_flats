"""
from error_logger import log_error, log_warning, log_info
from datetime import datetime
from datetime import datetime, timedelta

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
    BASE_URL = "https://baranovichi.etagi.com"  # По умолчанию для Барановичей
    
    def _get_city_url(self, city: str) -> str:
        """Преобразует название города в URL для Etagi"""
        city_lower = city.lower().strip()
        
        # Маппинг городов на поддомены Etagi
        city_mapping = {
            "барановичи": "baranovichi.etagi.com",
            "брест": "brest.etagi.com",
            "минск": "minsk.etagi.com",
            "гомель": "gomel.etagi.com",
            "гродно": "grodno.etagi.com",
            "витебск": "vitebsk.etagi.com",
            "могилев": "mogilev.etagi.com",
            "могилёв": "mogilev.etagi.com",
            "орша": "orsha.etagi.com",
        }
        
        # Если город найден в маппинге, используем его
        if city_lower in city_mapping:
            return f"https://{city_mapping[city_lower]}"
        
        # По умолчанию используем Барановичи
        log_warning("etagi", f"Город '{city}' не найден в маппинге, используем Барановичи")
        return f"https://{city_mapping['барановичи']}"
    
    async def fetch_listings(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
    ) -> List[Listing]:
        """Получает список объявлений через HTML парсинг"""
        
        # Получаем URL для города
        base_url = self._get_city_url(city)
        url = f"{base_url}/realty/"
        
        html = await self._fetch_html(url)
        if not html:
            log_warning("etagi", f"Не удалось загрузить страницу для города {city}: {url}")
            return []
        
        log_info("etagi", f"Загружена страница для города {city}: {url}")
        return self._parse_html(html, min_rooms, max_rooms, min_price, max_price, base_url, city)
    
    def _parse_html(
        self, 
        html: str,
        min_rooms: int,
        max_rooms: int,
        min_price: int,
        max_price: int,
        base_url: str = None,
        city: str = "Минск"
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
            
            # Формируем полный URL
            if href.startswith('http'):
                full_url = href
            else:
                # Используем переданный base_url или fallback на self.BASE_URL
                url_base = base_url if base_url else self.BASE_URL
                full_url = f"{url_base}{href}"
            
            # Ищем родительский контейнер с данными объявления
            container = link.find_parent(['div', 'article', 'li', 'generic'])
            if not container:
                continue
            
            # Парсим данные из контейнера
            listing = self._parse_listing_from_container(container, listing_id, full_url, city)
            if listing:
                if self._matches_filters(listing, min_rooms, max_rooms, min_price, max_price):
                    listings.append(listing)
        
        log_info("etagi", f"Найдено: {len(listings)} объявлений")
        return listings
    
    def _parse_listing_from_container(self, container, listing_id: str, url: str, city: str = "Минск") -> Optional[Listing]:
        """Парсит объявление из контейнера"""
        try:
            text = container.get_text(separator=' ', strip=True)
            
            # Комнаты - ищем "X-комн. кв." или "X-комнатная"
            rooms = 0
            rooms_match = re.search(r'(\d+)\s*-?\s*комн', text, re.I)
            if rooms_match:
                rooms = int(rooms_match.group(1))
            
            # Площадь - ищем "XX.X м²" или "XX.X м2"
            area = 0.0
            area_match = re.search(r'(\d+(?:[.,]\d+)?)\s*м[²2]', text)
            if area_match:
                area = float(area_match.group(1).replace(',', '.'))
            
            # Цена в BYN - ищем "XXX XXX BYN"
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
            
            # Этаж - ищем "X/Y эт."
            floor = ""
            floor_match = re.search(r'(\d+)/(\d+)\s*эт', text)
            if floor_match:
                floor = f"{floor_match.group(1)}/{floor_match.group(2)}"
            
            # Год постройки - улучшенный парсинг
            year_built = ""
            # Ищем различные варианты: "1967 г.", "год постройки: 1967", "построен в 1967", "1967 год"
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
                        # Проверяем разумность года (1900-2025)
                        if 1900 <= year_int <= 2025:
                            year_built = str(year_int)
                            break
                    except:
                        pass
            
            # Дата создания объявления
            created_at = ""
            # Ищем дату в тексте: "сегодня", "вчера", "X дней назад", "DD.MM.YYYY"
            date_patterns = [
                r'сегодня',
                r'вчера',
                r'(\d+)\s+дн[яей]\s+назад',
                r'(\d{1,2})\.(\d{1,2})\.(\d{4})',  # DD.MM.YYYY
                r'(\d{4})-(\d{1,2})-(\d{1,2})',  # YYYY-MM-DD
            ]
            for pattern in date_patterns:
                date_match = re.search(pattern, text, re.IGNORECASE)
                if date_match:
                    if pattern == r'сегодня':
                        created_at = datetime.now().strftime("%Y-%m-%d")
                        break
                    elif pattern == r'вчера':
                        created_at = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                        break
                    elif pattern == r'(\d+)\s+дн[яей]\s+назад':
                        days_ago = int(date_match.group(1))
                        created_at = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
                        break
                    elif pattern == r'(\d{1,2})\.(\d{1,2})\.(\d{4})':
                        day, month, year = date_match.groups()
                        created_at = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
                        break
                    elif pattern == r'(\d{4})-(\d{1,2})-(\d{1,2})':
                        created_at = date_match.group(0)
                        break
            
            # Адрес - ищем "ул. XXX"
            city_name = city.title()
            address = city_name
            addr_match = re.search(r'ул\.\s*([^,\d]+)', text)
            if addr_match:
                address = f"ул. {addr_match.group(1).strip()}, {city_name}"
            
            # Пропускаем если цена = 0
            if price_byn == 0:
                return None
            
            # Конвертация BYN -> USD (курс ~2.95)
            price_usd = int(price_byn / 2.95) if price_byn else 0
            
            # Фото - временно отключено (требуют авторизацию или неверный формат)
            photos = []
            # TODO: Исправить парсинг фото Etagi
            # for img in container.find_all('img', src=True)[:3]:
            #     img_src = img.get('src', '')
            #     if img_src and not any(x in img_src.lower() for x in ['sprite', 'icon', 'logo', 'placeholder']):
            #         if not img_src.startswith('http'):
            #             img_src = f"{self.BASE_URL}{img_src}"
            #         photos.append(img_src)
            
            # Формируем заголовок
            title = f"{rooms}-комн., {area} м²" if rooms and area else "Квартира"
            
            # Валидация через DTO перед созданием полного Listing
            dto = self.validate_listing_data(
                title=title,
                price=price_byn,
                url=url,
                location=address,
                source="Etagi.com"
            )
            
            if not dto:
                # Данные не прошли валидацию
                log_warning("etagi", f"Объявление не прошло валидацию DTO: title='{title[:50]}...', price={price_byn}, url={url[:50]}...")
                return None
            
            # Создаем полный Listing объект из валидного DTO
            return Listing(
                id=listing_id,
                source=dto.source,
                title=dto.title,
                price=dto.price,
                price_formatted=f"{price_byn:,} BYN (≈${price_usd:,})".replace(",", " "),
                rooms=rooms,
                area=area,
                floor=floor,
                address=dto.location,
                photos=photos,
                url=dto.url,
                currency="BYN",
                price_usd=price_usd,
                price_byn=price_byn,
                price_per_sqm=price_per_sqm,
                price_per_sqm_formatted=f"{price_per_sqm:,} BYN/м²".replace(",", " ") if price_per_sqm else "",
                year_built=year_built,
                created_at=created_at,
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
